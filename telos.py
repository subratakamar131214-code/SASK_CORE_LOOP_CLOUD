"""Telos Engine: Milestone and Task-Sequencing Engine.

Provides tactical sub-quest generation, macro-objective progression toward
system mastery, error-reduction stagnation detection, metabolic resets,
and search-basin clearing for autonomous execution cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set


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
        """Mark milestone as running."""
        self.status = MilestoneStatus.RUNNING
        self.started_at = datetime.now(timezone.utc).isoformat()

    def mark_completed(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Mark milestone as completed successfully."""
        self.status = MilestoneStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        if metadata:
            self.metadata.update(metadata)

    def mark_failed(self, error: str) -> None:
        """Mark milestone as failed with error context."""
        self.status = MilestoneStatus.FAILED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.error_message = error

    def mark_skipped(self, reason: str = "") -> None:
        """Mark milestone as skipped."""
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


class TelosEngine:
    """Milestone and task-sequencing engine with adaptive objective generation."""

    def __init__(self, stagnation_threshold: int = 3, error_tolerance_delta: float = 0.001) -> None:
        self._milestones: Dict[str, Milestone] = {}
        self._macro_objectives: Dict[str, MacroObjective] = {}
        self._execution_history: List[Dict[str, Any]] = []
        self._error_history: List[float] = []
        self._reset_events: List[ResetEvent] = []
        self.stagnation_threshold = stagnation_threshold
        self.error_tolerance_delta = error_tolerance_delta

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
        """Generate and sequence phased tactical sub-quests for a macro-objective.

        Each spec is expected to contain:
            - id: str
            - title: str
            - phase: TacticalPhase or str
            - depends_on: Optional[List[str]]
            - description: Optional[str]
            - weight: Optional[float]
        """
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

        # Check the recent stagnation window
        window = self._error_history[-self.stagnation_threshold:]
        initial_error = window[0]
        recent_delta = initial_error - window[-1]

        # Stagnation condition: errors are non-zero and not improving significantly
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

        # Fallback to simple milestone ratio if no macro objectives registered
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
