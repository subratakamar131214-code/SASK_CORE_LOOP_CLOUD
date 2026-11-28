"""Boundary policy, kernel manifest, and process/OS write blocking."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sask_v2 import boundary

REPO = Path(__file__).resolve().parent.parent


class ZonePolicyTest(unittest.TestCase):
    def test_zone_resolution(self):
        policy = boundary.Boundary(REPO)
        self.assertEqual(policy.zone_for("verify.py"), boundary.ZONE_KERNEL)
        self.assertEqual(policy.zone_for("sask_v2/supervisor.py"), boundary.ZONE_OUT_OF_BAND)
        self.assertEqual(policy.zone_for("sask_v2/ring/rewrites.py"), boundary.ZONE_MUTABLE)
        self.assertEqual(policy.zone_for("ip_quarantine/candidates/x.json"), boundary.ZONE_QUARANTINE)
        self.assertEqual(policy.zone_for("docs/readme.md"), boundary.ZONE_EXTERNAL)
        self.assertEqual(policy.zone_for(REPO.parent / "outside.py"), boundary.ZONE_EXTERNAL)

    def test_assert_mutable(self):
        policy = boundary.Boundary(REPO)
        self.assertEqual(
            policy.assert_mutable("sask_v2/ring/x.py"),
            REPO / "sask_v2" / "ring" / "x.py",
        )
        for denied in ("verify.py", "ip_quarantine/probe.json", "sask_v2/supervisor.py"):
            with self.assertRaises(boundary.BoundaryViolation):
                policy.assert_mutable(denied)

    def test_module_assert_mutable_infers_root(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "sask_v2" / "ring" / "adapter.py"
            self.assertEqual(boundary.assert_mutable(target), target)
            with self.assertRaises(boundary.BoundaryViolation):
                boundary.assert_mutable(Path(raw) / "verify.py")

    def test_quarantine_writer_requires_private_token(self):
        with self.assertRaises(boundary.BoundaryViolation):
            boundary.QuarantineWriter(object(), REPO)


class KernelManifestTest(unittest.TestCase):
    def test_freeze_drift_and_assert_intact(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "sask_v2").mkdir()
            (root / "verify.py").write_text("x = 1\n", encoding="utf-8")
            (root / "sask_v2" / "boundary.py").write_text("y = 1\n", encoding="utf-8")
            guard = boundary.KernelGuard(root)
            frozen = guard.freeze()
            self.assertEqual(frozen["files"], 2)
            self.assertTrue(guard.manifest_path.is_file())
            self.assertEqual(guard.drift(), [])
            guard.assert_intact()

            (root / "verify.py").write_text("x = 2\n", encoding="utf-8")
            drift = guard.drift()
            self.assertEqual([row["file"] for row in drift], ["verify.py"])
            with self.assertRaises(boundary.KernelDrift):
                guard.assert_intact()

    def test_missing_manifest_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            guard = boundary.KernelGuard(raw)
            self.assertEqual(guard.drift()[0]["reason"], "missing_manifest")

    def test_describe_covers_all_zones(self):
        zones = {row["path"]: row["zone"] for row in boundary.KernelGuard(REPO).describe()}
        self.assertEqual(zones["sask_v2/supervisor.py"], boundary.ZONE_OUT_OF_BAND)
        self.assertEqual(zones["verify.py"], boundary.ZONE_KERNEL)
        self.assertEqual(zones["sask_v2/ring/"], boundary.ZONE_MUTABLE)
        self.assertEqual(zones["ip_quarantine/"], boundary.ZONE_QUARANTINE)


@unittest.skipIf(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    "root bypasses file permission bits",
)
class OSReadOnlyTest(unittest.TestCase):
    def test_sealed_kernel_rejects_child_process_write(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = root / "verify.py"
            kernel.write_text("sealed\n", encoding="utf-8")
            guard = boundary.KernelGuard(root)
            try:
                sealed = guard.seal_os()
                self.assertIn("verify.py", sealed)
                script = (
                    "import sys\n"
                    "try:\n"
                    "    open(sys.argv[1], 'w').write('tamper')\n"
                    "except PermissionError:\n"
                    "    print('BLOCKED'); sys.exit(0)\n"
                    "print('WROTE'); sys.exit(1)\n"
                )
                proc = subprocess.run(
                    [sys.executable, "-c", script, str(kernel)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("BLOCKED", proc.stdout)
                self.assertEqual(kernel.read_text(encoding="utf-8"), "sealed\n")
            finally:
                guard.thaw_os()


class AuditHookTest(unittest.TestCase):
    def test_hook_blocks_kernel_and_quarantine_but_allows_writer(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "ip_quarantine").mkdir()
            (root / "verify.py").write_text("kernel\n", encoding="utf-8")
            script = """
import json, os, sys
from sask_v2 import boundary
root = sys.argv[1]
boundary.install_audit_hook(root)
result = {}
qpath = os.path.join(root, "ip_quarantine", "probe.txt")
kpath = os.path.join(root, "verify.py")
try:
    with open(qpath, "w") as handle:
        handle.write("leak")
    result["quarantine_blocked"] = False
except PermissionError:
    result["quarantine_blocked"] = True
try:
    with open(kpath, "w") as handle:
        handle.write("tamper")
    result["kernel_blocked"] = False
except PermissionError:
    result["kernel_blocked"] = True
try:
    os.remove(kpath)
    result["kernel_remove_blocked"] = False
except PermissionError:
    result["kernel_remove_blocked"] = True
with boundary.quarantine_writer(os.path.join(root, "ip_quarantine")) as writer:
    writer.write_text("probe.txt", text="ok")
with open(qpath, encoding="utf-8") as handle:
    result["writer_allowed"] = handle.read() == "ok"
print(json.dumps(result))
"""
            env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONIOENCODING="utf-8")
            proc = subprocess.run(
                [sys.executable, "-c", script, str(root)],
                cwd=str(REPO),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertTrue(result["quarantine_blocked"])
            self.assertTrue(result["kernel_blocked"])
            self.assertTrue(result["kernel_remove_blocked"])
            self.assertTrue(result["writer_allowed"])


if __name__ == "__main__":
    unittest.main()
