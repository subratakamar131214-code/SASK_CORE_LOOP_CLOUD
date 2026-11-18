"""Core Module: Continuous execution loop with 340-minute container handoff timer.

Coordinates and integrates:
  - telos.py: Phased tactical sub-quests, system mastery tracking, metabolic resets,
               and universal goal-agnostic ingestion engine (GoalIngestionEngine).
  - mutate.py: Code inspection, AST validation, and surgical transformations.
  - prover.py: Subprocess syntax proving, Lean 4 / compiler execution pipeline.
  - verify.py: Automated verification test suites and component integrity checks.
  - drives.py: Unified Discipline Engine — D_floor ≥ 0.90 adaptive drive parameters.
  - bio_gen.py: Bio-Gen Replicator — specialized harness spawning across 3 vectors.

Enforces an internal wall-clock timer (configured to a 340-minute window) that
guarantees clean state serialization and graceful container handoff.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Module integrations
from mutate import CodeInspector, CodeMutator, SurgicalAstModifier
from prover import ProverInterface
from telos import (
    GoalIngestionEngine,
    GoalPriority,
    GoalStatus,
    MilestoneStatus,
    TacticalPhase,
    TelosEngine,
)
from verify import ComponentIntegrityChecker, VerificationRunner

# Optional integrations — degrade gracefully if unavailable
try:
    from drives import UnifiedDisciplineEngine  # type: ignore[import]
    _DRIVES_AVAILABLE = True
except ImportError:
    _DRIVES_AVAILABLE = False

try:
    from bio_gen import BioGenReplicator, HarnessVector  # type: ignore[import]
    _BIOGEN_AVAILABLE = True
except ImportError:
    _BIOGEN_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("CoreKernel")


class CoreState(str, Enum):
    """Lifecycle states of the core event loop."""
    INITIALIZED = "INITIALIZED"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TERMINATED = "TERMINATED"
    HANDOFF_READY = "HANDOFF_READY"


@dataclass
class CycleMetrics:
    """Telemetry captured during a single execution cycle."""
    cycle_index: int
    started_at: str
    completed_at: str
    duration_seconds: float
    milestones_total: int
    milestones_completed: int
    milestones_failed: int
    system_mastery: float
    success: bool
    details: Dict[str, Any] = field(default_factory=dict)


class WallClockTimer:
    """Tracks continuous execution windows against wall-clock deadlines."""

    def __init__(self, window_minutes: float = 340.0, margin_seconds: float = 60.0) -> None:
        self.window_minutes = window_minutes
        self.window_seconds = window_minutes * 60.0
        self.margin_seconds = margin_seconds
        self._start_monotonic = time.monotonic()
        self.start_utc = datetime.now(timezone.utc).isoformat()

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed monotonic time in seconds since timer initialization."""
        return time.monotonic() - self._start_monotonic

    @property
    def elapsed_minutes(self) -> float:
        """Elapsed time in minutes."""
        return self.elapsed_seconds / 60.0

    @property
    def remaining_seconds(self) -> float:
        """Remaining seconds before the hard window deadline."""
        return max(0.0, self.window_seconds - self.elapsed_seconds)

    @property
    def remaining_minutes(self) -> float:
        """Remaining minutes before the hard window deadline."""
        return self.remaining_seconds / 60.0

    def is_threshold_reached(self) -> bool:
        """True if the elapsed wall-clock time has reached or exceeded the execution window margin."""
        return self.elapsed_seconds >= (self.window_seconds - self.margin_seconds)


