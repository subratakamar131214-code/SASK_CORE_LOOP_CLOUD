"""Out-of-band watchdog: hangs, RSS ceilings, rotation, boot-loop rollback, pinning."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sask_v2 import supervisor


def write_heartbeat(root: Path, pid: int, epoch: float) -> Path:
    state = root / "sask_v2" / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "heartbeat.json"
    path.write_text(
        json.dumps({"pid": pid, "epoch": epoch, "cycle": 0}),
        encoding="utf-8",
    )
    return path


class HangKillTest(unittest.TestCase):
    def test_stale_heartbeat_kills_hung_child(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(120)"]
            )
            try:
                write_heartbeat(root, child.pid, time.time() - 1000.0)
                config = supervisor.SupervisorConfig(
                    root=root, hang_timeout=5.0, min_free_gb=0.0
                )
                result = supervisor.Watchdog(config).tick()
                actions = [item["action"] for item in result["actions"]]
                self.assertIn("kill_hang", actions)
                self.assertFalse(result["healthy"])
                child.wait(timeout=30)
                self.assertIsNotNone(child.poll())
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=30)

    def test_dead_pid_is_reported_not_killed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            write_heartbeat(root, 2_000_000_000, time.time() - 1000.0)
            config = supervisor.SupervisorConfig(root=root, hang_timeout=5.0, min_free_gb=0.0)
            result = supervisor.Watchdog(config).tick()
            actions = [item["action"] for item in result["actions"]]
            self.assertIn("stale_heartbeat", actions)
            self.assertNotIn("kill_hang", actions)

    def test_rss_ceiling_triggers_kill(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            write_heartbeat(root, 4242, time.time())
            config = supervisor.SupervisorConfig(
                root=root, hang_timeout=600.0, max_rss_mb=128.0, min_free_gb=0.0
            )
            with mock.patch.object(supervisor, "_rss_mb", return_value=9999.0), mock.patch.object(
                supervisor, "_kill_tree", return_value=True
            ):
                result = supervisor.Watchdog(config).tick()
            actions = [item["action"] for item in result["actions"]]
            self.assertIn("kill_rss", actions)
            self.assertFalse(result["healthy"])


class TelemetryRotationTest(unittest.TestCase):
    def test_rotation_archives_and_truncates(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            telemetry = root / "cycle_telemetry.jsonl"
            telemetry.write_text("x" * 200, encoding="utf-8")
            config = supervisor.SupervisorConfig(
                root=root,
                max_telemetry_bytes=64,
                min_free_gb=0.0,
            )
            result = supervisor.Watchdog(config).tick()
            actions = {item["action"]: item for item in result["actions"]}
            self.assertIn("rotate_telemetry", actions)
            self.assertIsNotNone(actions["rotate_telemetry"]["archive"])
            self.assertEqual(telemetry.read_text(encoding="utf-8"), "")
            archives = list((root / "sask_v2" / "state" / "telemetry_archive").glob("*.gz"))
            self.assertEqual(len(archives), 1)


class BootLoopTest(unittest.TestCase):
    def test_repeated_crashes_request_rollback(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / "sask_v2" / "state"
            state.mkdir(parents=True)
            now = time.time()
            (state / "boot_events.json").write_text(
                json.dumps({"events": [now - 1, now - 2, now - 3]}),
                encoding="utf-8",
            )
            config = supervisor.SupervisorConfig(
                root=root,
                bootloop_threshold=2,
                bootloop_window=100.0,
                last_good_tag="v1.0",
                allow_rollback=False,
                min_free_gb=0.0,
            )
            result = supervisor.Watchdog(config).tick(now)
            actions = [item["action"] for item in result["actions"]]
            self.assertIn("rollback_request", actions)
            request = json.loads((state / "rollback_request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["target"], "v1.0")
            self.assertGreaterEqual(request["crashes"], 2)


class PinTest(unittest.TestCase):
    def test_pin_states(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "sask_v2").mkdir()
            source = root / "sask_v2" / "supervisor.py"
            source.write_text("# supervisor\n", encoding="utf-8")
            self.assertEqual(supervisor.check_pin(root)["reason"], "not_frozen")

            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            (root / supervisor.MANIFEST_NAME).write_text(
                json.dumps({"files": {"sask_v2/supervisor.py": digest}}),
                encoding="utf-8",
            )
            pinned = supervisor.check_pin(root)
            self.assertTrue(pinned["ok"])
            self.assertEqual(pinned["reason"], "pinned")

            source.write_text("# tampered\n", encoding="utf-8")
            drifted = supervisor.check_pin(root)
            self.assertFalse(drifted["ok"])
            self.assertEqual(drifted["reason"], "drift")

    def test_cli_once_and_refusal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "sask_v2").mkdir()
            source = root / "sask_v2" / "supervisor.py"
            source.write_text("# supervisor\n", encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = supervisor.main(["--root", str(root), "--once", "--skip-pin"])
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertIn("actions", payload)

            (root / supervisor.MANIFEST_NAME).write_text(
                json.dumps({"files": {"sask_v2/supervisor.py": "0" * 64}}),
                encoding="utf-8",
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = supervisor.main(["--root", str(root), "--once"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(buffer.getvalue())["supervisor"], "refused")


if __name__ == "__main__":
    unittest.main()
