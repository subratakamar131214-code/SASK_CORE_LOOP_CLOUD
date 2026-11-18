"""Telos v2: Epistemic Curiosity Engine (Free Energy Principle).

Upgrades the fixed-objective Telos pipeline into an autonomous curiosity-driven
goal generator. The engine maintains a Gaussian belief over every knowledge
domain, converts observations into surprisal and variational information gain,
and scores candidate hypotheses by expected free energy. High-value hypotheses
are ingested into the goal engine and submitted to the asynchronous goal stream
without waiting for external input.

Intrinsic reward (per observation, bounded to ``[0, 1]``)::

    reward = 0.5 * normalized_surprisal + 0.5 * normalized_information_gain

Expected free energy for a hypothesis (lower is more attractive)::

    F = surprise_potential - expected_information_gain
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("SASK.TelosV2")

try:  # Goal engine is the natural consumer of curiosity proposals
    from telos import GoalIngestionEngine, GoalPriority, GoalVector  # type: ignore[import-not-found]

    _TELOS_AVAILABLE = True
except Exception:  # pragma: no cover - telos is a hard dependency in practice
    GoalIngestionEngine = Any  # type: ignore[assignment,misc]
    GoalVector = Any  # type: ignore[assignment,misc]
    GoalPriority = None  # type: ignore[assignment]
    _TELOS_AVAILABLE = False


_EPSILON = 1e-9
_MIN_VARIANCE = 1e-6
_MAX_VARIANCE = 1.0


def _hash_unit(*parts: Any) -> float:
    """Stable deterministic scalar in ``[0, 1)`` derived from the inputs."""
    digest = hashlib.blake2b(
        "\x1f".join(str(part) for part in parts).encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _tokens(text: str) -> set:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------

DOMAINS: Dict[str, Dict[str, Any]] = {
    "compiler_theory": {
        "prior_mean": 0.55,
        "prior_variance": 0.18,
        "vector": "ALGORITHMIC_CODE",
        "concepts": (
            "SSA dominance frontiers",
            "polyhedral loop transformations",
            "register allocation pressure",
            "effect-typing inference",
            "speculative JIT guards",
        ),
        "templates": (
            "Derive a minimal rewrite rule set that collapses {concept} into a canonical normal form while preserving observable semantics.",
            "Formulate a hypothesis that {concept} admits a polynomial-time decision procedure under a stricter type discipline.",
            "Test whether {concept} can be expressed as a linear resource transformation with bounded proof obligations.",
        ),
    },
    "abstract_algebra": {
        "prior_mean": 0.5,
        "prior_variance": 0.22,
        "vector": "FORMAL_PROOF",
        "concepts": (
            "semidirect product decompositions",
            "Cayley graph spectra",
            "idempotent semifields",
            "quotient lattice congruences",
            "operadic composition laws",
        ),
        "templates": (
            "Conjecture a structure theorem for {concept} and search for a minimal counterexample with an automated prover.",
            "Hypothesize an isomorphism between {concept} and a refinement-typed state machine, then verify transport of invariants.",
            "Determine whether {concept} yields a compact equational basis and attempt a formal proof of its completeness.",
        ),
    },
    "distributed_systems": {
        "prior_mean": 0.5,
        "prior_variance": 0.2,
        "vector": "ALGORITHMIC_CODE",
        "concepts": (
            "consensus epochs",
            "vector-clock antichains",
            "quorum intersection lattices",
            "gossip convergence rates",
            "partition-tolerant commit protocols",
        ),
        "templates": (
            "Derive a liveness proof for {concept} under adversarial reordering and measure the information gain of the result.",
            "Conjecture a refinement-typed invariant that makes {concept} locally checkable, then attempt an automated proof.",
            "Test whether {concept} is isomorphic to a ring topology under a topology-preserving embedding.",
        ),
    },
    "physical_simulation": {
        "prior_mean": 0.5,
        "prior_variance": 0.24,
        "vector": "FRONTIER_EXPLORE",
        "concepts": (
            "lattice gauge excitations",
            "non-equilibrium free-energy flows",
            "topological defect transport",
            "multiscale PDE coupling",
            "entropy-production bounds",
        ),
        "templates": (
            "Formulate a falsifiable hypothesis about {concept} and design a simulation probe that maximizes expected information gain.",
            "Conjecture a symmetry that reduces {concept} to a lower-dimensional integrable model, then test it numerically.",
            "Hypothesize that {concept} is structurally equivalent to a distributed consensus protocol and transfer the convergence proof.",
        ),
    },
    "quantum_dynamics": {
        "prior_mean": 0.45,
        "prior_variance": 0.26,
        "vector": "FRONTIER_EXPLORE",
        "concepts": (
            "contextuality witnesses",
            "variational ansatz expressivity",
            "decoherence-free subspaces",
            "entanglement entropy scaling",
            "measurement incompatibility graphs",
        ),
        "templates": (
            "Design a low-depth circuit experiment that maximizes information gain about {concept}.",
            "Conjecture a structural analogy between {concept} and a compiler data-flow property, then map it through the manifold bridge.",
            "Hypothesize an invariant that survives {concept} transformations and attempt to verify it with the meta-prover.",
        ),
    },
    "formal_logic": {
        "prior_mean": 0.55,
        "prior_variance": 0.2,
        "vector": "FORMAL_PROOF",
        "concepts": (
            "univalent transport paths",
            "linear implication resource bounds",
            "refinement-type subtyping lattices",
            "cut-elimination normal forms",
            "constructive excluded-middle fragments",
        ),
        "templates": (
            "Search for an alternative axiom basis that shortens the proof path for {concept} without losing soundness.",
            "Conjecture a compactness ordering over {concept} and test it against at least three logical frameworks.",
            "Formulate a meta-theorem that {concept} is invariant under the axiom mutations explored by the meta-prover.",
        ),
    },
    "optimization": {
        "prior_mean": 0.6,
        "prior_variance": 0.16,
        "vector": "ALGORITHMIC_CODE",
        "concepts": (
            "saddle-point escape directions",
            "trust-region curvature estimates",
            "constraint-relaxation duals",
            "gradient-free perturbation schedules",
            "basin-of-attraction boundaries",
        ),
        "templates": (
            "Hypothesize a perturbation schedule that escapes the current basin for {concept} and predict the resulting information gain.",
            "Formulate a dual relaxation of {concept} that preserves constraint satisfaction and measure residual reduction.",
            "Test whether an adversarial inversion of {concept} reveals a previously unexplored feasible region.",
        ),
    },
    "biological_pipeline": {
        "prior_mean": 0.5,
        "prior_variance": 0.22,
        "vector": "FRONTIER_EXPLORE",
        "concepts": (
            "protein-folding funnels",
            "metabolic flux bottlenecks",
            "gene-regulatory feedback motifs",
            "sequence-to-structure mappings",
            "protocol reproducibility envelopes",
        ),
        "templates": (
            "Conjecture an algebraic invariant for {concept} and test it against simulator traces.",
            "Hypothesize that {concept} admits a compiler-style data-flow abstraction, then transfer an optimization from compiler theory.",
            "Design an information-maximizing wet-lab protocol variant for {concept} with explicit falsification criteria.",
        ),
    },
}

DOMAIN_NAMES: Tuple[str, ...] = tuple(DOMAINS)


@dataclass
class GaussianBelief:
    """Diagonal Gaussian posterior over one domain's latent truth signal."""

    domain: str
    mean: float
    variance: float
    observations: int = 0
    last_surprise: float = 0.0
    last_information_gain: float = 0.0
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def uncertainty(self) -> float:
        """Uncertainty scales linearly with posterior variance."""
        return _clamp(self.variance, 0.0, _MAX_VARIANCE)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "mean": round(self.mean, 8),
            "variance": round(self.variance, 8),
            "observations": self.observations,
            "last_surprise": round(self.last_surprise, 8),
            "last_information_gain": round(self.last_information_gain, 8),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GaussianBelief":
        domain = str(data.get("domain", "unknown"))
        return cls(
            domain=domain,
            mean=_clamp(float(data.get("mean", 0.5)), 0.0, 1.0),
            variance=_clamp(
                float(data.get("variance", 0.2)), _MIN_VARIANCE, _MAX_VARIANCE
            ),
            observations=int(data.get("observations", 0)),
            last_surprise=float(data.get("last_surprise", 0.0)),
            last_information_gain=float(data.get("last_information_gain", 0.0)),
            updated_at=str(
                data.get("updated_at", datetime.now(timezone.utc).isoformat())
            ),
        )


