"""Compounding non-public knowledge store for SASK v2.0.

Proven lemmas, rewrite rules, distilled cloud solutions, and failure
signatures live in an AES-256-GCM sealed blob beneath the private quarantine.
The key comes from ``SASK_VAULT_KEY`` (or an explicit argument) and is only
ever held in memory. Without a key the store is an in-memory scratch pad: it
never writes plaintext to disk, and it will not report a durable save.

The crypto primitives are reused from the v1.0 ``novelty_vault`` module rather
than reimplemented, so the kernel's sealed format stays authoritative.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import novelty_vault  # type: ignore[import]

from . import boundary

SCHEMA_VERSION = 1
VAULT_KEY_ENV = "SASK_VAULT_KEY"
MIN_KEY_CHARS = 16
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "ip_quarantine" / "knowledge"
STORE_FILE = "store.saskv"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signature(payload: str) -> str:
    return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()


class KnowledgeStore:
    """Encrypted, deduplicated rule store with utility-based pruning."""

    def __init__(
        self,
        root: Optional[Path | str] = None,
        key: Optional[str] = None,
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self._secret = key if key is not None else os.environ.get(VAULT_KEY_ENV)
        if self._secret is not None and len(self._secret) < MIN_KEY_CHARS:
            self._secret = None
            self.status = "invalid_key"
        else:
            self.status = "unlocked" if self._secret else "memory_only"
        self._records: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._load_failed = False
        self._lock = threading.RLock()
        if self._secret:
            self._load()

    # -- persistence -------------------------------------------------------

    @property
    def store_path(self) -> Path:
        return self.root / STORE_FILE

    def _load(self) -> None:
        if not self.store_path.is_file():
            self.status = "unlocked_empty"
            return
        try:
            blob = self.store_path.read_bytes()
            plain = novelty_vault.decrypt_bundle(blob, self._secret or "")
            payload = json.loads(plain.decode("utf-8"))
        except Exception:
            self.status = "locked"
            self._load_failed = True
            return
        records = payload.get("records") if isinstance(payload, dict) else None
        for record in records or ():
            if isinstance(record, dict) and isinstance(record.get("signature"), str):
                self._records[record["signature"]] = dict(record)
        self.status = "unlocked"

    def flush(self) -> Dict[str, Any]:
        with self._lock:
            if not self._secret:
                return {"status": "not_persisted", "reason": "no_key", "records": len(self._records)}
            if self._load_failed:
                return {"status": "not_persisted", "reason": "vault_unreadable_refusing_to_overwrite"}
            payload = {
                "schema": SCHEMA_VERSION,
                "updated_at": _utc_now(),
                "records": [self._records[key] for key in sorted(self._records)],
            }
            blob = novelty_vault.encrypt_bundle(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"),
                self._secret,
            )
            writer = boundary.quarantine_writer(self.root)
            with writer:
                target = writer.path(STORE_FILE)
                tmp = target.with_name(f".{STORE_FILE}.{os.getpid()}.tmp")
                target.parent.mkdir(parents=True, exist_ok=True)
                with tmp.open("wb") as handle:
                    handle.write(blob)
                os.replace(tmp, target)
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
            self._dirty = False
            return {"status": "persisted", "records": len(self._records), "bytes": len(blob)}

    # -- record lifecycle --------------------------------------------------

    def upsert(
        self,
        record: Dict[str, Any],
        *,
        signature_from: Optional[str] = None,
        utility_delta: float = 1.0,
    ) -> Dict[str, Any]:
        signature = str(record.get("signature") or _signature(signature_from or json.dumps(record, sort_keys=True, default=str)))
        with self._lock:
            existing = self._records.get(signature)
            if existing is None:
                entry = dict(record)
                entry["signature"] = signature
                entry.setdefault("created_at", _utc_now())
                entry["uses"] = int(entry.get("uses", 0) or 0) + 1
                entry["utility"] = round(float(entry.get("utility", 0.0) or 0.0) + float(utility_delta), 4)
                entry["last_used_cycle"] = int(entry.get("last_used_cycle", 0) or 0)
                self._records[signature] = entry
                self._dirty = True
                return {"status": "created", "signature": signature, "record": dict(entry)}
            existing["uses"] = int(existing.get("uses", 0) or 0) + 1
            existing["utility"] = round(float(existing.get("utility", 0.0) or 0.0) + float(utility_delta), 4)
            for key, value in record.items():
                if key not in {"signature", "created_at", "uses", "utility", "last_used_cycle"}:
                    existing[key] = value
            self._dirty = True
            return {"status": "updated", "signature": signature, "record": dict(existing)}

    def lookup(self, signature: str) -> Optional[Dict[str, Any]]:
        record = self._records.get(str(signature))
        return dict(record) if record is not None else None

    def records(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            values = [dict(record) for record in self._records.values()]
        if kind is not None:
            values = [record for record in values if record.get("kind") == kind]
        return sorted(values, key=lambda record: record.get("signature", ""))

    def prune(
        self,
        min_utility: float = 0.0,
        max_unused_cycles: int = 10,
        current_cycle: int = 0,
    ) -> Dict[str, Any]:
        removed: List[str] = []
        with self._lock:
            for signature, record in list(self._records.items()):
                idle = int(current_cycle) - int(record.get("last_used_cycle", 0) or 0)
                if float(record.get("utility", 0.0) or 0.0) <= float(min_utility) and idle >= int(max_unused_cycles):
                    removed.append(signature)
                    del self._records[signature]
                    self._dirty = True
        return {"removed": removed, "remaining": len(self._records)}

    # -- domain helpers ----------------------------------------------------

    def remember_failure(self, signature: str, detail: str = "", max_attempts: int = 3) -> Dict[str, Any]:
        record = self._records.get(signature) or {
            "kind": "failure_signature",
            "signature": signature,
            "attempts": 0,
            "blacklisted": False,
            "created_at": _utc_now(),
            "last_used_cycle": 0,
            "utility": 0.0,
            "uses": 0,
        }
        record["attempts"] = int(record.get("attempts", 0) or 0) + 1
        record["last_error"] = str(detail)[:280]
        record["blacklisted"] = record["attempts"] >= int(max_attempts)
        with self._lock:
            self._records[signature] = record
            self._dirty = True
        return dict(record)

    def is_blacklisted(self, signature: str) -> bool:
        record = self._records.get(signature)
        return bool(record and record.get("blacklisted"))

    def distill(self, prompt: str, response: str, max_chars: int = 1200) -> Dict[str, Any]:
        signature = _signature(prompt)
        return self.upsert(
            {
                "kind": "distilled_rule",
                "signature": signature,
                "prompt_sha256": signature,
                "response": str(response)[: max(1, int(max_chars))],
            },
            utility_delta=1.0,
        )

    def recall_distilled(self, prompt: str) -> Optional[str]:
        record = self._records.get(_signature(prompt))
        if record is None or record.get("kind") != "distilled_rule":
            return None
        return str(record.get("response") or "")

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            kinds: Dict[str, int] = {}
            for record in self._records.values():
                kind = str(record.get("kind", "unknown"))
                kinds[kind] = kinds.get(kind, 0) + 1
            return {
                "status": self.status,
                "records": len(self._records),
                "kinds": kinds,
                "dirty": self._dirty,
                "persisted": self.store_path.is_file(),
            }

    def public_index(self) -> List[str]:
        """Signatures only; safe for read-only digests."""
        return sorted(self._records)
