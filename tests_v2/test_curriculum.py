"""Falsifiability gate, replenishment, saturation, and curriculum expansion."""

from __future__ import annotations

import unittest

from sask_v2.curriculum import (
    FalsifiabilityError,
    CurriculumEngine,
    GoalSpec,
    compile_predicate,
    sample_values,
    validate_goal,
)


def make_goal(**overrides):
    payload = {
        "goal_id": "goal-1",
        "statement": "prove x is non-negative",
        "source": "test",
        "inputs": {"x": "float:0:1"},
        "invariants": ("isfinite(x)",),
        "budget": {"max_steps": 10, "seconds": 1.0},
        "predicate_src": "lambda x: x >= 0",
    }
    payload.update(overrides)
    return GoalSpec(**payload)


class FalsifiabilityGateTest(unittest.TestCase):
    def test_valid_goal_passes(self):
        validate_goal(make_goal())

    def test_missing_inputs_rejected(self):
        with self.assertRaises(FalsifiabilityError):
            validate_goal(make_goal(inputs={}, predicate_src="lambda: True"))

    def test_missing_invariants_rejected(self):
        with self.assertRaises(FalsifiabilityError):
            validate_goal(make_goal(invariants=()))

    def test_missing_budget_rejected(self):
        with self.assertRaises(FalsifiabilityError):
            validate_goal(make_goal(budget={}))

    def test_bad_grammar_rejected(self):
        with self.assertRaises(FalsifiabilityError):
            validate_goal(make_goal(inputs={"x": "unsupported"}))

    def test_non_bool_predicate_rejected(self):
        with self.assertRaises(FalsifiabilityError):
            validate_goal(make_goal(predicate_src="lambda x: 1"))

    def test_predicate_exception_rejected(self):
        with self.assertRaises(FalsifiabilityError):
            validate_goal(make_goal(predicate_src="lambda x: 1 / 0"))

    def test_sample_values_cover_boundaries(self):
        self.assertEqual(sample_values("int:2:4", count=3)[:3], [2, 4, 3])
        self.assertEqual(sample_values("bool", count=2), [True, False])
        self.assertEqual(sample_values("choice:a|b", count=3), ["a", "b", "a"])
        floats = sample_values("float:-1:1", count=3)
        self.assertEqual(floats[0], -1.0)
        self.assertEqual(floats[1], 1.0)

    def test_compile_predicate_returns_bool(self):
        predicate = compile_predicate("lambda x: x > 0")
        self.assertIs(predicate(1.0), True)
        self.assertIs(predicate(-1.0), False)


class ReplenishmentTest(unittest.TestCase):
    def test_counterexample_is_deterministic_and_deduplicated(self):
        engine = CurriculumEngine()
        item = {"metric": "latency", "inverted_target": 0.4}
        first = engine.add_counterexample(item)
        second = engine.add_counterexample(item)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(first.source, "devil_advocate")
        predicate = compile_predicate(first.predicate_src)
        self.assertIs(predicate(0.4), True)
        self.assertIs(predicate(0.0), False)

    def test_replenish_sorts_by_score(self):
        engine = CurriculumEngine()
        engine.replenish(
            counterexamples=[{"metric": "latency", "inverted_target": 0.4}],
            transfers=[{"source_domain": "physics", "target_domain": "biology", "confidence": 0.9}],
            frontiers=[{"source": "egraph_engine", "cost": 10.0}],
        )
        goals = engine.next_goals(10)
        self.assertEqual(len(goals), 3)
        scores = [goal.score() for goal in goals]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(goals[0].source, "devil_advocate")

    def test_pop_next_removes_goal(self):
        engine = CurriculumEngine()
        engine.add_transfer({"source_domain": "a", "target_domain": "b", "confidence": 0.5})
        goal = engine.pop_next()
        self.assertIsNotNone(goal)
        self.assertIsNone(engine.pop_next())
        self.assertEqual(engine.status()["queued"], 0)

    def test_persistence_roundtrip(self):
        engine = CurriculumEngine()
        engine.add_counterexample({"metric": "latency", "inverted_target": 0.4})
        engine.observe("goal-x", True)
        engine.observe("goal-x", True)
        clone = CurriculumEngine.from_dict(engine.to_dict())
        self.assertEqual(clone.status()["queued"], engine.status()["queued"])
        self.assertEqual(clone.status()["samples"], 2)


class SaturationAndExpansionTest(unittest.TestCase):
    def test_saturation_requires_min_samples(self):
        engine = CurriculumEngine()
        for _ in range(7):
            engine.observe("g", True)
        self.assertFalse(engine.is_saturated(min_samples=8))
        engine.observe("g", True)
        self.assertTrue(engine.is_saturated(min_samples=8))

    def test_frontier_tasks_stay_inside_band(self):
        engine = CurriculumEngine()
        tasks = engine.frontier_tasks(3)
        self.assertEqual(len(tasks), 3)
        for task in tasks:
            self.assertGreaterEqual(task.difficulty, 0.30)
            self.assertLessEqual(task.difficulty, 0.70)
            predicate = compile_predicate(task.predicate_src)
            self.assertIs(predicate(task.difficulty), True)
            self.assertIs(predicate(0.0 if task.difficulty > 0.2 else 1.0), False)

    def test_compression_tasks_require_similar_rules(self):
        engine = CurriculumEngine()
        rules = [
            {"lhs": "f(a + b)", "signature": "r1"},
            {"lhs": "f(a + c)", "signature": "r2"},
            {"lhs": "g(x * y)", "signature": "r3"},
        ]
        tasks = engine.compression_tasks(rules, limit=5)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].source, "compression")

    def test_maybe_expand_only_when_saturated(self):
        engine = CurriculumEngine()
        rules = [{"lhs": "f(a + b)"}, {"lhs": "f(a + c)"}]
        self.assertEqual(engine.maybe_expand(rules), {"frontier": [], "compression": []})
        for _ in range(8):
            engine.observe("g", True)
        expansion = engine.maybe_expand(rules)
        self.assertEqual(len(expansion["frontier"]), 3)
        self.assertEqual(len(expansion["compression"]), 1)
        self.assertGreaterEqual(engine.status()["saturation_events"], 1)


if __name__ == "__main__":
    unittest.main()
