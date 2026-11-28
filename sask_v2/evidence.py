"""Filing-ready evidence bundles for quarantined discoveries.

Every discovery that enters ``ip_quarantine`` can be compiled into a
self-contained packet: the artifact, verification trace, provenance record,
A2UI alerts, failure-mode bounds, a reproducible benchmark skeleton, and a
manifest of SHA-256 digests. Prior-art and novelty comparisons are left as
explicit human-review placeholders -- the autonomous loop never asserts a
novelty claim on its own.

Evidence is written beneath the quarantine through the writer capability, so
none of it can reach the public repository.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import boundary

SCHEMA_VERSION = 1

_BENCHMARK = '''\
"""Reproducible benchmark skeleton for quarantined artifact {short}.

The artifact is executed only by the human reviewer, in an isolated
environment. Supply validated case vectors as JSON:

    [{{"args": [2], "expected": 4}}, ...]

then run:  python benchmark.py --cases cases.json
"""

import argparse
import importlib.util
import json
import pathlib
import sys


def load_artifact(path):
    spec = importlib.util.spec_from_file_location("sask_artifact", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", default="artifact.py")
    parser.add_argument("--entrypoint", default={entrypoint!r})
    parser.add_argument("--cases", default="")
    args = parser.parse_args()
    if not args.cases:
        print("no validated case vectors supplied; human review required")
        return 2
    cases = json.loads(pathlib.Path(args.cases).read_text(encoding="utf-8"))
    module = load_artifact(pathlib.Path(args.artifact))
    fn = getattr(module, args.entrypoint)
    failures = 0
    for index, case in enumerate(cases):
        try:
            actual = fn(*case.get("args", []))
        except Exception as exc:
            failures += 1
            print(json.dumps({{"case": index, "error": type(exc).__name__}}))
            continue
        expected = case.get("expected")
        ok = actual == expected
        failures += 0 if ok else 1
        print(json.dumps({{"case": index, "ok": ok, "actual": actual, "expected": expected}}))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


class EvidenceBundler:
    def __init__(self, quarantine_root: Path | str) -> None:
        self.root = Path(quarantine_root)

    def _candidate(self, artifact_sha: str) -> Optional[Dict[str, Any]]:
        path = self.root / "candidates" / f"{artifact_sha}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def bundle(self, artifact_sha: str) -> Dict[str, Any]:
        artifact_sha = str(artifact_sha)
        candidate = self._candidate(artifact_sha)
        if candidate is None:
            return {"status": "missing", "artifact_sha256": artifact_sha}
        provenance = [
            row
            for row in _read_jsonl(self.root / "provenance" / "provenance.jsonl")
            if row.get("artifact_sha256") == artifact_sha
        ]
        surface = f"sask-novelty-{artifact_sha[:12]}"
        outbox = [
            row
            for row in _read_jsonl(self.root / "a2ui" / "outbox.jsonl")
            if row.get("surfaceId") == surface
        ]
        messages: List[Dict[str, Any]] = []
        for batch in outbox:
            for message in batch.get("messages") or ():
                if isinstance(message, dict):
                    messages.append(message)
        entry = provenance[-1] if provenance else {}
        verification = entry.get("verification") or candidate.get("verification") or {}
        risk = float(entry.get("risk_score", 0.0) or 0.0)
        short = artifact_sha[:12]

        files: Dict[str, str] = {
            "artifact.py": str(candidate.get("artifact") or ""),
            "verification.json": json.dumps(verification, indent=2, sort_keys=True),
            "provenance.json": json.dumps(provenance[-1:] if provenance else [], indent=2, sort_keys=True),
            "a2ui.json": json.dumps(messages, indent=2, sort_keys=True),
            "failure_bounds.json": json.dumps(
                {
                    "risk_score": risk,
                    "method": "deterministic_template_v1",
                    "verification": verification,
                    "note": "Bounds reflect the verification envelope, not a proof of global safety.",
                },
                indent=2,
                sort_keys=True,
            ),
            "novelty_comparison.json": json.dumps(
                {
                    "status": "requires_human_review",
                    "prior_art": [],
                    "differentiators": [],
                    "human_reviewed": False,
                    "note": "Autonomous loop does not assert novelty; a human inventor completes this section.",
                },
                indent=2,
                sort_keys=True,
            ),
            "benchmark.py": _BENCHMARK.format(
                short=short,
                entrypoint=str(candidate.get("entrypoint") or "solve"),
            ),
            "README.md": (
                f"# Evidence bundle {short}\n\n"
                f"- artifact SHA-256: `{artifact_sha}`\n"
                f"- captured: {candidate.get('captured_at') or entry.get('captured_at') or 'unknown'}\n"
                f"- verification passed: {bool(verification.get('passed'))}\n"
                f"- risk score: {risk:.3f}\n"
                f"- human review: pending\n\n"
                "Run `python benchmark.py --cases cases.json` inside an isolated environment "
                "after supplying validated case vectors.\n"
            ),
        }
        manifest = {
            "schema": SCHEMA_VERSION,
            "artifact_sha256": artifact_sha,
            "created_at": _utc_now(),
            "human_reviewed": False,
            "files": {name: _sha256_text(content) for name, content in sorted(files.items())},
        }

        writer = boundary.quarantine_writer(self.root)
        with writer:
            for name, content in files.items():
                writer.write_text("bundles", short, name, text=content)
            writer.write_text(
                "bundles",
                short,
                "manifest.json",
                text=json.dumps(manifest, indent=2, sort_keys=True),
            )
        return {
            "status": "bundled",
            "artifact_sha256": artifact_sha,
            "bundle": str(self.root / "bundles" / short),
            "files": sorted(list(files) + ["manifest.json"]),
            "manifest": manifest,
        }
