"""Out-of-band OS-level supervisor for SASK v2.0.

Runs as a separate process (``python -m sask_v2.supervisor``), outside the
agent, and:

- watches a heartbeat file and kills workers whose heartbeat goes stale or
  whose resident memory exceeds the ceiling;
- rotates ``cycle_telemetry.jsonl`` when it grows or free disk runs low;
- detects boot loops (repeated crashes inside a window) and files a rollback
  request; when rollback is explicitly enabled it resets the working tree to a
  last-known-good tag behind a recovery branch;
- pins its own source hash against ``kernel_manifest.json`` and refuses to run
  on drift unless the operator overrides.

Killing uses the OS process tree (``taskkill /T /F`` on Windows, process group
SIGKILL on POSIX). Memory observation uses ``psutil`` when installed and falls
back to ``tasklist`` / ``/proc``; absence of a metric is reported, never
guessed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import boundary

STATE_DIRNAME = "sask_v2/state"
HEARTBEAT_NAME = "heartbeat.json"
BOOT_EVENTS_NAME = "boot_events.json"
ROLLBACK_REQUEST_NAME = "rollback_request.json"
MANIFEST_NAME = boundary.MANIFEST_NAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SupervisorConfig:
    root: Path
    interval: float = 5.0
    hang_timeout: float = 900.0
    max_rss_mb: float = 6144.0
    min_free_gb: float = 2.0
    max_telemetry_bytes: int = 64 * 1024 * 1024
    bootloop_threshold: int = 3
    bootloop_window: float = 600.0
    allow_rollback: bool = False
    last_good_tag: str = "v1.0"

    @property
    def state_dir(self) -> Path:
        return Path(self.root) / STATE_DIRNAME

    @property
    def heartbeat_path(self) -> Path:
        return self.state_dir / HEARTBEAT_NAME

    @property
    def telemetry_path(self) -> Path:
        return Path(self.root) / "cycle_telemetry.jsonl"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def write_heartbeat(root: Path | str, pid: Optional[int] = None, cycle: int = 0) -> Path:
    path = Path(root) / STATE_DIRNAME / HEARTBEAT_NAME
    _write_json(path, {"pid": int(pid if pid is not None else os.getpid()), "epoch": time.time(), "cycle": int(cycle), "written_at": _utc_now()})
    return path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore[import]

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    if os.name == "nt":
        try:
            output = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return f'"{pid}"' in output
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _rss_mb(pid: int) -> Optional[float]:
    try:
        import psutil  # type: ignore[import]

        process = psutil.Process(pid)
        return round(process.memory_info().rss / (1024 * 1024), 2)
    except ImportError:
        pass
    except Exception:
        return None
    if os.name == "nt":
        try:
            output = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        if not output or not output.startswith('"'):
            return None
        try:
            fields = [field.strip('"') for field in output.split('","')]
            memory = fields[4].replace(",", "").replace(" K", "").strip()
            return round(float(memory) / 1024.0, 2)
        except (IndexError, ValueError):
            return None
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                return round(float(parts[1]) / 1024.0, 2)
    return None


def _kill_tree(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return True
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except OSError:
            return False


def check_pin(root: Path | str) -> Dict[str, Any]:
    root_path = Path(root)
    manifest = _read_json(root_path / MANIFEST_NAME, {})
    expected = (manifest.get("files") or {}).get("sask_v2/supervisor.py")
    source = root_path / "sask_v2" / "supervisor.py"
    if not source.is_file():
        return {"ok": True, "reason": "supervisor_absent"}
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if expected is None:
        return {"ok": True, "reason": "not_frozen", "actual": digest}
    return {"ok": expected == digest, "reason": "pinned" if expected == digest else "drift", "expected": expected, "actual": digest}


class Watchdog:
    def __init__(self, config: SupervisorConfig) -> None:
        self.config = config
        self.root = Path(config.root).resolve()
        self.state_dir = self.root / STATE_DIRNAME
        self._boot_events: List[float] = self._load_boot_events()

    # -- boot-loop state ---------------------------------------------------

    def _load_boot_events(self) -> List[float]:
        payload = _read_json(self.state_dir / BOOT_EVENTS_NAME, {})
        events = payload.get("events") if isinstance(payload, dict) else None
        return [float(item) for item in (events or []) if isinstance(item, (int, float))]

    def _save_boot_events(self, now: float) -> None:
        window = max(1.0, float(self.config.bootloop_window))
        self._boot_events = [item for item in self._boot_events if now - item <= window]
        _write_json(self.state_dir / BOOT_EVENTS_NAME, {"events": self._boot_events[-50:], "updated_at": _utc_now()})

    def _record_crash(self, now: float, reason: str) -> None:
        self._boot_events.append(float(now))
        self._save_boot_events(now)
        _write_json(
            self.state_dir / "last_crash.json",
            {"epoch": float(now), "reason": reason, "recorded_at": _utc_now()},
        )

    # -- telemetry rotation ------------------------------------------------

    def rotate_telemetry(self) -> Optional[str]:
        path = self.config.telemetry_path
        if not path.is_file() or path.stat().st_size == 0:
            return None
        archive_dir = self.state_dir / "telemetry_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = archive_dir / f"{path.name}.{stamp}.gz"
        try:
            with path.open("rb") as source, gzip.open(archive, "wb") as sink:
                sink.write(source.read())
            with path.open("w", encoding="utf-8"):
                pass
        except OSError:
            return None
        archives = sorted(archive_dir.glob(f"{path.name}.*.gz"))
        for stale in archives[:-5]:
            try:
                stale.unlink()
            except OSError:
                pass
        return str(archive)

    # -- main tick ---------------------------------------------------------

    def tick(self, now: Optional[float] = None) -> Dict[str, Any]:
        moment = time.time() if now is None else float(now)
        actions: List[Dict[str, Any]] = []
        heartbeat = _read_json(self.config.heartbeat_path, {})
        heartbeat_age: Optional[float] = None
        if isinstance(heartbeat, dict) and heartbeat:
            pid = int(heartbeat.get("pid", 0) or 0)
            epoch = float(heartbeat.get("epoch", 0.0) or 0.0)
            heartbeat_age = round(max(0.0, moment - epoch), 3)
            if heartbeat_age > float(self.config.hang_timeout):
                if _pid_alive(pid):
                    killed = _kill_tree(pid)
                    self._record_crash(moment, "hang")
                    actions.append({"action": "kill_hang", "pid": pid, "killed": killed, "age": heartbeat_age})
                else:
                    actions.append({"action": "stale_heartbeat", "pid": pid, "age": heartbeat_age})
            else:
                rss = _rss_mb(pid)
                if rss is not None and rss > float(self.config.max_rss_mb):
                    killed = _kill_tree(pid)
                    self._record_crash(moment, "rss")
                    actions.append({"action": "kill_rss", "pid": pid, "rss_mb": rss, "killed": killed})
        else:
            actions.append({"action": "no_heartbeat"})

        telemetry = self.config.telemetry_path
        try:
            size = telemetry.stat().st_size if telemetry.is_file() else 0
        except OSError:
            size = 0
        try:
            free_bytes = shutil_disk_free(self.root)
        except OSError:
            free_bytes = float("inf")
        if size > int(self.config.max_telemetry_bytes) or (
            free_bytes < float(self.config.min_free_gb) * (1024 ** 3) and size > 0
        ):
            archive = self.rotate_telemetry()
            actions.append(
                {
                    "action": "rotate_telemetry",
                    "archive": archive,
                    "freed_bytes": size,
                    "free_gb": round(free_bytes / (1024 ** 3), 2),
                }
            )

        recent = [item for item in self._boot_events if moment - item <= float(self.config.bootloop_window)]
        if len(recent) >= int(self.config.bootloop_threshold):
            _write_json(
                self.state_dir / ROLLBACK_REQUEST_NAME,
                {
                    "requested_at": _utc_now(),
                    "crashes": len(recent),
                    "window_seconds": float(self.config.bootloop_window),
                    "target": self.config.last_good_tag,
                },
            )
            action: Dict[str, Any] = {
                "action": "rollback_request",
                "crashes": len(recent),
                "tag": self.config.last_good_tag,
            }
            if self.config.allow_rollback:
                from .repair import RepairEngine
                from .knowledge import KnowledgeStore

                engine = RepairEngine(KnowledgeStore(key=None))
                action["rollback"] = engine.rollback_to_tag(self.root, self.config.last_good_tag, allow=True)
                self._boot_events = []
                self._save_boot_events(moment)
            actions.append(action)

        kills = [item for item in actions if item.get("action") in {"kill_hang", "kill_rss"}]
        return {
            "ts": _utc_now(),
            "heartbeat_age": heartbeat_age,
            "actions": actions,
            "healthy": not kills,
            "state_dir": str(self.state_dir),
        }


def shutil_disk_free(path: Path) -> int:
    import shutil

    return shutil.disk_usage(str(path)).free


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SASK v2.0 out-of-band supervisor")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--once", action="store_true", help="run a single tick and exit")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--hang-timeout", type=float, default=900.0)
    parser.add_argument("--max-rss-mb", type=float, default=6144.0)
    parser.add_argument("--min-free-gb", type=float, default=2.0)
    parser.add_argument("--max-telemetry-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--bootloop-threshold", type=int, default=3)
    parser.add_argument("--bootloop-window", type=float, default=600.0)
    parser.add_argument("--allow-rollback", action="store_true")
    parser.add_argument("--tag", default="v1.0")
    parser.add_argument("--skip-pin", action="store_true", help="skip supervisor self-hash pinning")
    parser.add_argument(
        "--guard",
        action="store_true",
        help="install the in-process audit hook for this supervisor process",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not args.skip_pin:
        pin = check_pin(root)
        if not pin.get("ok"):
            print(json.dumps({"supervisor": "refused", "pin": pin}, indent=2))
            return 2
    if args.guard:
        boundary.install_audit_hook(root, extra_kernel=[root / MANIFEST_NAME])

    config = SupervisorConfig(
        root=root,
        interval=max(0.1, args.interval),
        hang_timeout=max(0.1, args.hang_timeout),
        max_rss_mb=max(64.0, args.max_rss_mb),
        min_free_gb=max(0.0, args.min_free_gb),
        max_telemetry_bytes=max(1024, args.max_telemetry_bytes),
        bootloop_threshold=max(1, args.bootloop_threshold),
        bootloop_window=max(1.0, args.bootloop_window),
        allow_rollback=bool(args.allow_rollback),
        last_good_tag=str(args.tag),
    )
    watchdog = Watchdog(config)
    if args.once:
        print(json.dumps(watchdog.tick(), indent=2))
        return 0
    try:
        while True:
            print(json.dumps(watchdog.tick()))
            sys.stdout.flush()
            time.sleep(config.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