class AutomationKernel:
    """Core continuous execution loop coordinating all micro-services and handoff timers."""

    DEFAULT_STATE_FILE = "execution_state.json"

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        window_minutes: float = 340.0,
        state_file: Optional[Path] = None,
        stream_enabled: bool = False,
        stream_level: int = 2,
        stream_objective: Optional[str] = None,
        stream_concurrency: int = 4,
        stream_max_goals_per_cycle: int = 32,
    ) -> None:
        self.workspace_root = workspace_root or Path.cwd()
        self.state_file = state_file or (self.workspace_root / self.DEFAULT_STATE_FILE)
        self.timer = WallClockTimer(window_minutes=window_minutes)
        self.state = CoreState.INITIALIZED
        self.current_cycle = 0
        self.cycle_history: List[CycleMetrics] = []
        self._stop_requested = False

        # Asynchronous goal-stream configuration
        self.stream_enabled = bool(stream_enabled)
        self.stream_level = max(0, min(3, int(stream_level)))
        self.stream_objective = stream_objective
        self.stream_concurrency = max(1, int(stream_concurrency))
        self.stream_max_goals_per_cycle = max(1, int(stream_max_goals_per_cycle))

        # Integrated sub-services
        self.prover = ProverInterface()
        self.verifier = VerificationRunner(
            ratchet_path=self.workspace_root / ".sask_ego_ratchet.json"
        )
        self.telos = self._build_pipeline_engine()

        # Unified Discipline Engine (drives.py)
        if _DRIVES_AVAILABLE:
            self.drives: Optional[Any] = UnifiedDisciplineEngine()
        else:
            self.drives = None
            logger.warning("drives.py not available — discipline engine disabled.")

        # Bio-Gen Replicator (bio_gen.py)
        if _BIOGEN_AVAILABLE:
            self.bio_gen: Optional[Any] = BioGenReplicator()
        else:
            self.bio_gen = None
            logger.warning("bio_gen.py not available — harness replication disabled.")

        self._register_signal_handlers()

    def _register_signal_handlers(self) -> None:
        """Register OS signal handlers for graceful shutdown and state preservation."""
        try:
            signal.signal(signal.SIGINT, self._handle_termination_signal)
            signal.signal(signal.SIGTERM, self._handle_termination_signal)
        except (ValueError, AttributeError):
            pass

    def _handle_termination_signal(self, signum: int, frame: Any) -> None:
        logger.warning(f"Termination signal ({signum}) caught. Halting task synthesis and serializing state...")
        self._stop_requested = True
        self.state = CoreState.TERMINATED
        if self.stream_enabled:
            try:
                self.telos.stream.persist()
            except Exception as exc:
                logger.warning(f"Goal stream persistence on signal failed: {exc}")
        self.save_state_to_disk()

    def _build_pipeline_engine(self) -> TelosEngine:
        """Initialize Telos milestone tracking engine with macro-objectives and tactical sub-quests."""
        engine = TelosEngine(
            stagnation_threshold=3,
            workspace_root=self.workspace_root,
            stream_max_level=self.stream_level,
            stream_concurrency=self.stream_concurrency,
        )

        # Register Macro-Objective
        engine.register_macro_objective(
            objective_id="system_mastery",
            title="System Mastery & Autonomous Pipeline Convergence",
            description="End-to-end multi-phase convergence toward verified, zero-defect execution.",
            target_mastery=1.0,
        )

        # Issue Phased Tactical Sub-Quests
        engine.issue_phased_tactical_subquests(
            macro_objective_id="system_mastery",
            subquest_specs=[
                {
                    "id": "env_check",
                    "title": "Environment & Module Integrity",
                    "description": "Verify Python runtime environment and module accessibility.",
                    "phase": TacticalPhase.RECONNAISSANCE,
                    "weight": 1.0,
                },
                {
                    "id": "code_analysis",
                    "title": "Code Structure & Mutation Analysis",
                    "description": "Parse source code AST and inspect structural metrics.",
                    "depends_on": ["env_check"],
                    "phase": TacticalPhase.TRANSFORMATION,
                    "weight": 1.5,
                },
                {
                    "id": "syntax_proving",
                    "title": "External Subprocess Syntax Proving",
                    "description": "Invoke compiler to verify byte-compilation of Python files.",
                    "depends_on": ["code_analysis"],
                    "phase": TacticalPhase.VERIFICATION,
                    "weight": 2.0,
                },
                {
                    "id": "test_verification",
                    "title": "Automated Test Verification",
                    "description": "Execute automated test runner across all pipeline modules.",
                    "depends_on": ["syntax_proving"],
                    "phase": TacticalPhase.VERIFICATION,
                    "weight": 2.5,
                },
                {
                    "id": "telemetry_report",
                    "title": "Telemetry & State Serialization",
                    "description": "Serialize execution state and aggregate telemetry.",
                    "depends_on": ["test_verification"],
                    "phase": TacticalPhase.CONVERGENCE,
                    "weight": 1.0,
                },
            ],
        )

        return engine

    @staticmethod
    def _build_harness_payload(goal: Any) -> str:
        """Compose a deterministic, isolated harness script for an ingested goal."""
        record = {
            "id": goal.id,
            "title": goal.title,
            "vector": goal.vector.value,
            "priority": goal.priority.name,
            "description": goal.description,
            "sub_tasks": list(goal.sub_tasks),
        }
        literal = json.dumps(json.dumps(record, ensure_ascii=True))
        return (
            "import json, os\n"
            f"_GOAL = json.loads({literal})\n"
            "_GOAL['harness_id'] = os.environ.get('SASK_HARNESS_ID', '')\n"
            "print(json.dumps({'status': 'HARNESS_ACK', 'goal': _GOAL}))\n"
        )

    # ------------------------------------------------------------------
    # Asynchronous goal stream integration
    # ------------------------------------------------------------------

    def _start_goal_stream(self) -> Dict[str, Any]:
        """Seed objectives, restore handoff state, and start the producer thread."""
        stream = self.telos.stream
        if not self.stream_enabled:
            return {"started": False, "reason": "stream_disabled"}

        self.telos.goal_engine.load_manifest(str(self.workspace_root / "goals_manifest.json"))
        objective = self.stream_objective or os.environ.get("SASK_PRIMARY_OBJECTIVE")
        seeded: List[Dict[str, Any]] = []

        if objective:
            seeded.append(
                stream.submit_objective(
                    objective,
                    level=self.stream_level,
                    priority=GoalPriority.CRITICAL,
                )
            )
        else:
            for goal in self.telos.goal_engine.get_all_goals():
                if (
                    goal.status in (GoalStatus.QUEUED, GoalStatus.ACTIVE)
                    and "stream" not in (goal.metadata or {})
                ):
                    seeded.append(
                        stream.submit_objective(
                            goal.raw_prompt,
                            level=self.stream_level,
                            priority=goal.priority,
                            objective_id=f"obj-{goal.id}",
                        )
                    )

        info = stream.start(resume=True)
        if not seeded and not (info.get("resumed") or {}).get("requeued"):
            info["seeded_default"] = stream.submit_objective(
                "System Mastery & Autonomous Pipeline Convergence",
                level=self.stream_level,
                priority=GoalPriority.HIGH,
            )
        info["seeded"] = seeded
        return info

    def _execute_goal(self, goal: Any) -> Dict[str, Any]:
        """Execute one goal on a worker thread: science router first, harness fallback second."""
        router = self.telos.goal_engine.resolve_router(goal)
        dispatch = (
            self.telos.goal_engine.dispatch_goal(
                goal.id,
                router=router,
                drive_engine=self.drives,
            )
            if router
            else {"handled": False}
        )

        if dispatch.get("handled"):
            result = dispatch.get("result", {}) or {}
            return {
                "goal": goal,
                "kind": "science",
                "success": bool(result.get("success")),
                "error": result.get("error") or "router execution failed",
                "record": {
                    "goal_id": goal.id,
                    "router": dispatch.get("router"),
                    "success": bool(result.get("success")),
                    "execution_mode": result.get("execution_mode"),
                    "latency_seconds": result.get("latency_seconds"),
                    "numerical_residual": result.get("numerical_residual"),
                    "optimizer_strategy": (result.get("optimizer") or {}).get("strategy"),
                    "discipline": result.get("discipline"),
                },
            }

        if self.bio_gen is None:
            return {
                "goal": goal,
                "kind": "none",
                "success": False,
                "error": "no_executor_available",
            }

        harness_result = self.bio_gen.spawn_specialized_harness(
            goal_id=goal.id,
            vector=goal.vector.value,
            payload=self._build_harness_payload(goal),
        )
        return {
            "goal": goal,
            "kind": "bio_gen",
            "success": bool(harness_result.get("success")),
            "error": harness_result.get("error", "unknown"),
            "harness_result": harness_result,
        }

    def _apply_goal_outcome(self, outcome: Dict[str, Any], from_stream: bool) -> bool:
        """Finalize a goal, releasing its stream slot when it came from the queue."""
        goal = outcome["goal"]
        success = bool(outcome.get("success"))
        error = outcome.get("error")

        if from_stream:
            self.telos.stream.complete(goal.id, success, error if not success else None)
        elif success:
            self.telos.goal_engine.mark_goal_complete(goal.id)
        else:
            self.telos.goal_engine.mark_goal_failed(goal.id, str(error or "unknown"))
        return success

    def run_cycle(self) -> CycleMetrics:
        """Execute a single integrated cycle across telos, mutate, prover, and verify."""
        self.current_cycle += 1
        cycle_start_time = time.monotonic()
        start_iso = datetime.now(timezone.utc).isoformat()
        self.state = CoreState.RUNNING

        logger.info(
            f"=== Execution Cycle #{self.current_cycle} "
            f"[Elapsed: {self.timer.elapsed_minutes:.2f}m / {self.timer.window_minutes}m, "
            f"Remaining: {self.timer.remaining_minutes:.2f}m] ==="
        )

        cycle_details: Dict[str, Any] = {}
        error_count = 0

        try:
            # 1. Reconnaissance: Environment & Module Integrity
            self.telos.update_status("env_check", MilestoneStatus.RUNNING)
            checker = ComponentIntegrityChecker()
            modules = ["core", "mutate", "prover", "verify", "telos", "drives", "bio_gen", "science_mcp"]
            import_results = {m: checker.verify_importable(m) for m in modules}
            if all(import_results.values()):
                self.telos.update_status("env_check", MilestoneStatus.COMPLETED, metadata={"modules": import_results})
            else:
                failed_mods = [m for m, ok in import_results.items() if not ok]
                err = f"Failed to import modules: {failed_mods}"
                self.telos.update_status("env_check", MilestoneStatus.FAILED, error=err)
                error_count += 1
                raise RuntimeError(err)

            # Check timer threshold between tactical phases
            if self.timer.is_threshold_reached():
                logger.warning("Execution window threshold reached during reconnaissance phase.")
                return self._finalize_cycle_metrics(cycle_start_time, start_iso, success=True, details=cycle_details)

            # Stage 1.5: Asynchronous goal stream consumption with harness fallback
            try:
                manifest_path = str(self.workspace_root / "goals_manifest.json")
                loaded = self.telos.goal_engine.load_manifest(manifest_path)
                if loaded > 0:
                    logger.info(f"GoalIngestionEngine: loaded {loaded} new goal(s) from manifest.")

                discipline_score = self.drives.get_unified_discipline() if self.drives else 1.0
                bio_gen_results: List[Dict[str, Any]] = []
                science_dispatches: List[Dict[str, Any]] = []

                stream = self.telos.stream
                stream_active = self.stream_enabled and stream.is_running
                budget = self.stream_max_goals_per_cycle if stream_active else 3

                if stream_active:
                    # Non-blocking pull bounded by free worker slots.
                    initial_goals = stream.drain()
                else:
                    pending_goals = self.telos.goal_engine.get_priority_queue(discipline=discipline_score)
                    initial_goals = pending_goals[:budget]
                    for goal in initial_goals:
                        goal.status = GoalStatus.ACTIVE

                if initial_goals:
                    processed = 0
                    workers = self.stream_concurrency if stream_active else 1
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=workers,
                        thread_name_prefix="sask-goal",
                    ) as pool:
                        futures = {
                            pool.submit(self._execute_goal, goal): (goal, stream_active)
                            for goal in initial_goals
                        }
                        processed += len(futures)

                        while futures:
                            done, _ = concurrent.futures.wait(
                                futures,
                                return_when=concurrent.futures.FIRST_COMPLETED,
                            )
                            for future in done:
                                goal, from_stream = futures.pop(future)
                                try:
                                    outcome = future.result()
                                except Exception as exc:
                                    outcome = {
                                        "goal": goal,
                                        "kind": "error",
                                        "success": False,
                                        "error": str(exc),
                                    }

                                success = self._apply_goal_outcome(outcome, from_stream)
                                kind = outcome.get("kind")

                                if kind == "science":
                                    science_dispatches.append(outcome["record"])
                                elif kind == "bio_gen":
                                    bio_gen_results.append({
                                        "goal_id": goal.id,
                                        "result": outcome.get("harness_result"),
                                    })

                                if success:
                                    logger.info(
                                        f"{kind} completed goal {goal.id}: {goal.title[:60]}"
                                    )
                                else:
                                    error_count += 1
                                    logger.warning(
                                        f"{kind} failed goal {goal.id}: {outcome.get('error')}"
                                    )

                                # A freed slot pulls the next queued goal immediately.
                                if (
                                    stream_active
                                    and processed < budget
                                    and not self._stop_requested
                                    and not self.timer.is_threshold_reached()
                                ):
                                    for next_goal in stream.drain(limit=budget - processed):
                                        futures[
                                            pool.submit(self._execute_goal, next_goal)
                                        ] = (next_goal, True)
                                        processed += 1

                self.telos.goal_engine.save_manifest(manifest_path)
                if self.stream_enabled:
                    try:
                        stream.persist()
                    except OSError as exc:
                        logger.warning(f"Goal stream persistence skipped: {exc}")
                cycle_details["bio_gen_results"] = bio_gen_results
                cycle_details["science_dispatches"] = science_dispatches
                cycle_details["goal_stream"] = stream.get_telemetry()
            except Exception as exc:
                logger.warning(f"Goal dispatch stage error (non-fatal): {exc}")
                cycle_details["goal_dispatch_error"] = str(exc)

            # 2. Transformation: AST Parsing & Code Structure Inspection
            self.telos.update_status("code_analysis", MilestoneStatus.RUNNING)
            analysis_summary: Dict[str, Any] = {}
            for py_file in self.workspace_root.glob("*.py"):
                try:
                    content = py_file.read_text(encoding="utf-8")
                    metrics = CodeInspector.inspect(content)
                    analysis_summary[py_file.name] = metrics
                    # Verify AST parse integrity via SurgicalAstModifier
                    SurgicalAstModifier.parse_source(content)
                except Exception as exc:
                    logger.warning(f"Could not parse/inspect {py_file.name}: {exc}")
                    error_count += 1

            self.telos.update_status(
                "code_analysis",
                MilestoneStatus.COMPLETED,
                metadata={"files_inspected": len(analysis_summary)},
            )
            cycle_details["code_metrics"] = analysis_summary

            if self.timer.is_threshold_reached():
                logger.warning("Execution window threshold reached during transformation phase.")
                return self._finalize_cycle_metrics(cycle_start_time, start_iso, success=True, details=cycle_details)

            # 3. Verification: Subprocess Syntax Proving & Formal Assertions
            self.telos.update_status("syntax_proving", MilestoneStatus.RUNNING)
            syntax_passed = True
            syntax_errors: List[str] = []
            for py_file in self.workspace_root.glob("*.py"):
                result = self.prover.verify_python_syntax(py_file)
                if not result.success:
                    syntax_passed = False
                    syntax_errors.append(f"{py_file.name}: {result.stderr.strip()}")
                    error_count += 1

            # Prover Lean 4 status check
            toolchain = self.prover.lean.get_toolchain_info()
            cycle_details["prover_toolchain"] = toolchain

            if syntax_passed:
                self.telos.update_status("syntax_proving", MilestoneStatus.COMPLETED)
            else:
                err = "; ".join(syntax_errors)
                self.telos.update_status("syntax_proving", MilestoneStatus.FAILED, error=err)
                raise RuntimeError(f"Syntax proving failed: {err}")

            if self.timer.is_threshold_reached():
                logger.warning("Execution window threshold reached during verification phase.")
                return self._finalize_cycle_metrics(cycle_start_time, start_iso, success=True, details=cycle_details)

            # 4. Automated Verification Suite — Ancestral Regression Gate
            self.telos.update_status("test_verification", MilestoneStatus.RUNNING)
            test_report = self.verifier.run_ancestral_gate()
            cycle_details["test_report"] = {
                "total": test_report.total_checks,
                "passed": test_report.passed_checks,
                "failed": test_report.failed_checks,
                "overthinking_depth": test_report.details.get("overthinking_depth"),
                "ego_ratchet": test_report.details.get("ego_ratchet"),
            }
            if test_report.is_successful:
                self.telos.update_status(
                    "test_verification",
                    MilestoneStatus.COMPLETED,
                    metadata={"passed_checks": test_report.passed_checks},
                )
            else:
                error_count += test_report.failed_checks
                err = f"{test_report.failed_checks} checks failed: {test_report.errors}"
                self.telos.update_status("test_verification", MilestoneStatus.FAILED, error=err)
                raise RuntimeError(err)

            # 5. Convergence: Telemetry & State Synchronization
            self.telos.update_status("telemetry_report", MilestoneStatus.RUNNING)

            # Record error metric for stagnation detection & metabolic reset
            reset_event = self.telos.record_error_metric(float(error_count))
            if reset_event:
                logger.warning(f"Metabolic reset triggered: {reset_event.reason}")
                cycle_details["metabolic_reset"] = asdict(reset_event)

            summary = self.telos.get_summary()
            cycle_details["milestone_summary"] = summary
            self.telos.update_status("telemetry_report", MilestoneStatus.COMPLETED)

            # Apply drives stimulus based on cumulative error count
            if self.drives is not None:
                try:
                    self.drives.apply_stimulus(challenge_intensity=error_count)
                    cycle_details["drives_discipline"] = self.drives.get_unified_discipline()
                except Exception as exc:
                    logger.warning(f"Drives stimulus error (non-fatal): {exc}")

            self.state = CoreState.COMPLETED
            success = True

        except Exception as exc:
            logger.error(f"Cycle #{self.current_cycle} exception: {exc}")
            self.state = CoreState.FAILED
            success = False
            cycle_details["cycle_error"] = str(exc)

        return self._finalize_cycle_metrics(cycle_start_time, start_iso, success=success, details=cycle_details)

    def _finalize_cycle_metrics(
        self,
        cycle_start_time: float,
        start_iso: str,
        success: bool,
        details: Dict[str, Any],
    ) -> CycleMetrics:
        """Compile and record metrics for the current execution cycle."""
        duration = time.monotonic() - cycle_start_time
        end_iso = datetime.now(timezone.utc).isoformat()
        summary = self.telos.get_summary()

        metrics = CycleMetrics(
            cycle_index=self.current_cycle,
            started_at=start_iso,
            completed_at=end_iso,
            duration_seconds=round(duration, 4),
            milestones_total=summary["total_milestones"],
            milestones_completed=summary["status_breakdown"][MilestoneStatus.COMPLETED.value],
            milestones_failed=summary["status_breakdown"][MilestoneStatus.FAILED.value],
            system_mastery=summary["system_mastery"],
            success=success,
            details=details,
        )
        self.cycle_history.append(metrics)
        logger.info(
            f"Cycle #{self.current_cycle} concluded in {metrics.duration_seconds}s "
            f"(Status: {'SUCCESS' if success else 'FAILED'}, Mastery: {metrics.system_mastery * 100:.1f}%)"
        )
        return metrics

    def run_continuous_loop(
        self,
        max_cycles: Optional[int] = None,
        cycle_delay_seconds: float = 0.0,
    ) -> None:
        """Run continuous execution cycles bounded by max_cycles or the 340-minute wall-clock timer."""
        logger.info(
            f"Starting continuous execution loop. Wall-clock window: {self.timer.window_minutes}m, "
            f"Max cycles: {max_cycles if max_cycles is not None else 'UNLIMITED'}"
        )
        cycles_completed = 0

        if self.stream_enabled:
            stream_info = self._start_goal_stream()
            logger.info(
                f"Goal stream producer active (level={self.stream_level}, "
                f"concurrency={self.stream_concurrency}, "
                f"resumed={stream_info.get('resumed')}, "
                f"seeded={len(stream_info.get('seeded') or [])})."
            )

        try:
            while not self._stop_requested:
                # Check if wall-clock threshold is reached before starting new cycle
                if self.timer.is_threshold_reached():
                    logger.warning(
                        f"Wall-clock execution threshold reached ({self.timer.elapsed_minutes:.2f}m / "
                        f"{self.timer.window_minutes}m). Halting task synthesis for container handoff..."
                    )
                    self.state = CoreState.HANDOFF_READY
                    break

                self.run_cycle()
                cycles_completed += 1

                # Check if cycle limit reached
                if max_cycles is not None and max_cycles > 0 and cycles_completed >= max_cycles:
                    logger.info(f"Target cycle limit of {max_cycles} reached.")
                    if self.state != CoreState.HANDOFF_READY:
                        self.state = CoreState.COMPLETED
                    break

                # Check window again before inter-cycle sleep
                if self.timer.is_threshold_reached():
                    logger.warning("Wall-clock execution threshold reached. Halting for container handoff...")
                    self.state = CoreState.HANDOFF_READY
                    break

                if not self._stop_requested and cycle_delay_seconds > 0:
                    self.state = CoreState.IDLE
                    time.sleep(cycle_delay_seconds)
        finally:
            if self.stream_enabled:
                try:
                    telemetry = self.telos.stream.stop()
                    logger.info(
                        f"Goal stream halted: produced={telemetry.get('produced')} "
                        f"duplicates_suppressed={telemetry.get('duplicates_suppressed')} "
                        f"completed={telemetry.get('completed')} "
                        f"ledger_entries={telemetry.get('ledger', {}).get('entries')}."
                    )
                except Exception as exc:
                    logger.warning(f"Goal stream shutdown error: {exc}")
            # On loop exit, cleanly serialize state to disk for container handoff
            self.save_state_to_disk()

        logger.info(
            f"Execution halted. Total cycles: {len(self.cycle_history)}, "
            f"Final state: {self.state.value}, Container handoff ready."
        )

    def export_state_json(self) -> str:
        """Export serialized system state, timer metrics, and cycle history as formatted JSON."""
        summary = self.telos.get_summary()

        # Drives telemetry
        drives_telemetry: Dict[str, Any] = {"available": _DRIVES_AVAILABLE}
        if self.drives is not None:
            try:
                drives_telemetry.update({
                    "unified_discipline": self.drives.get_unified_discipline(),
                    "mutation_pressure": self.drives.get_mutation_pressure(),
                    "overthinking_depth": self.drives.get_overthinking_depth(),
                })
            except Exception:
                pass

        # Bio-Gen summary
        bio_gen_summary: Dict[str, Any] = {"available": _BIOGEN_AVAILABLE}
        if self.bio_gen is not None:
            try:
                bio_gen_summary.update(self.bio_gen.get_summary() if hasattr(self.bio_gen, "get_summary") else {})
            except Exception:
                pass

        # 10-router science engine telemetry
        science_telemetry: Dict[str, Any] = {"available": False}
        try:
            from science_mcp import get_science_telemetry  # type: ignore[import-not-found]

            science_telemetry = get_science_telemetry()
        except Exception as exc:
            science_telemetry = {"available": False, "error": str(exc)}

        # Asynchronous goal stream telemetry
        goal_stream: Dict[str, Any] = {"enabled": self.stream_enabled}
        try:
            goal_stream.update(self.telos.stream.get_telemetry())
        except Exception as exc:
            goal_stream["error"] = str(exc)

        payload = {
            "state": self.state.value,
            "container_handoff": self.state in (CoreState.HANDOFF_READY, CoreState.COMPLETED),
            "wall_clock": {
                "started_at": self.timer.start_utc,
                "elapsed_seconds": round(self.timer.elapsed_seconds, 2),
                "elapsed_minutes": round(self.timer.elapsed_minutes, 2),
                "remaining_minutes": round(self.timer.remaining_minutes, 2),
                "window_minutes": self.timer.window_minutes,
                "threshold_reached": self.timer.is_threshold_reached(),
            },
            "system_mastery": summary["system_mastery"],
            "total_cycles_executed": len(self.cycle_history),
            "telos_summary": summary,
            "drives_telemetry": drives_telemetry,
            "bio_gen_summary": bio_gen_summary,
            "science_engine": science_telemetry,
            "goal_stream": goal_stream,
            "cycles": [asdict(c) for c in self.cycle_history],
        }
        return json.dumps(payload, indent=2)

    def save_state_to_disk(self, target_path: Optional[Path] = None) -> Path:
        """Atomically persist state JSON to disk for container handoff."""
        out_path = Path(target_path or self.state_file).resolve()
        data = self.export_state_json()
        temp_path = out_path.with_suffix(".tmp")
        temp_path.write_text(data, encoding="utf-8")
        temp_path.replace(out_path)
        logger.info(f"Cleanly serialized execution state to {out_path}")
        return out_path