@dataclass
class CuriosityHypothesis:
    """A self-generated hypothesis scored by expected free energy."""

    id: str
    statement: str
    domain: str
    vector: str
    novelty: float
    expected_information_gain: float
    surprise_potential: float
    free_energy: float
    intrinsic_reward: float
    score: float
    priority: str = "LOW"
    status: str = "FORMULATED"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "domain": self.domain,
            "vector": self.vector,
            "novelty": round(self.novelty, 6),
            "expected_information_gain": round(
                self.expected_information_gain, 6
            ),
            "surprise_potential": round(self.surprise_potential, 6),
            "free_energy": round(self.free_energy, 6),
            "intrinsic_reward": round(self.intrinsic_reward, 6),
            "score": round(self.score, 6),
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CuriosityHypothesis":
        return cls(
            id=str(data.get("id", "")),
            statement=str(data.get("statement", "")),
            domain=str(data.get("domain", "optimization")),
            vector=str(data.get("vector", "FRONTIER_EXPLORE")),
            novelty=float(data.get("novelty", 0.0)),
            expected_information_gain=float(
                data.get("expected_information_gain", 0.0)
            ),
            surprise_potential=float(data.get("surprise_potential", 0.0)),
            free_energy=float(data.get("free_energy", 0.0)),
            intrinsic_reward=float(data.get("intrinsic_reward", 0.0)),
            score=float(data.get("score", 0.0)),
            priority=str(data.get("priority", "LOW")),
            status=str(data.get("status", "FORMULATED")),
            created_at=str(
                data.get("created_at", datetime.now(timezone.utc).isoformat())
            ),
            metadata=dict(data.get("metadata") or {}),
        )


