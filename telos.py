"""Telos Engine: Task sequencing and milestone tracking engine.

Provides dependency-aware task sequencing, milestone state transitions,
and execution progress tracking for modular automation pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set


class MilestoneStatus(str, Enum):
    """Execution status for pipeline milestones."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class Milestone:
    """Represents an atomic task milestone in the automation lifecycle."""
    id: str
    title: str
    description: str = ""
    depends_on: List[str] = field(default_factory=list)
    status: MilestoneStatus = MilestoneStatus.PENDING
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


class TelosEngine:
    """Task sequencing and milestone tracking coordinator."""

    def __init__(self) -> None:
        self._milestones: Dict[str, Milestone] = {}
        self._execution_history: List[Dict[str, Any]] = []

    def register_milestone(
        self,
        milestone_id: str,
        title: str,
        description: str = "",
        depends_on: Optional[List[str]] = None,
        action: Optional[Callable[..., Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Milestone:
        """Register a new milestone into the engine."""
        if milestone_id in self._milestones:
            raise ValueError(f"Milestone '{milestone_id}' is already registered.")

        milestone = Milestone(
            id=milestone_id,
            title=title,
            description=description,
            depends_on=depends_on or [],
            action=action,
            metadata=metadata or {},
        )
        self._milestones[milestone_id] = milestone
        return milestone

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
        """Update the status of a registered milestone."""
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
        return ms

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
        """Generate a structured summary of milestones and progression metrics."""
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
            "status_breakdown": counts,
            "is_complete": self.is_all_complete(),
            "has_failures": self.has_failures(),
            "milestones": [
                {
                    "id": m.id,
                    "title": m.title,
                    "status": m.status.value,
                    "depends_on": m.depends_on,
                    "error": m.error_message,
                }
                for m in self._milestones.values()
            ],
        }