def _env_int(name: str, default: int) -> int:
    """Read an integer environment override, falling back to a default."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def main() -> None:
    """CLI Entry point for the Core kernel."""
    parser = argparse.ArgumentParser(description="Modular Continuous Python Automation Kernel")
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="Number of execution cycles to run (default: 1; pass 0 or omit with --continuous for continuous mode)",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuously until the 340-minute wall-clock threshold is reached",
    )
    parser.add_argument(
        "--window-minutes",
        type=float,
        default=340.0,
        help="Execution window in wall-clock minutes before container handoff (default: 340.0)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay in seconds between execution cycles (default: 0.0)",
    )
    parser.add_argument(
        "--export-state",
        type=str,
        default=None,
        help="Explicit file path to write serialized state JSON (default: execution_state.json)",
    )
    parser.add_argument(
        "--stream",
        dest="stream",
        action="store_true",
        default=None,
        help="Force-enable the asynchronous continuous goal streaming pipeline.",
    )
    parser.add_argument(
        "--no-stream",
        dest="stream",
        action="store_false",
        help="Disable the asynchronous continuous goal streaming pipeline.",
    )
    parser.add_argument(
        "--stream-level",
        type=int,
        default=_env_int("SASK_STREAM_LEVEL", 2),
        choices=[0, 1, 2, 3],
        help="Exhaustive expansion level: 0=primary, 1=phases (5), 2=vectors x phases (15), 3=all variations (75).",
    )
    parser.add_argument(
        "--stream-objective",
        type=str,
        default=os.environ.get("SASK_PRIMARY_OBJECTIVE") or None,
        help="Primary objective to expand exhaustively when the stream starts.",
    )
    parser.add_argument(
        "--stream-concurrency",
        type=int,
        default=_env_int("SASK_MAX_CONCURRENCY", 4),
        help="Maximum goals in flight (worker slots) for the streaming consumer.",
    )
    args = parser.parse_args()

    state_path = Path(args.export_state) if args.export_state else None
    max_cycles = None if args.continuous else (None if args.cycles == 0 else args.cycles)

    env_stream_enabled = os.environ.get("SASK_STREAM_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
    stream_enabled = (
        args.stream
        if args.stream is not None
        else (max_cycles is None and env_stream_enabled)
    )

    kernel = AutomationKernel(
        window_minutes=args.window_minutes,
        state_file=state_path,
        stream_enabled=stream_enabled,
        stream_level=args.stream_level,
        stream_objective=args.stream_objective,
        stream_concurrency=args.stream_concurrency,
    )

    kernel.run_continuous_loop(
        max_cycles=max_cycles,
        cycle_delay_seconds=args.delay,
    )

    # Clean exit for container handoff
    if kernel.state in (CoreState.COMPLETED, CoreState.HANDOFF_READY, CoreState.IDLE):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
