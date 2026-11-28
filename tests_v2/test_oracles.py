"""Oracle synthesis, adversarial audit, differential consensus, multi-axis gate."""

from __future__ import annotations

import math
import unittest

from sask_v2.curriculum import GoalSpec
from sask_v2.oracles import (
    OracleSynthesizer,
    audit_source,
    differential_validate,
    multi_axis_promote,
)


def goal(**overrides):
    payload = {
        "goal_id": "oracle-goal",
        "statement": "double a non-negative input",
        "source": "test",
        "inputs": {"x": "float:0:10"},
        "invariants": ("result >= 0", "abs(result - 2 * x) < 1e-9"),
        "budget": {"max_steps": 100, "seconds": 5.0},
        "predicate_src": "lambda x: x >= 0",
    }
    payload.update(overrides)
    return GoalSpec(**payload)


class AuditTest(unittest.TestCase):
    def test_clean_source_passes(self):
        self.assertTrue(audit_source("def solve(x):\n    return 2 * x\n")["ok"])

    def test_dangerous_import_rejected(self):
        audit = audit_source("import os\n\ndef solve(x):\n    return x\n")
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["violations"][0]["kind"], "import")

    def test_banned_call_rejected(self):
        audit = audit_source("def solve(x):\n    return open('/etc/passwd').read()\n")
        self.assertFalse(audit["ok"])
        self.assertTrue(any(item["name"] == "open" for item in audit["violations"]))


class SynthesisTest(unittest.TestCase):
    def test_correct_candidate_is_accepted(self):
        result = OracleSynthesizer().synthesize(goal(), "def solve(x):\n    return 2 * x\n")
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["executed"], 0)
        self.assertTrue(result["checks"]["boundary"]["ok"])
        self.assertTrue(result["checks"]["property"]["ok"])

    def test_loophole_candidate_is_rejected(self):
        result = OracleSynthesizer().synthesize(goal(), "def solve(x):\n    return 0\n")
        self.assertFalse(result["ok"])
        self.assertTrue(result["failures"])

    def test_audit_rejection_short_circuits(self):
        result = OracleSynthesizer().synthesize(
            goal(), "import socket\n\ndef solve(x):\n    return x\n"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "audit_rejected")

    def test_inverse_roundtrip(self):
        target = goal(
            inputs={"x": "float:1:5"},
            invariants=("abs(result - x) < 1e-9",),
            predicate_src="lambda x: x > 0",
            inverse_src="lambda y: y",
        )
        result = OracleSynthesizer().synthesize(target, "def solve(x):\n    return x\n")
        self.assertTrue(result["ok"], result)


class DifferentialTest(unittest.TestCase):
    def test_agreement_with_independent_solvers_promotes(self):
        result = differential_validate(lambda t: math.exp(-t))
        self.assertTrue(result["promoted"], result)
        self.assertGreaterEqual(len(result["consensus_group"]), 2)

    def test_divergent_candidate_is_rejected(self):
        result = differential_validate(lambda t: 0.0)
        self.assertFalse(result["promoted"])
        self.assertEqual(result["reason"], "candidate_diverged")

    def test_parameterized_solution_promotes(self):
        result = differential_validate(
            lambda t: math.exp(-0.5 * t), params={"k": 0.5}
        )
        self.assertTrue(result["promoted"], result)


class MultiAxisTest(unittest.TestCase):
    def test_two_axis_gain_promotes(self):
        result = multi_axis_promote(
            {"compression": 0.8, "ood_generalization": 0.7, "speed": 1.0},
            {"compression": 0.5, "ood_generalization": 0.5, "speed": 1.0},
            axes=("compression", "ood_generalization", "speed"),
        )
        self.assertTrue(result["promoted"])
        self.assertEqual(set(result["improved"]), {"compression", "ood_generalization"})

    def test_single_axis_gain_is_not_enough(self):
        result = multi_axis_promote(
            {"compression": 0.9, "ood_generalization": 0.5},
            {"compression": 0.5, "ood_generalization": 0.5},
        )
        self.assertFalse(result["promoted"])
        self.assertEqual(result["reason"], "insufficient_axes")

    def test_regression_blocks_promotion(self):
        result = multi_axis_promote(
            {"compression": 0.9, "ood_generalization": 0.4},
            {"compression": 0.5, "ood_generalization": 0.5},
        )
        self.assertFalse(result["promoted"])
        self.assertEqual(result["reason"], "regression")


if __name__ == "__main__":
    unittest.main()
