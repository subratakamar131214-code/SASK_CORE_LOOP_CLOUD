"""Execution boundary policy for SASK v2.0.

Three zones with different write authority:

``out_of_band`` (OS supervisor)
    Runs as a separate process (``python -m sask_v2.supervisor``), hash-pinned
    against ``kernel_manifest.json``. It may kill workers, rotate telemetry,
    and (only when explicitly enabled) roll the working tree back to a
    last-known-good tag.

``immutable_kernel``
    ``verify.py``, ``scale_orchestrator.py``, ``novelty_vault.py``, the sealed
    suites, the integrity manifest, and this package's own boundary/supervisor
    code. Autonomous writers may read these files but never mutate them.

``mutable_ring``
    Rewrite tables, prompt templates, endpoint adapters, and non-kernel
    helpers under ``sask_v2/ring``, ``sask_v2/templates`` and
    ``sask_v2/adapters``. These are the only files the repair engine may patch.

The quarantine (``ip_quarantine`` and ``artifacts/private_candidates``) is not
a fourth writable zone: it is writable **only** while a ``QuarantineWriter``
capability is active in the current thread. That capability is private to this
module, so ordinary v2 code (and any injected code that imports it) cannot
forge one.

Enforcement is layered and deliberately honest about its limits:

1. ``assert_mutable`` / ``quarantine_writer`` are the cooperative in-process
   policy every v2 writer calls.
2. ``install_audit_hook`` uses ``sys.addaudithook`` to raise
   ``BoundaryViolation`` for write-mode ``open``/``os.*`` events that target a
   kernel file or the quarantine without an active capability. Because an
   audit hook is per-process, each autonomous Python process must install it
   at startup (``python -m sask_v2.supervisor --guard`` does this for the
   supervisor); once installed in a process it cannot be removed. The OS seal
   below is the layer that spans unrelated processes.
3. ``KernelGuard.seal_os`` clears the write bit / sets the Windows read-only
   attribute on kernel files. This blocks non-privileged writers at the OS
   boundary. It is intentionally opt-in (``freeze --harden``) because it also
   blocks maintainers running as the same OS user, who must ``thaw`` first.

Same-user OS accounts can always chmod or kill their own processes. True
privilege separation requires running the supervisor as a different OS user or
service account (see ``docs/V2_AUTONOMY.md``); nothing here pretends otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

ZONE_OUT_OF_BAND = "out_of_band"
ZONE_KERNEL = "immutable_kernel"
ZONE_MUTABLE = "mutable_ring"
ZONE_QUARANTINE = "quarantine"
ZONE_EXTERNAL = "unclassified"

IMMUTABLE_FILES: Tuple[str, ...] = (
    "verify.py",
    "scale_orchestrator.py",
    "novelty_vault.py",
    "verify_integrity.json",
    "verify_holdout_suite.json",
    "verify_golden_suite.json",
    "sask_v2/boundary.py",
    "sask_v2/supervisor.py",
)
OUT_OF_BAND_FILES: Tuple[str, ...] = (
    "sask_v2/supervisor.py",
    "sask_v2/boundary.py",
)
MUTABLE_PREFIXES: Tuple[str, ...] = (
    "sask_v2/ring/",
    "sask_v2/templates/",
    "sask_v2/adapters/",
)
QUARANTINE_PREFIXES: Tuple[str, ...] = (
    "ip_quarantine/",
    "artifacts/private_candidates/",
)

MANIFEST_NAME = "sask_v2/kernel_manifest.json"
SCHEMA_VERSION = 1


class BoundaryViolation(PermissionError):
    """Raised when code attempts a write outside its authority."""


class KernelDrift(RuntimeError):
    """Raised when a kernel file no longer matches the frozen manifest."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _abspath(path: Any) -> Optional[str]:
    try:
        raw = os.fspath(path)
    except TypeError:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "surrogateescape")
    if not raw:
        return None
    return os.path.normcase(os.path.abspath(raw))


def _matches_prefix(rel: str, prefix: str) -> bool:
    return rel == prefix.rstrip("/") or rel.startswith(prefix)


