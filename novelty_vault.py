"""Private quarantine, provenance ledger, and A2UI novelty alerts for SASK.

Verified discoveries are never written into the public repository. Raw
candidates land in ``ip_quarantine/`` (git-ignored) together with a
deterministic provenance entry. A2UI surface messages carry only hashes and
scores, so existing UI/notification channels can render an alert without
leaking the discovery itself.

Cross-cycle persistence is optional. When ``SASK_VAULT_KEY`` is present (for
example from a GitHub Actions secret) the candidate bundle is encrypted at
rest with AES-256-GCM and decrypted strictly in memory; neither the key nor
the plaintext is ever logged or printed.

Novelty here is deterministic: a candidate is novel the first time its
SHA-256 appears in the private ledger. There is no model or heuristic in the
loop, so the same artifact always produces the same decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
A2UI_CATALOG = "sask.novelty.v1"
VAULT_KEY_ENV = "SASK_VAULT_KEY"
VAULT_MAGIC = b"SASKV1"
VAULT_MIN_KEY_CHARS = 16
RAW_ARTIFACT_LIMIT = 2_000_000

DEFAULT_ROOT = Path(__file__).resolve().parent / "ip_quarantine"

_LEDGER_LOCK = threading.RLock()


class CryptoUnavailable(RuntimeError):
    """Raised when encryption is requested but no crypto backend is installed."""


def _resolve_root(root: Optional[Path]) -> Path:
    if root is not None:
        return Path(root)
    override = os.environ.get("SASK_NOVELTY_DIR")
    return Path(override) if override else DEFAULT_ROOT


def _paths(root: Path) -> Dict[str, Path]:
    return {
        "root": root,
        "candidates": root / "candidates",
        "provenance": root / "provenance" / "provenance.jsonl",
        "outbox": root / "a2ui" / "outbox.jsonl",
        "vault": root / "vault",
    }


def _utc(now: Optional[datetime] = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_bytes_private(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(blob)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _append_line(path: Path, record: Dict[str, Any]) -> None:
    line = json.dumps(record, sort_keys=True, ensure_ascii=False)
    with _LEDGER_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows


def is_capture_eligible(*, passed: bool, formal: bool, novelty: bool) -> bool:
    """Deterministic trigger: a passing candidate the submitter marked novel or formal."""
    return bool(passed) and (bool(formal) or bool(novelty))


def _verification_summary(verification: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = verification or {}
    tiers: Dict[str, float] = {}
    for name, value in (payload.get("tiers") or {}).items():
        if isinstance(value, (int, float)):
            tiers[str(name)] = round(float(value), 3)
    duration = payload.get("duration_ms")
    return {
        "passed": bool(payload.get("passed", True)),
        "reason_code": str(payload.get("reason_code") or "")[:64],
        "tiers": dict(sorted(tiers.items())),
        "duration_ms": round(float(duration), 3) if isinstance(duration, (int, float)) else None,
    }


def _novelty_score(goal_sha: str, entries: List[Dict[str, Any]]) -> float:
    seen_goal = any(
        isinstance(entry.get("goal_ref"), dict)
        and entry["goal_ref"].get("sha256") == goal_sha
        for entry in entries
    )
    return 0.6 if seen_goal else 1.0


def _risk_score(
    *,
    formal: bool,
    changed_files: List[str],
    verification: Dict[str, Any],
    allow_nonfinite: bool,
) -> float:
    risk = 0.15
    if not formal:
        risk += 0.25
    if not changed_files:
        risk += 0.10
    if verification.get("reason_code") != "ok":
        risk += 0.30
    if allow_nonfinite:
        risk += 0.10
    return round(min(1.0, max(0.0, risk)), 3)


def build_a2ui_messages(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Render one discovery as A2UI surface messages with no raw content."""
    artifact_sha = str(entry["artifact_sha256"])
    surface_id = f"sask-novelty-{artifact_sha[:12]}"
    novelty = float(entry["novelty_score"])
    risk = float(entry["risk_score"])
    tone = "success" if risk < 0.35 else ("warning" if risk < 0.7 else "danger")
    verification = entry.get("verification") or {}
    duration = verification.get("duration_ms")
    timing = f"{float(duration):.0f} ms" if isinstance(duration, (int, float)) else "n/a"
    tier_count = len(verification.get("tiers") or {})
    return [
        {"createSurface": {"surfaceId": surface_id, "catalogId": A2UI_CATALOG}},
        {
            "updateComponents": {
                "surfaceId": surface_id,
                "components": [
                    {
                        "id": "root",
                        "component": "Card",
                        "children": ["title", "status", "metrics", "hash", "review"],
                    },
                    {
                        "id": "title",
                        "component": "Text",
                        "variant": "h3",
                        "text": "Verified novel candidate",
                    },
                    {"id": "status", "component": "Badge", "label": "VERIFIED", "tone": "success"},
                    {
                        "id": "metrics",
                        "component": "Row",
                        "children": ["novelty", "risk", "timing"],
                    },
                    {
                        "id": "novelty",
                        "component": "Badge",
                        "label": f"novelty {novelty:.2f}",
                        "tone": "info",
                    },
                    {
                        "id": "risk",
                        "component": "Badge",
                        "label": f"risk {risk:.2f}",
                        "tone": tone,
                    },
                    {
                        "id": "timing",
                        "component": "Text",
                        "variant": "caption",
                        "text": f"{tier_count} tiers | {timing}",
                    },
                    {"id": "hash", "component": "Text", "variant": "code", "text": artifact_sha},
                    {
                        "id": "review",
                        "component": "Button",
                        "label": "Request human review",
                        "action": {
                            "name": "sask.human_review",
                            "context": {"artifact_sha256": artifact_sha, "surfaceId": surface_id},
                        },
                    },
                ],
            }
        },
        {
            "updateDataModel": {
                "surfaceId": surface_id,
                "path": "/novelty",
                "contents": {
                    "artifact_sha256": artifact_sha,
                    "novelty_score": novelty,
                    "risk_score": risk,
                    "verification": {
                        "passed": bool(verification.get("passed")),
                        "reason_code": verification.get("reason_code"),
                        "duration_ms": verification.get("duration_ms"),
                    },
                    "human_reviewed": False,
                    "review_action": "sask.human_review",
                },
            }
        },
    ]


