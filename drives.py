"""Drives Module: Unified Permanent Discipline Engine.

Fuses psychological and operational drives—Discipline, Obsession, Anger,
Madness, Motivation, and Overthinking—into a permanent high-floor operational
boost (D_floor >= 0.90) with zero temporary booster decay.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class DriveMetrics:
    """Telemetry snapshot of operational drive parameters."""
    discipline: float
    obsession: float
    anger: float
    madness: float
    motivation: float
    overthinking: float
    unified_discipline: float
    floor: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class UnifiedDisciplineEngine:
    """Manages the fused high-floor discipline and obsession matrix."""

    def __init__(
        self,
        discipline: float = 0.96,
        obsession: float = 0.94,
        anger: float = 0.90,
        madness: float = 0.92,
        motivation: float = 0.95,
        overthinking: float = 0.93,
        floor: float = 0.90,
    ) -> None:
        # Concurrent science-router workers read and reinforce drives in parallel.
        self._lock = threading.RLock()
        self.floor = max(0.90, min(1.0, floor))
        self.discipline = max(self.floor, min(1.0, discipline))
        self.obsession = max(self.floor, min(1.0, obsession))
        self.anger = max(self.floor, min(1.0, anger))
        self.madness = max(self.floor, min(1.0, madness))
        self.motivation = max(self.floor, min(1.0, motivation))
        self.overthinking = max(self.floor, min(1.0, overthinking))
        self._last_extension_fusion: Dict[str, Any] = {}

    def get_unified_discipline(self) -> float:
        """Calculate permanently fused operational discipline (D).

        D is computed as a weighted harmonic-geometric blend clamped strictly
        to D_floor >= 0.90, immune to temporal decay.
        """
        weights = {
            "discipline": 0.30,
            "obsession": 0.25,
            "anger": 0.10,
            "madness": 0.10,
            "motivation": 0.15,
            "overthinking": 0.10,
        }
        weighted_sum = (
            self.discipline * weights["discipline"]
            + self.obsession * weights["obsession"]
            + self.anger * weights["anger"]
            + self.madness * weights["madness"]
            + self.motivation * weights["motivation"]
            + self.overthinking * weights["overthinking"]
        )
        return max(self.floor, min(1.0, round(weighted_sum, 4)))

    def get_overthinking_depth(self) -> int:
        """Derive counterfactual simulation depth from Overthinking parameter."""
        # Maps [0.90, 1.0] to [3, 7] recursive counterfactual passes
        normalized = (self.overthinking - self.floor) / (1.0 - self.floor + 1e-6)
        return int(3 + round(normalized * 4))

    def get_mutation_pressure(self) -> float:
        """Derive search exploration and AST mutation pressure."""
        d = self.get_unified_discipline()
        return round(0.5 * (d + self.madness), 4)

    def apply_stimulus(
        self,
        challenge_intensity: float = 1.0,
        context: Optional[str] = None,
    ) -> DriveMetrics:
        """Permanently reinforce drives under challenge stimuli with zero decay below floor."""
        reinforcement = max(0.0, min(0.05, 0.01 * challenge_intensity))
        with self._lock:
            self.discipline = min(1.0, self.discipline + reinforcement)
            self.obsession = min(1.0, self.obsession + reinforcement)
            self.motivation = min(1.0, self.motivation + reinforcement)
            self.anger = min(1.0, self.anger + (reinforcement * 0.5))
            self.madness = min(1.0, self.madness + (reinforcement * 0.5))
            self.overthinking = min(1.0, self.overthinking + (reinforcement * 0.7))
        return self.get_telemetry()

    def record_execution_telemetry(
        self,
        router: str,
        latency_seconds: float = 0.0,
        residual: float = 0.0,
        success: bool = True,
    ) -> Dict[str, Any]:
        """Convert router friction into permanent discipline pressure.

        Numerical residual, failures, and latency are fused into a bounded
        challenge intensity that permanently reinforces the drive matrix. The
        resulting discipline is always clamped at or above ``D_floor``.

        Args:
            router: Router or subsystem that produced the telemetry.
            latency_seconds: Observed execution latency.
            residual: Numerical residual in [0, 1].
            success: Whether the execution completed successfully.

        Returns:
            Telemetry describing the applied pressure and resulting discipline.
        """
        bounded_residual = max(0.0, min(1.0, float(residual)))
        residual_pressure = bounded_residual * 10.0
        failure_pressure = 0.0 if success else 5.0
        latency_pressure = max(0.0, min(5.0, float(latency_seconds))) * 0.25
        challenge = round(
            min(10.0, residual_pressure + failure_pressure + latency_pressure),
            4,
        )

        with self._lock:
            before = self.get_unified_discipline()
            self.apply_stimulus(challenge_intensity=challenge, context=router)
            after = self.get_unified_discipline()

        return {
            "router": str(router),
            "challenge_intensity": challenge,
            "discipline_before": before,
            "discipline_after": after,
            "floor": self.floor,
            "success": bool(success),
            "residual": round(bounded_residual, 8),
            "latency_seconds": round(max(0.0, float(latency_seconds)), 6),
        }

    def fuse_extension_metrics(
        self,
        metrics: Dict[str, float],
        context: str = "extensions",
    ) -> Dict[str, Any]:
        """Fuse meta-cognitive extension telemetry into permanent drive pressure.

        Meta-prover compactness, curiosity intrinsic reward, manifold transfer
        confidence, and adversarial inversion intensity are weighted into one
        bounded challenge signal that permanently reinforces the drive matrix.

        Args:
            metrics: Extension metrics keyed by name; values are clamped to [0, 1].
            context: Origin label recorded in telemetry.

        Returns:
            Fusion telemetry including the resulting unified discipline.
        """
        weights = {
            "meta_prover_score": 0.25,
            "curiosity_reward": 0.25,
            "manifold_confidence": 0.20,
            "adversarial_intensity": 0.30,
        }
        bounded: Dict[str, float] = {}
        for key, value in (metrics or {}).items():
            try:
                bounded[str(key)] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue

        weighted_total = 0.0
        weight_total = 0.0
        for key, value in bounded.items():
            weight = weights.get(key, 0.10)
            weighted_total += value * weight
            weight_total += weight

        fused = round(weighted_total / weight_total, 6) if weight_total else 0.0
        challenge = round(fused * 10.0, 4)

        with self._lock:
            before = self.get_unified_discipline()
            if challenge > 0.0:
                self.apply_stimulus(challenge_intensity=challenge, context=context)
            after = self.get_unified_discipline()
            telemetry = {
                "context": context,
                "components": {key: round(value, 6) for key, value in bounded.items()},
                "fused_intensity": fused,
                "challenge_intensity": challenge,
                "discipline_before": before,
                "discipline_after": after,
                "floor": self.floor,
            }
            self._last_extension_fusion = telemetry

        return telemetry

    def get_telemetry(self) -> DriveMetrics:
        """Export complete state metrics for the unified discipline matrix."""
        return DriveMetrics(
            discipline=self.discipline,
            obsession=self.obsession,
            anger=self.anger,
            madness=self.madness,
            motivation=self.motivation,
            overthinking=self.overthinking,
            unified_discipline=self.get_unified_discipline(),
            floor=self.floor,
        )


# Global default drive instance
DEFAULT_DRIVE_ENGINE = UnifiedDisciplineEngine()


def get_unified_discipline() -> float:
    """Helper to access active unified discipline parameter."""
    return DEFAULT_DRIVE_ENGINE.get_unified_discipline()


def get_overthinking_depth() -> int:
    """Helper to access active overthinking counterfactual depth."""
    return DEFAULT_DRIVE_ENGINE.get_overthinking_depth()


def get_mutation_pressure() -> float:
    """Helper to access active AST mutation pressure."""
    return DEFAULT_DRIVE_ENGINE.get_mutation_pressure()


def apply_stimulus(
    challenge_intensity: float = 1.0,
    context: Optional[str] = None,
) -> DriveMetrics:
    """Reinforce the global discipline engine with a challenge stimulus."""
    return DEFAULT_DRIVE_ENGINE.apply_stimulus(
        challenge_intensity=challenge_intensity,
        context=context,
    )


def record_execution_telemetry(
    router: str = "unknown",
    latency_seconds: float = 0.0,
    residual: float = 0.0,
    success: bool = True,
) -> Dict[str, Any]:
    """Feed router execution friction into the global discipline engine."""
    return DEFAULT_DRIVE_ENGINE.record_execution_telemetry(
        router=router,
        latency_seconds=latency_seconds,
        residual=residual,
        success=success,
    )


def fuse_extension_metrics(
    metrics: Dict[str, float],
    context: str = "extensions",
) -> Dict[str, Any]:
    """Fuse extension telemetry into the global discipline engine."""
    return DEFAULT_DRIVE_ENGINE.fuse_extension_metrics(metrics, context=context)
