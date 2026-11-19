"""Verify Module: Automated test suite runner for validating software components.

Provides automated test suite discovery, program execution, integrity checks,
and structured diagnostic reporting.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
import time
import unittest
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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


@dataclass
class EgoRatchetState:
    """Persisted high-watermark telemetry for the regression ratchet."""
    high_watermark_passed: int = 0
    high_watermark_total: int = 0
    gate_runs: int = 0
    updated_at: Optional[str] = None


class EgoRatchet:
    """Persistent high-watermark ratchet: never regress below the best-known pass count."""

    DEFAULT_PATH = ".sask_ego_ratchet.json"

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self.state_path = Path(state_path or self.DEFAULT_PATH)
        self.state = self._load()

    def _load(self) -> EgoRatchetState:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return EgoRatchetState(
                high_watermark_passed=int(data.get("high_watermark_passed", 0)),
                high_watermark_total=int(data.get("high_watermark_total", 0)),
                gate_runs=int(data.get("gate_runs", 0)),
                updated_at=data.get("updated_at"),
            )
        except (OSError, ValueError, TypeError):
            return EgoRatchetState()

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(asdict(self.state), indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)

    def check(self, passed_checks: int) -> bool:
        """True if the pass count is at or above the persisted high-watermark."""
        return passed_checks >= self.state.high_watermark_passed

    def record(self, passed_checks: int, total_checks: int) -> bool:
        """Record a gate run; promotes the high-watermark and returns True if promoted."""
        self.state.gate_runs += 1
        promoted = passed_checks > self.state.high_watermark_passed
        if promoted:
            self.state.high_watermark_passed = passed_checks
        if total_checks > self.state.high_watermark_total:
            self.state.high_watermark_total = total_checks
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return promoted

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self.state)


class AncestralRegressionGate:
    """Ancestral regression gate combining overthinking consensus with an ego ratchet."""

    def __init__(self, ratchet_path: Optional[Path] = None) -> None:
        self.ratchet = EgoRatchet(ratchet_path)

    def evaluate(self, report: VerificationReport, depth: int) -> Dict[str, Any]:
        """Apply the ego ratchet to a consensus report, mutating it on regression."""
        snapshot_before = self.ratchet.snapshot()
        regressed = not self.ratchet.check(report.passed_checks)
        promoted = self.ratchet.record(report.passed_checks, report.total_checks)
        snapshot_after = self.ratchet.snapshot()

        info: Dict[str, Any] = {
            "overthinking_depth": depth,
            "passed_checks": report.passed_checks,
            "high_watermark_before": snapshot_before["high_watermark_passed"],
            "high_watermark_after": snapshot_after["high_watermark_passed"],
            "promoted": promoted,
            "regressed": regressed,
            "gate_runs": snapshot_after["gate_runs"],
        }

        if regressed:
            report.failed_checks += 1
            report.errors.append(
                f"EGO RATCHET: passed {report.passed_checks} checks, below high-watermark "
                f"{snapshot_before['high_watermark_passed']}."
            )
        return info


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

    def __init__(
        self,
        verbosity: int = 1,
        ratchet_path: Optional[Path] = None,
    ) -> None:
        self.verbosity = verbosity
        self.gate = AncestralRegressionGate(ratchet_path=ratchet_path)

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

    @staticmethod
    def _resolve_depth(depth_override: Optional[int] = None) -> int:
        """Resolve the overthinking consensus depth from drives.py."""
        if depth_override is not None:
            return max(1, int(depth_override))
        try:
            from drives import get_overthinking_depth  # type: ignore[import]
            return max(1, int(get_overthinking_depth()))
        except Exception:
            return 3

    def run_ancestral_gate(
        self,
        depth_override: Optional[int] = None,
    ) -> VerificationReport:
        """Run the suite at overthinking depth, requiring consensus, then apply the ego ratchet.

        Every pass must succeed. The consensus report uses the minimum pass count
        across runs, so the ratchet is insensitive to depth changes.
        """
        depth = self._resolve_depth(depth_override)
        run_reports: List[VerificationReport] = []

        for _ in range(depth):
            report = self.run_self_verification()
            run_reports.append(report)
            if not report.is_successful:
                break

        total_checks = max((r.total_checks for r in run_reports), default=0)
        passed_checks = min((r.passed_checks for r in run_reports), default=0)
        failed_checks = max((r.failed_checks for r in run_reports), default=0)

        errors: List[str] = []
        for pass_index, report in enumerate(run_reports):
            for error in report.errors:
                errors.append(f"[pass {pass_index + 1}/{depth}] {error}")

        completed = len(run_reports)
        if completed < depth:
            failed_checks = max(failed_checks, 1)
            errors.append(
                f"OVERTHINKING GATE: consensus aborted after {completed}/{depth} passes."
            )

        consensus = VerificationReport(
            total_checks=total_checks,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            errors=errors,
            duration_seconds=round(sum(r.duration_seconds for r in run_reports), 4),
            details={"overthinking_depth": depth, "passes_completed": completed},
        )

        ratchet_info = self.gate.evaluate(consensus, depth=depth)
        consensus.details["ego_ratchet"] = ratchet_info
        return consensus


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

    def test_telos_tactical_subquests_and_metabolic_reset(self) -> None:
        """Test phased sub-quest generation, mastery progression, and metabolic resets."""
        from telos import MilestoneStatus, TacticalPhase, TelosEngine

        engine = TelosEngine(stagnation_threshold=3)
        macro = engine.register_macro_objective(
            objective_id="mastery_demo",
            title="System Mastery Demo",
            target_mastery=1.0,
        )

        subquests = engine.issue_phased_tactical_subquests(
            macro_objective_id="mastery_demo",
            subquest_specs=[
                {"id": "sq1", "title": "Recon", "phase": TacticalPhase.RECONNAISSANCE, "weight": 1.0},
                {"id": "sq2", "title": "Transform", "phase": TacticalPhase.TRANSFORMATION, "depends_on": ["sq1"], "weight": 1.0},
            ],
        )
        self.assertEqual(len(subquests), 2)
        self.assertEqual(macro.current_mastery, 0.0)

        # Complete first subquest and check mastery score
        engine.update_status("sq1", MilestoneStatus.COMPLETED)
        self.assertEqual(macro.current_mastery, 0.5)

        # Mark sq2 as failed
        engine.update_status("sq2", MilestoneStatus.FAILED, error="Stuck in local basin")
        self.assertTrue(engine.has_failures())

        # Simulate stagnation across 3 cycles with stagnant error rate 0.5
        reset_event = None
        for _ in range(3):
            reset_event = engine.record_error_metric(0.5)

        self.assertIsNotNone(reset_event)
        self.assertEqual(reset_event.reset_type, "METABOLIC_RESET")
        self.assertIn("sq2", reset_event.cleared_milestone_ids)

        # Confirm failed task was cleared from basin and reset to PENDING
        ms2 = engine.get_milestone("sq2")
        self.assertEqual(ms2.status, MilestoneStatus.PENDING)
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

    def test_lean4_prover_pipeline(self) -> None:
        """Test Lean 4 / elan translation, wrapping, and verification pipeline."""
        from prover import Lean4Prover, ProverInterface

        lean_prover = Lean4Prover(allow_sorry=False)

        # 1. Test wrapping of code assertion with Mathlib
        wrapped_code = lean_prover.wrap_assertion(
            assertion="∀ (n : Nat), n + 0 = n",
            theorem_name="nat_add_zero",
            use_mathlib=True,
        )
        self.assertIn("import Mathlib", wrapped_code)
        self.assertIn("theorem nat_add_zero : ∀ (n : Nat), n + 0 = n", wrapped_code)

        # 2. Test strict rejection of unproved 'sorry' obligations
        sorry_assertion = "theorem test_sorry : 1 = 1 := by sorry"
        result_sorry = lean_prover.verify_lean_source(sorry_assertion)
        self.assertFalse(result_sorry.verified)
        self.assertFalse(bool(result_sorry))
        self.assertTrue(result_sorry.has_sorry)

        # 3. Test ProverInterface integration returning strict boolean
        prover = ProverInterface()
        status = prover.verify_lean_assertion(sorry_assertion)
        self.assertIsInstance(status, bool)
        self.assertFalse(status)

    def test_component_integrity(self) -> None:
        """Verify all pipeline modules exist and can be imported."""
        checker = ComponentIntegrityChecker()
        for mod in ["core", "telos", "mutate", "prover", "verify", "drives", "bio_gen"]:
            self.assertTrue(
                checker.verify_importable(mod),
                f"Module {mod} failed import check.",
            )

    def test_drives_discipline_engine(self) -> None:
        """Verify UnifiedDisciplineEngine maintains D_floor >= 0.90 under all conditions."""
        from drives import (
            DEFAULT_DRIVE_ENGINE,
            UnifiedDisciplineEngine,
            get_overthinking_depth,
            get_unified_discipline,
        )

        # Fresh engine — discipline must be at or above floor
        engine = UnifiedDisciplineEngine()
        self.assertGreaterEqual(engine.get_unified_discipline(), 0.90)

        # Module-level helpers
        self.assertGreaterEqual(get_unified_discipline(), 0.90)
        depth = get_overthinking_depth()
        self.assertGreaterEqual(depth, 3)
        self.assertLessEqual(depth, 7)

        pressure = engine.get_mutation_pressure()
        self.assertGreaterEqual(pressure, 0.0)
        self.assertLessEqual(pressure, 1.0)

        # apply_stimulus should never decay discipline below D_floor
        stressed = UnifiedDisciplineEngine()
        for challenge in [0, 5, 50, 500]:
            stressed.apply_stimulus(challenge_intensity=challenge)
        self.assertGreaterEqual(stressed.get_unified_discipline(), 0.90)
        self.assertGreaterEqual(DEFAULT_DRIVE_ENGINE.get_unified_discipline(), 0.90)

    def test_bio_gen_harness_spawn(self) -> None:
        """Verify BioGenReplicator can spawn an ALGORITHMIC_CODE harness and capture output."""
        from bio_gen import BioGenReplicator, HarnessVector

        replicator = BioGenReplicator()
        result = replicator.spawn_specialized_harness(
            goal_id="test-bio-gen-001",
            vector=HarnessVector.ALGORITHMIC_CODE,
            payload='print("OK")',
        )
        self.assertTrue(result.get("success", False), f"Harness failed: {result}")
        self.assertIn("OK", result.get("output", ""))

    def test_goal_ingestion_engine(self) -> None:
        """Verify GoalIngestionEngine can ingest, classify, decompose, and queue goals."""
        from telos import GoalIngestionEngine, GoalPriority, GoalStatus, GoalVector

        engine = GoalIngestionEngine()

        # Ingest with explicit vector
        g1 = engine.ingest_goal(
            raw_prompt="Prove that every Lean 4 theorem with sorry is invalid.",
            vector=GoalVector.FORMAL_PROOF,
            priority=GoalPriority.HIGH,
        )
        self.assertEqual(g1.vector, GoalVector.FORMAL_PROOF)
        self.assertEqual(g1.priority, GoalPriority.HIGH)
        self.assertEqual(g1.status, GoalStatus.QUEUED)
        self.assertTrue(len(g1.sub_tasks) >= 1)

        # Ingest without vector — auto-classification
        g2 = engine.ingest_goal("Implement an efficient quicksort algorithm in Python.")
        self.assertEqual(g2.vector, GoalVector.ALGORITHMIC_CODE)

        # Priority queue ordering
        engine.ingest_goal("Low priority explore task.", priority=GoalPriority.LOW)
        engine.ingest_goal("Critical discovery.", priority=GoalPriority.CRITICAL)
        queue = engine.get_priority_queue(discipline=1.0)
        self.assertGreaterEqual(queue[0].priority.value, queue[-1].priority.value)

        # Lifecycle transitions
        engine.mark_goal_complete(g1.id)
        self.assertEqual(engine.get_goal(g1.id).status, GoalStatus.COMPLETED)

        engine.mark_goal_failed(g2.id, error="test error")
        self.assertEqual(engine.get_goal(g2.id).status, GoalStatus.FAILED)

        summary = engine.get_summary()
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["failed"], 1)

    def test_dual_track_macro_discovery_taxonomy(self) -> None:
        """Verify goals are classified into convergent-mastery and open-frontier tracks."""
        from telos import DiscoveryTrack, GoalIngestionEngine, GoalVector

        engine = GoalIngestionEngine()
        convergent = engine.ingest_goal(
            "Prove the soundness theorem in Lean.",
            vector=GoalVector.FORMAL_PROOF,
        )
        frontier = engine.ingest_goal(
            "Explore open-ended hypotheses about emergent computation."
        )

        self.assertEqual(convergent.discovery_track, DiscoveryTrack.CONVERGENT_MASTERY)
        self.assertEqual(frontier.discovery_track, DiscoveryTrack.OPEN_FRONTIER)

        summary = engine.get_summary()["discovery_tracks"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["tracks"][DiscoveryTrack.CONVERGENT_MASTERY.value], 1)
        self.assertEqual(summary["tracks"][DiscoveryTrack.OPEN_FRONTIER.value], 1)

        # Manifest round-trip preserves the track
        restored = type(convergent).from_dict(convergent.to_dict())
        self.assertEqual(restored.discovery_track, DiscoveryTrack.CONVERGENT_MASTERY)

    def test_ego_ratchet_high_watermark(self) -> None:
        """Verify the ego ratchet persists and never lowers its high-watermark."""
        import tempfile
        from verify import EgoRatchet

        with tempfile.TemporaryDirectory() as tmp_dir:
            ratchet_path = Path(tmp_dir) / "ratchet.json"
            ratchet = EgoRatchet(ratchet_path)
            self.assertTrue(ratchet.check(0))
            self.assertTrue(ratchet.record(passed_checks=10, total_checks=12))
            self.assertTrue(ratchet.check(10))
            self.assertFalse(ratchet.check(9))

            # A regression must not lower the watermark
            self.assertFalse(ratchet.record(passed_checks=7, total_checks=12))
            reloaded = EgoRatchet(ratchet_path)
            self.assertEqual(reloaded.snapshot()["high_watermark_passed"], 10)
            self.assertEqual(reloaded.snapshot()["gate_runs"], 2)

    def test_ancestral_gate_regression_detection(self) -> None:
        """Verify the ancestral gate flags regressions below the ego high-watermark."""
        import tempfile
        from verify import AncestralRegressionGate, VerificationReport

        with tempfile.TemporaryDirectory() as tmp_dir:
            gate = AncestralRegressionGate(Path(tmp_dir) / "ratchet.json")

            baseline = VerificationReport(total_checks=10, passed_checks=10)
            info = gate.evaluate(baseline, depth=3)
            self.assertFalse(info["regressed"])
            self.assertTrue(info["promoted"])
            self.assertEqual(info["high_watermark_after"], 10)

            regressed = VerificationReport(total_checks=10, passed_checks=8)
            info = gate.evaluate(regressed, depth=3)
            self.assertTrue(info["regressed"])
            self.assertEqual(regressed.failed_checks, 1)
            self.assertFalse(regressed.is_successful)
            self.assertFalse(gate.ratchet.check(8))

    def test_overthinking_depth_resolution(self) -> None:
        """Verify overthinking depth resolves within the drives-sanctioned range."""
        from verify import VerificationRunner

        runner = VerificationRunner()
        self.assertEqual(runner._resolve_depth(2), 2)
        depth = runner._resolve_depth()
        self.assertGreaterEqual(depth, 3)
        self.assertLessEqual(depth, 7)

    def test_science_router_matrix(self) -> None:
        """Verify all ten science routers execute and return bounded telemetry."""
        from science_mcp import (
            ROUTER_NAMES,
            _ROUTER_FUNCTIONS,
            dispatch_router,
            get_science_telemetry,
        )

        self.assertEqual(len(ROUTER_NAMES), 10)
        for router in ROUTER_NAMES:
            self.assertTrue(
                callable(_ROUTER_FUNCTIONS[router]),
                f"{router} registry entry is not callable: {_ROUTER_FUNCTIONS[router]!r}",
            )
            result = dispatch_router(router, payload={})
            self.assertTrue(
                result.get("success"),
                f"{router} failed: {result.get('error')}",
            )
            self.assertEqual(result["router"], router)
            self.assertIn(
                result["execution_mode"],
                {"simulated_fallback", "live_adapter"},
            )
            self.assertGreaterEqual(result["numerical_residual"], 0.0)
            self.assertLessEqual(result["numerical_residual"], 1.0)
            self.assertGreaterEqual(result["latency_seconds"], 0.0)
            self.assertIn("task_graph", result)
            self.assertIn("optimizer", result)
            self.assertIn("discipline", result)

        telemetry = get_science_telemetry()
        self.assertEqual(telemetry["router_count"], 10)
        self.assertEqual(len(telemetry["routers"]), 10)

    def test_telos_science_goal_dispatch(self) -> None:
        """Verify telos routes a science prompt to the right router and executes it."""
        from telos import GoalIngestionEngine

        engine = GoalIngestionEngine()
        goal = engine.ingest_goal(
            "Run a quantum circuit swarm with high precision."
        )
        self.assertEqual(engine.resolve_router(goal), "quantum_router")
        self.assertEqual(goal.metadata.get("router"), "quantum_router")

        report = engine.dispatch_goal(goal.id)
        self.assertTrue(report["handled"])
        self.assertEqual(report["router"], "quantum_router")
        self.assertTrue(report["result"]["success"])
        self.assertEqual(goal.metadata["router"], "quantum_router")
        self.assertEqual(engine.get_router_histogram()["quantum_router"], 1)
        self.assertEqual(engine.get_summary()["router_dispatches"], 1)

    def test_drives_execution_friction(self) -> None:
        """Verify router friction raises discipline without breaching D_floor."""
        from drives import UnifiedDisciplineEngine

        engine = UnifiedDisciplineEngine()
        before = engine.get_unified_discipline()
        telemetry = engine.record_execution_telemetry(
            router="quantum_router",
            latency_seconds=3.0,
            residual=1.0,
            success=False,
        )
        self.assertGreater(telemetry["challenge_intensity"], 0.0)
        self.assertLessEqual(telemetry["challenge_intensity"], 10.0)
        self.assertGreaterEqual(telemetry["discipline_after"], 0.90)
        self.assertGreaterEqual(telemetry["discipline_after"], before)
        self.assertEqual(telemetry["floor"], 0.90)

    def test_bio_gen_router_harness(self) -> None:
        """Verify bio_gen can spawn a harness for a specific science router."""
        from bio_gen import BioGenReplicator

        replicator = BioGenReplicator()
        result = replicator.spawn_router_harness(
            goal_id="test-router-harness-001",
            router="quantum_router",
            payload={
                "qasm_batch": ["OPENQASM 2.0; qreg q[1];"],
                "target_backends": ["statevector_sim"],
                "precision_target": 1e-6,
            },
        )
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertIn("quantum_router", result.get("output", ""))

    def test_goal_stream_expansion_and_dedup(self) -> None:
        """Verify exhaustive level-wise expansion and ledger deduplication."""
        import tempfile

        from telos import GoalIngestionEngine, GoalPriority, GoalStreamingPipeline

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            objective = "Prove the convergence theorem for autonomous kernels."

            engine = GoalIngestionEngine()
            stream = GoalStreamingPipeline(
                engine,
                ledger_path=root / "ledger.json",
                state_path=root / "stream.json",
                max_level=3,
                max_concurrency=2,
                producer_poll_seconds=0.05,
            )
            info = stream.submit_objective(objective, level=2, priority=GoalPriority.HIGH)
            self.assertTrue(info["new"])
            self.assertTrue(stream.start(resume=False)["started"])

            # L0=1, L1=5, L2=15 => 21 exhaustive permutations.
            self.assertTrue(stream.wait_for_items(21, timeout=10), stream.get_telemetry())
            drained = stream.drain()
            self.assertEqual(len(drained), 2)  # bounded by max_concurrency
            for goal in drained:
                self.assertTrue(stream.complete(goal.id, True)["freed_slot"])
            self.assertEqual(len(stream.drain()), 2)

            telemetry = stream.stop()
            self.assertEqual(telemetry["produced"], 21)
            self.assertEqual(telemetry["level_counts"], {"0": 1, "1": 5, "2": 15})
            self.assertEqual(telemetry["ledger"]["entries"], 21)
            self.assertEqual(telemetry["completed"], 2)
            self.assertTrue((root / "stream.json").is_file())
            self.assertTrue((root / "ledger.json").is_file())

            # A fresh runner with the same ledger must suppress every duplicate.
            engine2 = GoalIngestionEngine()
            stream2 = GoalStreamingPipeline(
                engine2,
                ledger_path=root / "ledger.json",
                state_path=root / "stream2.json",
                max_level=3,
                max_concurrency=2,
                producer_poll_seconds=0.05,
            )
            stream2.submit_objective(objective, level=2, priority=GoalPriority.HIGH)
            stream2.start(resume=False)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                snapshot = stream2.get_telemetry()
                if snapshot["producer_passes"] >= 1 and snapshot["queue_depth"] == 0:
                    break
                time.sleep(0.02)
            telemetry2 = stream2.stop()
            self.assertEqual(telemetry2["produced"], 0)
            self.assertGreaterEqual(telemetry2["duplicates_suppressed"], 21)
            self.assertEqual(len(engine2.get_all_goals()), 0)

    def test_goal_stream_resume_and_slot_release(self) -> None:
        """Verify in-flight and pending work survives a handoff and is requeued."""
        import tempfile

        from telos import GoalIngestionEngine, GoalPriority, GoalStreamingPipeline

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            objective = "Explore novel protein docking frontiers."

            stream = GoalStreamingPipeline(
                GoalIngestionEngine(),
                ledger_path=root / "ledger.json",
                state_path=root / "stream.json",
                max_level=1,
                max_concurrency=2,
                producer_poll_seconds=0.05,
            )
            stream.submit_objective(objective, level=1, priority=GoalPriority.NORMAL)
            stream.start(resume=False)
            self.assertTrue(stream.wait_for_items(6, timeout=10))  # L0=1 + L1=5

            in_flight = stream.drain()
            self.assertEqual(len(in_flight), 2)
            self.assertEqual(stream.available_slots, 0)
            self.assertEqual(stream.drain(), [])  # slots exhausted

            handoff = stream.stop()
            self.assertEqual(handoff["in_flight"], 2)

            resumed_engine = GoalIngestionEngine()
            resumed = GoalStreamingPipeline(
                resumed_engine,
                ledger_path=root / "ledger.json",
                state_path=root / "stream.json",
                max_level=1,
                max_concurrency=2,
                producer_poll_seconds=0.05,
            )
            info = resumed.start(resume=True)
            self.assertEqual(info["resumed"]["requeued"], 6)
            self.assertTrue(resumed.wait_for_items(6, timeout=5))
            self.assertEqual(len(resumed_engine.get_all_goals()), 6)
            resumed.stop()

    def test_core_goal_stream_wiring(self) -> None:
        """Verify the core starts, drains, persists, and exports the goal stream."""
        import tempfile

        from core import AutomationKernel

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            kernel = AutomationKernel(
                workspace_root=root,
                window_minutes=1.0,
                stream_enabled=True,
                stream_level=1,
                stream_concurrency=2,
            )
            info = kernel._start_goal_stream()
            self.assertTrue(info["started"])
            self.assertTrue(kernel.telos.stream.wait_for_items(6, timeout=10))

            telemetry = kernel.telos.stream.stop()
            self.assertGreaterEqual(telemetry["produced"], 6)

            state_path = kernel.save_state_to_disk()
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["goal_stream"]["enabled"])
            self.assertGreaterEqual(payload["goal_stream"]["produced"], 6)
            self.assertGreaterEqual(payload["goal_stream"]["ledger"]["entries"], 6)
            self.assertTrue((root / ".sask_goal_ledger.json").is_file())
            self.assertTrue((root / ".sask_goal_stream.json").is_file())

    def test_core_wall_clock_timer_and_handoff(self) -> None:
        """Test the 340-minute wall-clock execution timer and container handoff serialization."""
        import json
        import tempfile
        from core import AutomationKernel, CoreState, WallClockTimer

        # 1. Verify standard 340-minute timer configuration
        timer = WallClockTimer(window_minutes=340.0)
        self.assertEqual(timer.window_minutes, 340.0)
        self.assertEqual(timer.window_seconds, 340.0 * 60.0)
        self.assertFalse(timer.is_threshold_reached())
        self.assertGreater(timer.remaining_minutes, 339.0)

        # 2. Test threshold expiration trigger
        expired_timer = WallClockTimer(window_minutes=0.001, margin_seconds=1.0)
        self.assertTrue(expired_timer.is_threshold_reached())

        # 3. Test AutomationKernel state serialization and handoff
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            tmp_path = tmp_root / "test_state.json"
            kernel = AutomationKernel(
                window_minutes=0.0001,  # triggers immediate handoff
                workspace_root=tmp_root,
                state_file=tmp_path,
            )
            kernel.run_continuous_loop(max_cycles=1)

            self.assertEqual(kernel.state, CoreState.HANDOFF_READY)
            self.assertTrue(tmp_path.is_file())

            payload = json.loads(tmp_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["container_handoff"])
            self.assertEqual(payload["state"], "HANDOFF_READY")
            self.assertEqual(payload["wall_clock"]["window_minutes"], 0.0001)

    def test_meta_prover_axiomatic_search(self) -> None:
        """Verify axiom mutation and evolutionary framework search persist state."""
        import tempfile

        from meta_prover import AxiomRegistry, MetaProver

        registry = AxiomRegistry()
        frameworks = registry.base_frameworks()
        self.assertGreaterEqual(len(frameworks), 6)
        mutated = registry.mutate(frameworks[0], salt=7)
        self.assertNotEqual(mutated.signature(), frameworks[0].signature())

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "meta.json"
            prover = MetaProver(
                state_path=state_path,
                population=6,
                max_generations=2,
            )
            result = prover.search(
                "construct a compact verified proof for distributed state",
                generations=2,
            )
            self.assertGreater(result["evaluations"], len(frameworks))
            self.assertGreaterEqual(result["best_score"], 0.5)
            self.assertLessEqual(result["best_score"], 1.0)
            self.assertEqual(len(result["ranking"]), 5)
            self.assertTrue(result["lean_skeleton"])
            self.assertTrue(state_path.is_file())

            telemetry = prover.get_telemetry()
            self.assertEqual(telemetry["searches"], 1)
            self.assertIsNotNone(telemetry["preferred_framework"])

            reloaded = MetaProver(state_path=state_path)
            reloaded_telemetry = reloaded.get_telemetry()
            self.assertEqual(reloaded_telemetry["searches"], 1)
            self.assertEqual(
                reloaded_telemetry["evaluations"], result["evaluations"]
            )

    def test_curiosity_free_energy_and_autonomous_goals(self) -> None:
        """Verify surprisal, information gain, novel hypotheses, and goal proposal."""
        import tempfile

        from telos import GoalIngestionEngine
        from telos_v2 import EpistemicCuriosityEngine

        engine = GoalIngestionEngine()
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "curiosity.json"
            curiosity = EpistemicCuriosityEngine(
                goal_engine=engine,
                state_path=state_path,
                seed=11,
                min_proposal_score=0.0,
            )
            observation = curiosity.observe("quantum_dynamics", 0.02, weight=1.0)
            self.assertGreater(observation["surprisal"], 0.0)
            self.assertGreater(observation["information_gain"], 0.0)
            self.assertGreaterEqual(observation["intrinsic_reward"], 0.0)
            self.assertLessEqual(observation["intrinsic_reward"], 1.0)

            tick = curiosity.tick(
                telemetry={
                    "system_mastery": 0.6,
                    "success_rate": 0.75,
                    "error_count": 2,
                },
                generate=3,
                propose_limit=2,
            )
            self.assertEqual(len(tick["hypotheses"]), 3)
            statements = {item["statement"] for item in tick["hypotheses"]}
            self.assertEqual(len(statements), 3)
            self.assertGreaterEqual(len(tick["proposals"]), 1)
            self.assertGreaterEqual(len(engine.get_all_goals()), 1)
            for goal in engine.get_all_goals():
                self.assertIn("curiosity", goal.metadata)

            telemetry = curiosity.get_telemetry()
            self.assertGreaterEqual(telemetry["hypotheses_generated"], 3)
            self.assertGreaterEqual(telemetry["hypotheses_proposed"], 1)
            self.assertTrue(state_path.is_file())

            reloaded = EpistemicCuriosityEngine(
                goal_engine=GoalIngestionEngine(),
                state_path=state_path,
            )
            self.assertGreaterEqual(
                reloaded.get_telemetry()["hypotheses_generated"], 3
            )

    def test_manifold_cross_domain_isomorphism_and_transfer(self) -> None:
        """Verify cross-domain structural isomorphism and solution transport."""
        import tempfile

        from manifold_bridge import AnalogicalManifoldBridge

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "manifold.json"
            bridge = AnalogicalManifoldBridge(state_path=state_path)

            ring_matches = bridge.find_isomorphisms(
                "net-ring-6", cross_domain_only=True
            )
            self.assertTrue(
                any(
                    match["target"] == "alg-cayley-z6"
                    and match["structural_isomorphism"]
                    for match in ring_matches
                )
            )
            lattice_matches = bridge.find_isomorphisms(
                "phys-lattice-2x3", cross_domain_only=True
            )
            self.assertTrue(
                any(
                    match["target"] == "net-mesh-2x3"
                    and match["structural_isomorphism"]
                    for match in lattice_matches
                )
            )
            cfg_matches = bridge.find_isomorphisms(
                "comp-cfg-diamond", cross_domain_only=True
            )
            self.assertFalse(
                any(match["structural_isomorphism"] for match in cfg_matches)
            )

            transfer = bridge.transfer_solution("alg-cayley-z6", "net-ring-6")
            self.assertTrue(transfer["transferred"])
            self.assertTrue(transfer["structural_isomorphism"])
            self.assertGreaterEqual(transfer["confidence"], 0.99)
            self.assertEqual(len(transfer["node_mapping"]), 6)
            self.assertTrue(state_path.is_file())

            reloaded = AnalogicalManifoldBridge(state_path=state_path)
            self.assertGreaterEqual(
                reloaded.get_telemetry()["transfers"], 1
            )

    def test_devil_advocate_stagnation_inversion(self) -> None:
        """Verify stagnation detection, assumption inversion, and drive pressure."""
        import tempfile

        from devil_advocate import DevilAdvocate
        from drives import UnifiedDisciplineEngine
        from telos import GoalIngestionEngine

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "devil.json"
            devil = DevilAdvocate(
                state_path=state_path,
                window=3,
                cooldown_cycles=0,
            )
            drives = UnifiedDisciplineEngine()
            goals = GoalIngestionEngine()
            before = drives.get_unified_discipline()

            result = None
            for _ in range(5):
                result = devil.challenge(
                    {
                        "system_mastery": 0.9,
                        "unified_discipline": 0.95,
                        "success_rate": 0.97,
                    },
                    drive_engine=drives,
                    goal_engine=goals,
                )

            self.assertTrue(result["challenged"])
            self.assertTrue(result["probes"])
            assessment = result["assessment"]
            self.assertTrue(
                assessment["stagnation"] or assessment["local_convergence"]
            )
            probe = result["probes"][0]
            self.assertTrue(probe["assumption_challenged"])
            self.assertTrue(probe["inverted_objective"])
            inversion = devil.invert_metric("system_mastery", 0.9)
            self.assertAlmostEqual(
                inversion["inverted_target"] + 0.9, 1.0, places=6
            )

            self.assertGreaterEqual(drives.get_unified_discipline(), before)
            self.assertGreaterEqual(drives.get_unified_discipline(), 0.90)
            self.assertGreaterEqual(len(goals.get_all_goals()), 1)
            self.assertTrue(state_path.is_file())

            reloaded = DevilAdvocate(state_path=state_path)
            self.assertGreaterEqual(
                reloaded.get_telemetry()["challenges_issued"], 1
            )

    def test_core_extensions_wiring_and_export(self) -> None:
        """Verify the kernel runs and exports all four extensions with state."""
        import tempfile

        from core import AutomationKernel
        from science_mcp import get_science_telemetry

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            kernel = AutomationKernel(
                workspace_root=root,
                window_minutes=1.0,
                extensions_enabled=True,
            )
            self.assertIsNotNone(kernel.meta_prover)
            self.assertIsNotNone(kernel.curiosity)
            self.assertIsNotNone(kernel.manifold)
            self.assertIsNotNone(kernel.devil)

            extensions = kernel._run_extensions({}, error_count=0)
            self.assertTrue(extensions["meta_prover"]["available"])
            self.assertTrue(extensions["meta_prover"]["best_success"])
            self.assertGreaterEqual(
                extensions["curiosity"]["hypotheses_generated"], 1
            )
            self.assertTrue(extensions["manifold"]["available"])
            self.assertGreaterEqual(extensions["manifold"]["transfers"], 1)
            self.assertTrue(extensions["persistence"]["state_files"])

            science_extensions = get_science_telemetry()["extensions"]
            for name in (
                "curiosity",
                "meta_prover",
                "manifold_bridge",
                "devil_advocate",
            ):
                self.assertIn(name, science_extensions)

            payload = json.loads(kernel.export_state_json())
            self.assertTrue(payload["extensions"]["enabled"])
            self.assertGreaterEqual(
                payload["extensions"]["meta_prover"]["searches"], 1
            )
            self.assertGreaterEqual(
                kernel.drives.get_unified_discipline(), 0.90
            )
            for filename in (
                ".sask_meta_prover.json",
                ".sask_curiosity.json",
                ".sask_manifold.json",
                ".sask_devil_advocate.json",
            ):
                self.assertTrue((root / filename).is_file(), filename)


if __name__ == "__main__":
    runner = VerificationRunner(verbosity=2)
    report = runner.run_ancestral_gate()
    depth = report.details.get("overthinking_depth", "?")
    print(f"\n--- Ancestral Verification Report (overthinking depth: {depth}) ---")
    print(f"Total: {report.total_checks} | Passed: {report.passed_checks} | Failed: {report.failed_checks}")
    print(f"Duration: {report.duration_seconds}s")
    print(f"Ego ratchet: {report.details.get('ego_ratchet')}")
    if not report.is_successful:
        for err in report.errors:
            print(err)
        sys.exit(1)
    print("All component checks PASSED successfully.")
