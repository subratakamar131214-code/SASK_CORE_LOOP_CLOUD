"""Lemma/macro extraction and multi-encoding reformulation."""

from __future__ import annotations

import unittest

from sask_v2.abstraction import (
    LemmaExtractor,
    MacroDetector,
    MultiEncodingRouter,
    persist_rules,
)
from sask_v2.knowledge import KnowledgeStore


class LemmaExtractorTest(unittest.TestCase):
    def test_constant_generalization(self):
        rule = LemmaExtractor().extract(
            "def f(x):\n    return x + 1\n",
            "def f(x):\n    return x + 2\n",
        )
        self.assertIsNotNone(rule)
        self.assertEqual(rule.kind, "lemma")
        self.assertEqual(rule.parameters, ((1, 2),))
        self.assertIn("+ 1", rule.lhs)
        self.assertIn("+ 2", rule.rhs)

    def test_structural_change_is_not_a_lemma(self):
        rule = LemmaExtractor().extract(
            "def f(x):\n    return x + 1\n",
            "def f(x):\n    return x * 2\n",
        )
        self.assertIsNone(rule)

    def test_identical_program_is_not_a_lemma(self):
        source = "def f(x):\n    return x + 1\n"
        self.assertIsNone(LemmaExtractor().extract(source, source))

    def test_syntax_error_returns_none(self):
        self.assertIsNone(LemmaExtractor().extract("def f(:\n", "def f():\n    return 1\n"))

    def test_rules_persist_to_store(self):
        store = KnowledgeStore()
        rule = LemmaExtractor().extract(
            "def f(x):\n    return x + 1\n",
            "def f(x):\n    return x + 2\n",
        )
        results = persist_rules(store, [rule])
        self.assertEqual(results[0]["status"], "created")
        self.assertEqual(len(store.records("lemma")), 1)


class MacroDetectorTest(unittest.TestCase):
    def test_repeated_call_run_becomes_macro(self):
        source = (
            "def run():\n"
            "    prepare()\n"
            "    prepare()\n"
            "    prepare()\n"
        )
        macros = MacroDetector().detect(source)
        self.assertEqual(len(macros), 1)
        self.assertEqual(macros[0].name, "macro_prepare_x3")
        self.assertEqual(macros[0].savings, 2)

    def test_single_call_is_not_a_macro(self):
        self.assertEqual(MacroDetector().detect("def run():\n    prepare()\n"), [])

    def test_interrupted_run_splits(self):
        source = (
            "def run():\n"
            "    a()\n"
            "    a()\n"
            "    b()\n"
            "    c()\n"
            "    c()\n"
        )
        macros = MacroDetector().detect(source)
        self.assertEqual(len(macros), 2)
        self.assertEqual({macro.name for macro in macros}, {"macro_a_x2", "macro_c_x2"})


class MultiEncodingRouterTest(unittest.TestCase):
    def test_falls_through_stalled_encoding(self):
        seen = []

        def symbolic(steps):
            seen.append(("symbolic", steps))
            return None

        def constraint(steps):
            seen.append(("constraint", steps))
            return 7

        router = MultiEncodingRouter(
            {"symbolic": symbolic, "constraint": constraint},
            verify=lambda candidate: candidate == 7,
            budget_steps=20,
        )
        result = router.solve()
        self.assertTrue(result["solved"])
        self.assertEqual(result["encoding"], "constraint")
        self.assertEqual([item["status"] for item in result["attempts"]], ["stalled", "verified"])
        self.assertEqual(seen, [("symbolic", 20), ("constraint", 20)])

    def test_rejected_candidate_continues(self):
        def first(steps):
            return 1

        def second(steps):
            return 2

        router = MultiEncodingRouter(
            {"first": first, "second": second},
            verify=lambda candidate: candidate == 2,
        )
        result = router.solve()
        self.assertTrue(result["solved"])
        self.assertEqual(result["encoding"], "second")
        self.assertEqual(result["attempts"][0]["status"], "rejected")

    def test_no_encoding_solves(self):
        router = MultiEncodingRouter(
            {"only": lambda steps: None},
            verify=lambda candidate: True,
        )
        result = router.solve()
        self.assertFalse(result["solved"])
        self.assertIsNone(result["encoding"])


if __name__ == "__main__":
    unittest.main()
