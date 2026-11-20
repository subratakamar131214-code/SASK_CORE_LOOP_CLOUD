"""Core Module: Continuous execution loop with 340-minute container handoff timer.

Coordinates and integrates:
  - telos.py: Phased tactical sub-quests, system mastery tracking, metabolic resets,
               and universal goal-agnostic ingestion engine (GoalIngestionEngine).
  - mutate.py: Code inspection, AST validation, and surgical transformations.
  - prover.py: Subprocess syntax proving, Lean 4 / compiler execution pipeline.
  - verify.py: Automated verification test suites and component integrity checks.
  - drives.py: Unified Discipline Engine — D_floor ≥ 0.90 adaptive drive parameters.
  - bio_gen.py: Bio-Gen Replicator — specialized harness spawning across 3 vectors.
  - science_mcp.py: 10-router hyper-specialized science engine with live telemetry.
  - meta_prover.py: Evolutionary axiom mutation over alternative logical frameworks.
  - telos_v2.py: Epistemic curiosity engine driven by surprisal and information gain.
  - manifold_bridge.py: Cross-domain structural isomorphism and solution transport.
  - devil_advocate.py: Adversarial metric inversion that escapes local minima.

Enforces an internal wall-clock timer (configured to a 340-minute window) that
guarantees clean state serialization and graceful container handoff.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Module integrations
from mutate import CodeInspector, CodeMutator, SurgicalAstModifier
from prover import ProverInterface, SearchBudget
from telos import (
    GoalIngestionEngine,
    GoalPriority,
    GoalStatus,
    MilestoneStatus,
    TacticalPhase,
    TelosEngine,
)
from verify import ComponentIntegrityChecker, VerificationRunner, ZeroTrustPipeline

# Optional integrations — degrade gracefully if unavailable
try:
    from egraph_engine import EquivalenceSaturationEngine  # type: ignore[import]

    _EGRAPH_AVAILABLE = True
except ImportError:
    _EGRAPH_AVAILABLE = False

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

# Meta-cognitive extensions — degrade gracefully when individual modules are absent
try:
    from meta_prover import MetaProver  # type: ignore[import]

    _META_PROVER_AVAILABLE = True
except ImportError:
    _META_PROVER_AVAILABLE = False

try:
    from telos_v2 import EpistemicCuriosityEngine  # type: ignore[import]

    _CURIOSITY_AVAILABLE = True
except ImportError:
    _CURIOSITY_AVAILABLE = False

try:
    from manifold_bridge import AnalogicalManifoldBridge  # type: ignore[import]

    _MANIFOLD_AVAILABLE = True
except ImportError:
    _MANIFOLD_AVAILABLE = False

try:
    from devil_advocate import DevilAdvocate  # type: ignore[import]

    _DEVIL_AVAILABLE = True
except ImportError:
    _DEVIL_AVAILABLE = False

# Elastic auto-scaling orchestrator & constitutional governor
try:
    from scale_orchestrator import ScaleOrchestrator  # type: ignore[import]

    _SCALE_AVAILABLE = True
except Exception:  # pragma: no cover - scaling must never block the kernel
    ScaleOrchestrator = None  # type: ignore[assignment]
    _SCALE_AVAILABLE = False

# Real cycle telemetry ledger (per-cycle/per-step CPU, RSS, durations)
try:
    import cycle_telemetry  # type: ignore[import]

    _TELEMETRY_AVAILABLE = True
except Exception:  # pragma: no cover - telemetry must never block the kernel
    cycle_telemetry = None  # type: ignore[assignment]
    _TELEMETRY_AVAILABLE = False

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


def _inspect_source_file(py_file: Path) -> Dict[str, Any]:
    """AST inspection unit dispatched to the elastic worker pool."""
    content = py_file.read_text(encoding="utf-8")
    metrics = CodeInspector.inspect(content)
    SurgicalAstModifier.parse_source(content)
    return metrics


def _ast_syntax_check(py_file: Path) -> Dict[str, Any]:
    try:
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")
        return {"success": True, "stderr": ""}
    except SyntaxError as exc:
        return {"success": False, "stderr": f"{exc.msg} (line {exc.lineno})"}
    except OSError as exc:
        return {"success": False, "stderr": str(exc)}


def _prover_syntax_scan(
    files: List[Path],
    prover: Any,
    stop_event: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """Subprocess proof strategy; cooperatively stops between files."""
    results: Dict[str, Dict[str, Any]] = {}
    for py_file in files:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            result = prover.verify_python_syntax(py_file)
            results[py_file.name] = {
                "success": bool(result.success),
                "stderr": result.stderr.strip(),
            }
        except Exception as exc:
            results[py_file.name] = {
                "success": False,
                "stderr": f"{type(exc).__name__}: {exc}",
            }
    return results


def _ast_syntax_scan(
    files: List[Path],
    stop_event: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """In-process compile strategy; races the subprocess prover."""
    results: Dict[str, Dict[str, Any]] = {}
    for py_file in files:
        if stop_event is not None and stop_event.is_set():
            break
        results[py_file.name] = _ast_syntax_check(py_file)
    return results


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
        extensions_enabled: bool = True,
        curiosity_interval_seconds: float = 0.0,
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

        # Meta-cognitive extension configuration
        self.extensions_enabled = bool(extensions_enabled)
        self.curiosity_interval_seconds = max(0.0, float(curiosity_interval_seconds))

        # Real cycle telemetry: recorder + applied safe-remediation tuning
        self._cycle_recorder: Optional[Any] = None
        self.last_cycle_telemetry: Optional[Dict[str, Any]] = None
        self._syntax_cache: Dict[str, Tuple[int, int, bool, str]] = {}
        self._tuning: Dict[str, Any] = {}
        if _TELEMETRY_AVAILABLE and cycle_telemetry is not None:
            try:
                self._tuning = cycle_telemetry.load_tuning()
            except Exception as exc:
                logger.warning(f"Telemetry tuning unavailable: {exc}")
                self._tuning = {}
        self._apply_tuning()

        # Integrated sub-services
        self.prover = ProverInterface()
        self.verifier = VerificationRunner(
            ratchet_path=self.workspace_root / ".sask_ego_ratchet.json"
        )
        self.zero_trust = ZeroTrustPipeline(root=self.workspace_root)

        # Elastic auto-scaling orchestrator & meta-scheduler (constitutional)
        self.scale: Optional[Any] = None
        if _SCALE_AVAILABLE:
            try:
                self.scale = ScaleOrchestrator(root=self.workspace_root)
            except Exception as exc:
                logger.warning(f"Scale orchestrator initialization failed: {exc}")
                self.scale = None

        self.telos = self._build_pipeline_engine()

        # Equivalence saturation engine (e-graph superposition)
        self.egraph: Optional[Any] = None
        if _EGRAPH_AVAILABLE:
            try:
                self.egraph = EquivalenceSaturationEngine(
                    state_file=self.workspace_root / ".sask_egraph.json"
                )
            except Exception as exc:
                logger.warning(f"E-graph engine initialization failed: {exc}")
        else:
            logger.warning("egraph_engine.py not available — equivalence compression disabled.")

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

        # Meta-cognitive extensions
        self.meta_prover: Optional[Any] = None
        self.curiosity: Optional[Any] = None
        self.manifold: Optional[Any] = None
        self.devil: Optional[Any] = None
        if self.extensions_enabled:
            self._initialize_extensions()

        self._register_signal_handlers()

    def _initialize_extensions(self) -> None:
        """Instantiate the four meta-cognitive extensions with handoff state files."""
        root = self.workspace_root
        if _META_PROVER_AVAILABLE:
            try:
                self.meta_prover = MetaProver(
                    prover=self.prover,
                    state_path=root / ".sask_meta_prover.json",
                )
            except Exception as exc:
                logger.warning(f"Meta-prover initialization failed: {exc}")
        else:
            logger.warning("meta_prover.py not available — axiomatic mutation disabled.")

        if _CURIOSITY_AVAILABLE:
            try:
                self.curiosity = EpistemicCuriosityEngine(
                    goal_engine=self.telos.goal_engine,
                    stream=self.telos.stream,
                    state_path=root / ".sask_curiosity.json",
                )
            except Exception as exc:
                logger.warning(f"Curiosity engine initialization failed: {exc}")
        else:
            logger.warning("telos_v2.py not available — epistemic curiosity disabled.")

        if _MANIFOLD_AVAILABLE:
            try:
                self.manifold = AnalogicalManifoldBridge(
                    state_path=root / ".sask_manifold.json"
                )
            except Exception as exc:
                logger.warning(f"Manifold bridge initialization failed: {exc}")
        else:
            logger.warning("manifold_bridge.py not available — analogical transport disabled.")

        if _DEVIL_AVAILABLE:
            try:
                self.devil = DevilAdvocate(
                    state_path=root / ".sask_devil_advocate.json"
                )
            except Exception as exc:
                logger.warning(f"Devil advocate initialization failed: {exc}")
        else:
            logger.warning("devil_advocate.py not available — counter-intuition disabled.")

        self._register_science_extensions()

    def _register_science_extensions(self) -> None:
        """Expose extension instances through the science engine registry."""
        try:
            from science_mcp import register_extension  # type: ignore[import-not-found]
        except Exception as exc:
            logger.warning(f"Science extension registry unavailable: {exc}")
            return

        mapping = {
            "meta_prover": self.meta_prover,
            "curiosity": self.curiosity,
            "manifold_bridge": self.manifold,
            "devil_advocate": self.devil,
            "egraph": self.egraph,
        }
        for name, engine in mapping.items():
            if engine is None:
                continue
            try:
                register_extension(name, engine)
            except Exception as exc:
                logger.warning(f"Failed to register science extension '{name}': {exc}")

    def _save_extension_state(self) -> Dict[str, Any]:
        """Persist every extension for container handoff; never fatal."""
        saved: List[str] = []
        failures: Dict[str, str] = {}
        for name in ("meta_prover", "curiosity", "manifold", "devil", "egraph"):
            engine = getattr(self, name, None)
            if engine is None or not hasattr(engine, "save"):
                continue
            try:
                path = engine.save()
                if path is not None:
                    saved.append(str(path))
            except Exception as exc:
                failures[name] = f"{type(exc).__name__}: {exc}"
        result: Dict[str, Any] = {"state_files": saved}
        if failures:
            result["save_failures"] = failures
        return result

    def _apply_tuning(self) -> Dict[str, Any]:
        """Apply safe, locally-tuned limits (git-ignored ``sask_tuning.json``)."""
        budget = self._tuning.get("stream_max_goals_per_cycle") if self._tuning else None
        if isinstance(budget, (int, float)) and budget > 0:
            self.stream_max_goals_per_cycle = min(
                self.stream_max_goals_per_cycle, max(1, int(budget))
            )
        return self._tuning

    def _new_cycle_recorder(self) -> Optional[Any]:
        """Start a real telemetry recorder for the current cycle (never fatal)."""
        self._cycle_recorder = None
        if not _TELEMETRY_AVAILABLE or cycle_telemetry is None:
            return None
        try:
            self._tuning = cycle_telemetry.load_tuning()
            self._apply_tuning()
            self._cycle_recorder = cycle_telemetry.CycleRecorder(
                cycle_id=self.current_cycle
            )
        except Exception as exc:
            logger.warning(f"Cycle telemetry recorder unavailable: {exc}")
            self._cycle_recorder = None
        return self._cycle_recorder

    def _mark_boundary(self, phase: str, module: str) -> None:
        """Close the current telemetry segment and attribute it to a phase."""
        recorder = self._cycle_recorder
        if recorder is None:
            return
        try:
            recorder.boundary(phase, module)
        except Exception:
            return

    def _finish_cycle_recorder(
        self, success: bool, details: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Persist the cycle record to the telemetry ledger (never fatal)."""
        recorder = self._cycle_recorder
        self._cycle_recorder = None
        if recorder is None:
            return None
        if success and self.state == CoreState.COMPLETED:
            exit_status = "completed"
        elif not success:
            exit_status = "failed"
        elif self.timer.is_threshold_reached():
            exit_status = "handoff_ready"
        else:
            exit_status = str(self.state.value).lower()
        try:
            return recorder.finish(success, exit_status, details, timer=self.timer)
        except Exception as exc:
            logger.warning(f"Cycle telemetry finalize failed: {exc}")
            return None

    def _syntax_cache_lookup(self, py_file: Path) -> Optional[Tuple[bool, str]]:
        """Return a cached syntax result when mtime+size are unchanged."""
        if not bool(self._tuning.get("syntax_cache_enabled", True)):
            return None
        try:
            stat = py_file.stat()
        except OSError:
            return None
        cached = self._syntax_cache.get(py_file.name)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2], cached[3]
        return None

    def _syntax_cache_store(
        self, py_file: Path, success: bool, stderr: str
    ) -> None:
        try:
            stat = py_file.stat()
        except OSError:
            return
        self._syntax_cache[py_file.name] = (
            stat.st_mtime_ns,
            stat.st_size,
            bool(success),
            stderr,
        )

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
        curiosity = getattr(self, "curiosity", None)
        if curiosity is not None:
            try:
                curiosity.stop_background()
            except Exception:
                pass
        self._save_extension_state()
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

    # ------------------------------------------------------------------
    # Meta-cognitive extension stage
    # ------------------------------------------------------------------

    def _inspect_pipeline_graph(self) -> Tuple[List[str], List[Tuple[str, str]]]:
        """Build the live module-import graph for the manifold bridge."""
        modules = sorted(path.stem for path in self.workspace_root.glob("*.py"))
        module_set = set(modules)
        pattern = re.compile(
            r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)",
            re.MULTILINE,
        )
        edges: set = set()
        for path in self.workspace_root.glob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in pattern.finditer(text):
                target = match.group(1)
                if target in module_set and target != path.stem:
                    edges.add((path.stem, target))
        return modules, sorted(edges)

    def _inject_manifold_transfer(self, transfer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ingest one manifold transfer directive as a deterministic goal."""
        if not transfer or not transfer.get("transferred"):
            return None
        directive = str(transfer.get("directive") or "").strip()
        if not directive:
            return None

        signature = "|".join(
            str(transfer.get(key, ""))
            for key in ("source", "target", "solution")
        )
        goal_id = "manifold-" + hashlib.blake2b(
            signature.encode("utf-8"), digest_size=6
        ).hexdigest()
        existing = self.telos.goal_engine.get_goal(goal_id)
        if existing is not None:
            return {"goal_id": goal_id, "injected": False, "reason": "exists"}

        try:
            goal = self.telos.goal_engine.ingest_goal(
                directive,
                priority=GoalPriority.NORMAL,
                title=f"Manifold transfer: {transfer.get('solution', 'solution')}",
                goal_id=goal_id,
            )
            goal.metadata["manifold_transfer"] = {
                "source": transfer.get("source"),
                "target": transfer.get("target"),
                "source_domain": transfer.get("source_domain"),
                "target_domain": transfer.get("target_domain"),
                "confidence": transfer.get("confidence"),
                "structural_isomorphism": transfer.get("structural_isomorphism"),
            }
        except Exception as exc:
            return {
                "goal_id": goal_id,
                "injected": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }

        streamed = False
        if self.stream_enabled and self.telos.stream.is_running:
            try:
                info = self.telos.stream.submit_objective(
                    directive,
                    level=1,
                    priority=GoalPriority.NORMAL,
                    objective_id=f"manifold-obj-{goal_id}",
                    metadata={
                        "source": "manifold_bridge",
                        "transfer": transfer.get("solution"),
                    },
                )
                streamed = bool(info.get("new", True))
            except Exception:
                streamed = False

        return {"goal_id": goal_id, "injected": True, "streamed": streamed}

    def _bidirectional_pipeline_search(
        self,
        modules: List[str],
        edges: List[Tuple[str, str]],
    ) -> Dict[str, Any]:
        """Prove a concrete import path from core to a verification engine."""
        module_set = set(modules)
        forward_map: Dict[str, List[str]] = {}
        backward_map: Dict[str, List[str]] = {}
        for source, target in edges:
            forward_map.setdefault(source, []).append(target)
            backward_map.setdefault(target, []).append(source)

        roots = [name for name in ("core", "verify") if name in module_set] or modules[:1]
        targets = [
            name for name in ("verify", "prover", "science_mcp") if name in module_set
        ]
        targets = [name for name in targets if name not in roots]
        if not roots or not targets:
            return {"available": True, "solved": False, "reason": "graph_incomplete"}

        def precheck(state: str) -> bool:
            return state in module_set

        def ranker(state: str) -> float:
            return 0.0 if state in targets else 1.0

        budget = SearchBudget(
            max_nodes_per_side=64,
            max_depth=8,
            max_seconds=1.0,
            beam_width=48,
        )
        try:
            result = self.prover.bidirectional_search(
                roots,
                targets,
                lambda state: forward_map.get(state, []),
                lambda state: backward_map.get(state, []),
                precheck=precheck,
                verify=precheck,
                ranker=ranker,
                budget=budget,
                tag="pipeline-imports",
            )
        except Exception as exc:
            return {
                "available": True,
                "solved": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        payload = result.to_dict()
        payload["available"] = True
        return payload

    def _run_egraph_superposition(
        self,
        metrics: Dict[str, float],
        module_count: int,
        include_sources: bool = False,
    ) -> Dict[str, Any]:
        """Superpose equivalent metric formulations and expose the best forms."""
        if self.egraph is None:
            return {"available": False}

        a = max(0.001, round(float(metrics.get("system_mastery", 0.0)) + 1.0, 4))
        b = max(0.001, round(float(metrics.get("unified_discipline", 1.0)) + 0.5, 4))
        c = float(max(2, module_count))
        expressions = [
            f"{a} + 0",
            f"0 + {a}",
            f"{a} * 1",
            f"1 * {a}",
            f"({a} + {b}) + {c}",
            f"{a} + ({b} + {c})",
            f"{a} - {a}",
            f"{a} ** 2",
            f"{a} * ({b} + {c})",
            f"{a} * {b} + {a} * {c}",
        ]
        report = self.egraph.superpose(
            expressions,
            goal=f"{a} * 1",
            verify=True,
            form_limit=3,
            origin="cycle_metrics",
        )
        merged = report.get("merged_classes") or []
        total = max(1, int(report.get("expressions", 0)))
        best_forms = [
            {
                "input": entry["input"],
                "best": entry["best"]["expression"],
                "verified": entry["best"]["verified"],
                "score": entry["best"]["score"],
            }
            for entry in report.get("results", [])
            if entry.get("best")
        ][:4]
        telemetry: Dict[str, Any] = {
            "available": True,
            "expressions": report.get("expressions", 0),
            "merged_classes": len(merged),
            "merged_members": [group.get("members") for group in merged][:4],
            "rewrites": (report.get("saturation") or {}).get("rewrites", 0),
            "elapsed_seconds": (report.get("saturation") or {}).get("elapsed_seconds", 0.0),
            "best_forms": best_forms,
            "goal_matched": bool((report.get("goal") or {}).get("matched")),
            "equivalence_gain": round(len(merged) / total, 6),
        }
        if include_sources:
            sources: Dict[str, str] = {}
            for path in sorted(self.workspace_root.glob("*.py")):
                try:
                    sources[path.stem] = path.read_text(encoding="utf-8")
                except OSError:
                    continue
            try:
                source_report = self.egraph.superpose_source_fragments(
                    sources, max_fragments=16
                )
                telemetry["source_fragments"] = {
                    "expressions": source_report.get("expressions", 0),
                    "merged_classes": len(source_report.get("merged_classes") or []),
                    "rewrites": (source_report.get("saturation") or {}).get(
                        "rewrites", 0
                    ),
                    "origins": source_report.get("origins", {}),
                }
            except Exception as exc:
                telemetry["source_fragments"] = {
                    "error": f"{type(exc).__name__}: {exc}"
                }
        return telemetry

    def _ratchet_devil_counterexamples(self, probes: List[Dict[str, Any]]) -> int:
        """Persist adversarial counter-goals so past failure modes cannot recur."""
        ratcheted = 0
        for probe in probes[:3]:
            counter_goal = probe.get("counter_goal")
            if not counter_goal:
                continue
            try:
                self.zero_trust.ratchet(
                    {
                        "kind": "goal_probe",
                        "args": [str(counter_goal)[:200]],
                        "metric": probe.get("metric"),
                        "source": "devil_advocate",
                    },
                    "holdout",
                    "adversarial_counter_goal",
                )
                ratcheted += 1
            except Exception as exc:
                logger.warning(f"Counterexample ratchet failed: {exc}")
        return ratcheted

    def _run_extensions(
        self,
        cycle_details: Dict[str, Any],
        error_count: int,
    ) -> Dict[str, Any]:
        """Run the meta-prover, curiosity, manifold, and adversarial extensions."""
        telemetry: Dict[str, Any] = {
            "enabled": self.extensions_enabled,
            "meta_prover": {"available": self.meta_prover is not None},
            "curiosity": {"available": self.curiosity is not None},
            "manifold": {"available": self.manifold is not None},
            "devil_advocate": {"available": self.devil is not None},
            "egraph": {"available": self.egraph is not None},
            "bidirectional": {"available": True},
        }
        if not self.extensions_enabled:
            self._mark_boundary("extensions_skipped", "core")
            return telemetry

        summary = self.telos.get_summary()
        goal_summary = summary.get("goal_ingestion", {}) or {}
        completed = int(goal_summary.get("completed", 0))
        failed = int(goal_summary.get("failed", 0))
        success_rate = (
            completed / (completed + failed) if (completed + failed) else 1.0
        )
        discipline = self.drives.get_unified_discipline() if self.drives else 1.0
        metrics = {
            "system_mastery": float(summary.get("system_mastery", 0.0)),
            "unified_discipline": float(discipline),
            "success_rate": round(success_rate, 6),
            "error_count": float(error_count),
        }
        fusion_components: Dict[str, float] = {}

        # 1. Axiomatic mutation & meta-prover search
        if self.meta_prover is not None and (
            self.current_cycle == 1 or self.current_cycle % 3 == 0 or error_count > 0
        ):
            target_goal = (
                self.stream_objective
                or os.environ.get("SASK_PRIMARY_OBJECTIVE")
                or (
                    "Converge the SASK kernel with compact, verified proof paths "
                    "across every science router and discipline constraint"
                )
            )
            try:
                search = self.meta_prover.search(target_goal)
                compact = {
                    key: search.get(key)
                    for key in (
                        "best_framework",
                        "best_framework_name",
                        "best_score",
                        "best_success",
                        "best_steps",
                        "best_compactness",
                        "best_residual",
                        "evaluations",
                        "generations",
                    )
                }
                compact["available"] = True
                try:
                    counterfactual = self.meta_prover.counterfactual_search(
                        target_goal, candidates_limit=6, promote=True
                    )
                    compact["counterfactual"] = {
                        "promoted": counterfactual.get("promoted", 0),
                        "rejected": counterfactual.get("rejected", 0),
                        "branches": len(counterfactual.get("branches") or []),
                        "best_operator": counterfactual.get("best_operator"),
                        "best_delta": counterfactual.get("best_delta"),
                        "verification_mode": counterfactual.get("verification_mode"),
                    }
                except Exception as exc:
                    compact["counterfactual"] = {
                        "error": f"{type(exc).__name__}: {exc}"
                    }
                telemetry["meta_prover"] = compact
                cycle_details["meta_prover"] = compact
                fusion_components["meta_prover_score"] = float(
                    search.get("best_score", 0.0)
                )
            except Exception as exc:
                telemetry["meta_prover"] = {
                    "available": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        # 2. Epistemic curiosity — intrinsic reward and autonomous hypotheses
        self._mark_boundary("meta_prover", "meta_prover")
        if self.curiosity is not None:
            try:
                tick = self.curiosity.tick(
                    telemetry=metrics,
                    generate=2,
                    propose_limit=1,
                )
                proposals = tick.get("proposals") or []
                telemetry["curiosity"] = {
                    "available": True,
                    "tick": tick.get("tick"),
                    "intrinsic_reward": tick.get("intrinsic_reward"),
                    "mean_surprise": tick.get("mean_surprise"),
                    "mean_information_gain": tick.get("mean_information_gain"),
                    "free_energy": tick.get("free_energy"),
                    "hypotheses_generated": len(tick.get("hypotheses") or []),
                    "proposals": proposals,
                }
                cycle_details["curiosity"] = {
                    "tick": tick.get("tick"),
                    "hypotheses": len(tick.get("hypotheses") or []),
                    "proposals": proposals,
                }
                fusion_components["curiosity_reward"] = float(
                    tick.get("intrinsic_reward", 0.0)
                )
            except Exception as exc:
                telemetry["curiosity"] = {
                    "available": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        # 3. Cross-domain analogical manifold bridge
        self._mark_boundary("curiosity", "telos")
        if self.manifold is not None and (
            self.current_cycle == 1 or self.current_cycle % 4 == 0
        ):
            try:
                modules, edges = self._inspect_pipeline_graph()
                analysis = self.manifold.analyze_pipeline(
                    "live-pipeline", edges, modules
                )
                transfers = list(analysis.get("transfers") or [])
                if not transfers:
                    transfers = self.manifold.auto_transfer(limit=1)
                injected = [
                    record
                    for record in (
                        self._inject_manifold_transfer(transfer)
                        for transfer in transfers[:1]
                    )
                    if record is not None
                ]
                telemetry["manifold"] = {
                    "available": True,
                    "structures": self.manifold.get_telemetry().get("structure_count"),
                    "matches": len(analysis.get("matches") or []),
                    "transfers": len(transfers),
                    "injected_goals": injected,
                }
                cycle_details["manifold"] = {
                    "nodes": analysis.get("nodes"),
                    "edges": analysis.get("edges"),
                    "matches": (analysis.get("matches") or [])[:3],
                    "transfers": transfers,
                    "injected_goals": injected,
                }
                boundary = self.manifold.boundary_probes(limit=4)
                boundary_goals: List[Dict[str, Any]] = []
                if boundary and self.curiosity is not None:
                    try:
                        boundary_goals = self.curiosity.propose_boundary_goals(
                            boundary, limit=2
                        )
                    except Exception as exc:
                        logger.warning(f"Boundary goal ingestion failed: {exc}")
                telemetry["manifold"].update(
                    {
                        "boundary_probes": [
                            probe.to_dict() for probe in boundary[:2]
                        ],
                        "boundary_goals": boundary_goals,
                    }
                )
                cycle_details["manifold"]["boundary_probes"] = [
                    probe.to_dict() for probe in boundary
                ]
                if boundary:
                    fusion_components["boundary_pressure"] = max(
                        float(probe.severity) for probe in boundary
                    )
                if transfers:
                    fusion_components["manifold_confidence"] = float(
                        transfers[0].get("confidence", 0.0)
                    )
            except Exception as exc:
                telemetry["manifold"] = {
                    "available": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        # 4. Adversarial counter-intuition injection on stagnation
        self._mark_boundary("manifold", "manifold_bridge")
        if self.devil is not None:
            devil_started = time.monotonic()
            try:
                challenge = self.devil.challenge(
                    metrics,
                    drive_engine=self.drives,
                    goal_engine=self.telos.goal_engine,
                    stream=self.telos.stream if self.stream_enabled else None,
                    sandbox=self.meta_prover,
                )
                probes = challenge.get("probes") or []
                assessment = challenge.get("assessment") or {}
                telemetry["devil_advocate"] = {
                    "available": True,
                    "challenged": challenge.get("challenged"),
                    "reason": challenge.get("reason"),
                    "probes": probes,
                    "counterfactual": challenge.get("counterfactual") or [],
                    "counterfactual_promotions": challenge.get(
                        "counterfactual_promotions", 0
                    ),
                    "cooldown_remaining": challenge.get("cooldown_remaining"),
                    "stagnation": assessment.get("stagnation"),
                    "local_convergence": assessment.get("local_convergence"),
                    "stable_metrics": assessment.get("stable_metrics"),
                }
                cycle_details["devil_advocate"] = telemetry["devil_advocate"]
                if probes:
                    fusion_components["adversarial_intensity"] = max(
                        float(probe.get("intensity", 0.0)) for probe in probes
                    )
                    telemetry["devil_advocate"]["counterexamples_ratcheted"] = (
                        self._ratchet_devil_counterexamples(probes)
                    )
            except Exception as exc:
                telemetry["devil_advocate"] = {
                    "available": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if self.scale is not None:
                try:
                    status = telemetry.get("devil_advocate") or {}
                    probes = status.get("probes") or []
                    self.scale.record_strategy(
                        "devil_probe",
                        success="error" not in status,
                        speedup=1.0 + 0.1 * len(probes),
                        cost_seconds=max(0.0, time.monotonic() - devil_started),
                    )
                except Exception:
                    pass

        # 5. Bidirectional meet-in-the-middle import-path proof search
        self._mark_boundary("devil_advocate", "devil_advocate")
        modules: List[str] = []
        try:
            modules, edges = self._inspect_pipeline_graph()
            bidirectional = self._bidirectional_pipeline_search(modules, edges)
            telemetry["bidirectional"] = bidirectional
            cycle_details["bidirectional"] = bidirectional
            if bidirectional.get("solved"):
                depth = max(1, int(bidirectional.get("depth", 1)))
                fusion_components["bidirectional_success"] = 1.0
                fusion_components["path_compression"] = min(1.0, 1.0 / depth)
        except Exception as exc:
            telemetry["bidirectional"] = {
                "available": True,
                "error": f"{type(exc).__name__}: {exc}",
            }

        # 6. Equivalence saturation: retain every equivalent metric formulation
        self._mark_boundary("bidirectional", "prover")
        if self.egraph is not None and (
            self.current_cycle == 1 or self.current_cycle % 2 == 0
        ):
            egraph_started = time.monotonic()
            try:
                egraph_telemetry = self._run_egraph_superposition(
                    metrics,
                    len(modules) or 1,
                    include_sources=(
                        self.current_cycle == 1 or self.current_cycle % 5 == 0
                    ),
                )
                telemetry["egraph"] = egraph_telemetry
                cycle_details["egraph"] = {
                    "merged_classes": egraph_telemetry.get("merged_classes"),
                    "rewrites": egraph_telemetry.get("rewrites"),
                    "goal_matched": egraph_telemetry.get("goal_matched"),
                    "best_forms": egraph_telemetry.get("best_forms"),
                    "source_fragments": egraph_telemetry.get("source_fragments"),
                }
                fusion_components["equivalence_gain"] = float(
                    egraph_telemetry.get("equivalence_gain", 0.0)
                )
            except Exception as exc:
                telemetry["egraph"] = {
                    "available": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if self.scale is not None:
                try:
                    status = telemetry.get("egraph") or {}
                    self.scale.record_strategy(
                        "egraph_batch",
                        success="error" not in status,
                        speedup=1.0 + min(2.0, float(status.get("merged_classes") or 0) / 10.0),
                        cost_seconds=max(0.0, time.monotonic() - egraph_started),
                    )
                except Exception:
                    pass

        # 7. Fuse extension pressure into the permanent drive matrix
        self._mark_boundary("egraph", "egraph_engine")
        if self.drives is not None and fusion_components:
            try:
                telemetry["drive_fusion"] = self.drives.fuse_extension_metrics(
                    fusion_components,
                    context=f"cycle:{self.current_cycle}",
                )
                cycle_details["drive_fusion"] = telemetry["drive_fusion"]
            except Exception as exc:
                telemetry["drive_fusion"] = {
                    "error": f"{type(exc).__name__}: {exc}"
                }

        try:
            self.telos.goal_engine.save_manifest(
                str(self.workspace_root / "goals_manifest.json")
            )
        except Exception as exc:
            logger.warning(f"Manifest persistence after extensions failed: {exc}")

        self._mark_boundary("drive_fusion", "drives")
        telemetry["persistence"] = self._save_extension_state()
        self._mark_boundary("extension_persistence", "core")
        return telemetry

    def run_cycle(self) -> CycleMetrics:
        """Execute a single integrated cycle across telos, mutate, prover, and verify."""
        self.current_cycle += 1
        cycle_start_time = time.monotonic()
        start_iso = datetime.now(timezone.utc).isoformat()
        self.state = CoreState.RUNNING
        self._new_cycle_recorder()

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

            self._mark_boundary("env_check", "core")

            # 1.5 Elastic scale tick: discover hardware and size this cycle.
            if self.scale is not None:
                try:
                    previous_records = (
                        cycle_telemetry.read_records(limit=8)
                        if _TELEMETRY_AVAILABLE and cycle_telemetry is not None
                        else []
                    )
                    scale_tick = self.scale.tick(
                        cycle_id=self.current_cycle,
                        telemetry_records=previous_records,
                    )
                    cycle_details["scale"] = scale_tick
                    active_workers = max(1, int(scale_tick.get("active_workers") or 1))
                    self.stream_concurrency = max(
                        1, min(self.stream_concurrency, active_workers)
                    )
                except Exception as exc:
                    logger.warning(f"Elastic scale tick error (non-fatal): {exc}")
                    cycle_details["scale_error"] = str(exc)
                self._mark_boundary("scale_tick", "scale_orchestrator")

            # Check timer threshold between tactical phases
            if self.timer.is_threshold_reached():
                logger.warning("Execution window threshold reached during reconnaissance phase.")
                return self._finalize_cycle_metrics(cycle_start_time, start_iso, success=True, details=cycle_details)

            # Stage 1.5: Asynchronous goal stream consumption with harness fallback
            dispatch_started = time.monotonic()
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
                tuned_budget = self._tuning.get("stream_max_goals_per_cycle")
                if isinstance(tuned_budget, (int, float)) and tuned_budget > 0:
                    budget = min(budget, max(1, int(tuned_budget)))

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

            self._mark_boundary("goal_dispatch", "telos")
            if self.scale is not None:
                try:
                    self.scale.record_strategy(
                        "goal_dispatch",
                        success=error_count == 0,
                        cost_seconds=max(0.0, time.monotonic() - dispatch_started),
                    )
                except Exception:
                    pass

            # 2. Transformation: AST Parsing & Code Structure Inspection
            self.telos.update_status("code_analysis", MilestoneStatus.RUNNING)
            analysis_summary: Dict[str, Any] = {}
            source_files = list(self.workspace_root.glob("*.py"))
            if self.scale is not None and len(source_files) > 1:
                analysis = self.scale.run_parallel(
                    [
                        (py_file.name, lambda path=py_file: _inspect_source_file(path))
                        for py_file in source_files
                    ],
                    strategy="code_analysis",
                    cycle_id=self.current_cycle,
                )
                analysis_summary = dict(analysis.get("results") or {})
                for name, error in (analysis.get("errors") or {}).items():
                    logger.warning(f"Could not parse/inspect {name}: {error}")
                    error_count += 1
            else:
                for py_file in source_files:
                    try:
                        analysis_summary[py_file.name] = _inspect_source_file(py_file)
                    except Exception as exc:
                        logger.warning(f"Could not parse/inspect {py_file.name}: {exc}")
                        error_count += 1

            self.telos.update_status(
                "code_analysis",
                MilestoneStatus.COMPLETED,
                metadata={"files_inspected": len(analysis_summary)},
            )
            cycle_details["code_metrics"] = analysis_summary
            self._mark_boundary("code_analysis", "mutate")

            if self.timer.is_threshold_reached():
                logger.warning("Execution window threshold reached during transformation phase.")
                return self._finalize_cycle_metrics(cycle_start_time, start_iso, success=True, details=cycle_details)

            # 3. Verification: Subprocess Syntax Proving & Formal Assertions
            self.telos.update_status("syntax_proving", MilestoneStatus.RUNNING)
            syntax_passed = True
            syntax_errors: List[str] = []
            syntax_cache_hits = 0
            syntax_cache_misses = 0
            uncached: List[Path] = []
            for py_file in self.workspace_root.glob("*.py"):
                cached = self._syntax_cache_lookup(py_file)
                if cached is not None:
                    syntax_cache_hits += 1
                    ok, stderr_text = cached
                    if not ok:
                        syntax_passed = False
                        syntax_errors.append(f"{py_file.name}: {stderr_text}")
                        error_count += 1
                else:
                    uncached.append(py_file)
                    syntax_cache_misses += 1

            syntax_results: Dict[str, Dict[str, Any]] = {}
            if uncached:
                if self.scale is not None and len(uncached) > 1:
                    race = self.scale.run_race(
                        [
                            (
                                "prover_subprocess",
                                lambda files=list(uncached), stop_event=None: _prover_syntax_scan(
                                    files, self.prover, stop_event
                                ),
                            ),
                            (
                                "ast_compile",
                                lambda files=list(uncached), stop_event=None: _ast_syntax_scan(
                                    files, stop_event
                                ),
                            ),
                        ],
                        is_winner=lambda value: (
                            isinstance(value, dict)
                            and len(value) == len(uncached)
                            and all(item.get("success") for item in value.values())
                        ),
                        strategy="syntax_proving",
                        max_seconds=max(5.0, 2.5 * len(uncached)),
                        cost_hint=0.25 * max(1, len(uncached) - 1),
                        cycle_id=self.current_cycle,
                    )
                    for value in (race.get("results") or {}).values():
                        if isinstance(value, dict):
                            for name, outcome in value.items():
                                syntax_results.setdefault(name, outcome)
                    cycle_details["syntax_race"] = {
                        "winner": race.get("winner_name"),
                        "early_stopped": race.get("early_stopped"),
                        "saved_compute_seconds": race.get("saved_compute_seconds"),
                        "fastest_seconds": race.get("elapsed_seconds"),
                    }
                else:
                    scan_started = time.monotonic()
                    syntax_results = _prover_syntax_scan(uncached, self.prover)
                    if self.scale is not None:
                        try:
                            self.scale.record_strategy(
                                "syntax_proving",
                                success=all(
                                    item.get("success") for item in syntax_results.values()
                                ),
                                cost_seconds=max(0.0, time.monotonic() - scan_started),
                            )
                        except Exception:
                            pass

                for py_file in uncached:
                    outcome = syntax_results.get(py_file.name) or {
                        "success": False,
                        "stderr": "syntax proof strategy produced no verdict",
                    }
                    ok = bool(outcome.get("success"))
                    stderr_text = str(outcome.get("stderr") or "")
                    self._syntax_cache_store(py_file, ok, stderr_text)
                    if not ok:
                        syntax_passed = False
                        syntax_errors.append(f"{py_file.name}: {stderr_text}")
                        error_count += 1

            cycle_details["syntax_cache"] = {
                "hits": syntax_cache_hits,
                "misses": syntax_cache_misses,
            }

            # Prover Lean 4 status check
            toolchain = self.prover.lean.get_toolchain_info()
            cycle_details["prover_toolchain"] = toolchain

            if syntax_passed:
                self.telos.update_status("syntax_proving", MilestoneStatus.COMPLETED)
            else:
                err = "; ".join(syntax_errors)
                self.telos.update_status("syntax_proving", MilestoneStatus.FAILED, error=err)
                raise RuntimeError(f"Syntax proving failed: {err}")

            self._mark_boundary("syntax_proving", "prover")

            # 3.5 Zero-trust Tier-0 integrity sweep. A sealed manifest is
            # fail-closed; a missing manifest is a bootstrap condition so a
            # fresh checkout can still generate its first seal.
            manifest_path = Path(self.zero_trust.manifest_path)
            zero_trust_report = self.zero_trust.scan_workspace(
                self.workspace_root, files=[]
            )
            integrity = zero_trust_report.get("integrity") or {}
            cycle_details["zero_trust"] = {
                "passed": zero_trust_report["passed"],
                "integrity": integrity.get("reason_code"),
                "failures": list(integrity.get("failures") or [])[:5],
                "duration_ms": zero_trust_report["duration_ms"],
                "bootstrap": not manifest_path.is_file(),
            }
            self._mark_boundary("zero_trust", "verify")
            if not zero_trust_report["passed"] and manifest_path.is_file():
                error_count += 1
                raise RuntimeError(
                    "Zero-trust integrity failed: "
                    + "; ".join(cycle_details["zero_trust"]["failures"])
                )

            if self.timer.is_threshold_reached():
                logger.warning("Execution window threshold reached during verification phase.")
                return self._finalize_cycle_metrics(cycle_start_time, start_iso, success=True, details=cycle_details)

            # 4. Automated Verification Suite — Ancestral Regression Gate
            self.telos.update_status("test_verification", MilestoneStatus.RUNNING)
            test_report = self.verifier.run_ancestral_gate()
            self._mark_boundary("ancestral_gate", "verify")
            cycle_details["test_report"] = {
                "total": test_report.total_checks,
                "passed": test_report.passed_checks,
                "failed": test_report.failed_checks,
                "overthinking_depth": test_report.details.get("overthinking_depth"),
                "ego_ratchet": test_report.details.get("ego_ratchet"),
            }

            # 4.5 Meta-cognitive extensions: axiomatic mutation, curiosity,
            # cross-domain transport, and adversarial injection. This stage is
            # non-fatal and also runs on verification failure so stagnation is
            # still challenged.
            if self.extensions_enabled:
                try:
                    cycle_details["extensions"] = self._run_extensions(
                        cycle_details, error_count
                    )
                except Exception as exc:
                    logger.warning(f"Extension stage error (non-fatal): {exc}")
                    cycle_details["extension_error"] = str(exc)
                self._mark_boundary("extensions", "core")

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

            # 5.5 Meta-scheduler: calibrate from real telemetry, A/B upgrade, rollback.
            if self.scale is not None:
                try:
                    records = (
                        cycle_telemetry.read_records(limit=8)
                        if _TELEMETRY_AVAILABLE and cycle_telemetry is not None
                        else []
                    )
                    workload: List[float] = []
                    if records:
                        latest_steps = records[-1].get("steps") or []
                        for step in latest_steps:
                            seconds = float(step.get("duration_ms", 0.0) or 0.0) / 1000.0
                            if seconds > 0:
                                workload.append(seconds)
                    scale_details = cycle_details.setdefault("scale", {})
                    if workload and test_report.is_successful and self.current_cycle % 3 == 1:
                        scale_details["upgrade"] = self.scale.maybe_self_upgrade(
                            workload,
                            telemetry_records=records,
                            cycle_id=self.current_cycle,
                        )
                    if workload:
                        failed_checks = int(test_report.failed_checks or 0) + int(error_count)
                        failure_rate = failed_checks / max(1, int(test_report.total_checks or 1))
                        scale_details["live"] = self.scale.observe_live(
                            max(workload) * 1000.0,
                            min(1.0, failure_rate),
                            cycle_id=self.current_cycle,
                        )
                    scale_details.update(self.scale.snapshot())
                except Exception as exc:
                    logger.warning(f"Meta-scheduler tuning error (non-fatal): {exc}")
                    cycle_details["scale_error"] = str(exc)
                self._mark_boundary("scale_tuning", "scale_orchestrator")

            self.telos.update_status("telemetry_report", MilestoneStatus.COMPLETED)
            self._mark_boundary("telemetry_report", "telos")

            # Apply drives stimulus based on cumulative error count
            if self.drives is not None:
                try:
                    self.drives.apply_stimulus(challenge_intensity=error_count)
                    cycle_details["drives_discipline"] = self.drives.get_unified_discipline()
                except Exception as exc:
                    logger.warning(f"Drives stimulus error (non-fatal): {exc}")
            self._mark_boundary("drives_stimulus", "drives")

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
        self.last_cycle_telemetry = self._finish_cycle_recorder(success, details)
        if self.extensions_enabled:
            try:
                self._save_extension_state()
            except Exception as exc:
                logger.warning(f"Extension persistence during finalize failed: {exc}")
        logger.info(
            f"Cycle #{self.current_cycle} concluded in {metrics.duration_seconds}s "
            f"(Status: {'SUCCESS' if success else 'FAILED'}, Mastery: {metrics.system_mastery * 100:.1f}%)"
        )
        return metrics

    def run_continuous_loop(
        self,
        max_cycles: Optional[int] = None,
        cycle_delay_seconds: Optional[float] = None,
    ) -> None:
        """Run continuous execution cycles bounded by max_cycles or the 340-minute wall-clock timer."""
        if cycle_delay_seconds is None:
            tuned_delay = self._tuning.get("cycle_delay_seconds")
            cycle_delay_seconds = (
                max(0.0, float(tuned_delay))
                if isinstance(tuned_delay, (int, float))
                else 0.0
            )
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

        curiosity_started = False
        if self.curiosity is not None and self.curiosity_interval_seconds > 0:
            curiosity_started = self.curiosity.start_background(
                self.curiosity_interval_seconds,
                generate=1,
                propose_limit=1,
            )
            if curiosity_started:
                logger.info(
                    f"Curiosity background loop active "
                    f"(interval={self.curiosity_interval_seconds}s)."
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
            if curiosity_started and self.curiosity is not None:
                try:
                    self.curiosity.stop_background()
                except Exception as exc:
                    logger.warning(f"Curiosity shutdown error: {exc}")
            if self.extensions_enabled:
                try:
                    self._save_extension_state()
                except Exception as exc:
                    logger.warning(f"Extension persistence on handoff failed: {exc}")
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

        # Meta-cognitive extension telemetry
        extensions_telemetry: Dict[str, Any] = {"enabled": self.extensions_enabled}
        for name in ("meta_prover", "curiosity", "manifold", "devil", "egraph"):
            engine = getattr(self, name, None)
            if engine is None:
                extensions_telemetry[name] = {"available": False}
                continue
            try:
                extensions_telemetry[name] = engine.get_telemetry()
            except Exception as exc:
                extensions_telemetry[name] = {
                    "available": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        last_details = self.cycle_history[-1].details if self.cycle_history else {}
        bidirectional_telemetry = last_details.get("bidirectional") or {
            "available": True,
            "solved": False,
            "reason": "not_run",
        }

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
            "extensions": extensions_telemetry,
            "bidirectional_search": bidirectional_telemetry,
            "scale": (
                self.scale.snapshot()
                if self.scale is not None
                else {"available": False}
            ),
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
    parser.add_argument(
        "--extensions",
        dest="extensions",
        action="store_true",
        default=None,
        help="Force-enable the meta-prover, curiosity, manifold, and adversarial extensions.",
    )
    parser.add_argument(
        "--no-extensions",
        dest="extensions",
        action="store_false",
        help="Disable all meta-cognitive extensions.",
    )
    parser.add_argument(
        "--curiosity-interval",
        type=float,
        default=float(os.environ.get("SASK_CURIOSITY_INTERVAL", "0") or 0.0),
        help="Background curiosity tick interval in seconds (0 disables the daemon; per-cycle ticks still run).",
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

    env_extensions_enabled = os.environ.get(
        "SASK_EXTENSIONS_ENABLED", "1"
    ).strip().lower() not in ("0", "false", "no", "off")
    extensions_enabled = (
        args.extensions
        if args.extensions is not None
        else env_extensions_enabled
    )

    kernel = AutomationKernel(
        window_minutes=args.window_minutes,
        state_file=state_path,
        stream_enabled=stream_enabled,
        stream_level=args.stream_level,
        stream_objective=args.stream_objective,
        stream_concurrency=args.stream_concurrency,
        extensions_enabled=extensions_enabled,
        curiosity_interval_seconds=args.curiosity_interval,
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
