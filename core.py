"""Core Module: Main event loop managing execution cycles and state tracking.

Coordinates execution cycles, state transitions, task sequencing (via Telos),
code inspection (via Mutate), external validation (via Prover), and automated
testing (via Verify).
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Module imports
from mutate import CodeInspector
from prover import ProverInterface
from telos import MilestoneStatus, TelosEngine
from verify import ComponentIntegrityChecker, VerificationRunner

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
    success: bool
    details: Dict[str, Any] = field(default_factory=dict)


class AutomationKernel:
    """Core event loop orchestrator for automated execution cycles and state tracking."""

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self.workspace_root = workspace_root or Path.cwd()
        self.state = CoreState.INITIALIZED
        self.current_cycle = 0
        self.cycle_history: List[CycleMetrics] = []
        self._stop_requested = False
        self.prover = ProverInterface()
        self.verifier = VerificationRunner()

        self._register_signal_handlers()

    def _register_signal_handlers(self) -> None:
        """Register OS signal handlers for graceful shutdown."""
        try:
            signal.signal(signal.SIGINT, self._handle_termination_signal)
            signal.signal(signal.SIGTERM, self._handle_termination_signal)
        except (ValueError, AttributeError):
            # Signals might not be available in all thread contexts or OS environments
            pass

    def _handle_termination_signal(self, signum: int, frame: Any) -> None:
        logger.warning(f"Termination signal {signum} received. Requesting graceful shutdown...")
        self._stop_requested = True
        self.state = CoreState.TERMINATED

    def _build_pipeline_engine(self) -> TelosEngine:
        """Initialize Telos milestone tracking engine with macro-objectives and tactical sub-quests."""
        engine = TelosEngine()

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
                    "phase": "RECONNAISSANCE",
                    "weight": 1.0,
                },
                {
                    "id": "code_analysis",
                    "title": "Code Structure & Mutation Analysis",
                    "description": "Parse source code AST and inspect structural metrics.",
                    "depends_on": ["env_check"],
                    "phase": "TRANSFORMATION",
                    "weight": 1.5,
                },
                {
                    "id": "syntax_proving",
                    "title": "External Subprocess Syntax Proving",
                    "description": "Invoke compiler to verify byte-compilation of Python files.",
                    "depends_on": ["code_analysis"],
                    "phase": "VERIFICATION",
                    "weight": 2.0,
                },
                {
                    "id": "test_verification",
                    "title": "Automated Test Verification",
                    "description": "Execute automated test runner across all pipeline modules.",
                    "depends_on": ["syntax_proving"],
                    "phase": "VERIFICATION",
                    "weight": 2.5,
                },
                {
                    "id": "telemetry_report",
                    "title": "Telemetry & State Serialization",
                    "description": "Serialize execution state and aggregate telemetry.",
                    "depends_on": ["test_verification"],
                    "phase": "CONVERGENCE",
                    "weight": 1.0,
                },
            ],
        )

        return engine

    def run_cycle(self) -> CycleMetrics:
        """Execute a single end-to-end automation cycle."""
        self.current_cycle += 1
        cycle_start_time = time.monotonic()
        start_iso = datetime.now(timezone.utc).isoformat()
        self.state = CoreState.RUNNING

        logger.info(f"=== Starting Execution Cycle #{self.current_cycle} ===")
        telos = self._build_pipeline_engine()
        cycle_details: Dict[str, Any] = {}

        try:
            # 1. Environment & Module Integrity
            telos.update_status("env_check", MilestoneStatus.RUNNING)
            checker = ComponentIntegrityChecker()
            modules = ["core", "mutate", "prover", "verify", "telos"]
            import_results = {m: checker.verify_importable(m) for m in modules}
            if all(import_results.values()):
                telos.update_status("env_check", MilestoneStatus.COMPLETED, metadata={"modules": import_results})
                logger.info("Stage 1/5: Environment & Module Integrity verified.")
            else:
                failed_mods = [m for m, ok in import_results.items() if not ok]
                err = f"Failed to import modules: {failed_mods}"
                telos.update_status("env_check", MilestoneStatus.FAILED, error=err)
                raise RuntimeError(err)

            # 2. Code Structure & Mutation Analysis
            telos.update_status("code_analysis", MilestoneStatus.RUNNING)
            analysis_summary: Dict[str, Any] = {}
            for py_file in self.workspace_root.glob("*.py"):
                try:
                    content = py_file.read_text(encoding="utf-8")
                    metrics = CodeInspector.inspect(content)
                    analysis_summary[py_file.name] = metrics
                except Exception as exc:
                    logger.warning(f"Could not inspect {py_file.name}: {exc}")
            telos.update_status(
                "code_analysis",
                MilestoneStatus.COMPLETED,
                metadata={"files_inspected": len(analysis_summary)},
            )
            cycle_details["code_metrics"] = analysis_summary
            logger.info(f"Stage 2/5: Inspected {len(analysis_summary)} source files.")

            # 3. Subprocess Validation (Prover)
            telos.update_status("syntax_proving", MilestoneStatus.RUNNING)
            syntax_passed = True
            syntax_errors: List[str] = []
            for py_file in self.workspace_root.glob("*.py"):
                result = self.prover.verify_python_syntax(py_file)
                if not result.success:
                    syntax_passed = False
                    syntax_errors.append(f"{py_file.name}: {result.stderr.strip()}")

            if syntax_passed:
                telos.update_status("syntax_proving", MilestoneStatus.COMPLETED)
                logger.info("Stage 3/5: Subprocess syntax proving passed for all modules.")
            else:
                err = "; ".join(syntax_errors)
                telos.update_status("syntax_proving", MilestoneStatus.FAILED, error=err)
                raise RuntimeError(f"Syntax proving failed: {err}")

            # 4. Automated Verification Suite
            telos.update_status("test_verification", MilestoneStatus.RUNNING)
            test_report = self.verifier.run_self_verification()
            cycle_details["test_report"] = {
                "total": test_report.total_checks,
                "passed": test_report.passed_checks,
                "failed": test_report.failed_checks,
            }
            if test_report.is_successful:
                telos.update_status(
                    "test_verification",
                    MilestoneStatus.COMPLETED,
                    metadata={"passed_checks": test_report.passed_checks},
                )
                logger.info(f"Stage 4/5: Verification suite passed ({test_report.passed_checks} checks).")
            else:
                err = f"{test_report.failed_checks} checks failed: {test_report.errors}"
                telos.update_status("test_verification", MilestoneStatus.FAILED, error=err)
                raise RuntimeError(err)

            # 5. Telemetry & State Serialization
            telos.update_status("telemetry_report", MilestoneStatus.RUNNING)
            summary = telos.get_summary()
            cycle_details["milestone_summary"] = summary
            telos.update_status("telemetry_report", MilestoneStatus.COMPLETED)
            logger.info("Stage 5/5: Telemetry recorded successfully.")

            self.state = CoreState.COMPLETED
            success = True

        except Exception as exc:
            logger.error(f"Execution cycle #{self.current_cycle} encountered failure: {exc}")
            self.state = CoreState.FAILED
            success = False
            cycle_details["cycle_error"] = str(exc)

        duration = time.monotonic() - cycle_start_time
        end_iso = datetime.now(timezone.utc).isoformat()
        milestone_summary = telos.get_summary()

        metrics = CycleMetrics(
            cycle_index=self.current_cycle,
            started_at=start_iso,
            completed_at=end_iso,
            duration_seconds=round(duration, 4),
            milestones_total=milestone_summary["total_milestones"],
            milestones_completed=milestone_summary["status_breakdown"][MilestoneStatus.COMPLETED.value],
            milestones_failed=milestone_summary["status_breakdown"][MilestoneStatus.FAILED.value],
            success=success,
            details=cycle_details,
        )
        self.cycle_history.append(metrics)
        logger.info(
            f"=== Cycle #{self.current_cycle} finished in {metrics.duration_seconds}s "
            f"(Status: {'SUCCESS' if success else 'FAILED'}) ==="
        )
        return metrics

    def run_event_loop(self, max_cycles: int = 1, cycle_delay_seconds: float = 0.0) -> None:
        """Run continuous or bounded execution cycles."""
        logger.info(f"Starting event loop: max_cycles={max_cycles}, cycle_delay={cycle_delay_seconds}s")
        executed = 0

        while not self._stop_requested and (max_cycles <= 0 or executed < max_cycles):
            self.run_cycle()
            executed += 1

            if max_cycles > 0 and executed >= max_cycles:
                break

            if not self._stop_requested and cycle_delay_seconds > 0:
                self.state = CoreState.IDLE
                time.sleep(cycle_delay_seconds)

        if not self._stop_requested:
            self.state = CoreState.COMPLETED

    def export_state_json(self) -> str:
        """Export state and cycle history as formatted JSON."""
        payload = {
            "state": self.state.value,
            "current_cycle": self.current_cycle,
            "total_cycles_executed": len(self.cycle_history),
            "cycles": [asdict(c) for c in self.cycle_history],
        }
        return json.dumps(payload, indent=2)


def main() -> None:
    """CLI Entry point for the Core kernel."""
    parser = argparse.ArgumentParser(description="Modular Python Automation Kernel")
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="Number of execution cycles to run (default: 1)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay in seconds between cycles (default: 0.0)",
    )
    parser.add_argument(
        "--export-state",
        type=str,
        default=None,
        help="Optional file path to write state JSON",
    )
    args = parser.parse_args()

    kernel = AutomationKernel()
    kernel.run_event_loop(max_cycles=args.cycles, cycle_delay_seconds=args.delay)

    if args.export_state:
        Path(args.export_state).write_text(kernel.export_state_json(), encoding="utf-8")
        logger.info(f"Exported state report to {args.export_state}")

    # Determine exit code based on state
    if kernel.state in (CoreState.COMPLETED, CoreState.IDLE):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
