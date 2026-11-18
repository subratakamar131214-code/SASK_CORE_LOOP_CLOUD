"""Telos Engine: Milestone and Task-Sequencing Engine with Universal Goal Ingestion.

Provides tactical sub-quest generation, macro-objective progression toward
system mastery, error-reduction stagnation detection, metabolic resets,
search-basin clearing, and an open-ended goal ingestion pipeline capable of
absorbing, vectorizing, and scheduling any arbitrary goal from the Goal Giver.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, Union


# ---------------------------------------------------------------------------
# Core Enumerations
# ---------------------------------------------------------------------------

class MilestoneStatus(str, Enum):
    """Execution status for pipeline milestones and tactical quests."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TacticalPhase(str, Enum):
    """Phases for tactical sub-quest execution."""
    RECONNAISSANCE = "RECONNAISSANCE"
    TRANSFORMATION = "TRANSFORMATION"
    VERIFICATION = "VERIFICATION"
    CONVERGENCE = "CONVERGENCE"
    MASTERY = "MASTERY"


class GoalVector(str, Enum):
    """The three universal execution vectors for goal decomposition."""
    FORMAL_PROOF = "FORMAL_PROOF"           # Vector 1: Lean 4 theorem proving & logic
    ALGORITHMIC_CODE = "ALGORITHMIC_CODE"   # Vector 2: Code mutation, structures, optimisation
    FRONTIER_EXPLORE = "FRONTIER_EXPLORE"   # Vector 3: Open-ended discovery and research


class GoalPriority(int, Enum):
    """Priority tiers for ingested goals (higher value = higher urgency)."""
    CRITICAL = 4
    HIGH = 3
    NORMAL = 2
    LOW = 1


class GoalStatus(str, Enum):
    """Lifecycle status for ingested goals."""
    QUEUED = "QUEUED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEFERRED = "DEFERRED"


