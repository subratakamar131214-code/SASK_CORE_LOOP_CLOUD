"""Evidence bundle integrity, A2UI secrecy, and read-only observer HUD."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import novelty_vault
from sask_v2.evidence import EvidenceBundler
from sask_v2.hud import ObserverHUD, build_digest

ARTIFACT = "def solve(x):\n    return 2 * x\n"
VERIFICATION = {
    "passed": True,
    "reason_code": "ok",
    "tiers": {"tier0": 1.0, "tier1": 0.8},
    "duration_ms": 12.5,
}


def quarantine_with_candidate(root: Path):
    return novelty_vault.observe_verified(
        artifact=ARTIFACT,
        label="oracle-goal",
        entrypoint="solve",
        verification=VERIFICATION,
        formal=True,
        changed_files=["sask_v2/ring/adapter.py"],
        root=root / "ip_quarantine",
    )


class EvidenceBundleTest(unittest.TestCase):
    def test_bundle_is_complete_and_hashed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            observed = quarantine_with_candidate(root)
            sha = observed["entry"]["artifact_sha256"]
            result = EvidenceBundler(root / "ip_quarantine").bundle(sha)
            self.assertEqual(result["status"], "bundled")
            bundle_dir = Path(result["bundle"])
            expected = {
                "artifact.py",
                "verification.json",
                "provenance.json",
                "a2ui.json",
                "failure_bounds.json",
                "novelty_comparison.json",
                "benchmark.py",
                "README.md",
                "manifest.json",
            }
            self.assertEqual(set(result["files"]), expected)
            for name in expected:
                self.assertTrue((bundle_dir / name).is_file(), name)

            manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["human_reviewed"])
            for name, digest in manifest["files"].items():
                body = (bundle_dir / name).read_bytes()
                self.assertEqual(hashlib.sha256(body).hexdigest(), digest, name)

            self.assertEqual((bundle_dir / "artifact.py").read_text(encoding="utf-8"), ARTIFACT)
            novelty = json.loads((bundle_dir / "novelty_comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(novelty["status"], "requires_human_review")

    def test_a2ui_bundle_contains_no_raw_artifact(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            observed = quarantine_with_candidate(root)
            sha = observed["entry"]["artifact_sha256"]
            EvidenceBundler(root / "ip_quarantine").bundle(sha)
            a2ui = (root / "ip_quarantine" / "bundles" / sha[:12] / "a2ui.json").read_text(
                encoding="utf-8"
            )
            self.assertTrue(a2ui)
            self.assertNotIn("def solve", a2ui)
            self.assertIn(sha, a2ui)

    def test_missing_candidate_reports_missing(self):
        with tempfile.TemporaryDirectory() as raw:
            result = EvidenceBundler(raw).bundle("0" * 64)
            self.assertEqual(result["status"], "missing")


class DigestTest(unittest.TestCase):
    def test_digest_folds_ledger_and_quarantine(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ledger = root / "cycle_telemetry.jsonl"
            rows = [
                {"record_type": "verification_verdict", "passed": True, "reason_code": "ok"},
                {"record_type": "verification_verdict", "passed": False, "reason_code": "timeout"},
                {"record_type": "scale_event", "kind": "rollback_triggered"},
                {"cycle": 3, "phase": "verify", "passed": True},
            ]
            ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            observed = quarantine_with_candidate(root)
            sha = observed["entry"]["artifact_sha256"]
            EvidenceBundler(root / "ip_quarantine").bundle(sha)

            state = root / "sask_v2" / "state"
            state.mkdir(parents=True)
            (state / "heartbeat.json").write_text('{"pid": 7, "cycle": 4}', encoding="utf-8")
            (state / "rollback_request.json").write_text('{"target": "v1.0"}', encoding="utf-8")

            digest = build_digest(root, limit=10)
            self.assertTrue(digest["read_only"])
            promoted = digest["sections"]["promoted_diffs"]
            self.assertEqual(promoted["passed_verdicts"], 1)
            self.assertEqual(promoted["revoked_attempts"], 1)
            self.assertEqual(promoted["rolled_back"], 1)
            self.assertEqual(digest["sections"]["supervisor"]["heartbeat"]["pid"], 7)
            self.assertEqual(digest["sections"]["supervisor"]["rollback_request"]["target"], "v1.0")
            self.assertEqual(digest["sections"]["quarantine"]["provenance_count"], 1)
            self.assertIn(sha[:12], digest["sections"]["quarantine"]["bundles"])
            self.assertEqual(len(digest["sections"]["recent_cycles"]), 1)


class HUDHttpTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        quarantine_with_candidate(self.root)
        self.server = ObserverHUD(self.root).make_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def test_get_digest_and_healthz(self):
        with urllib.request.urlopen(self.base + "/api/digest", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["read_only"])
        with urllib.request.urlopen(self.base + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read().decode("utf-8")), {"ok": True, "read_only": True})

    def test_html_and_head(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("SASK v2.0 OBSERVER", response.read().decode("utf-8"))
        request = urllib.request.Request(self.base + "/", method="HEAD")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")

    def test_mutating_methods_are_rejected(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            request = urllib.request.Request(
                self.base + "/api/digest", data=b"{}", method=method
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(ctx.exception.code, 405, method)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/etc/passwd", timeout=5)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
