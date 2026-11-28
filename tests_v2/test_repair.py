"""Crash-to-patch repair, boundary denial, blacklisting, gated git rollback."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from sask_v2.knowledge import KnowledgeStore
from sask_v2.repair import RepairEngine, classify_traceback, failure_signature

IMPORT_SOURCE = (
    "import totally_missing_dependency\n"
    "\n"
    "def run():\n"
    "    return totally_missing_dependency\n"
)
IMPORT_TRACE = "ModuleNotFoundError: No module named 'totally_missing_dependency'"

SCHEMA_SOURCE = "def read(payload):\n    return payload['answer']\n"
SCHEMA_TRACE = "KeyError: 'answer'"


def engine():
    return RepairEngine(KnowledgeStore(key=None), max_attempts=3)


class ClassificationTest(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(classify_traceback(IMPORT_TRACE), "import_error")
        self.assertEqual(classify_traceback(SCHEMA_TRACE), "schema_error")
        self.assertEqual(classify_traceback("HTTP 429 Too Many Requests"), "rate_limit")
        self.assertEqual(classify_traceback("410 Gone: endpoint deprecated"), "deprecated_endpoint")
        self.assertEqual(classify_traceback("ConnectionResetError"), "connection")
        self.assertEqual(classify_traceback("SyntaxError: invalid syntax"), "syntax")
        self.assertEqual(classify_traceback("some other failure"), "unknown")

    def test_signature_is_stable(self):
        first = failure_signature("import_error", IMPORT_TRACE)
        second = failure_signature("import_error", IMPORT_TRACE)
        self.assertEqual(first, second)
        self.assertNotEqual(first, failure_signature("schema_error", IMPORT_TRACE))


class PatchSynthesisTest(unittest.TestCase):
    def test_import_degrades_to_none(self):
        plan = engine().plan(IMPORT_TRACE, IMPORT_SOURCE)
        self.assertEqual(plan.kind, "import_error")
        self.assertEqual(plan.action, "patch")
        namespace = {}
        exec(plan.patched_source, namespace)
        self.assertIsNone(namespace["totally_missing_dependency"])

    def test_schema_reads_are_healed(self):
        plan = engine().plan(SCHEMA_TRACE, SCHEMA_SOURCE)
        self.assertEqual(plan.kind, "schema_error")
        self.assertEqual(plan.action, "patch")
        self.assertIn("_sask_get", plan.patched_source)
        namespace = {}
        exec(plan.patched_source, namespace)
        missing = namespace["read"]({})
        self.assertFalse(bool(missing))
        self.assertEqual(missing.get("answer", "fallback"), "fallback")

    def test_transport_errors_delegate_to_rotation(self):
        for trace in ("429 rate limit", "410 deprecated", "TimeoutError: connection timed out"):
            plan = engine().plan(trace, "x = 1\n")
            self.assertEqual(plan.action, "rotate")

    def test_syntax_error_reverts(self):
        plan = engine().plan("SyntaxError: invalid syntax", "def broken(:\n")
        self.assertEqual(plan.action, "revert")


class PatchApplicationTest(unittest.TestCase):
    def test_verified_patch_is_written_to_ring(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "sask_v2" / "ring" / "adapter.py"
            target.parent.mkdir(parents=True)
            result = engine().attempt(
                IMPORT_TRACE,
                IMPORT_SOURCE,
                target,
                regression=lambda source: "ImportError" in source,
            )
            self.assertEqual(result["status"], "applied")
            self.assertTrue(target.is_file())
            self.assertIn("ImportError", target.read_text(encoding="utf-8"))

    def test_failed_regression_does_not_touch_disk(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "sask_v2" / "ring" / "adapter.py"
            result = engine().attempt(
                IMPORT_TRACE,
                IMPORT_SOURCE,
                target,
                regression=lambda source: False,
            )
            self.assertEqual(result["status"], "reverted")
            self.assertFalse(target.exists())

    def test_kernel_path_is_denied(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "verify.py"
            result = engine().attempt(
                IMPORT_TRACE,
                IMPORT_SOURCE,
                target,
                regression=lambda source: True,
            )
            self.assertEqual(result["status"], "boundary_denied")
            self.assertFalse(target.exists())

    def test_repeated_failures_blacklist_signature(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "sask_v2" / "ring" / "adapter.py"
            repair = engine()
            statuses = []
            for _ in range(4):
                statuses.append(
                    repair.attempt(
                        IMPORT_TRACE,
                        IMPORT_SOURCE,
                        target,
                        regression=lambda source: False,
                    )["status"]
                )
            self.assertEqual(statuses[:3], ["reverted", "reverted", "reverted"])
            self.assertEqual(statuses[3], "blacklisted")
            self.assertFalse(target.exists())


class RollbackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._git("init")
        (self.root / "state.txt").write_text("good\n", encoding="utf-8")
        self._git("add", "state.txt")
        self._git("-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", "init")
        self._git("tag", "v1")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, timeout=60
        )

    def test_disabled_by_default(self):
        result = engine().rollback_to_tag(self.root, "v1", allow=False)
        self.assertFalse(result["performed"])
        self.assertEqual(result["reason"], "rollback_disabled")

    def test_unknown_tag_and_dirty_tree_are_refused(self):
        repair = engine()
        self.assertEqual(repair.rollback_to_tag(self.root, "v-missing", allow=True)["reason"], "unknown_tag")
        (self.root / "state.txt").write_text("bad\n", encoding="utf-8")
        self.assertEqual(repair.rollback_to_tag(self.root, "v1", allow=True)["reason"], "dirty_worktree")

    def test_clean_tree_rolls_back_behind_backup_branch(self):
        (self.root / "state.txt").write_text("bad\n", encoding="utf-8")
        self._git("checkout", "--", "state.txt")
        result = engine().rollback_to_tag(self.root, "v1", allow=True)
        self.assertTrue(result["performed"], result)
        self.assertEqual((self.root / "state.txt").read_text(encoding="utf-8"), "good\n")
        branches = self._git("branch", "--list", "sask-pre-rollback-*").stdout
        self.assertIn("sask-pre-rollback-", branches)


if __name__ == "__main__":
    unittest.main()
