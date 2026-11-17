"""Telos Engine: Milestone and Task-Sequencing Engine with Universal Goal Ingestion.

Provides tactical sub-quest generation, macro-objective progression toward
system mastery, error-reduction stagnation detection, metabolic resets,
search-basin clearing, and an open-ended goal ingestion pipeline capable of
absorbing, vectorizing, and scheduling any arbitrary goal from the Goal Giver.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set


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

    def __init__(self) -> None:
        self._goals: Dict[str, IngestionGoal] = {}
        self.taxonomy = MacroDiscoveryTaxonomy()

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
    ) -> IngestionGoal:
        """
        Parse and vectorize any arbitrary goal string into an IngestionGoal.

        If no vector is specified, the engine auto-classifies the goal using
        keyword heuristics. Sub-tasks are auto-decomposed across all three
        execution vectors when no vector is specified (open-frontier mode).
        """
        goal_id = str(uuid.uuid4())[:12]
        effective_vector = vector or self._classify_vector(raw_prompt)
        effective_priority = priority or GoalPriority.NORMAL
        effective_title = title or self._derive_title(raw_prompt)
        effective_desc = description or raw_prompt[:256]
        effective_track = self.taxonomy.classify(raw_prompt, effective_vector)

        sub_tasks = self._decompose_into_subtasks(
            goal_id=goal_id,
            raw_prompt=raw_prompt,
            vector=effective_vector,
            multi_vector=(vector is None),
        )

        goal = IngestionGoal(
            id=goal_id,
            title=effective_title,
            description=effective_desc,
            vector=effective_vector,
            priority=effective_priority,
            raw_prompt=raw_prompt,
            sub_tasks=sub_tasks,
            discipline_weight=discipline_weight,
            discovery_track=effective_track,
        )
        self._goals[goal_id] = goal
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
        payload = [g.to_dict() for g in self._goals.values()]
        tmp_path = manifest_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(manifest_path)

    def get_priority_queue(self, discipline: float = 1.0) -> List[IngestionGoal]:
        """
        Return active (QUEUED or ACTIVE) goals sorted by effective priority score:
            score = priority.value * discipline_weight * discipline
        """
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
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.COMPLETED
            goal.completed_at = datetime.now(timezone.utc).isoformat()

    def mark_goal_failed(self, goal_id: str, error: str) -> None:
        """Mark a goal as failed with an error message."""
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.FAILED
            goal.error = error
            goal.completed_at = datetime.now(timezone.utc).isoformat()

    def get_all_goals(self) -> List[IngestionGoal]:
        """Return all ingested goals."""
        return list(self._goals.values())

    def get_goal(self, goal_id: str) -> Optional[IngestionGoal]:
        """Retrieve a goal by ID."""
        return self._goals.get(goal_id)

    def get_summary(self) -> Dict[str, Any]:
        """Return a structured summary of the current goal queue."""
        total = len(self._goals)
        counts: Dict[str, int] = {s.value: 0 for s in GoalStatus}
        for g in self._goals.values():
            counts[g.status.value] += 1
        return {
            "total_goals": total,
            "status_counts": counts,
            "queued": counts[GoalStatus.QUEUED.value],
            "active": counts[GoalStatus.ACTIVE.value],
            "completed": counts[GoalStatus.COMPLETED.value],
            "failed": counts[GoalStatus.FAILED.value],
            "discovery_tracks": self.taxonomy.summarize(self._goals.values()),
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
# Legacy TelosEngine (preserving all existing public API)
# ---------------------------------------------------------------------------

class TelosEngine:
    """Milestone and task-sequencing engine with adaptive objective generation.

    Embeds a GoalIngestionEngine as ``self.goal_engine`` for universal
    goal-agnostic ingestion alongside existing milestone tracking.
    """

    def __init__(self, stagnation_threshold: int = 3, error_tolerance_delta: float = 0.001) -> None:
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