def _under(path: str, base: str) -> bool:
    try:
        common = os.path.commonpath([path, base])
    except ValueError:
        return False
    return common == base


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Boundary:
    """Resolves a path to a zone relative to a repository root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def relative(self, path: Path | str) -> str:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            return candidate.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return ""

    def zone_for(self, path: Path | str) -> str:
        rel = self.relative(path)
        if not rel:
            return ZONE_EXTERNAL
        if rel in OUT_OF_BAND_FILES:
            return ZONE_OUT_OF_BAND
        if rel in IMMUTABLE_FILES:
            return ZONE_KERNEL
        if any(_matches_prefix(rel, prefix) for prefix in QUARANTINE_PREFIXES):
            return ZONE_QUARANTINE
        if any(_matches_prefix(rel, prefix) for prefix in MUTABLE_PREFIXES):
            return ZONE_MUTABLE
        return ZONE_EXTERNAL

    def kernel_paths(self) -> List[Path]:
        return [self.root / name for name in IMMUTABLE_FILES if (self.root / name).is_file()]

    def quarantine_roots(self) -> List[Path]:
        return [self.root / prefix for prefix in QUARANTINE_PREFIXES]

    def assert_mutable(self, path: Path | str) -> Path:
        zone = self.zone_for(path)
        if zone != ZONE_MUTABLE:
            raise BoundaryViolation(
                f"write denied: {self.relative(path) or path} is {zone}, not {ZONE_MUTABLE}"
            )
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        return target


def _infer_root(path: Path | str) -> Optional[Path]:
    parts = Path(path).resolve().parts
    for prefix in MUTABLE_PREFIXES:
        segments = Path(prefix).parts
        for index in range(len(parts) - len(segments) + 1):
            if parts[index:index + len(segments)] == segments:
                return Path(*parts[:index]) if index else Path(".")
    return None


def assert_mutable(path: Path | str, root: Optional[Path | str] = None) -> Path:
    """Module-level gate used by the repair engine.

    The current working directory is not trusted, so the repository root is
    inferred from the mutable-ring segment when the caller does not supply it.
    """
    boundary_root = Path(root) if root is not None else _infer_root(path)
    if boundary_root is None:
        raise BoundaryViolation(f"write denied: {path} is outside every mutable ring")
    return Boundary(boundary_root).assert_mutable(path)


# ---------------------------------------------------------------------------
# Quarantine write capability
# ---------------------------------------------------------------------------


class _Capability:
    __slots__ = ()


_CAPABILITY = _Capability()
_local = threading.local()


def _capability_stack() -> List[str]:
    stack = getattr(_local, "caps", None)
    if stack is None:
        stack = []
        _local.caps = stack
    return stack


class QuarantineWriter:
    """The only sanctioned way to write beneath the private quarantine.

    The constructor token is module-private; v2 and generated code cannot mint
    a writer. While the context manager is active in the current thread the
    audit hook permits writes beneath this writer's root.
    """

    __slots__ = ("root",)

    def __init__(self, token: object, root: Path | str) -> None:
        if token is not _CAPABILITY:
            raise BoundaryViolation("quarantine capability is module-private")
        self.root = Path(root).resolve()

    def __enter__(self) -> "QuarantineWriter":
        _capability_stack().append(os.path.normcase(os.path.abspath(str(self.root))))
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        stack = _capability_stack()
        if stack:
            stack.pop()
        return False

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def _write(self, parts: Sequence[str], data: bytes, mode: int) -> Path:
        target = self.path(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        with tmp.open("wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
        try:
            os.chmod(target, mode)
        except OSError:
            pass
        return target

    def write_bytes(self, *parts: str, data: bytes, mode: int = 0o600) -> Path:
        return self._write(parts, data, mode)

    def write_text(self, *parts: str, text: str, mode: int = 0o600) -> Path:
        return self._write(parts, text.encode("utf-8"), mode)

    def append_line(self, *parts: str, record: Dict[str, Any]) -> Path:
        target = self.path(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        return target


def quarantine_writer(root: Path | str) -> QuarantineWriter:
    return QuarantineWriter(_CAPABILITY, root)


# ---------------------------------------------------------------------------
# Runtime audit hook
# ---------------------------------------------------------------------------

_AUDIT_INSTALLED = False
_AUDIT_LOCK = threading.Lock()
_WRITE_EVENTS = {
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.chmod": (0,),
    "os.rename": (0, 1),
    "os.replace": (0, 1),
    "os.truncate": (0,),
    "shutil.copyfile": (1,),
    "shutil.move": (0, 1),
}


def _mode_is_write(mode: Any, flags: Any) -> bool:
    if isinstance(mode, str):
        return any(ch in mode for ch in ("w", "a", "x", "+"))
    if isinstance(flags, int):
        write_bits = (
            getattr(os, "O_WRONLY", 0)
            | getattr(os, "O_RDWR", 0)
            | getattr(os, "O_CREAT", 0)
            | getattr(os, "O_APPEND", 0)
            | getattr(os, "O_TRUNC", 0)
        )
        return bool(flags & write_bits)
    return False


def install_audit_hook(
    root: Path | str,
    extra_kernel: Iterable[Path | str] = (),
) -> Dict[str, Any]:
    """Block write events targeting kernel files or the quarantine.

    Idempotent per process. Installed by the supervisor before it spawns
    workers, and by boundary tests in child processes.
    """
    global _AUDIT_INSTALLED
    boundary = Boundary(root)
    kernel = {os.path.normcase(os.path.abspath(str(path))) for path in boundary.kernel_paths()}
    for extra in extra_kernel:
        kernel.add(os.path.normcase(os.path.abspath(str(extra))))
    quarantine = [os.path.normcase(os.path.abspath(str(path))) for path in boundary.quarantine_roots()]

    def _guard(path: Any) -> None:
        resolved = _abspath(path)
        if not resolved:
            return
        if any(_under(resolved, item) for item in kernel):
            raise BoundaryViolation(f"kernel file is read-only: {resolved}")
        if any(_under(resolved, item) for item in quarantine):
            caps = _capability_stack()
            if not any(_under(resolved, cap) for cap in caps):
                raise BoundaryViolation(f"quarantine requires a writer capability: {resolved}")

    def _hook(event: str, args: Tuple[Any, ...]) -> None:
        if event == "open":
            path = args[0] if args else None
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            if _mode_is_write(mode, flags):
                _guard(path)
            return
        indexes = _WRITE_EVENTS.get(event)
        if indexes is None:
            return
        for index in indexes:
            if index < len(args):
                _guard(args[index])

    with _AUDIT_LOCK:
        if not _AUDIT_INSTALLED:
            sys.addaudithook(_hook)
            _AUDIT_INSTALLED = True
    return {
        "installed": True,
        "kernel_files": sorted(kernel),
        "quarantine_roots": quarantine,
    }


# ---------------------------------------------------------------------------
# Kernel manifest
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KernelGuard:
    """Freeze and verify the immutable-kernel digest set."""

    def __init__(self, root: Path | str) -> None:
        self.boundary = Boundary(root)
        self.root = self.boundary.root

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    def compute(self) -> Dict[str, str]:
        digests: Dict[str, str] = {}
        for name in IMMUTABLE_FILES:
            path = self.root / name
            if path.is_file():
                digests[name] = _sha256_file(path)
        return digests

    def freeze(self, harden: bool = False) -> Dict[str, Any]:
        manifest = {
            "schema": SCHEMA_VERSION,
            "frozen_at": _utc_now(),
            "files": self.compute(),
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.manifest_path)
        result: Dict[str, Any] = {"manifest": str(self.manifest_path), "files": len(manifest["files"])}
        if harden:
            result["hardened"] = self.seal_os()
        return result

    def load(self) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def drift(self) -> List[Dict[str, Any]]:
        manifest = self.load()
        if manifest is None:
            return [{"file": "<manifest>", "expected": None, "actual": None, "reason": "missing_manifest"}]
        expected: Dict[str, str] = manifest.get("files") or {}
        actual = self.compute()
        rows: List[Dict[str, Any]] = []
        for name in sorted(set(expected) | set(actual)):
            want = expected.get(name)
            have = actual.get(name)
            if want != have:
                rows.append({"file": name, "expected": want, "actual": have})
        return rows

    def assert_intact(self) -> None:
        drift = self.drift()
        if drift:
            names = ", ".join(row["file"] for row in drift)
            raise KernelDrift(f"immutable kernel drift detected: {names}")

    # -- OS-level hardening ------------------------------------------------

    def seal_os(self) -> List[str]:
        """Clear write permission on kernel files (opt-in, maintainer action)."""
        sealed: List[str] = []
        for name, path in zip(IMMUTABLE_FILES, [self.root / n for n in IMMUTABLE_FILES]):
            if not path.is_file():
                continue
            try:
                current = path.stat().st_mode
                os.chmod(path, current & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
                sealed.append(name)
            except OSError:
                continue
        return sealed

    def thaw_os(self) -> List[str]:
        """Restore owner write permission so maintainers can edit kernel files."""
        thawed: List[str] = []
        for name in IMMUTABLE_FILES:
            path = self.root / name
            if not path.is_file():
                continue
            try:
                current = path.stat().st_mode
                os.chmod(path, current | stat.S_IWUSR)
                thawed.append(name)
            except OSError:
                continue
        return thawed

    def describe(self) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for name in sorted(set(IMMUTABLE_FILES) | set(MUTABLE_PREFIXES) | set(QUARANTINE_PREFIXES) | set(OUT_OF_BAND_FILES)):
            zone = self.boundary.zone_for(name)
            rows.append({"path": name, "zone": zone})
        return rows