def _aesgcm() -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

        return AESGCM
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise CryptoUnavailable(
            "install the 'cryptography' package to enable the encrypted vault"
        ) from exc


def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_bundle(payload: bytes, secret: str) -> bytes:
    """AES-256-GCM encrypt; output is ``SASKV1 | 12-byte nonce | ciphertext``."""
    nonce = os.urandom(12)
    sealed = _aesgcm()(_derive_key(secret)).encrypt(nonce, payload, VAULT_MAGIC)
    return VAULT_MAGIC + nonce + sealed


def decrypt_bundle(blob: bytes, secret: str) -> bytes:
    """Decrypt in memory. A wrong key raises the backend's authentication error."""
    if not blob.startswith(VAULT_MAGIC) or len(blob) < len(VAULT_MAGIC) + 12:
        raise ValueError("not a SASK vault bundle")
    nonce = blob[len(VAULT_MAGIC) : len(VAULT_MAGIC) + 12]
    ciphertext = blob[len(VAULT_MAGIC) + 12 :]
    return _aesgcm()(_derive_key(secret)).decrypt(nonce, ciphertext, VAULT_MAGIC)


def _vault_store(paths: Dict[str, Path], document: Dict[str, Any], secret: str) -> Dict[str, Any]:
    try:
        blob = encrypt_bundle(
            json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8"), secret
        )
    except CryptoUnavailable as exc:
        return {"status": "crypto_unavailable", "detail": str(exc)}
    target = paths["vault"] / f"{document['artifact_sha256'][:16]}.saskv"
    _write_bytes_private(target, blob)
    return {"status": "encrypted", "path": str(target), "bytes": len(blob)}


