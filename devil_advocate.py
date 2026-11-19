"""Adversarial Counter-Intuition Injector.

A devil's-advocate sub-agent that watches the kernel's most stable telemetry.
When the system plateaus (local convergence) or stagnates, it inverts the
optimization assumptions behind the most entrenched metrics, emits adversarial
probes, and injects counter-goals into the goal engine and stream. Each probe
also applies permanent drive stimulus so escape pressure never decays.

The injector is deterministic, thread-safe, and persists its probe ledger for
24/7/365 container handoffs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("SASK.DevilAdvocate")

try:
    from telos import GoalPriority  # type: ignore[import-not-found]

    _TELOS_AVAILABLE = True
except Exception:  # pragma: no cover
    GoalPriority = None  # type: ignore[assignment]
    _TELOS_AVAILABLE = False

_MAX_PROBES = 192
_DEFAULT_WINDOW = 8

# Metric -> (assumption challenged, inversion directive, counter-goal template)
ASSUMPTIONS: Dict[str, Dict[str, str]] = {
    "system_mastery": {
        "assumption": "Rising system mastery monotonically implies real capability.",
        "inversion": "Actively lower reported mastery while preserving verified correctness to expose metric overfitting.",
        "counter_goal": (
            "Adversarially falsify the mastery metric: construct a probe where system_mastery "
            "decreases while verified correctness and residual quality stay fixed, then repair "
            "the underlying capability gap the metric was hiding."
        ),
    },
    "unified_discipline": {
        "assumption": "Higher unified discipline is always the optimal control input.",
        "inversion": "Drive discipline below its current plateau through deliberate exploratory slack and measure whether search diversity recovers.",
        "counter_goal": (
            "Invert the discipline assumption: schedule a bounded exploration window where the "
            "agent deliberately relaxes its highest-confidence heuristic, then quantify the "
            "information gained against the discipline that was temporarily sacrificed."
        ),
    },
    "discipline": {
        "assumption": "Higher discipline always produces better long-run outcomes.",
        "inversion": "Test a controlled slack regime to determine whether discipline has become a local-minimum trap.",
        "counter_goal": (
            "Run a controlled anti-discipline experiment: relax the dominant convergence heuristic "
            "for one cycle and search for a proof or optimization that the disciplined regime "
            "systematically skipped."
        ),
    },
    "success_rate": {
        "assumption": "Maximizing success rate is aligned with discovering novel capability.",
        "inversion": "Deliberately attempt goals with low prior success probability and optimize for information gain rather than pass rate.",
        "counter_goal": (
            "Invert the success-rate objective: select the three lowest-probability hypotheses from "
            "the curiosity engine and execute them under falsification criteria, reporting the "
            "information gain regardless of pass/fail."
        ),
    },
    "error_count": {
        "assumption": "Error count should always be minimized immediately.",
        "inversion": "Permit bounded, instrumented failures to reveal constraints that low-error search never observes.",
        "counter_goal": (
            "Challenge zero-error optimization: design a fault-injection experiment that surfaces "
            "latent invariants, then strengthen the verification gate from the observed failure mode."
        ),
    },
    "residual": {
        "assumption": "Numerical residual should always decrease monotonically.",
        "inversion": "Increase residual deliberately in a controlled subspace to test whether the solver is trapped in a narrow basin.",
        "counter_goal": (
            "Invert residual minimization: perturb the current solver state along its least-sensitive "
            "direction, measure the resulting basin topology, and transfer any discovered escape "
            "strategy back to the primary numerical pipeline."
        ),
    },
    "latency": {
        "assumption": "Lower latency is always better.",
        "inversion": "Spend extra latency on speculative deep reasoning to test whether speed has crowded out insight.",
        "counter_goal": (
            "Challenge latency minimization: allocate a high-latency deep-reasoning budget to one goal "
            "and compare its proof compactness and structural insight against the fast path."
        ),
    },
    "isomorphisms_found": {
        "assumption": "More discovered isomorphisms are inherently valuable.",
        "inversion": "Minimize isomorphism count and force the bridge to justify each mapping by transferable utility.",
        "counter_goal": (
            "Adversarially prune the analogical bridge: retain only isomorphisms whose transferred "
            "solutions improve a measurable invariant, then re-run cross-domain transfer on the survivors."
        ),
    },
}


def _hash_unit(*parts: Any) -> float:
    digest = hashlib.blake2b(
        "\x1f".join(str(part) for part in parts).encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class AdversarialProbe:
    """One counter-intuition injection targeting a stable metric."""

    id: str
    metric: str
    observed_value: float
    stability: float
    assumption_challenged: str
    inverted_objective: str
    counter_goal: str
    intensity: float
    rationale: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "metric": self.metric,
            "observed_value": round(self.observed_value, 8),
            "stability": round(self.stability, 6),
            "assumption_challenged": self.assumption_challenged,
            "inverted_objective": self.inverted_objective,
            "counter_goal": self.counter_goal,
            "intensity": round(self.intensity, 6),
            "rationale": self.rationale,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AdversarialProbe":
        return cls(
            id=str(data.get("id", "")),
            metric=str(data.get("metric", "system_mastery")),
            observed_value=float(data.get("observed_value", 0.0)),
            stability=float(data.get("stability", 0.0)),
            assumption_challenged=str(data.get("assumption_challenged", "")),
            inverted_objective=str(data.get("inverted_objective", "")),
            counter_goal=str(data.get("counter_goal", "")),
            intensity=float(data.get("intensity", 0.0)),
            rationale=str(data.get("rationale", "")),
            created_at=str(
                data.get("created_at", datetime.now(timezone.utc).isoformat())
            ),
        )


class DevilAdvocate:
    """Adversarial sub-agent that escapes local minima by inverting metrics."""

    def __init__(
        self,
        state_path: Optional[Path] = None,
        window: int = _DEFAULT_WINDOW,
        stagnation_epsilon: float = 0.0015,
        cooldown_cycles: int = 3,
        max_probes_per_challenge: int = 2,
        stability_cv_threshold: float = 0.02,
        intensity: float = 0.85,
    ) -> None:
        self.state_path = Path(state_path) if state_path else None
        self.window = max(3, int(window))
        self.stagnation_epsilon = max(0.0, float(stagnation_epsilon))
        self.cooldown_cycles = max(0, int(cooldown_cycles))
        self.max_probes_per_challenge = max(1, int(max_probes_per_challenge))
        self.stability_cv_threshold = max(0.0, float(stability_cv_threshold))
        self.intensity = _clamp(float(intensity), 0.0, 1.0)

        self._lock = threading.RLock()
        self._history: Dict[str, List[float]] = {}
        self._probes: List[AdversarialProbe] = []
        self._cycles_observed = 0
        self._challenges = 0
        self._cooldown = 0
        self._stagnation_events = 0
        self._local_convergence_events = 0
        self._last_intensity = 0.0
        self._last_assessment: Dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _ingest_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        summarized: Dict[str, Dict[str, float]] = {}
        for name, raw in metrics.items():
            if isinstance(raw, bool) or raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            history = self._history.setdefault(str(name), [])
            history.append(value)
            if len(history) > 64:
                del history[:-64]
            window = history[-self.window:]
            mean = sum(window) / len(window)
            variance = (
                sum((item - mean) ** 2 for item in window) / len(window)
                if len(window) > 1
                else 0.0
            )
            std = math.sqrt(max(0.0, variance))
            cv = std / abs(mean) if abs(mean) > 1e-9 else (0.0 if std <= 1e-12 else 1.0)
            span = max(window) - min(window)
            stable = len(window) >= 3 and cv <= self.stability_cv_threshold
            flat = len(window) >= 3 and span <= self.stagnation_epsilon
            summarized[str(name)] = {
                "value": value,
                "mean": mean,
                "std": std,
                "cv": cv,
                "span": span,
                "samples": len(window),
                "stable": stable,
                "flat": flat,
            }
        return summarized

    def assess(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Record a telemetry snapshot and decide whether a challenge is due."""
        with self._lock:
            self._cycles_observed += 1
            if self._cooldown > 0:
                self._cooldown -= 1
            summarized = self._ingest_metrics(metrics)
            stable = [
                name
                for name, stats in summarized.items()
                if stats["stable"] and name in ASSUMPTIONS
            ]
            flat = [
                name
                for name, stats in summarized.items()
                if stats["flat"] and stats["samples"] >= max(3, self.window // 2)
            ]
            locally_converged = [
                name
                for name, stats in summarized.items()
                if stats["stable"]
                and stats["samples"] >= 3
                and abs(stats["value"] - stats["mean"]) <= max(self.stagnation_epsilon, 0.005)
            ]
            stagnation = bool(flat) or (
                len(stable) >= max(3, len(ASSUMPTIONS) // 2)
                and all(
                    stats["cv"] <= self.stability_cv_threshold
                    for stats in summarized.values()
                    if stats["samples"] >= 3
                )
            )
            if stagnation:
                self._stagnation_events += 1
            if locally_converged:
                self._local_convergence_events += 1

            assessment = {
                "cycles_observed": self._cycles_observed,
                "stagnation": stagnation,
                "local_convergence": bool(locally_converged),
                "stable_metrics": stable,
                "flat_metrics": flat,
                "locally_converged_metrics": locally_converged,
                "cooldown_remaining": self._cooldown,
                "metrics": {
                    name: {
                        "value": round(stats["value"], 8),
                        "mean": round(stats["mean"], 8),
                        "cv": round(stats["cv"], 6),
                        "span": round(stats["span"], 8),
                        "samples": stats["samples"],
                        "stable": stats["stable"],
                        "flat": stats["flat"],
                    }
                    for name, stats in summarized.items()
                },
            }
            self._last_assessment = assessment
            return assessment

    def detect_stagnation(self) -> bool:
        """True when the last assessment flagged stagnation or local convergence."""
        with self._lock:
            return bool(
                self._last_assessment.get("stagnation")
                or self._last_assessment.get("local_convergence")
            )

    # ------------------------------------------------------------------
    # Inversion
    # ------------------------------------------------------------------

    @staticmethod
    def invert_metric(metric: str, value: float) -> Dict[str, Any]:
        """Return a counter-intuitive objective for one metric."""
        spec = ASSUMPTIONS.get(metric)
        if spec is None:
            spec = {
                "assumption": f"{metric} should be optimized in its current direction.",
                "inversion": f"Optimize {metric} in the opposite direction to expose hidden trade-offs.",
                "counter_goal": (
                    f"Invert the objective for {metric}: construct a controlled experiment where "
                    f"the metric moves against its assumed-good direction, then measure the "
                    f"information gained about the true objective."
                ),
            }
        inverted_value = 1.0 - _clamp(value, 0.0, 1.0)
        return {
            "metric": metric,
            "observed_value": round(float(value), 8),
            "inverted_direction": "descending" if value >= 0.5 else "ascending",
            "inverted_target": round(inverted_value, 8),
            "assumption_challenged": spec["assumption"],
            "inverted_objective": spec["inversion"],
            "counter_goal": spec["counter_goal"],
        }

    def _select_targets(
        self,
        assessment: Dict[str, Any],
    ) -> List[Tuple[str, float, float]]:
        candidates: List[Tuple[str, float, float]] = []
        for metric, stats in assessment.get("metrics", {}).items():
            if stats.get("samples", 0) < 3:
                continue
            stability = 1.0 / (1.0 + stats["cv"])
            if stats["stable"] or stats["flat"]:
                candidates.append((metric, stats["value"], stability))
        if not candidates:
            # Fall back to the most entrenched known metrics so escape pressure
            # still exists when telemetry is sparse.
            with self._lock:
                for metric in ASSUMPTIONS:
                    history = self._history.get(metric) or []
                    if history:
                        candidates.append(
                            (metric, history[-1], 1.0)
                        )
        candidates.sort(key=lambda item: item[2], reverse=True)
        return candidates[: self.max_probes_per_challenge]

    def challenge(
        self,
        metrics: Dict[str, Any],
        drive_engine: Optional[Any] = None,
        goal_engine: Optional[Any] = None,
        stream: Optional[Any] = None,
        sandbox: Optional[Any] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Assess telemetry; when warranted, invert assumptions and inject goals."""
        assessment = self.assess(metrics)
        triggered = bool(
            force
            or assessment["stagnation"]
            or assessment["local_convergence"]
            or (self._cooldown == 0 and assessment["stable_metrics"])
        )
        if self._cooldown > 0 and not force:
            return {
                "challenged": False,
                "reason": "cooldown",
                "assessment": assessment,
                "probes": [],
                "counterfactual": [],
                "cooldown_remaining": self._cooldown,
            }
        if not triggered:
            return {
                "challenged": False,
                "reason": "no_stagnation",
                "assessment": assessment,
                "probes": [],
                "counterfactual": [],
                "cooldown_remaining": self._cooldown,
            }

        targets = self._select_targets(assessment)
        probes: List[AdversarialProbe] = []
        reasons: List[str] = []
        for index, (metric, value, stability) in enumerate(targets):
            inversion = self.invert_metric(metric, value)
            intensity = _clamp(
                self.intensity * (0.85 + 0.15 * stability), 0.0, 1.0
            )
            probe_id = "adv-" + hashlib.blake2b(
                f"{metric}:{value:.6f}:{self._challenges}:{index}".encode("utf-8"),
                digest_size=6,
            ).hexdigest()
            reasons.append(
                "stagnation" if assessment["stagnation"] else "local_convergence"
            )
            probe = AdversarialProbe(
                id=probe_id,
                metric=metric,
                observed_value=value,
                stability=stability,
                assumption_challenged=inversion["assumption_challenged"],
                inverted_objective=inversion["inverted_objective"],
                counter_goal=inversion["counter_goal"],
                intensity=intensity,
                rationale=(
                    f"{metric} plateaued at {value:.4f} (stability {stability:.3f}); "
                    f"challenging: {inversion['assumption_challenged']}"
                ),
            )
            probes.append(probe)

            if drive_engine is not None:
                try:
                    drive_engine.apply_stimulus(
                        challenge_intensity=round(intensity * 10.0, 4),
                        context=f"devil_advocate:{metric}",
                    )
                except Exception as exc:
                    logger.warning("Drive stimulus from devil advocate failed: %s", exc)

            if goal_engine is not None and hasattr(goal_engine, "ingest_goal"):
                try:
                    vector = None
                    try:
                        from telos import GoalVector  # type: ignore[import-not-found]

                        vector = GoalVector.FRONTIER_EXPLORE
                    except Exception:
                        vector = None
                    priority = (
                        getattr(GoalPriority, "HIGH", None)
                        if _TELOS_AVAILABLE
                        else None
                    )
                    goal = goal_engine.ingest_goal(
                        probe.counter_goal,
                        vector=vector,
                        priority=priority,
                        title=f"Counter-intuition: invert {metric}",
                        discipline_weight=1.0 + intensity,
                        goal_id=f"adv-{probe.id}",
                    )
                    goal.metadata["devil_advocate"] = probe.to_dict()
                except Exception as exc:
                    logger.warning("Counter-goal ingestion failed: %s", exc)

            if stream is not None and getattr(stream, "is_running", False):
                try:
                    priority = (
                        getattr(GoalPriority, "HIGH", None)
                        if _TELOS_AVAILABLE
                        else None
                    )
                    stream.submit_objective(
                        probe.counter_goal,
                        level=2,
                        priority=priority,
                        objective_id=f"adv-obj-{probe.id}",
                        metadata={
                            "source": "devil_advocate",
                            "metric": metric,
                            "intensity": intensity,
                        },
                    )
                except Exception as exc:
                    logger.warning("Counter-goal stream injection failed: %s", exc)

        sandbox_reports: List[Dict[str, Any]] = []
        if sandbox is not None and probes:
            search = getattr(sandbox, "counterfactual_search", None)
            if callable(search):
                for probe in probes[:2]:
                    try:
                        report = search(probe.counter_goal, candidates_limit=8)
                        sandbox_reports.append(
                            {
                                "probe_id": probe.id,
                                "metric": probe.metric,
                                "promoted": int(report.get("promoted", 0)),
                                "rejected": int(report.get("rejected", 0)),
                                "branches": len(report.get("branches") or []),
                                "best_delta": report.get("best_delta"),
                                "best_operator": report.get("best_operator"),
                                "verification_mode": report.get("verification_mode"),
                                "baseline_score": report.get("baseline_score"),
                            }
                        )
                    except Exception as exc:
                        sandbox_reports.append(
                            {
                                "probe_id": probe.id,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )

        with self._lock:
            if probes:
                self._challenges += 1
                self._last_intensity = max(
                    (probe.intensity for probe in probes), default=0.0
                )
                self._cooldown = self.cooldown_cycles
                self._probes.extend(probes)
                if len(self._probes) > _MAX_PROBES:
                    self._probes = self._probes[-_MAX_PROBES:]

        if probes:
            self.save()

        return {
            "challenged": bool(probes),
            "reason": reasons[0] if reasons else ("forced" if force else "unknown"),
            "assessment": assessment,
            "probes": [probe.to_dict() for probe in probes],
            "counterfactual": sandbox_reports,
            "counterfactual_promotions": sum(
                int(report.get("promoted", 0)) for report in sandbox_reports
            ),
            "drive_stimulus_applied": bool(probes and drive_engine is not None),
            "goals_injected": bool(probes and (goal_engine is not None or stream is not None)),
            "cooldown_remaining": self._cooldown,
        }

    # ------------------------------------------------------------------
    # Telemetry / persistence
    # ------------------------------------------------------------------

    def get_telemetry(self) -> Dict[str, Any]:
        with self._lock:
            history_summary = {
                metric: {
                    "samples": len(values),
                    "latest": round(values[-1], 8) if values else None,
                    "mean": round(sum(values) / len(values), 8) if values else None,
                }
                for metric, values in self._history.items()
            }
            return {
                "available": True,
                "engine": "adversarial_counter_intuition",
                "cycles_observed": self._cycles_observed,
                "challenges_issued": self._challenges,
                "stagnation_events": self._stagnation_events,
                "local_convergence_events": self._local_convergence_events,
                "cooldown_remaining": self._cooldown,
                "last_intensity": round(self._last_intensity, 6),
                "last_assessment": self._last_assessment,
                "metric_history": dict(list(history_summary.items())[:12]),
                "recent_probes": [probe.to_dict() for probe in self._probes[-3:]],
            }

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": 1,
                "cycles_observed": self._cycles_observed,
                "challenges": self._challenges,
                "stagnation_events": self._stagnation_events,
                "local_convergence_events": self._local_convergence_events,
                "cooldown": self._cooldown,
                "last_intensity": self._last_intensity,
                "history": {
                    metric: list(values[-self.window * 4 :])
                    for metric, values in self._history.items()
                },
                "probes": [probe.to_dict() for probe in self._probes[-_MAX_PROBES:]],
            }

    def from_dict(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        with self._lock:
            self._cycles_observed = max(
                self._cycles_observed, int(data.get("cycles_observed", 0))
            )
            self._challenges = max(self._challenges, int(data.get("challenges", 0)))
            self._stagnation_events = max(
                self._stagnation_events, int(data.get("stagnation_events", 0))
            )
            self._local_convergence_events = max(
                self._local_convergence_events,
                int(data.get("local_convergence_events", 0)),
            )
            self._cooldown = max(0, int(data.get("cooldown", 0)))
            self._last_intensity = float(data.get("last_intensity", 0.0))
            for metric, values in (data.get("history") or {}).items():
                if isinstance(values, list):
                    self._history[str(metric)] = [
                        float(value) for value in values if isinstance(value, (int, float))
                    ][-64:]
            for payload in data.get("probes") or []:
                if isinstance(payload, dict) and payload.get("id"):
                    self._probes.append(AdversarialProbe.from_dict(payload))

    def save(self) -> Optional[Path]:
        if self.state_path is None:
            return None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)
        return self.state_path

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            self.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            logger.warning("Devil-advocate state at %s is unreadable; starting fresh.", self.state_path)


# Explicit alias for integrations that use the descriptive name
AdversarialCounterIntuitionInjector = DevilAdvocate
