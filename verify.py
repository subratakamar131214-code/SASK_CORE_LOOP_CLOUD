"""Verify Module: Automated test suite runner for validating software components.

Provides automated test suite discovery, program execution, integrity checks,
and structured diagnostic reporting.
"""

from __future__ import annotations

import importlib
import io
import sys
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Type


@dataclass
class VerificationReport:
    """Consolidated report summarizing test execution and integrity checks."""
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_successful(self) -> bool:
        """Returns True if all checks passed without failures or errors."""
        return self.failed_checks == 0 and len(self.errors) == 0


class ComponentIntegrityChecker:
    """Validates structural integrity, imports, and interface adherence of modules."""

    @staticmethod
    def verify_importable(module_name: str) -> bool:
        """Check if a module can be imported cleanly without exceptions."""
        try:
            importlib.import_module(module_name)
            return True
        except Exception:
            return False

    @staticmethod
    def check_module_exports(
        module_name: str,
        expected_symbols: List[str],
    ) -> Dict[str, bool]:
        """Verify that expected classes or functions exist in the target module."""
        try:
            mod = importlib.import_module(module_name)
            return {sym: hasattr(mod, sym) for sym in expected_symbols}
        except Exception:
            return {sym: False for sym in expected_symbols}


class VerificationRunner:
    """Automated test runner orchestrating discovery and programmatic execution."""

    def __init__(self, verbosity: int = 1) -> None:
        self.verbosity = verbosity

    def run_suite(self, suite: unittest.TestSuite) -> VerificationReport:
        """Execute a unittest.TestSuite and compile a VerificationReport."""
        stream = io.StringIO()
        runner = unittest.TextTestRunner(stream=stream, verbosity=self.verbosity)

        start_time = time.monotonic()
        result = runner.run(suite)
        duration = time.monotonic() - start_time

        errors: List[str] = []
        for test_case, err in result.errors:
            errors.append(f"ERROR: {test_case} - {err.strip()}")
        for test_case, failure in result.failures:
            errors.append(f"FAILURE: {test_case} - {failure.strip()}")

        passed = result.testsRun - len(result.failures) - len(result.errors)

        return VerificationReport(
            total_checks=result.testsRun,
            passed_checks=passed,
            failed_checks=len(result.failures) + len(result.errors),
            errors=errors,
            duration_seconds=round(duration, 4),
            details={"output": stream.getvalue()},
        )

    def run_tests_in_module(self, test_case_class: Type[unittest.TestCase]) -> VerificationReport:
        """Run all test methods in a single TestCase class."""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(test_case_class)
        return self.run_suite(suite)

    def run_self_verification(self) -> VerificationReport:
        """Run built-in verification suite covering the core modules."""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(PipelineComponentTests)
        return self.run_suite(suite)


class PipelineComponentTests(unittest.TestCase):
    """Unit tests validating the pipeline modules."""

    def test_telos_engine_milestones(self) -> None:
        """Test milestone creation, dependency resolution, and state updates."""
        from telos import MilestoneStatus, TelosEngine

        engine = TelosEngine()
        m1 = engine.register_milestone("m1", "Init", "Initialize pipeline")
        m2 = engine.register_milestone("m2", "Build", "Build artifacts", depends_on=["m1"])

        runnable = engine.get_runnable_milestones()
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0].id, "m1")

        engine.update_status("m1", MilestoneStatus.COMPLETED)
        runnable = engine.get_runnable_milestones()
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0].id, "m2")

        engine.update_status("m2", MilestoneStatus.COMPLETED)
        self.assertTrue(engine.is_all_complete())
        self.assertFalse(engine.has_failures())

    def test_mutate_code_and_text(self) -> None:
        """Test code syntax verification and template rendering."""
        from mutate import CodeInspector, CodeMutator, TextMutator

        valid_code = "def hello():\n    return 'world'\n"
        self.assertTrue(CodeMutator.ensure_valid_syntax(valid_code))
        self.assertFalse(CodeMutator.ensure_valid_syntax("def invalid syntax():"))

        inspection = CodeInspector.inspect(valid_code)
        self.assertIn("hello", inspection["functions"])

        rendered = TextMutator.render_template(
            "Hello, {{ name }}!",
            {"name": "Automation"},
        )
        self.assertEqual(rendered, "Hello, Automation!")

    def test_surgical_ast_modifier(self) -> None:
        """Test surgical AST parsing, function injection, block modification, and compilation."""
        from mutate import CodeMutator, SurgicalAstModifier

        source = (
            "def existing_task(x: int) -> int:\n"
            "    return x * 2\n"
        )
        tree = SurgicalAstModifier.parse_source(source)

        # 1. Inject a new function
        new_func_code = (
            "def injected_helper(msg: str) -> str:\n"
            "    return f'injected: {msg}'\n"
        )
        SurgicalAstModifier.inject_function(tree, new_func_code)

        # 2. Inject a statement into existing function
        stmt_injected = SurgicalAstModifier.inject_statement_into_function(
            tree,
            func_name="existing_task",
            statement_code="print('Task executed')\n",
            index=0,
        )
        self.assertTrue(stmt_injected)

        # 3. Compile back to source code
        compiled = SurgicalAstModifier.compile_tree(tree)
        self.assertTrue(CodeMutator.ensure_valid_syntax(compiled))
        self.assertIn("injected_helper", compiled)
        self.assertIn("Task executed", compiled)

    def test_prover_execution(self) -> None:
        """Test subprocess execution safety and syntax checker."""
        from prover import ProverInterface

        prover = ProverInterface(default_timeout=10.0)
        res = prover.execute([sys.executable, "-c", "print('PROVER_OK')"])
        self.assertTrue(res.success)
        self.assertIn("PROVER_OK", res.stdout)

    def test_component_integrity(self) -> None:
        """Verify all pipeline modules exist and can be imported."""
        checker = ComponentIntegrityChecker()
        for mod in ["telos", "mutate", "prover", "verify"]:
            self.assertTrue(
                checker.verify_importable(mod),
                f"Module {mod} failed import check.",
            )


if __name__ == "__main__":
    runner = VerificationRunner(verbosity=2)
    report = runner.run_self_verification()
    print(f"\n--- Verification Report ---")
    print(f"Total: {report.total_checks} | Passed: {report.passed_checks} | Failed: {report.failed_checks}")
    print(f"Duration: {report.duration_seconds}s")
    if not report.is_successful:
        for err in report.errors:
            print(err)
        sys.exit(1)
    print("All component checks PASSED successfully.")