def observe_verified(
    *,
    artifact: str,
    label: str = "candidate",
    entrypoint: str = "solve",
    verification: Optional[Dict[str, Any]] = None,
    formal: bool = False,
    changed_files: Optional[List[str]] = None,
    allow_nonfinite: bool = False,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
    key: Optional[str] = None,
) -> Dict[str, Any]:
    """Quarantine one passing candidate and emit its provenance + A2UI alert.

    Returns a summary that never contains the artifact body, so callers can log
    it safely. The raw body is written only to the git-ignored quarantine.
    """
    paths = _paths(_resolve_root(root))
    body = artifact if isinstance(artifact, str) else str(artifact)
    artifact_sha = sha256_text(body)
    entries = _read_jsonl(paths["provenance"])
    for entry in entries:
        if entry.get("artifact_sha256") == artifact_sha:
            return {
                "status": "duplicate",
                "entry": entry,
                "a2ui": [],
                "vault": {"status": "skipped"},
            }

    label_text = str(label)[:200]
    goal_sha = sha256_text(label_text)
    summary = _verification_summary(verification)
    files = [Path(name).name for name in (changed_files or [])][:20]
    captured_at = _utc(now)
    entry = {
        "schema": SCHEMA_VERSION,
        "record_type": "sask_provenance",
        "captured_at": captured_at,
        "artifact_sha256": artifact_sha,
        "goal_ref": {
            "sha256": goal_sha,
            "chars": len(label_text),
            "entrypoint": str(entrypoint)[:80],
        },
        "verification": summary,
        "changed_files": files,
        "novelty_score": _novelty_score(goal_sha, entries),
        "risk_score": _risk_score(
            formal=formal,
            changed_files=files,
            verification=summary,
            allow_nonfinite=allow_nonfinite,
        ),
        "human_reviewed": False,
        "human_inventor_note": "",
    }
    candidate_doc = {
        "schema": SCHEMA_VERSION,
        "captured_at": captured_at,
        "artifact_sha256": artifact_sha,
        "label": label_text,
        "entrypoint": str(entrypoint)[:80],
        "verification": summary,
        "artifact": body[:RAW_ARTIFACT_LIMIT],
        "truncated": len(body) > RAW_ARTIFACT_LIMIT,
    }
    _write_private(
        paths["candidates"] / f"{artifact_sha}.json",
        json.dumps(candidate_doc, indent=2, sort_keys=True, ensure_ascii=False),
    )
    _append_line(paths["provenance"], entry)
    messages = build_a2ui_messages(entry)
    _append_line(
        paths["outbox"],
        {"ts": captured_at, "surfaceId": f"sask-novelty-{artifact_sha[:12]}", "messages": messages},
    )

    secret = key if key is not None else os.environ.get(VAULT_KEY_ENV)
    if not secret:
        vault = {"status": "disabled"}
    elif len(secret) < VAULT_MIN_KEY_CHARS:
        vault = {"status": "disabled", "detail": "vault key shorter than 16 characters"}
    else:
        vault = _vault_store(paths, candidate_doc, secret)
    return {"status": "quarantined", "entry": entry, "a2ui": messages, "vault": vault}


def read_provenance(limit: int = 20, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    entries = _read_jsonl(_paths(_resolve_root(root))["provenance"])
    return entries[-max(1, int(limit)) :]


def read_outbox(limit: int = 20, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Flatten the most recent A2UI messages for any surface renderer."""
    records = _read_jsonl(_paths(_resolve_root(root))["outbox"])
    messages: List[Dict[str, Any]] = []
    for record in records[-max(1, int(limit)) :]:
        batch = record.get("messages")
        if isinstance(batch, list):
            messages.extend(item for item in batch if isinstance(item, dict))
    return messages