class DiscoveryTrack(str, Enum):
    """Dual-track macro-discovery taxonomy for ingested goals."""
    CONVERGENT_MASTERY = "CONVERGENT_MASTERY"   # Closed convergence on verifiable objectives
    OPEN_FRONTIER = "OPEN_FRONTIER"             # Open-ended exploration and discovery


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Milestone:
    """Represents an atomic task milestone or tactical sub-quest."""
    id: str
    title: str
    description: str = ""
    depends_on: List[str] = field(default_factory=list)
    status: MilestoneStatus = MilestoneStatus.PENDING
    phase: TacticalPhase = TacticalPhase.RECONNAISSANCE
    weight: float = 1.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    action: Optional[Callable[..., Any]] = None

    def mark_started(self) -> None:
        self.status = MilestoneStatus.RUNNING
        self.started_at = datetime.now(timezone.utc).isoformat()

    def mark_completed(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.status = MilestoneStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        if metadata:
            self.metadata.update(metadata)

    def mark_failed(self, error: str) -> None:
        self.status = MilestoneStatus.FAILED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.error_message = error

    def mark_skipped(self, reason: str = "") -> None:
        self.status = MilestoneStatus.SKIPPED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        if reason:
            self.metadata["skip_reason"] = reason


@dataclass
class ResetEvent:
    """Telemetry recorded when a metabolic reset or search-basin clearing occurs."""
    timestamp: str
    reset_type: str
    reason: str
    cleared_milestone_ids: List[str]
    previous_error_rate: float
    recalibrated_state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MacroObjective:
    """High-level system goal tracking multi-cycle progression toward mastery."""
    id: str
    title: str
    description: str = ""
    target_mastery: float = 1.0
    current_mastery: float = 0.0
    sub_quest_ids: List[str] = field(default_factory=list)
    stagnation_limit: int = 3
    stagnation_count: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class IngestionGoal:
    """An arbitrary goal ingested from the Goal Giver and decomposed into sub-tasks."""
    id: str
    title: str
    description: str
    vector: GoalVector
    priority: GoalPriority
    raw_prompt: str
    sub_tasks: List[str] = field(default_factory=list)
    status: GoalStatus = GoalStatus.QUEUED
    discipline_weight: float = 1.0
    discovery_track: DiscoveryTrack = DiscoveryTrack.CONVERGENT_MASTERY
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["vector"] = self.vector.value
        d["priority"] = self.priority.name
        d["status"] = self.status.value
        d["discovery_track"] = self.discovery_track.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IngestionGoal":
        data = dict(data)
        vector = GoalVector(data.get("vector", GoalVector.FRONTIER_EXPLORE.value))
        data["vector"] = vector
        priority_raw = data.get("priority", "NORMAL")
        if isinstance(priority_raw, int):
            data["priority"] = GoalPriority(priority_raw)
        else:
            data["priority"] = GoalPriority[priority_raw]
        data["status"] = GoalStatus(data.get("status", GoalStatus.QUEUED.value))
        track_raw = data.get("discovery_track")
        data["discovery_track"] = (
            DiscoveryTrack(track_raw)
            if track_raw
            else MacroDiscoveryTaxonomy.classify_vector(vector)
        )
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Dual-Track Macro-Discovery Taxonomy
# ---------------------------------------------------------------------------

class MacroDiscoveryTaxonomy:
    """Classifies goals into convergent-mastery or open-frontier discovery tracks.

    Convergent goals have a closed success criterion and are driven to verified
    completion. Frontier goals are open-ended and are driven toward exploration
    breadth rather than convergence.
    """

    _FRONTIER_MARKERS: Set[str] = {
        "explore", "exploration", "discover", "research", "novel", "frontier",
        "open-ended", "unknown", "hypothesis", "hypotheses", "survey",
        "brainstorm", "speculative", "emergent",
    }

    @staticmethod
    def classify_vector(vector: GoalVector) -> DiscoveryTrack:
        """Classify by execution vector when no prompt-level signal is available."""
        if vector in (GoalVector.FORMAL_PROOF, GoalVector.ALGORITHMIC_CODE):
            return DiscoveryTrack.CONVERGENT_MASTERY
        return DiscoveryTrack.OPEN_FRONTIER

    @classmethod
    def classify(cls, raw_prompt: str, vector: GoalVector) -> DiscoveryTrack:
        """Classify a goal using frontier markers, falling back to its vector."""
        lower = raw_prompt.lower()
        if any(marker in lower for marker in cls._FRONTIER_MARKERS):
            return DiscoveryTrack.OPEN_FRONTIER
        return cls.classify_vector(vector)

    @staticmethod
    def summarize(goals: Iterable[IngestionGoal]) -> Dict[str, Any]:
        """Aggregate goals per discovery track."""
        counts: Dict[str, int] = {t.value: 0 for t in DiscoveryTrack}
        for goal in goals:
            counts[goal.discovery_track.value] += 1
        total = sum(counts.values())
        convergent = counts[DiscoveryTrack.CONVERGENT_MASTERY.value]
        return {
            "total": total,
            "tracks": counts,
            "convergent_ratio": round(convergent / total, 4) if total else 0.0,
        }


# ---------------------------------------------------------------------------
# Goal Ingestion Engine
# ---------------------------------------------------------------------------

class GoalIngestionEngine:
    """
    Universal goal ingestion pipeline capable of absorbing, vectorizing, and
    scheduling any arbitrary goal issued by the Goal Giver — no matter how
    abstract, multi-disciplinary, or novel.

    Goals are auto-decomposed into sub-tasks across the three universal execution
    vectors (FORMAL_PROOF, ALGORITHMIC_CODE, FRONTIER_EXPLORE) and scheduled
    via a discipline-weighted priority queue.
    """

    # Heuristic keyword sets for automatic vector classification
    _FORMAL_PROOF_KEYWORDS: Set[str] = {
        "prove", "theorem", "lemma", "axiom", "formal", "lean", "mathlib",
        "logic", "induction", "verify", "proof", "corollary", "deduction",
        "consistency", "soundness", "completeness",
    }
    _ALGORITHMIC_CODE_KEYWORDS: Set[str] = {
        "implement", "code", "algorithm", "function", "data structure", "optimize",
        "benchmark", "refactor", "mutate", "ast", "parser", "runtime", "efficient",
        "sort", "search", "graph", "tree", "dynamic", "programming",
    }

    # Keyword routing surface for the ten science_mcp routers
    _ROUTER_KEYWORDS: Dict[str, Set[str]] = {
        "quantum_router": {
            "quantum", "qasm", "qubit", "circuit", "anneal", "superconduct",
        },
        "atomistic_router": {
            "atomistic", "atom", "dft", "molecular dynamics", "lattice",
            "interatomic", "calphad",
        },
        "hpc_router": {
            "hpc", "slurm", "mpi", "cluster", "supercomput", "job array",
            "batch scheduler",
        },
        "wetlab_router": {
            "wetlab", "wet lab", "assay", "pipette", "laboratory",
            "protocol", "synthesis",
        },
        "sensing_router": {
            "sensor", "satellite", "imagery", "remote sensing", "telemetry",
            "stac", "bounding box",
        },
        "beamline_router": {
            "beamline", "synchrotron", "x-ray", "diffraction", "flyscan",
        },
        "free_grid_router": {
            "grid compute", "volunteer", "boinc", "distributed comput",
            "swarm", "work pool",
        },
        "multiphysics_router": {
            "pde", "multiphysics", "finite element", "cfd", "thermal",
            "structural", "mesh",
        },
        "digital_twin_router": {
            "digital twin", "usd", "kinematic", "physics engine",
            "simulation scene",
        },
        "biomolecular_router": {
            "protein", "pdb", "biomolecular", "genome", "docking",
            "forcefield",
        },
    }

    def __init__(self) -> None:
        self._goals: Dict[str, IngestionGoal] = {}
        self.taxonomy = MacroDiscoveryTaxonomy()
        self._router_dispatch_count = 0
        # Reentrant lock: the streaming producer thread and the main execution
        # loop mutate and iterate the goal registry concurrently.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_goal(
        self,
        raw_prompt: str,
        vector: Optional[GoalVector] = None,
        priority: Optional[GoalPriority] = None,
        discipline_weight: float = 1.0,
        title: Optional[str] = None,
        description: Optional[str] = None,
        goal_id: Optional[str] = None,
    ) -> IngestionGoal:
        """
        Parse and vectorize any arbitrary goal string into an IngestionGoal.

        If no vector is specified, the engine auto-classifies the goal using
        keyword heuristics. Sub-tasks are auto-decomposed across all three
        execution vectors when no vector is specified (open-frontier mode).

        Args:
            goal_id: Optional deterministic identifier. Re-ingesting an existing
                id returns the stored goal unchanged, which makes streamed goals
                idempotent across producer passes and handoffs.
        """
        with self._lock:
            if goal_id and goal_id in self._goals:
                return self._goals[goal_id]

            effective_id = goal_id or str(uuid.uuid4())[:12]
            effective_vector = vector or self._classify_vector(raw_prompt)
            effective_priority = priority or GoalPriority.NORMAL
            effective_title = title or self._derive_title(raw_prompt)
            effective_desc = description or raw_prompt[:256]
            effective_track = self.taxonomy.classify(raw_prompt, effective_vector)

            sub_tasks = self._decompose_into_subtasks(
                goal_id=effective_id,
                raw_prompt=raw_prompt,
                vector=effective_vector,
                multi_vector=(vector is None),
            )

            goal = IngestionGoal(
                id=effective_id,
                title=effective_title,
                description=effective_desc,
                vector=effective_vector,
                priority=effective_priority,
                raw_prompt=raw_prompt,
                sub_tasks=sub_tasks,
                discipline_weight=discipline_weight,
                discovery_track=effective_track,
            )
            router = self.resolve_router(raw_prompt)
            if router:
                goal.metadata["router"] = router
            self._goals[effective_id] = goal
            return goal

    def load_manifest(self, path: str = "goals_manifest.json") -> int:
        """
        Read and merge goals from a JSON manifest file.
        Returns the count of newly loaded goals (skips duplicates by id).
        """
        manifest_path = Path(path)
        if not manifest_path.is_file():
            return 0

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            goals_data: List[Dict[str, Any]] = raw if isinstance(raw, list) else raw.get("goals", [])
            loaded = 0
            with self._lock:
                for entry in goals_data:
                    goal_id = entry.get("id")
                    if goal_id and goal_id not in self._goals:
                        try:
                            goal = IngestionGoal.from_dict(entry)
                            self._goals[goal.id] = goal
                            loaded += 1
                        except Exception:
                            pass  # Tolerate malformed entries
            return loaded
        except (json.JSONDecodeError, OSError):
            return 0

    def save_manifest(self, path: str = "goals_manifest.json") -> None:
        """Persist the current goal queue to a JSON manifest file."""
        manifest_path = Path(path)
        with self._lock:
            payload = [g.to_dict() for g in self._goals.values()]
        tmp_path = manifest_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(manifest_path)

    def get_priority_queue(self, discipline: float = 1.0) -> List[IngestionGoal]:
        """
        Return active (QUEUED or ACTIVE) goals sorted by effective priority score:
            score = priority.value * discipline_weight * discipline
        """
        with self._lock:
            active = [
                g for g in self._goals.values()
                if g.status in (GoalStatus.QUEUED, GoalStatus.ACTIVE)
            ]
        active.sort(
            key=lambda g: g.priority.value * g.discipline_weight * max(discipline, 0.0),
            reverse=True,
        )
        return active

    def mark_goal_complete(self, goal_id: str) -> None:
        """Mark a goal as successfully completed."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal:
                goal.status = GoalStatus.COMPLETED
                goal.completed_at = datetime.now(timezone.utc).isoformat()

    def mark_goal_failed(self, goal_id: str, error: str) -> None:
        """Mark a goal as failed with an error message."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal:
                goal.status = GoalStatus.FAILED
                goal.error = error
                goal.completed_at = datetime.now(timezone.utc).isoformat()

    def mark_goal_queued(self, goal_id: str) -> None:
        """Return a goal to the QUEUED state (used when a handoff interrupts work)."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal:
                goal.status = GoalStatus.QUEUED
                goal.completed_at = None

    def get_all_goals(self) -> List[IngestionGoal]:
        """Return all ingested goals."""
        with self._lock:
            return list(self._goals.values())

    def get_goal(self, goal_id: str) -> Optional[IngestionGoal]:
        """Retrieve a goal by ID."""
        with self._lock:
            return self._goals.get(goal_id)

    def resolve_router(self, goal_or_prompt: Union[IngestionGoal, str]) -> Optional[str]:
        """Resolve a goal to one of the ten science routers.

        Explicit ``metadata["router"]`` hints take precedence. Otherwise the
        prompt is scored against router keyword sets and the strongest match is
        returned.

        Args:
            goal_or_prompt: An ingested goal or a raw prompt string.

        Returns:
            A canonical router name, or None when no router matches.
        """
        if isinstance(goal_or_prompt, IngestionGoal):
            explicit = (goal_or_prompt.metadata or {}).get("router")
            if explicit:
                return str(explicit)
            prompt = goal_or_prompt.raw_prompt
        else:
            prompt = str(goal_or_prompt)

        lower = prompt.lower()
        scores: Dict[str, int] = {}
        for router, keywords in self._ROUTER_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in lower)
            if score:
                scores[router] = score
        if not scores:
            return None
        return max(scores, key=lambda name: (scores[name], name))

    @staticmethod
    def _normalize_router_name(router: str) -> str:
        """Expand short router names to their canonical identifier."""
        key = router.strip().lower().replace("-", "_").replace(" ", "_")
        if key.endswith("_router"):
            return key
        return f"{key}_router"

    def _default_router_payload(
        self,
        goal: IngestionGoal,
        router: str,
    ) -> Dict[str, Any]:
        """Derive a minimal router payload from an ingested goal."""
        text = (goal.description or goal.raw_prompt).strip()
        payloads: Dict[str, Dict[str, Any]] = {
            "quantum_router": {
                "qasm_batch": [text],
                "target_backends": ["statevector_sim"],
                "precision_target": 1e-6,
            },
            "atomistic_router": {
                "atoms_batch": [{"goal": text}],
                "calculator_chain": ["mlip", "dft"],
                "live_sensor_feed": None,
            },
            "hpc_router": {
                "execution_segments": [{"goal": text}],
                "target_hpc_matrix": ["local"],
            },
            "wetlab_router": {
                "protocol_rdf_graphs": [{"goal": text}],
                "facility_matrix": ["simulated_facility"],
            },
            "sensing_router": {
                "bbox": None,
                "datetime_range": {},
                "collections": [text],
                "auto_inject_simulators": True,
            },
            "beamline_router": {
                "scan_plans": [{"goal": text}],
                "beamline_nodes": ["simulated_beamline"],
            },
            "free_grid_router": {
                "executable_cmds": [f"# simulated task: {text[:180]}"],
                "input_matrix": [],
                "target_pools": ["local_pool"],
            },
            "multiphysics_router": {
                "mesh_files": [{"goal": text}],
                "physics_domains": ["thermal", "structural"],
                "coupled_solvers": ["fenics"],
            },
            "digital_twin_router": {
                "usd_scenes": [{"goal": text}],
                "physics_engines": ["physx"],
                "dt": 0.01,
            },
            "biomolecular_router": {
                "pdb_ids": [],
                "pipeline_steps": [text],
                "forcefields": ["amber"],
            },
        }
        return dict(payloads.get(router, {"goal": text}))

    def dispatch_goal(
        self,
        goal_id: str,
        router: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        drive_engine: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Dispatch an ingested goal to one of the ten FastMCP science routers.

        Args:
            goal_id: Identifier of the ingested goal.
            router: Optional explicit router; otherwise resolved from the goal.
            payload: Optional router payload; otherwise derived from the goal.
            drive_engine: Optional discipline engine to receive router telemetry.

        Returns:
            Dict with ``handled``, ``router``, and the router ``result``. Goal
            lifecycle status is left to the caller.
        """
        with self._lock:
            goal = self._goals.get(goal_id)
        if goal is None:
            return {
                "handled": False,
                "router": router,
                "error": f"Unknown goal '{goal_id}'.",
            }

        selected = router or self.resolve_router(goal)
        if not selected:
            return {"handled": False, "router": None, "reason": "no_router_match"}
        selected = self._normalize_router_name(selected)

        try:
            from science_mcp import dispatch_router  # type: ignore[import-not-found]
        except Exception as exc:
            return {
                "handled": False,
                "router": selected,
                "error": f"science_mcp unavailable: {exc}",
            }

        effective_payload = (
            payload
            if payload is not None
            else self._default_router_payload(goal, selected)
        )
        result = dispatch_router(
            selected,
            payload=effective_payload,
            drive_engine=drive_engine,
        )

        with self._lock:
            goal.metadata["router"] = selected
            goal.metadata["last_router_dispatch"] = {
                "success": bool(result.get("success")),
                "execution_mode": result.get("execution_mode"),
                "latency_seconds": result.get("latency_seconds"),
                "numerical_residual": result.get("numerical_residual"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._router_dispatch_count += 1
        return {"handled": True, "router": selected, "result": result}

    def get_router_histogram(self) -> Dict[str, int]:
        """Count ingested goals assigned to each science router."""
        histogram: Dict[str, int] = {}
        with self._lock:
            goals = list(self._goals.values())
        for goal in goals:
            assigned = (goal.metadata or {}).get("router")
            if assigned:
                key = str(assigned)
                histogram[key] = histogram.get(key, 0) + 1
        return histogram

    def get_summary(self) -> Dict[str, Any]:
        """Return a structured summary of the current goal queue."""
        with self._lock:
            goals = list(self._goals.values())
            dispatch_count = self._router_dispatch_count
        total = len(goals)
        counts: Dict[str, int] = {s.value: 0 for s in GoalStatus}
        for g in goals:
            counts[g.status.value] += 1
        return {
            "total_goals": total,
            "status_counts": counts,
            "queued": counts[GoalStatus.QUEUED.value],
            "active": counts[GoalStatus.ACTIVE.value],
            "completed": counts[GoalStatus.COMPLETED.value],
            "failed": counts[GoalStatus.FAILED.value],
            "discovery_tracks": self.taxonomy.summarize(goals),
            "science_router_hints": self.get_router_histogram(),
            "router_dispatches": dispatch_count,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _classify_vector(self, prompt: str) -> GoalVector:
        """Auto-classify a goal prompt to one of the three execution vectors."""
        lower = prompt.lower()
        proof_hits = sum(1 for kw in self._FORMAL_PROOF_KEYWORDS if kw in lower)
        code_hits = sum(1 for kw in self._ALGORITHMIC_CODE_KEYWORDS if kw in lower)
        if proof_hits >= code_hits and proof_hits > 0:
            return GoalVector.FORMAL_PROOF
        if code_hits > 0:
            return GoalVector.ALGORITHMIC_CODE
        return GoalVector.FRONTIER_EXPLORE

    @staticmethod
    def _derive_title(prompt: str) -> str:
        """Derive a short title from the first sentence or 72 characters."""
        first_sentence = prompt.split(".")[0].strip()
        return first_sentence[:72] if first_sentence else prompt[:72]

    @staticmethod
    def _decompose_into_subtasks(
        goal_id: str,
        raw_prompt: str,
        vector: GoalVector,
        multi_vector: bool,
    ) -> List[str]:
        """
        Auto-decompose a goal into executable sub-task descriptors.
        In multi-vector (open-frontier) mode, sub-tasks are generated across
        all three execution vectors to ensure comprehensive coverage.
        """
        sub_tasks: List[str] = []

        if multi_vector or vector == GoalVector.FORMAL_PROOF:
            sub_tasks.append(
                f"[{goal_id}][FORMAL_PROOF] Formalise and prove the core assertion: {raw_prompt[:120]}"
            )
        if multi_vector or vector == GoalVector.ALGORITHMIC_CODE:
            sub_tasks.append(
                f"[{goal_id}][ALGORITHMIC_CODE] Design and benchmark a novel algorithm for: {raw_prompt[:120]}"
            )
        if multi_vector or vector == GoalVector.FRONTIER_EXPLORE:
            sub_tasks.append(
                f"[{goal_id}][FRONTIER_EXPLORE] Explore open-ended implications and adjacent discoveries for: {raw_prompt[:120]}"
            )

        return sub_tasks


# ---------------------------------------------------------------------------
# Asynchronous Continuous Goal Streaming Pipeline
# ---------------------------------------------------------------------------

class GoalStreamLedger:
    """Persistent deduplication ledger for stream-generated goals.

    Each generated tactical permutation is identified by a stable blake2b hash
    of (objective, level, vector, phase, state variation). The ledger survives
    producer passes and relay handoffs, so the same permutation is never
    enqueued twice while the remaining space is flooded at full throughput.

    The ledger is a JSON state cache (atomic temp-file replace) rather than
    SQLite so it can be committed alongside the execution state and recovered
    by the next GitHub Actions runner.
    """

    VERSION = 1

    def __init__(self, path: Union[str, Path] = ".sask_goal_ledger.json") -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._records: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    @staticmethod
    def hash_key(*parts: Any) -> str:
        """Return a deterministic 128-bit hash for an ordered permutation key."""
        canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).hexdigest()

    def load(self) -> int:
        """Lazily load the ledger from disk, tolerating malformed caches."""
        with self._lock:
            if self._loaded:
                return len(self._records)
            self._loaded = True
            if not self.path.is_file():
                return 0
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                records = raw.get("records", {}) if isinstance(raw, dict) else {}
                if isinstance(records, dict):
                    self._records = {
                        str(key): dict(value)
                        for key, value in records.items()
                        if isinstance(value, dict)
                    }
            except (json.JSONDecodeError, OSError):
                self._records = {}
            return len(self._records)

    def contains(self, key: str) -> bool:
        """True when the permutation hash has already been generated."""
        with self._lock:
            return key in self._records

    def register(self, key: str, record: Dict[str, Any]) -> bool:
        """Record a new permutation hash. Returns False when already present."""
        with self._lock:
            if key in self._records:
                return False
            payload = dict(record)
            payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            payload.setdefault("status", GoalStatus.QUEUED.value)
            self._records[key] = payload
            return True

    def update(self, key: str, **fields: Any) -> bool:
        """Merge lifecycle fields into an existing ledger record."""
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return False
            record.update(fields)
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            return True

    def save(self) -> Path:
        """Atomically persist the ledger to disk."""
        with self._lock:
            payload = {
                "version": self.VERSION,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "count": len(self._records),
                "records": self._records,
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(self.path)
        return self.path

    def stats(self) -> Dict[str, Any]:
        """Aggregate ledger entries by lifecycle status and expansion level."""
        with self._lock:
            records = list(self._records.values())
        by_status: Dict[str, int] = {}
        by_level: Dict[str, int] = {}
        for record in records:
            status = str(record.get("status", "UNKNOWN"))
            by_status[status] = by_status.get(status, 0) + 1
            level = str(record.get("level", "-"))
            by_level[level] = by_level.get(level, 0) + 1
        return {"entries": len(records), "by_status": by_status, "by_level": by_level}

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


class GoalStreamingPipeline:
    """Asynchronous continuous producer/consumer pipeline for goal generation.

    A daemon producer thread continuously expands every submitted primary
    objective into the exhaustive cartesian space of tactical permutations and
    enqueues novel goals into a bounded priority queue. The core execution loop
    is a consumer: it drains only as many goals as it has free worker slots and
    releases each slot the moment that goal finishes, so generation never waits
    on execution and execution never waits on generation.

    Expansion levels (complete permutations — no random sampling):
        L0: primary objective                          (1)
        L1: phases                                     (5)
        L2: vectors x phases                           (15)
        L3: vectors x phases x state variations        (75)

    Deduplication is enforced by :class:`GoalStreamLedger`, which is consulted
    before ingest so repeated producer passes and relay handoffs are idempotent.
    """

    VERSION = 1
    STATE_VARIATIONS: Tuple[str, ...] = (
        "BASELINE",
        "HIGH_PRECISION",
        "FAULT_TOLERANT",
        "ADVERSARIAL",
        "LOW_LATENCY",
    )
    _PHASE_ACTIONS: Dict[TacticalPhase, str] = {
        TacticalPhase.RECONNAISSANCE: "map constraints, inputs and failure modes for",
        TacticalPhase.TRANSFORMATION: "construct and mutate candidate solutions for",
        TacticalPhase.VERIFICATION: "formally verify and stress-test",
        TacticalPhase.CONVERGENCE: "resolve residuals and converge execution for",
        TacticalPhase.MASTERY: "generalize, benchmark and master",
    }
    _VARIATION_MODIFIERS: Dict[str, str] = {
        "BASELINE": "under baseline operating constraints",
        "HIGH_PRECISION": "under high-precision numerical tolerances",
        "FAULT_TOLERANT": "under injected fault and recovery conditions",
        "ADVERSARIAL": "under adversarial and worst-case inputs",
        "LOW_LATENCY": "under low-latency execution budgets",
    }

    def __init__(
        self,
        engine: GoalIngestionEngine,
        ledger_path: Union[str, Path] = ".sask_goal_ledger.json",
        state_path: Union[str, Path] = ".sask_goal_stream.json",
        max_level: int = 3,
        max_queue_size: int = 4096,
        max_concurrency: int = 4,
        producer_poll_seconds: float = 1.0,
    ) -> None:
        self.engine = engine
        self.ledger = GoalStreamLedger(ledger_path)
        self.state_path = Path(state_path)
        self.max_level = max(1, min(3, int(max_level)))
        self.max_concurrency = max(1, int(max_concurrency))
        self.producer_poll_seconds = max(0.05, float(producer_poll_seconds))
        self._queue: "queue.PriorityQueue[Tuple[int, int, str]]" = queue.PriorityQueue(
            maxsize=max(1, int(max_queue_size))
        )
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._objectives: Dict[str, Dict[str, Any]] = {}
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._in_flight: Dict[str, Dict[str, Any]] = {}
        self._sequence = 0
        self._producer_passes = 0
        self._revision = 0
        self._last_scanned_revision = -1
        self._produced = 0
        self._duplicates = 0
        self._completed = 0
        self._failed = 0
        self._level_counts: Dict[str, int] = {}
        self._restored = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True while the producer daemon thread is alive."""
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def start(self, resume: bool = True) -> Dict[str, Any]:
        """Load the ledger/state, restore pending work, and start producing.

        Args:
            resume: When True, requeue goals left pending or in flight by a
                previous runner before the producer begins.

        Returns:
            Telemetry including how much state was resumed.
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"started": False, "already_running": True, **self.get_telemetry()}

            self.ledger.load()
            resumed = self._restore_state() if resume and not self._restored else {
                "objectives": 0,
                "requeued": 0,
            }
            self._restored = True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._producer_loop,
                name="sask-goal-producer",
                daemon=True,
            )
            self._thread.start()
        return {"started": True, "resumed": resumed, **self.get_telemetry()}

    def stop(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Signal the producer to halt, join it, and persist stream state."""
        with self._lock:
            thread = self._thread
        self._stop.set()
        self._wake.set()
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
        self.persist()
        return self.get_telemetry()

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def submit_objective(
        self,
        objective: str,
        level: Optional[int] = None,
        priority: Optional[GoalPriority] = None,
        objective_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Register a primary objective for exhaustive level-wise expansion.

        The objective is expanded to every permutation from level 0 up to
        ``level`` by the producer thread. Submissions are idempotent: the same
        objective text yields the same objective id and is not re-registered.
        """
        text = str(objective).strip()
        if not text:
            raise ValueError("objective must be a non-empty string")
        effective_level = self.max_level if level is None else max(0, min(self.max_level, int(level)))
        obj_id = objective_id or f"obj-{GoalStreamLedger.hash_key('objective', text)[:12]}"
        record = {
            "id": obj_id,
            "objective": text,
            "level": effective_level,
            "priority": (priority or GoalPriority.NORMAL).name,
            "metadata": dict(metadata or {}),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            existing = self._objectives.get(obj_id)
            if existing is not None:
                return {
                    "objective_id": obj_id,
                    "level": existing["level"],
                    "new": False,
                }
            self._objectives[obj_id] = record
            self._revision += 1
        self._wake.set()
        return {"objective_id": obj_id, "level": effective_level, "new": True}

    def drain(self, limit: Optional[int] = None) -> List[IngestionGoal]:
        """Non-blocking consumer pull bounded by free worker slots.

        Args:
            limit: Optional additional cap for callers that only want a batch.

        Returns:
            Goals marked ACTIVE and admitted into the in-flight registry.
        """
        with self._lock:
            capacity = self.max_concurrency - len(self._in_flight)
        if limit is not None:
            capacity = min(capacity, max(0, int(limit)))

        goals: List[IngestionGoal] = []
        while capacity > 0:
            try:
                _score, _seq, goal_id = self._queue.get_nowait()
            except queue.Empty:
                break

            goal = self.engine.get_goal(goal_id)
            if goal is None or goal.status not in (GoalStatus.QUEUED, GoalStatus.ACTIVE):
                with self._lock:
                    self._pending.pop(goal_id, None)
                continue

            goal.status = GoalStatus.ACTIVE
            with self._lock:
                item = self._pending.pop(goal_id, None) or {"goal_id": goal_id}
                self._in_flight[goal_id] = {
                    **item,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            goals.append(goal)
            capacity -= 1
        return goals

    def complete(self, goal_id: str, success: bool, error: Optional[str] = None) -> Dict[str, Any]:
        """Release a worker slot and finalize the goal and its ledger record."""
        with self._lock:
            item = self._in_flight.pop(goal_id, None) or {}
            if success:
                self._completed += 1
            else:
                self._failed += 1

        if success:
            self.engine.mark_goal_complete(goal_id)
        else:
            self.engine.mark_goal_failed(goal_id, error or "execution failed")

        key = str(item.get("hash") or "")
        if not key:
            goal = self.engine.get_goal(goal_id)
            key = str(((goal.metadata or {}).get("stream") or {}).get("hash", "")) if goal else ""
        if key:
            self.ledger.update(
                key,
                status=GoalStatus.COMPLETED.value if success else GoalStatus.FAILED.value,
                error=error,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        return {"goal_id": goal_id, "success": bool(success), "freed_slot": True}

    # ------------------------------------------------------------------
    # Telemetry / persistence
    # ------------------------------------------------------------------

    @property
    def queue_depth(self) -> int:
        """Number of goals waiting in the priority queue."""
        return self._queue.qsize()

    @property
    def in_flight_count(self) -> int:
        """Goals currently admitted to workers and awaiting completion."""
        with self._lock:
            return len(self._in_flight)

    @property
    def available_slots(self) -> int:
        """Worker slots currently free to accept another goal."""
        return max(0, self.max_concurrency - self.in_flight_count)

    def wait_for_items(self, count: int = 1, timeout: float = 10.0) -> bool:
        """Block until at least ``count`` goals are queued or the timeout elapses."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if self._queue.qsize() >= count:
                return True
            time.sleep(0.01)
        return self._queue.qsize() >= count

    def get_telemetry(self) -> Dict[str, Any]:
        """Return a structured snapshot of producer/consumer and ledger state."""
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "max_level": self.max_level,
                "max_concurrency": self.max_concurrency,
                "queue_depth": self._queue.qsize(),
                "in_flight": len(self._in_flight),
                "available_slots": max(0, self.max_concurrency - len(self._in_flight)),
                "objectives": len(self._objectives),
                "producer_passes": self._producer_passes,
                "produced": self._produced,
                "duplicates_suppressed": self._duplicates,
                "completed": self._completed,
                "failed": self._failed,
                "level_counts": dict(self._level_counts),
                "ledger": self.ledger.stats(),
                "ledger_path": str(self.ledger.path),
                "state_path": str(self.state_path),
            }

    def persist(self) -> Path:
        """Atomically serialize the queue, in-flight work, and ledger for handoff."""
        self.ledger.save()
        with self._lock:
            pending = list(self._pending.values())
            in_flight = list(self._in_flight.values())
            payload = {
                "version": self.VERSION,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "objectives": self._objectives,
                "pending": pending,
                "in_flight": in_flight,
                "stats": {
                    "produced": self._produced,
                    "duplicates_suppressed": self._duplicates,
                    "completed": self._completed,
                    "failed": self._failed,
                    "producer_passes": self._producer_passes,
                    "level_counts": dict(self._level_counts),
                },
            }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(self.state_path)
        return self.state_path

    # ------------------------------------------------------------------
    # Producer internals
    # ------------------------------------------------------------------

    def _producer_loop(self) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    objectives = list(self._objectives.values())
                    revision = self._revision

                # Idle until a new objective is submitted: the permutation space
                # is already exhausted and ledger-checked, so rescanning would
                # only produce duplicate suppressions.
                if objectives and revision == self._last_scanned_revision:
                    self._wake.wait(timeout=self.producer_poll_seconds)
                    self._wake.clear()
                    continue

                for objective in objectives:
                    if self._stop.is_set():
                        break
                    for level in range(0, int(objective["level"]) + 1):
                        for spec in self._expand_level(objective, level):
                            if self._stop.is_set():
                                break
                            self._enqueue_spec(objective, spec, level)
                        self.ledger.save()

                with self._lock:
                    self._producer_passes += 1
                    self._last_scanned_revision = revision
                try:
                    self.persist()
                except OSError:
                    pass
        finally:
            try:
                self.ledger.save()
            except OSError:
                pass

    def _expand_level(self, objective: Dict[str, Any], level: int) -> List[Dict[str, Any]]:
        """Generate every permutation for one expansion level (no sampling)."""
        base = str(objective["objective"])
        specs: List[Dict[str, Any]] = []

        if level == 0:
            specs.append({
                "raw_prompt": base,
                "vector": self.engine._classify_vector(base),
                "phase": "PRIMARY",
                "variation": "PRIMARY",
                "title": base[:72],
            })
            return specs

        base_vector = self.engine._classify_vector(base)
        for phase in TacticalPhase:
            action = self._PHASE_ACTIONS[phase]
            if level == 1:
                specs.append({
                    "raw_prompt": f"[L1][{phase.value}] {action} :: {base}",
                    "vector": base_vector,
                    "phase": phase.value,
                    "variation": "BASELINE",
                    "title": f"[L1][{phase.value}] {base[:48]}",
                })
                continue

            for vector in GoalVector:
                if level == 2:
                    specs.append({
                        "raw_prompt": (
                            f"[L2][{vector.value}][{phase.value}] {action} :: {base} "
                            f"under the {vector.value} execution vector"
                        ),
                        "vector": vector,
                        "phase": phase.value,
                        "variation": "BASELINE",
                        "title": f"[L2][{vector.value}][{phase.value}] {base[:40]}",
                    })
                    continue

                for variation in self.STATE_VARIATIONS:
                    specs.append({
                        "raw_prompt": (
                            f"[L3][{vector.value}][{phase.value}][{variation}] {action} :: {base} "
                            f"under the {vector.value} execution vector "
                            f"{self._VARIATION_MODIFIERS[variation]}"
                        ),
                        "vector": vector,
                        "phase": phase.value,
                        "variation": variation,
                        "title": f"[L3][{vector.value}][{phase.value}][{variation}] {base[:32]}",
                    })
        return specs

    def _enqueue_spec(
        self,
        objective: Dict[str, Any],
        spec: Dict[str, Any],
        level: int,
    ) -> bool:
        """Deduplicate, ingest, and enqueue one generated permutation."""
        key = GoalStreamLedger.hash_key(
            objective["id"],
            level,
            spec["vector"].value,
            spec["phase"],
            spec["variation"],
        )
        if self.ledger.contains(key):
            with self._lock:
                self._duplicates += 1
            return False

        goal_id = f"strm-{key[:12]}"
        priority = GoalPriority[objective.get("priority", GoalPriority.NORMAL.name)]
        goal = self.engine.ingest_goal(
            raw_prompt=spec["raw_prompt"],
            vector=spec["vector"],
            priority=priority,
            discipline_weight=1.0,
            title=spec["title"],
            goal_id=goal_id,
        )

        stream_meta = {
            "objective_id": objective["id"],
            "objective": objective["objective"][:180],
            "level": level,
            "phase": spec["phase"],
            "variation": spec["variation"],
            "hash": key,
        }
        goal.metadata.setdefault("stream", {}).update(stream_meta)
        router = self.engine.resolve_router(goal)
        if router:
            goal.metadata["router"] = router

        score = -int(priority.value * 1000 + (self.max_level - level) * 50)
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        item = {
            "goal_id": goal_id,
            "hash": key,
            "title": spec["title"],
            "raw_prompt": spec["raw_prompt"],
            "vector": spec["vector"].value,
            "priority": priority.name,
            "discipline_weight": 1.0,
            "level": level,
            "phase": spec["phase"],
            "variation": spec["variation"],
            "score": score,
            "sequence": sequence,
            "metadata": {"stream": stream_meta, "router": goal.metadata.get("router")},
        }

        while not self._stop.is_set():
            try:
                self._queue.put((score, sequence, goal_id), timeout=0.5)
                break
            except queue.Full:
                continue
        else:
            return False

        self.ledger.register(key, {
            "goal_id": goal_id,
            "objective_id": objective["id"],
            "level": level,
            "vector": spec["vector"].value,
            "phase": spec["phase"],
            "variation": spec["variation"],
            "status": GoalStatus.QUEUED.value,
        })
        with self._lock:
            self._pending[goal_id] = item
            self._produced += 1
            level_key = str(level)
            self._level_counts[level_key] = self._level_counts.get(level_key, 0) + 1
        return True

    def _restore_state(self) -> Dict[str, Any]:
        """Requeue pending and interrupted goals from the last handoff."""
        if not self.state_path.is_file():
            return {"objectives": 0, "requeued": 0}

        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"objectives": 0, "requeued": 0}

        with self._lock:
            for obj_id, record in (state.get("objectives") or {}).items():
                if isinstance(record, dict) and obj_id not in self._objectives:
                    self._objectives[str(obj_id)] = record
            stats = state.get("stats") or {}
            self._produced = max(self._produced, int(stats.get("produced", 0)))
            self._duplicates = max(self._duplicates, int(stats.get("duplicates_suppressed", 0)))
            self._completed = max(self._completed, int(stats.get("completed", 0)))
            self._failed = max(self._failed, int(stats.get("failed", 0)))
            self._producer_passes = max(self._producer_passes, int(stats.get("producer_passes", 0)))
            for level_key, count in (stats.get("level_counts") or {}).items():
                self._level_counts[str(level_key)] = max(
                    self._level_counts.get(str(level_key), 0), int(count)
                )

        items = list(state.get("pending") or []) + list(state.get("in_flight") or [])
        requeued = 0
        max_sequence = 0
        for item in items:
            if not isinstance(item, dict) or not item.get("goal_id"):
                continue
            try:
                goal_id = str(item["goal_id"])
                goal = self.engine.get_goal(goal_id)
                if goal is None:
                    goal = self.engine.ingest_goal(
                        raw_prompt=str(item["raw_prompt"]),
                        vector=GoalVector(item["vector"]),
                        priority=GoalPriority[item.get("priority", GoalPriority.NORMAL.name)],
                        discipline_weight=float(item.get("discipline_weight", 1.0)),
                        title=item.get("title"),
                        goal_id=goal_id,
                    )
                    goal.metadata.update(item.get("metadata") or {})
                else:
                    self.engine.mark_goal_queued(goal_id)

                score = int(item.get("score", 0))
                sequence = int(item.get("sequence", 0))
                max_sequence = max(max_sequence, sequence)
                self._queue.put_nowait((score, sequence, goal_id))
                with self._lock:
                    self._pending[goal_id] = item
                requeued += 1
            except Exception:
                continue

        with self._lock:
            self._sequence = max(self._sequence, max_sequence) + 1
            objective_count = len(self._objectives)
        return {"objectives": objective_count, "requeued": requeued}


# ---------------------------------------------------------------------------
# Legacy TelosEngine (preserving all existing public API)
# ---------------------------------------------------------------------------

class TelosEngine:
    """Milestone and task-sequencing engine with adaptive objective generation.

    Embeds a GoalIngestionEngine as ``self.goal_engine`` for universal
    goal-agnostic ingestion alongside existing milestone tracking.
    """

    def __init__(
        self,
        stagnation_threshold: int = 3,
        error_tolerance_delta: float = 0.001,
        workspace_root: Optional[Union[str, Path]] = None,
        stream_max_level: int = 3,
        stream_concurrency: int = 4,
    ) -> None:
        self._milestones: Dict[str, Milestone] = {}
        self._macro_objectives: Dict[str, MacroObjective] = {}
        self._execution_history: List[Dict[str, Any]] = []
        self._error_history: List[float] = []
        self._reset_events: List[ResetEvent] = []
        self.stagnation_threshold = stagnation_threshold
        self.error_tolerance_delta = error_tolerance_delta

        # Universal goal ingestion engine — wired into TelosEngine
        self.goal_engine = GoalIngestionEngine()
        self.discovery_taxonomy = self.goal_engine.taxonomy

        # Asynchronous continuous goal streaming pipeline. Inert until started;
        # paths are rooted at the workspace so relay handoffs persist with it.
        root = Path(workspace_root) if workspace_root else Path.cwd()
        self.stream = GoalStreamingPipeline(
            self.goal_engine,
            ledger_path=root / ".sask_goal_ledger.json",
            state_path=root / ".sask_goal_stream.json",
            max_level=stream_max_level,
            max_concurrency=stream_concurrency,
        )

    def register_milestone(
        self,
        milestone_id: str,
        title: str,
        description: str = "",
        depends_on: Optional[List[str]] = None,
        action: Optional[Callable[..., Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        phase: TacticalPhase = TacticalPhase.RECONNAISSANCE,
        weight: float = 1.0,
    ) -> Milestone:
        """Register an atomic milestone or sub-quest into the engine."""
        if milestone_id in self._milestones:
            raise ValueError(f"Milestone '{milestone_id}' is already registered.")

        milestone = Milestone(
            id=milestone_id,
            title=title,
            description=description,
            depends_on=depends_on or [],
            action=action,
            metadata=metadata or {},
            phase=phase,
            weight=weight,
        )
        self._milestones[milestone_id] = milestone
        return milestone

    def register_macro_objective(
        self,
        objective_id: str,
        title: str,
        description: str = "",
        target_mastery: float = 1.0,
        stagnation_limit: Optional[int] = None,
    ) -> MacroObjective:
        """Register a high-level macro-objective to coordinate tactical sub-quests."""
        if objective_id in self._macro_objectives:
            raise ValueError(f"Macro objective '{objective_id}' is already registered.")

        obj = MacroObjective(
            id=objective_id,
            title=title,
            description=description,
            target_mastery=target_mastery,
            stagnation_limit=stagnation_limit or self.stagnation_threshold,
        )
        self._macro_objectives[objective_id] = obj
        return obj

    def issue_phased_tactical_subquests(
        self,
        macro_objective_id: str,
        subquest_specs: List[Dict[str, Any]],
    ) -> List[Milestone]:
        """Generate and sequence phased tactical sub-quests for a macro-objective."""
        macro = self._macro_objectives.get(macro_objective_id)
        if not macro:
            raise KeyError(f"Macro objective '{macro_objective_id}' not found.")

        created_milestones: List[Milestone] = []
        for spec in subquest_specs:
            q_id = spec["id"]
            phase_val = spec.get("phase", TacticalPhase.RECONNAISSANCE)
            if isinstance(phase_val, str):
                phase_val = TacticalPhase(phase_val)

            milestone = self.register_milestone(
                milestone_id=q_id,
                title=spec["title"],
                description=spec.get("description", ""),
                depends_on=spec.get("depends_on", []),
                metadata=spec.get("metadata", {}),
                phase=phase_val,
                weight=float(spec.get("weight", 1.0)),
            )
            macro.sub_quest_ids.append(q_id)
            created_milestones.append(milestone)

        return created_milestones

    def get_milestone(self, milestone_id: str) -> Optional[Milestone]:
        """Retrieve a milestone by its unique identifier."""
        return self._milestones.get(milestone_id)

    def get_all_milestones(self) -> List[Milestone]:
        """Return all registered milestones."""
        return list(self._milestones.values())

    def get_runnable_milestones(self) -> List[Milestone]:
        """Return milestones that are PENDING and all dependencies are COMPLETED."""
        runnable: List[Milestone] = []
        for ms in self._milestones.values():
            if ms.status != MilestoneStatus.PENDING:
                continue

            deps_satisfied = True
            for dep_id in ms.depends_on:
                dep = self._milestones.get(dep_id)
                if not dep or dep.status != MilestoneStatus.COMPLETED:
                    deps_satisfied = False
                    break

            if deps_satisfied:
                runnable.append(ms)

        return runnable

    def update_status(
        self,
        milestone_id: str,
        status: MilestoneStatus,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Milestone:
        """Update the status of a registered milestone and recalculate mastery."""
        ms = self._milestones.get(milestone_id)
        if not ms:
            raise KeyError(f"Milestone '{milestone_id}' not found.")

        if status == MilestoneStatus.RUNNING:
            ms.mark_started()
        elif status == MilestoneStatus.COMPLETED:
            ms.mark_completed(metadata)
        elif status == MilestoneStatus.FAILED:
            ms.mark_failed(error or "Unknown failure")
        elif status == MilestoneStatus.SKIPPED:
            ms.mark_skipped(error or "")

        self._execution_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "milestone_id": milestone_id,
            "status": status.value,
            "error": error,
        })

        self._refresh_mastery_scores()
        return ms

    def _refresh_mastery_scores(self) -> None:
        """Update mastery progression scores across all active macro-objectives."""
        for macro in self._macro_objectives.values():
            if not macro.sub_quest_ids:
                macro.current_mastery = 0.0
                continue

            total_weight = 0.0
            completed_weight = 0.0
            for q_id in macro.sub_quest_ids:
                ms = self._milestones.get(q_id)
                if ms:
                    total_weight += ms.weight
                    if ms.status == MilestoneStatus.COMPLETED:
                        completed_weight += ms.weight

            macro.current_mastery = round(completed_weight / total_weight, 4) if total_weight > 0 else 0.0

    def record_error_metric(self, error_metric: float) -> Optional[ResetEvent]:
        """Record error metric from a cycle and trigger a reset if error reduction stagnates."""
        self._error_history.append(error_metric)

        if len(self._error_history) < self.stagnation_threshold:
            return None

        window = self._error_history[-self.stagnation_threshold:]
        initial_error = window[0]
        recent_delta = initial_error - window[-1]

        is_stagnant = (window[-1] > 0.0) and (abs(recent_delta) <= self.error_tolerance_delta)

        if is_stagnant:
            return self.trigger_metabolic_reset(
                reason=(
                    f"Error reduction stagnated over {self.stagnation_threshold} cycles "
                    f"(recent error: {window[-1]:.4f}, delta: {recent_delta:.4f})."
                )
            )

        return None

    def trigger_metabolic_reset(self, reason: str = "Stagnation threshold reached") -> ResetEvent:
        """Trigger a metabolic reset: clear search-basin, purge failed branches, recalibrate."""
        cleared_ids = self.clear_search_basin()
        current_error = self._error_history[-1] if self._error_history else 0.0

        event = ResetEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            reset_type="METABOLIC_RESET",
            reason=reason,
            cleared_milestone_ids=cleared_ids,
            previous_error_rate=current_error,
            recalibrated_state={
                "remaining_active_milestones": len(self.get_runnable_milestones()),
                "total_milestones": len(self._milestones),
                "stagnation_counter_reset": True,
            },
        )
        self._reset_events.append(event)
        return event

    def clear_search_basin(self, preserve_completed: bool = True) -> List[str]:
        """Clear search basin: reset failed or unstarted tasks to unblock alternate exploration paths."""
        cleared: List[str] = []
        for ms in self._milestones.values():
            if ms.status == MilestoneStatus.FAILED:
                ms.status = MilestoneStatus.PENDING
                ms.error_message = None
                ms.metadata["cleared_from_basin"] = True
                cleared.append(ms.id)
            elif not preserve_completed and ms.status == MilestoneStatus.COMPLETED:
                ms.status = MilestoneStatus.PENDING
                cleared.append(ms.id)

        self._refresh_mastery_scores()
        return cleared

    def calculate_system_mastery(self) -> float:
        """Calculate aggregate system mastery score across all macro objectives and milestones."""
        if self._macro_objectives:
            total_mastery = sum(m.current_mastery for m in self._macro_objectives.values())
            return round(total_mastery / len(self._macro_objectives), 4)

        total = len(self._milestones)
        if total == 0:
            return 1.0
        completed = sum(1 for m in self._milestones.values() if m.status == MilestoneStatus.COMPLETED)
        return round(completed / total, 4)

    def is_all_complete(self) -> bool:
        """Check if all milestones have completed successfully or skipped."""
        if not self._milestones:
            return True
        return all(
            ms.status in (MilestoneStatus.COMPLETED, MilestoneStatus.SKIPPED)
            for ms in self._milestones.values()
        )

    def has_failures(self) -> bool:
        """Check if any milestone encountered a failure."""
        return any(
            ms.status == MilestoneStatus.FAILED
            for ms in self._milestones.values()
        )

    def get_summary(self) -> Dict[str, Any]:
        """Generate a structured summary of milestones, macro-objectives, and telemetry."""
        counts = {s.value: 0 for s in MilestoneStatus}
        for ms in self._milestones.values():
            counts[ms.status.value] += 1

        total = len(self._milestones)
        completed_or_skipped = (
            counts[MilestoneStatus.COMPLETED.value] + counts[MilestoneStatus.SKIPPED.value]
        )
        progress = (completed_or_skipped / total * 100.0) if total > 0 else 100.0

        return {
            "total_milestones": total,
            "progress_percent": round(progress, 2),
            "system_mastery": self.calculate_system_mastery(),
            "status_breakdown": counts,
            "is_complete": self.is_all_complete(),
            "has_failures": self.has_failures(),
            "macro_objectives": [
                {
                    "id": mo.id,
                    "title": mo.title,
                    "mastery": mo.current_mastery,
                    "sub_quests": len(mo.sub_quest_ids),
                }
                for mo in self._macro_objectives.values()
            ],
            "metabolic_resets": len(self._reset_events),
            "goal_ingestion": self.goal_engine.get_summary(),
            "milestones": [
                {
                    "id": m.id,
                    "title": m.title,
                    "phase": m.phase.value,
                    "status": m.status.value,
                    "depends_on": m.depends_on,
                    "error": m.error_message,
                }
                for m in self._milestones.values()
            ],
        }
