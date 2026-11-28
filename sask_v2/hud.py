"""Read-only long-horizon observer digest for SASK v2.0.

``build_digest`` folds the local telemetry ledger, verifier verdicts, scale
events, quarantine provenance, A2UI outbox, provider health, and supervisor
heartbeat into one JSON document. ``ObserverHUD`` serves it on
``127.0.0.1:8420`` with GET/HEAD only; every mutating method returns 405 and
the response carries a deny-all content-security policy. Nothing on disk is
served, so the HUD cannot expose quarantine contents beyond the hashes and
summaries the digest already contains.
"""

from __future__ import annotations

import argparse
import html
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _tail_jsonl(path: Path, limit: int) -> List[Dict[str, Any]]:
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
    return rows[-max(1, int(limit)):]


def build_digest(root: Path | str = ".", limit: int = 20) -> Dict[str, Any]:
    root_path = Path(root).resolve()
    state = root_path / "sask_v2" / "state"
    quarantine = root_path / "ip_quarantine"

    events = _tail_jsonl(root_path / "cycle_telemetry.jsonl", max(limit * 5, 100))
    verdicts = [item for item in events if item.get("record_type") == "verification_verdict"][-limit:]
    scale = [item for item in events if item.get("record_type") == "scale_event"][-limit:]
    cycles = [
        item
        for item in events
        if isinstance(item, dict) and item.get("record_type") not in {"verification_verdict", "scale_event"}
    ][-limit:]

    provenance_rows = _tail_jsonl(quarantine / "provenance" / "provenance.jsonl", limit)
    outbox_rows = _tail_jsonl(quarantine / "a2ui" / "outbox.jsonl", limit)
    bundles = []
    try:
        bundles = sorted(path.name for path in (quarantine / "bundles").iterdir() if path.is_dir())
    except OSError:
        bundles = []

    heartbeats = _read_json(state / "heartbeat.json", {})
    providers = _read_json(state / "providers.json", {})
    rollback_request = _read_json(state / "rollback_request.json", {})

    promoted = sum(1 for item in verdicts if item.get("passed"))
    revoked = sum(1 for item in verdicts if not item.get("passed"))
    rolled_back = sum(
        1
        for item in scale
        if str(item.get("kind") or item.get("status") or "").lower().find("rollback") >= 0
    )

    return {
        "schema": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "read_only": True,
        "sections": {
            "promoted_diffs": {
                "passed_verdicts": promoted,
                "revoked_attempts": revoked,
                "rolled_back": rolled_back,
                "recent_verdicts": [
                    {
                        "ts": item.get("ts"),
                        "passed": bool(item.get("passed")),
                        "reason_code": item.get("reason_code"),
                        "label": item.get("label"),
                        "duration_ms": item.get("duration_ms"),
                    }
                    for item in verdicts[-8:]
                ],
            },
            "holdout_deltas": {
                "available": False,
                "note": "Holdout deltas are read from sealed v1.0 artifacts; see verify.py reports.",
            },
            "compute": {
                "providers": (providers.get("providers") if isinstance(providers, dict) else {}) or {},
                "recent_scale_events": scale[-8:],
            },
            "supervisor": {
                "heartbeat": heartbeats if isinstance(heartbeats, dict) else {},
                "rollback_request": rollback_request if isinstance(rollback_request, dict) else {},
            },
            "quarantine": {
                "provenance_count": len(provenance_rows),
                "a2ui_batches": len(outbox_rows),
                "bundles": bundles[-8:],
                "recent": [
                    {
                        "artifact_sha256": row.get("artifact_sha256"),
                        "captured_at": row.get("captured_at"),
                        "novelty_score": row.get("novelty_score"),
                        "risk_score": row.get("risk_score"),
                        "human_reviewed": bool(row.get("human_reviewed")),
                    }
                    for row in provenance_rows[-8:]
                ],
            },
            "recent_cycles": [
                {
                    key: item.get(key)
                    for key in ("cycle", "ts", "phase", "passed", "duration_seconds", "status")
                    if key in item
                }
                for item in cycles[-8:]
            ],
        },
    }


_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SASK v2.0 observer</title>
<style>
body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #0b0f14; color: #d5e2ea; margin: 0; }}
header {{ padding: 16px 24px; border-bottom: 1px solid #22303c; display: flex; justify-content: space-between; align-items: baseline; }}
h1 {{ font-size: 16px; margin: 0; letter-spacing: .08em; }}
.badge {{ padding: 2px 8px; border-radius: 999px; background: #123f2a; color: #7ee2a8; font-size: 12px; }}
main {{ padding: 24px; }}
pre {{ white-space: pre-wrap; word-break: break-word; background: #111820; border: 1px solid #22303c; border-radius: 10px; padding: 16px; overflow: auto; }}
footer {{ padding: 0 24px 24px; color: #6c8090; font-size: 12px; }}
</style>
</head>
<body>
<header><h1>SASK v2.0 OBSERVER</h1><span class="badge">READ-ONLY</span></header>
<main><pre>{digest}</pre></main>
<footer>GET /api/digest for JSON. Mutating methods are rejected with 405.</footer>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "SASKHUD/2.0"
    sys_version = ""

    def _digest(self) -> Dict[str, Any]:
        return self.server.digest_fn()  # type: ignore[attr-defined]

    def _send(self, status: int, body: bytes, content_type: str, head_only: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path in {"/", "/index.html"}:
            digest = self._digest()
            body = _HTML.format(digest=html.escape(json.dumps(digest, indent=2, sort_keys=True))).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if self.path == "/api/digest":
            body = json.dumps(self._digest(), indent=2, sort_keys=True).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if self.path == "/healthz":
            self._send(200, b'{"ok":true,"read_only":true}', "application/json; charset=utf-8")
            return
        self._send(404, b'{"error":"not_found"}', "application/json; charset=utf-8")

    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        self._send(200, b"", "text/html; charset=utf-8", head_only=True)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        self._send(405, b'{"error":"read_only"}', "application/json; charset=utf-8")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, fmt: str, *args: Any) -> None:  # silence access logs
        return


class ObserverHUD:
    def __init__(self, root: Path | str = ".", limit: int = 20) -> None:
        self.root = Path(root)
        self.limit = max(1, int(limit))

    def digest(self) -> Dict[str, Any]:
        return build_digest(self.root, self.limit)

    def make_server(self, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer((host, int(port)), _Handler)
        server.digest_fn = self.digest  # type: ignore[attr-defined]
        server.daemon_threads = True
        return server

    def serve(self, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> None:
        server = self.make_server(port, host)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            server.server_close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SASK v2.0 read-only observer HUD")
    parser.add_argument("--root", default=".")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    ObserverHUD(args.root, args.limit).serve(args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
