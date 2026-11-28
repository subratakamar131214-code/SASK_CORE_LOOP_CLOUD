"""Encrypted knowledge store: dedupe, persistence, pruning, no plaintext."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sask_v2.knowledge import KnowledgeStore

KEY = "sask-test-key-0123456789"


class MemoryOnlyTest(unittest.TestCase):
    def test_memory_only_never_persists(self):
        with tempfile.TemporaryDirectory() as raw:
            store = KnowledgeStore(root=raw, key=None)
            self.assertEqual(store.status, "memory_only")
            store.upsert({"kind": "lemma", "signature": "sig-1", "lhs": "secret"})
            flushed = store.flush()
            self.assertEqual(flushed["status"], "not_persisted")
            self.assertFalse((Path(raw) / "store.saskv").exists())

    def test_invalid_key_downgrades_to_memory(self):
        store = KnowledgeStore(root=tempfile.gettempdir(), key="short")
        self.assertEqual(store.status, "invalid_key")
        self.assertEqual(store.flush()["status"], "not_persisted")


class EncryptedStoreTest(unittest.TestCase):
    def test_flush_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as raw:
            store = KnowledgeStore(root=raw, key=KEY)
            self.assertEqual(store.status, "unlocked_empty")
            store.upsert({"kind": "lemma", "signature": "sig-1", "lhs": "x + 1", "rhs": "x + 2"})
            self.assertEqual(store.flush()["status"], "persisted")
            self.assertTrue(store.store_path.is_file())

            reloaded = KnowledgeStore(root=raw, key=KEY)
            self.assertEqual(reloaded.status, "unlocked")
            self.assertIsNotNone(reloaded.lookup("sig-1"))

    def test_plaintext_never_reaches_disk(self):
        with tempfile.TemporaryDirectory() as raw:
            store = KnowledgeStore(root=raw, key=KEY)
            store.upsert({"kind": "lemma", "signature": "sig-1", "body": "TOP-SECRET-DISCOVERY"})
            store.flush()
            blob = store.store_path.read_bytes()
            self.assertNotIn(b"TOP-SECRET-DISCOVERY", blob)
            self.assertNotIn(KEY.encode("utf-8"), blob)

    def test_wrong_key_is_locked_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as raw:
            store = KnowledgeStore(root=raw, key=KEY)
            store.upsert({"kind": "lemma", "signature": "sig-1", "body": "keep-me"})
            store.flush()
            before = store.store_path.read_bytes()

            intruder = KnowledgeStore(root=raw, key="wrong-key-0123456789")
            self.assertEqual(intruder.status, "locked")
            self.assertEqual(
                intruder.flush()["reason"], "vault_unreadable_refusing_to_overwrite"
            )
            self.assertEqual(store.store_path.read_bytes(), before)
            self.assertIsNone(intruder.lookup("sig-1"))

    def test_dedupe_increments_use_count(self):
        store = KnowledgeStore(root=tempfile.gettempdir(), key=None)
        first = store.upsert({"kind": "lemma", "signature": "sig-1", "lhs": "a"})
        second = store.upsert({"kind": "lemma", "signature": "sig-1", "lhs": "b"})
        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "updated")
        self.assertEqual(len(store.records()), 1)
        record = store.lookup("sig-1")
        self.assertEqual(record["uses"], 2)
        self.assertEqual(record["lhs"], "b")


class LifecycleTest(unittest.TestCase):
    def test_prune_removes_idle_low_utility_records(self):
        store = KnowledgeStore(root=tempfile.gettempdir(), key=None)
        store.upsert({"kind": "lemma", "signature": "sig-1", "utility": 0.0})
        store.upsert({"kind": "lemma", "signature": "sig-2", "utility": 5.0})
        pruned = store.prune(min_utility=1.0, max_unused_cycles=5, current_cycle=10)
        self.assertEqual(pruned["removed"], ["sig-1"])
        self.assertEqual(pruned["remaining"], 1)

    def test_failure_blacklist_after_max_attempts(self):
        store = KnowledgeStore(root=tempfile.gettempdir(), key=None)
        self.assertFalse(store.is_blacklisted("sig-fail"))
        store.remember_failure("sig-fail", "boom", max_attempts=3)
        store.remember_failure("sig-fail", "boom", max_attempts=3)
        self.assertFalse(store.is_blacklisted("sig-fail"))
        record = store.remember_failure("sig-fail", "boom", max_attempts=3)
        self.assertTrue(record["blacklisted"])
        self.assertTrue(store.is_blacklisted("sig-fail"))

    def test_distillation_roundtrip(self):
        store = KnowledgeStore(root=tempfile.gettempdir(), key=None)
        store.distill("What is 2+2?", "4")
        self.assertEqual(store.recall_distilled("What is 2+2?"), "4")
        self.assertIsNone(store.recall_distilled("unknown prompt"))

    def test_public_index_exposes_signatures_only(self):
        store = KnowledgeStore(root=tempfile.gettempdir(), key=None)
        store.upsert({"kind": "lemma", "signature": "sig-1", "body": "private"})
        self.assertEqual(store.public_index(), ["sig-1"])


if __name__ == "__main__":
    unittest.main()
