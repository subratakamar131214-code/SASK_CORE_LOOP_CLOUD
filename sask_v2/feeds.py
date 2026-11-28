"""Read-only empirical ingestion for SASK v2.0.

Feeds are fetched over HTTPS from an explicit host allowlist, capped in size,
hashed with SHA-256, and cached beneath the private quarantine through a
writer capability. Nothing in this module writes to the upstream source; local
fixtures can be loaded directly for offline verification. Every dataset carries
its content hash so hypotheses can be tied to an exact empirical snapshot.
"""

from __future__ import annotations

import csv
import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlsplit

from . import boundary

DEFAULT_ALLOW_HOSTS = frozenset(
    {
        "api.github.com",
        "raw.githubusercontent.com",
        "storage.googleapis.com",
        "datasets.githubusercontent.com",
    }
)
MAX_BYTES = 10_000_000


class FeedError(RuntimeError):
    """Raised when a feed violates the read-only ingestion policy."""


def _default_transport(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "sask-v2-feeds/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - allowlisted https only
        return response.read(MAX_BYTES + 1)


@dataclass
class DatasetRef:
    name: str
    sha256: str
    rows: List[Dict[str, Any]]
    source: str
    cached_path: Optional[Path] = None


class FeedRegistry:
    """Allowlisted, content-addressed fetch with a local read-only cache."""

    def __init__(
        self,
        cache_root: Path | str,
        allow_hosts: Optional[Iterable[str]] = None,
        transport: Optional[Callable[[str, float], bytes]] = None,
        timeout: float = 20.0,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self.cache_root = Path(cache_root)
        self.allow_hosts = frozenset(host.lower() for host in (allow_hosts or DEFAULT_ALLOW_HOSTS))
        self.transport = transport or _default_transport
        self.timeout = max(1.0, float(timeout))
        self.max_bytes = int(max_bytes)

    def _validate(self, url: str) -> str:
        parts = urlsplit(str(url))
        if parts.scheme != "https":
            raise FeedError("only https feeds are allowed")
        host = (parts.hostname or "").lower()
        if host not in self.allow_hosts:
            raise FeedError(f"host not allowlisted: {host or '<none>'}")
        return host

    def _read_index(self) -> Dict[str, str]:
        try:
            payload = json.loads(
                (self.cache_root / "datasets" / "index.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items()}

    def _cached(self, digest: str, url: str) -> Optional[Dict[str, Any]]:
        bin_path = self.cache_root / "datasets" / f"{digest}.bin"
        meta_path = self.cache_root / "datasets" / f"{digest}.json"
        if not (bin_path.is_file() and meta_path.is_file()):
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        return {
            "sha256": digest,
            "bytes": bin_path.stat().st_size,
            "path": str(bin_path),
            "cached": True,
            "source": str(meta.get("source") or url),
        }

    def fetch(self, url: str) -> Dict[str, Any]:
        self._validate(url)
        # A previously recorded URL->digest pair avoids a repeat download.
        # Deleting ``datasets/index.json`` forces a fresh pull.
        known = self._read_index().get(str(url))
        if known:
            cached = self._cached(known, url)
            if cached is not None:
                return cached

        payload = self.transport(url, self.timeout)
        if not isinstance(payload, (bytes, bytearray)):
            raise FeedError("transport returned a non-bytes payload")
        if len(payload) > self.max_bytes:
            raise FeedError(f"feed exceeds {self.max_bytes} bytes")
        digest = hashlib.sha256(bytes(payload)).hexdigest()
        writer = boundary.quarantine_writer(self.cache_root)
        existing = self._cached(digest, url)
        if existing is not None:
            return existing
        index = self._read_index()
        index[str(url)] = digest
        with writer:
            writer.write_bytes("datasets", f"{digest}.bin", data=bytes(payload))
            writer.write_text(
                "datasets",
                f"{digest}.json",
                text=json.dumps({"sha256": digest, "source": url, "bytes": len(payload)}, indent=2),
            )
            writer.write_text(
                "datasets",
                "index.json",
                text=json.dumps(index, indent=2, sort_keys=True),
            )
        return {
            "sha256": digest,
            "bytes": len(payload),
            "path": str(writer.path("datasets", f"{digest}.bin")),
            "cached": False,
            "source": url,
        }

    def ingest_csv(self, name: str, url: str) -> DatasetRef:
        record = self.fetch(url)
        path = Path(record["path"])
        rows = load_csv(path)
        return DatasetRef(name=name, sha256=record["sha256"], rows=rows, source=record["source"], cached_path=path)


def load_csv(path: Path | str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            row: Dict[str, Any] = {}
            for key, value in raw.items():
                text = "" if value is None else str(value).strip()
                try:
                    row[key] = float(text)
                except ValueError:
                    row[key] = text
            rows.append(row)
    return rows


class EmpiricalValidator:
    """Compare a hypothesis against measured columns of a dataset."""

    @staticmethod
    def validate(
        hypothesis: Callable[[Dict[str, Any]], float],
        rows: Sequence[Dict[str, Any]],
        target_key: str,
        tolerance: float = 1e-6,
    ) -> Dict[str, Any]:
        errors: List[float] = []
        failures: List[Dict[str, Any]] = []
        for index, row in enumerate(rows):
            if target_key not in row:
                continue
            try:
                predicted = float(hypothesis(row))
            except Exception as exc:
                failures.append({"row": index, "error": type(exc).__name__})
                continue
            try:
                expected = float(row[target_key])
            except (TypeError, ValueError):
                continue
            errors.append(abs(predicted - expected))
            if abs(predicted - expected) > tolerance and len(failures) < 10:
                failures.append({"row": index, "predicted": predicted, "expected": expected})
        if not errors:
            return {"ok": False, "reason": "no_comparable_rows", "rows": len(rows)}
        mae = sum(errors) / len(errors)
        max_error = max(errors)
        return {
            "ok": max_error <= tolerance,
            "rows": len(errors),
            "mae": round(mae, 10),
            "max_error": round(max_error, 10),
            "tolerance": tolerance,
            "failures": failures,
        }
