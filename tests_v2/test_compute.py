"""Provider failover mesh and portfolio allocator."""

from __future__ import annotations

import unittest

from sask_v2.compute import (
    PortfolioAllocator,
    Provider,
    ProviderError,
    ProviderMesh,
)
from sask_v2.knowledge import KnowledgeStore


class FakeProvider(Provider):
    def __init__(self, name, kind, fn):
        self.name = name
        self.kind = kind
        self._fn = fn
        self.calls = 0

    def available(self):
        return True

    def complete(self, prompt, timeout=30.0):
        self.calls += 1
        return self._fn(prompt)


def raising(category, detail="failure"):
    def _fn(prompt):
        raise ProviderError(category, detail)

    return _fn


class ProviderMeshTest(unittest.TestCase):
    def test_local_failure_rotates_to_cloud_and_distills(self):
        store = KnowledgeStore(key=None)
        local = FakeProvider("local", "local", raising("connection", "down"))
        cloud = FakeProvider("cloud", "cloud", lambda prompt: "answer")
        mesh = ProviderMesh([local, cloud], store=store, clock=lambda: 0.0)

        result = mesh.complete("q")
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "cloud")
        self.assertFalse(result["fallback"])
        self.assertEqual(result["attempts"], [{"provider": "local", "status": "error", "category": "connection"}])
        self.assertEqual(store.recall_distilled("q"), "answer")

    def test_rate_limit_applies_cooldown(self):
        failing = FakeProvider("remote", "cloud", raising("rate_limit", "429"))
        healthy = FakeProvider("local", "local", lambda prompt: "ok")
        mesh = ProviderMesh([failing, healthy], clock=lambda: 0.0)

        first = mesh.complete("q")
        self.assertEqual(first["provider"], "local")
        health = mesh.health()["remote"]
        self.assertEqual(health["fails"], 1)
        self.assertGreater(health["cooldown_remaining"], 0.0)

        second = mesh.complete("q")
        self.assertIn({"provider": "remote", "status": "cooldown"}, second["attempts"])
        self.assertEqual(failing.calls, 1)

    def test_backoff_grows_exponentially(self):
        now = {"t": 0.0}
        failing = FakeProvider("remote", "cloud", raising("connection", "down"))
        mesh = ProviderMesh(
            [failing],
            clock=lambda: now["t"],
            base_backoff=2.0,
            max_backoff=100.0,
        )
        mesh.complete("q")
        mesh.complete("q")
        now["t"] = 3.0
        mesh.complete("q")
        self.assertEqual(failing.calls, 2)
        health = mesh.health()["remote"]
        self.assertEqual(health["fails"], 2)
        self.assertAlmostEqual(health["cooldown_remaining"], 4.0, places=3)

    def test_deprecated_endpoint_is_retired(self):
        failing = FakeProvider("old", "cloud", raising("deprecated", "410"))
        mesh = ProviderMesh([failing], clock=lambda: 0.0)

        first = mesh.complete("q")
        self.assertTrue(first["fallback"])
        self.assertTrue(first["text"].startswith("[offline:"))
        self.assertEqual(failing.calls, 1)

        second = mesh.complete("q")
        self.assertIn({"provider": "old", "status": "retired"}, second["attempts"])
        self.assertEqual(failing.calls, 1)
        self.assertTrue(mesh.health()["old"]["deprecated"])

    def test_offline_fallback_recalls_distilled_rule(self):
        store = KnowledgeStore(key=None)
        store.distill("prompt", "remembered")
        dead = FakeProvider("dead", "cloud", raising("connection", "down"))
        mesh = ProviderMesh([dead], store=store, clock=lambda: 0.0)

        result = mesh.complete("prompt")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["text"], "remembered")
        self.assertEqual(result["provider"], "offline-rules")


class PortfolioTest(unittest.TestCase):
    def test_allocation_covers_every_lane(self):
        allocator = PortfolioAllocator()
        plan = allocator.allocate(6)
        self.assertEqual(sum(plan.values()), 6)
        for lane in allocator.lanes:
            self.assertGreaterEqual(plan[lane], 1)

    def test_low_yield_lane_is_pruned(self):
        allocator = PortfolioAllocator(min_yield=0.1, prune_after=3)
        for _ in range(3):
            allocator.record("explore", reward=0.0, cost=1.0)
        allocator.record("exploit", reward=5.0, cost=1.0)
        self.assertTrue(allocator.is_pruned("explore"))
        plan = allocator.allocate(4)
        self.assertEqual(plan["explore"], 0)
        self.assertGreater(plan["exploit"], 0)

    def test_weights_sum_to_one(self):
        allocator = PortfolioAllocator()
        allocator.record("exploit", reward=2.0, cost=1.0)
        allocator.record("bridge", reward=1.0, cost=2.0)
        weights = allocator.weights()
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)
        self.assertGreater(weights["exploit"], weights["bridge"])


if __name__ == "__main__":
    unittest.main()