class EpistemicCuriosityEngine:
    """Autonomous curiosity loop driven by variational free energy."""

    def __init__(
        self,
        goal_engine: Optional[Any] = None,
        stream: Optional[Any] = None,
        state_path: Optional[Path] = None,
        seed: int = 0x5A5C,
        hypothesis_limit: int = 256,
        trace_limit: int = 256,
        min_proposal_score: float = 0.45,
    ) -> None:
        self.goal_engine = goal_engine
        self.stream = stream
        self.state_path = Path(state_path) if state_path else None
        self.seed = int(seed)
        self.hypothesis_limit = max(16, int(hypothesis_limit))
        self.trace_limit = max(16, int(trace_limit))
        self.min_proposal_score = _clamp(float(min_proposal_score), 0.0, 1.0)

        self._lock = threading.RLock()
        self._beliefs: Dict[str, GaussianBelief] = {
            domain: GaussianBelief(
                domain=domain,
                mean=_clamp(spec["prior_mean"], 0.0, 1.0),
                variance=_clamp(
                    spec["prior_variance"], _MIN_VARIANCE, _MAX_VARIANCE
                ),
            )
            for domain, spec in DOMAINS.items()
        }
        self._hypotheses: Dict[str, CuriosityHypothesis] = {}
        self._statements: List[str] = []
        self._trace: List[Dict[str, Any]] = []
        self._observations = 0
        self._generated = 0
        self._proposed = 0
        self._ticks = 0
        self._last_reward = 0.0
        self._total_surprise = 0.0
        self._total_information_gain = 0.0
        self._background: Optional[threading.Thread] = None
        self._background_stop = threading.Event()
        self._rng = random.Random(self.seed)
        self._load()

    # ------------------------------------------------------------------
    # Bayesian observation model
    # ------------------------------------------------------------------

    def observe(
        self,
        domain: str,
        value: float,
        weight: float = 1.0,
        source: str = "internal",
    ) -> Dict[str, Any]:
        """Fold one bounded observation into a domain posterior.

        Surprisal is the Gaussian negative log-likelihood of the observation
        under the prior; information gain is the entropy reduction from the
        conjugate posterior update.
        """
        belief = self._beliefs.get(domain)
        if belief is None:
            spec = DOMAINS.get(domain, DOMAINS["optimization"])
            belief = GaussianBelief(
                domain=domain,
                mean=float(spec["prior_mean"]),
                variance=float(spec["prior_variance"]),
            )
            self._beliefs[domain] = belief

        observed = _clamp(float(value), 0.0, 1.0)
        effective_weight = max(_EPSILON, float(weight))
        with self._lock:
            prior_variance = _clamp(
                belief.variance, _MIN_VARIANCE, _MAX_VARIANCE
            )
            sigma = math.sqrt(prior_variance)
            z_score = (observed - belief.mean) / sigma
            surprisal = max(
                0.0, 0.5 * z_score * z_score + math.log(sigma * math.sqrt(2 * math.pi))
            )

            observation_variance = max(1e-4, 0.05 / effective_weight)
            prior_precision = 1.0 / prior_variance
            posterior_precision = prior_precision + 1.0 / observation_variance
            posterior_variance = _clamp(
                1.0 / posterior_precision, _MIN_VARIANCE, _MAX_VARIANCE
            )
            posterior_mean = _clamp(
                posterior_variance
                * (belief.mean * prior_precision + observed / observation_variance),
                0.0,
                1.0,
            )
            information_gain = max(
                0.0, 0.5 * math.log2(prior_variance / posterior_variance)
            )

            normalized_surprise = surprisal / (1.0 + surprisal)
            normalized_gain = information_gain / (1.0 + information_gain)
            intrinsic_reward = _clamp(
                0.5 * normalized_surprise + 0.5 * normalized_gain, 0.0, 1.0
            )

            belief.mean = posterior_mean
            belief.variance = posterior_variance
            belief.observations += 1
            belief.last_surprise = surprisal
            belief.last_information_gain = information_gain
            belief.updated_at = datetime.now(timezone.utc).isoformat()

            self._observations += 1
            self._last_reward = intrinsic_reward
            self._total_surprise += surprisal
            self._total_information_gain += information_gain
            record = {
                "domain": domain,
                "value": round(observed, 6),
                "source": source,
                "surprisal": round(surprisal, 6),
                "information_gain": round(information_gain, 6),
                "intrinsic_reward": round(intrinsic_reward, 6),
                "posterior_mean": round(posterior_mean, 6),
                "posterior_variance": round(posterior_variance, 6),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._trace.append(record)
            if len(self._trace) > self.trace_limit:
                self._trace = self._trace[-self.trace_limit:]
        return record

    def observe_telemetry(self, telemetry: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Map kernel telemetry into domain observations."""
        if not isinstance(telemetry, dict):
            return []

        records: List[Dict[str, Any]] = []
        mapping: List[Tuple[str, Optional[float]]] = [
            ("mastery", telemetry.get("system_mastery")),
            ("success", telemetry.get("success_rate")),
            (
                "error",
                telemetry.get("error_rate")
                if telemetry.get("error_rate") is not None
                else telemetry.get("errors"),
            ),
            ("discipline", telemetry.get("unified_discipline")),
            ("residual", telemetry.get("residual")),
        ]
        for key, raw in mapping:
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if key == "error":
                value = 1.0 - _clamp(value, 0.0, 1.0)
            domain = self._telemetry_domain(key)
            records.append(self.observe(domain, value, weight=1.0, source=f"telemetry:{key}"))
        return records

    @staticmethod
    def _telemetry_domain(key: str) -> str:
        return {
            "mastery": "optimization",
            "success": "distributed_systems",
            "error": "compiler_theory",
            "discipline": "formal_logic",
            "residual": "physical_simulation",
        }.get(key, "optimization")

    # ------------------------------------------------------------------
    # Hypothesis synthesis
    # ------------------------------------------------------------------

    def _novelty(self, statement: str) -> float:
        candidate = _tokens(statement)
        if not candidate:
            return 1.0
        best_overlap = 0.0
        for previous in self._statements[-64:]:
            prior = _tokens(previous)
            if not prior:
                continue
            union = candidate | prior
            if not union:
                continue
            overlap = len(candidate & prior) / len(union)
            best_overlap = max(best_overlap, overlap)
        return _clamp(1.0 - best_overlap, 0.0, 1.0)

    def _rank_domains(self) -> List[str]:
        ranked = sorted(
            self._beliefs.values(),
            key=lambda belief: (
                belief.variance * 2.0
                + 0.5 / (1.0 + belief.observations)
                + 0.25 * _hash_unit(self._ticks, belief.domain, self._generated)
            ),
            reverse=True,
        )
        return [belief.domain for belief in ranked]

    def _build_hypothesis(self, domain: str, attempt: int) -> Optional[CuriosityHypothesis]:
        spec = DOMAINS.get(domain, DOMAINS["optimization"])
        pick = self._rng.random()
        concept = spec["concepts"][int(pick * len(spec["concepts"])) % len(spec["concepts"])]
        template_index = (self._generated + attempt) % len(spec["templates"])
        statement = spec["templates"][template_index].format(concept=concept)
        novelty = self._novelty(statement)
        if novelty < 0.05 and attempt < 12:
            return None

        belief = self._beliefs.get(domain)
        variance = belief.variance if belief else float(spec["prior_variance"])
        observation_confidence = (
            1.0 - 1.0 / (1.0 + (belief.observations if belief else 0))
        )
        expected_gain = _clamp(variance * (1.0 + 0.25 * novelty), 0.0, 1.0)
        surprise_potential = _clamp(
            0.4 + 0.6 * (1.0 - observation_confidence), 0.0, 1.0
        )
        free_energy = _clamp(surprise_potential - expected_gain, 0.0, 1.0)
        intrinsic_reward = _clamp(
            0.5 * surprise_potential + 0.5 * expected_gain, 0.0, 1.0
        )
        score = _clamp(
            0.40 * expected_gain
            + 0.30 * surprise_potential
            + 0.20 * novelty
            + 0.10 * (1.0 / (1.0 + free_energy)),
            0.0,
            1.0,
        )
        if score >= 0.80:
            priority = "HIGH"
        elif score >= 0.60:
            priority = "NORMAL"
        else:
            priority = "LOW"

        digest = hashlib.blake2b(
            statement.encode("utf-8"), digest_size=6
        ).hexdigest()
        return CuriosityHypothesis(
            id=f"hyp-{digest}",
            statement=statement[:240],
            domain=domain,
            vector=str(spec["vector"]),
            novelty=round(novelty, 6),
            expected_information_gain=round(expected_gain, 6),
            surprise_potential=round(surprise_potential, 6),
            free_energy=round(free_energy, 6),
            intrinsic_reward=round(intrinsic_reward, 6),
            score=round(score, 6),
            priority=priority,
            metadata={
                "concept": concept,
                "template_index": template_index,
                "belief_variance": round(variance, 6),
                "expected_free_energy": round(free_energy, 6),
            },
        )

    def generate_hypotheses(
        self,
        count: int = 1,
        domains: Optional[Iterable[str]] = None,
    ) -> List[CuriosityHypothesis]:
        """Synthesize novel, deduplicated hypotheses from epistemic uncertainty."""
        wanted = max(0, int(count))
        if wanted == 0:
            return []

        with self._lock:
            ranked = list(domains) if domains is not None else self._rank_domains()
            produced: List[CuriosityHypothesis] = []
            attempts = 0
            while len(produced) < wanted and attempts < wanted * 16:
                domain = ranked[attempts % len(ranked)]
                hypothesis = self._build_hypothesis(domain, attempts)
                attempts += 1
                if hypothesis is None:
                    continue
                if hypothesis.id in self._hypotheses:
                    continue
                self._hypotheses[hypothesis.id] = hypothesis
                self._statements.append(hypothesis.statement)
                if len(self._statements) > 512:
                    self._statements = self._statements[-512:]
                self._generated += 1
                produced.append(hypothesis)

            if len(self._hypotheses) > self.hypothesis_limit:
                ordered = sorted(
                    self._hypotheses.values(),
                    key=lambda item: (item.score, item.created_at),
                )
                for hypothesis in ordered[: len(self._hypotheses) - self.hypothesis_limit]:
                    self._hypotheses.pop(hypothesis.id, None)
            return produced

    # ------------------------------------------------------------------
    # Goal proposition
    # ------------------------------------------------------------------

    def propose_goals(
        self,
        limit: int = 1,
        min_score: Optional[float] = None,
        use_stream: bool = True,
    ) -> List[Dict[str, Any]]:
        """Ingest the highest-scoring hypotheses as executable goals."""
        threshold = (
            self.min_proposal_score if min_score is None else _clamp(float(min_score), 0.0, 1.0)
        )
        with self._lock:
            candidates = [
                hypothesis
                for hypothesis in self._hypotheses.values()
                if hypothesis.status == "FORMULATED" and hypothesis.score >= threshold
            ]
            candidates.sort(key=lambda item: item.score, reverse=True)
            selected = candidates[: max(0, int(limit))]

        proposals: List[Dict[str, Any]] = []
        for hypothesis in selected:
            record: Dict[str, Any] = {
                "hypothesis_id": hypothesis.id,
                "statement": hypothesis.statement,
                "score": hypothesis.score,
                "priority": hypothesis.priority,
                "domain": hypothesis.domain,
                "ingested": False,
                "streamed": False,
            }
            goal_id = f"curio-{hypothesis.id}"

            if self.goal_engine is not None and hasattr(self.goal_engine, "ingest_goal"):
                try:
                    vector = (
                        getattr(GoalVector, hypothesis.vector, None)
                        if _TELOS_AVAILABLE
                        else hypothesis.vector
                    )
                    priority = (
                        getattr(GoalPriority, hypothesis.priority, None)
                        if _TELOS_AVAILABLE
                        else None
                    )
                    goal = self.goal_engine.ingest_goal(
                        hypothesis.statement,
                        vector=vector,
                        priority=priority,
                        title=f"Curiosity: {hypothesis.domain}",
                        discipline_weight=1.0 + hypothesis.score,
                        goal_id=goal_id,
                    )
                    goal.metadata["curiosity"] = {
                        "hypothesis_id": hypothesis.id,
                        "expected_information_gain": hypothesis.expected_information_gain,
                        "free_energy": hypothesis.free_energy,
                        "novelty": hypothesis.novelty,
                    }
                    record["ingested"] = True
                    record["goal_id"] = getattr(goal, "id", goal_id)
                except Exception as exc:
                    record["ingest_error"] = f"{type(exc).__name__}: {exc}"

            if use_stream and self.stream is not None and getattr(self.stream, "is_running", False):
                try:
                    stream_priority = (
                        getattr(GoalPriority, hypothesis.priority, None)
                        if _TELOS_AVAILABLE
                        else None
                    )
                    stream_info = self.stream.submit_objective(
                        hypothesis.statement,
                        level=2,
                        priority=stream_priority,
                        objective_id=f"curio-obj-{hypothesis.id}",
                        metadata={
                            "source": "epistemic_curiosity",
                            "domain": hypothesis.domain,
                            "free_energy": hypothesis.free_energy,
                        },
                    )
                    record["streamed"] = bool(stream_info.get("new", True))
                    record["objective_id"] = stream_info.get("objective_id")
                except Exception as exc:
                    record["stream_error"] = f"{type(exc).__name__}: {exc}"

            hypothesis.status = "PROPOSED"
            if record["ingested"] or record["streamed"]:
                self._proposed += 1
            proposals.append(record)

        if proposals:
            self.save()
        return proposals

    # ------------------------------------------------------------------
    # Autonomous tick / background loop
    # ------------------------------------------------------------------

    def tick(
        self,
        telemetry: Optional[Dict[str, Any]] = None,
        generate: int = 2,
        propose_limit: int = 1,
    ) -> Dict[str, Any]:
        """Advance one curiosity step without external prompting."""
        with self._lock:
            self._ticks += 1

        observed = self.observe_telemetry(telemetry or {})

        if not observed:
            # No external signal: sample the engine's own posterior to surface
            # the most uncertain domain through an endogenous probe.
            ranked = self._rank_domains()
            target = ranked[0]
            belief = self._beliefs[target]
            probe = _clamp(
                belief.mean + (self._rng.random() - 0.5) * 2.0 * math.sqrt(belief.variance),
                0.0,
                1.0,
            )
            observed.append(
                self.observe(target, probe, weight=0.25, source="endogenous_probe")
            )

        hypotheses = self.generate_hypotheses(count=max(1, int(generate)))
        proposals = self.propose_goals(limit=max(0, int(propose_limit)))

        result = {
            "tick": self._ticks,
            "observations": observed,
            "hypotheses": [item.to_dict() for item in hypotheses],
            "proposals": proposals,
            "intrinsic_reward": round(self._last_reward, 6),
            "mean_surprise": round(
                self._total_surprise / max(1, self._observations), 6
            ),
            "mean_information_gain": round(
                self._total_information_gain / max(1, self._observations), 6
            ),
            "free_energy": round(
                max(
                    0.0,
                    (
                        self._total_surprise / max(1, self._observations)
                    )
                    - (
                        self._total_information_gain / max(1, self._observations)
                    ),
                ),
                6,
            ),
        }
        self.save()
        return result

    def start_background(
        self,
        interval_seconds: float = 60.0,
        generate: int = 1,
        propose_limit: int = 1,
    ) -> bool:
        """Start a daemon curiosity loop that runs without external input."""
        with self._lock:
            if self._background is not None and self._background.is_alive():
                return False
            self._background_stop.clear()
            interval = max(1.0, float(interval_seconds))

            def _loop() -> None:
                while not self._background_stop.wait(interval):
                    try:
                        self.tick(generate=generate, propose_limit=propose_limit)
                    except Exception as exc:  # Never kill the daemon
                        logger.warning("Curiosity tick failed: %s", exc)

            thread = threading.Thread(
                target=_loop,
                name="sask-curiosity",
                daemon=True,
            )
            self._background = thread
            thread.start()
            return True

    def stop_background(self, timeout: float = 3.0) -> bool:
        """Signal and join the background curiosity loop."""
        self._background_stop.set()
        with self._lock:
            thread = self._background
            self._background = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
        return thread is None or not thread.is_alive()

    @property
    def background_running(self) -> bool:
        with self._lock:
            return self._background is not None and self._background.is_alive()

    # ------------------------------------------------------------------
    # Telemetry / persistence
    # ------------------------------------------------------------------

    def get_telemetry(self) -> Dict[str, Any]:
        with self._lock:
            beliefs = {
                domain: belief.to_dict()
                for domain, belief in sorted(self._beliefs.items())
            }
            top = sorted(
                self._hypotheses.values(),
                key=lambda item: item.score,
                reverse=True,
            )[:3]
            return {
                "available": True,
                "engine": "epistemic_curiosity",
                "observations": self._observations,
                "hypotheses_generated": self._generated,
                "hypotheses_proposed": self._proposed,
                "ticks": self._ticks,
                "intrinsic_reward": round(self._last_reward, 6),
                "mean_surprise": round(
                    self._total_surprise / max(1, self._observations), 6
                ),
                "mean_information_gain": round(
                    self._total_information_gain / max(1, self._observations), 6
                ),
                "free_energy": round(
                    max(
                        0.0,
                        (
                            self._total_surprise / max(1, self._observations)
                        )
                        - (
                            self._total_information_gain / max(1, self._observations)
                        ),
                    ),
                    6,
                ),
                "background_running": self.background_running,
                "beliefs": beliefs,
                "top_hypotheses": [item.to_dict() for item in top],
                "recent_observations": list(self._trace[-5:]),
            }

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            hypotheses = sorted(
                self._hypotheses.values(),
                key=lambda item: item.created_at,
            )[-self.hypothesis_limit:]
            return {
                "version": 2,
                "seed": self.seed,
                "observations": self._observations,
                "generated": self._generated,
                "proposed": self._proposed,
                "ticks": self._ticks,
                "last_reward": self._last_reward,
                "total_surprise": self._total_surprise,
                "total_information_gain": self._total_information_gain,
                "beliefs": {
                    domain: belief.to_dict()
                    for domain, belief in self._beliefs.items()
                },
                "statements": list(self._statements[-128:]),
                "trace": list(self._trace[-64:]),
                "hypotheses": [item.to_dict() for item in hypotheses],
            }

    def from_dict(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        with self._lock:
            self.seed = int(data.get("seed", self.seed))
            self._rng = random.Random(self.seed + int(data.get("generated", 0)))
            self._observations = max(self._observations, int(data.get("observations", 0)))
            self._generated = max(self._generated, int(data.get("generated", 0)))
            self._proposed = max(self._proposed, int(data.get("proposed", 0)))
            self._ticks = max(self._ticks, int(data.get("ticks", 0)))
            self._last_reward = float(data.get("last_reward", self._last_reward))
            self._total_surprise = max(
                self._total_surprise, float(data.get("total_surprise", 0.0))
            )
            self._total_information_gain = max(
                self._total_information_gain,
                float(data.get("total_information_gain", 0.0)),
            )
            for domain, payload in (data.get("beliefs") or {}).items():
                if isinstance(payload, dict):
                    payload.setdefault("domain", domain)
                    self._beliefs[str(domain)] = GaussianBelief.from_dict(payload)
            for statement in data.get("statements") or []:
                if isinstance(statement, str):
                    self._statements.append(statement)
            for record in data.get("trace") or []:
                if isinstance(record, dict):
                    self._trace.append(record)
            for payload in data.get("hypotheses") or []:
                if isinstance(payload, dict) and payload.get("id"):
                    hypothesis = CuriosityHypothesis.from_dict(payload)
                    self._hypotheses[hypothesis.id] = hypothesis

    def save(self) -> Optional[Path]:
        if self.state_path is None:
            return None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self.to_dict(), indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.state_path)
        return self.state_path

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            self.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            logger.warning("Curiosity state at %s is unreadable; starting fresh.", self.state_path)


# Backwards-friendly alias
CuriosityEngine = EpistemicCuriosityEngine
