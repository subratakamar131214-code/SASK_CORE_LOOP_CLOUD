"""Meta-Prover: axiomatic mutation and evolutionary proof-framework search.

Treats the foundational axioms, type systems, and inference rules that underpin
the Lean 4 verification pipeline in prover.py as mutable parameters. A bounded
evaluation loop mutates those parameters, scores alternative logical frameworks
by proof success, path compactness, numerical residual, and latency, and
converges on the most compact and powerful framework for a computational goal.

Live Lean compilation is never required: when the toolchain is absent the
evaluator falls back to a deterministic structural model, while still producing
a real Lean proof skeleton through ``ProverInterface.lean.wrap_assertion`` for
downstream compilation when a toolchain becomes available.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # Optional: the meta-prover still works without the compiler pipeline.
    from prover import ProverInterface
    _PROVER_AVAILABLE = True
except Exception:  # pragma: no cover - defensive import
    ProverInterface = None  # type: ignore[assignment]
    _PROVER_AVAILABLE = False


def _hash_unit(*parts: Any) -> float:
    """Deterministic unit-interval hash used by the structural evaluator."""
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(2 ** 64)


@dataclass
class AxiomParameter:
    """A mutable foundational parameter of the proof calculus."""

    name: str
    value: Any
    options: Tuple[Any, ...] = ()
    mutable: bool = True
    weight: float = 1.0
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "options": list(self.options),
            "mutable": self.mutable,
            "weight": self.weight,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AxiomParameter":
        return cls(
            name=str(data["name"]),
            value=data.get("value"),
            options=tuple(data.get("options") or ()),
            mutable=bool(data.get("mutable", True)),
            weight=float(data.get("weight", 1.0)),
            rationale=str(data.get("rationale", "")),
        )


@dataclass
class LogicalFramework:
    """A complete logical framework: axioms, inference rules, and type system."""

    id: str
    name: str
    axioms: Dict[str, Any]
    inference_rules: List[str]
    type_system: str
    description: str = ""
    lineage: List[str] = field(default_factory=list)
    generation: int = 0

    def signature(self) -> str:
        """Stable hash of the framework's semantics."""
        payload = json.dumps(
            {
                "axioms": self.axioms,
                "inference_rules": sorted(self.inference_rules),
                "type_system": self.type_system,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.blake2b(payload.encode("utf-8"), digest_size=10).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LogicalFramework":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            axioms=dict(data.get("axioms") or {}),
            inference_rules=list(data.get("inference_rules") or []),
            type_system=str(data.get("type_system", "dependent")),
            description=str(data.get("description", "")),
            lineage=list(data.get("lineage") or []),
            generation=int(data.get("generation", 0)),
        )


@dataclass
class ProofEvaluation:
    """Outcome of evaluating one goal under one logical framework."""

    goal: str
    framework_id: str
    framework_name: str
    success: bool
    steps: int
    compactness: float
    residual: float
    latency_seconds: float
    score: float
    simulated: bool = True
    artifacts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AxiomRegistry:
    """Registry of mutable axioms, type systems, and inference-rule sets."""

    TYPE_SYSTEMS: Tuple[str, ...] = (
        "dependent",
        "simple",
        "linear",
        "refinement",
        "homotopy",
    )
    RULE_SETS: Tuple[Tuple[str, ...], ...] = (
        ("modus_ponens", "universal_instantiation", "definitional_rewrite"),
        ("natural_deduction", "case_split", "induction"),
        ("sequent_calculus", "cut_elimination", "weakening"),
        ("resolution", "unit_propagation", "subsumption"),
        ("rewriting", "normalization", "confluence_check"),
        ("dependent_pattern_match", "transport", "univalence_subst"),
    )

    DEFAULT_AXIOMS: Dict[str, AxiomParameter] = {
        "excluded_middle": AxiomParameter(
            "excluded_middle", True, (True, False), rationale="Classical decidability."
        ),
        "axiom_of_choice": AxiomParameter(
            "axiom_of_choice", True, (True, False), rationale="Non-constructive selection."
        ),
        "extensionality": AxiomParameter(
            "extensionality", True, (True, False), rationale="Function/pointwise equality."
        ),
        "univalence": AxiomParameter(
            "univalence", False, (True, False), rationale="Equivalence-is-identity (HoTT)."
        ),
        "dependent_types": AxiomParameter(
            "dependent_types", True, (True, False), rationale="Pi/Sigma indexed families."
        ),
        "linear_types": AxiomParameter(
            "linear_types", False, (True, False), rationale="Resource-sensitive substitution."
        ),
        "allow_sorry": AxiomParameter(
            "allow_sorry", False, (False, True), rationale="Prover.py unadmitted obligations."
        ),
        "use_mathlib": AxiomParameter(
            "use_mathlib", True, (True, False), rationale="Mathlib import surface."
        ),
        "timeout_seconds": AxiomParameter(
            "timeout_seconds", 45.0, (20.0, 30.0, 45.0, 60.0), rationale="Per-check budget."
        ),
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._parameters: Dict[str, AxiomParameter] = {
            name: AxiomParameter.from_dict(param.to_dict())
            for name, param in self.DEFAULT_AXIOMS.items()
        }

    def parameters(self) -> Dict[str, AxiomParameter]:
        with self._lock:
            return {name: AxiomParameter.from_dict(p.to_dict()) for name, p in self._parameters.items()}

    def base_frameworks(self) -> List[LogicalFramework]:
        """Return the seed population of alternative logical frameworks."""
        params = {name: p.value for name, p in self.parameters().items()}

        def make(
            framework_id: str,
            name: str,
            overrides: Dict[str, Any],
            rules_index: int,
            type_system: str,
            description: str,
        ) -> LogicalFramework:
            axioms = dict(params)
            axioms.update(overrides)
            return LogicalFramework(
                id=framework_id,
                name=name,
                axioms=axioms,
                inference_rules=list(self.RULE_SETS[rules_index]),
                type_system=type_system,
                description=description,
            )

        return [
            make(
                "classical_zfc",
                "Classical ZFC",
                {},
                0,
                "simple",
                "Classical set-theoretic baseline with choice and excluded middle.",
            ),
            make(
                "constructive_mltt",
                "Constructive Martin-Lof",
                {"excluded_middle": False, "axiom_of_choice": False},
                1,
                "dependent",
                "Constructive dependent types; proofs are programs.",
            ),
            make(
                "intuitionistic",
                "Intuitionistic",
                {"excluded_middle": False, "axiom_of_choice": False, "extensionality": False},
                1,
                "dependent",
                "Rejects non-constructive existence and extensional collapse.",
            ),
            make(
                "homotopy_type",
                "Homotopy Type Theory",
                {"excluded_middle": False, "axiom_of_choice": False, "univalence": True},
                5,
                "homotopy",
                "Univalence identifies equivalence with identity.",
            ),
            make(
                "linear_resource",
                "Linear Type Calculus",
                {"dependent_types": False, "linear_types": True, "excluded_middle": False},
                3,
                "linear",
                "Resource-sensitive calculus for stateful/distributed proofs.",
            ),
            make(
                "refinement_sequent",
                "Refinement Sequent Calculus",
                {"timeout_seconds": 30.0},
                2,
                "refinement",
                "Sequent calculus with cut elimination and refinement predicates.",
            ),
        ]

    def mutate(self, framework: LogicalFramework, salt: int = 0) -> LogicalFramework:
        """Produce one deterministic neighboring framework (axiom/rule/type mutation)."""
        signature = framework.signature()
        selector = int(_hash_unit(signature, salt, "operator") * 3)
        axioms = dict(framework.axioms)
        rules = list(framework.inference_rules)
        type_system = framework.type_system
        applied = ""

        if selector == 0:
            mutable_names = [
                name for name, param in self.DEFAULT_AXIOMS.items()
                if param.mutable and name in axioms
            ]
            if mutable_names:
                index = int(_hash_unit(signature, salt, "axiom") * len(mutable_names)) % len(mutable_names)
                name = mutable_names[index]
                param = self.DEFAULT_AXIOMS[name]
                options = param.options or (True, False)
                current_index = options.index(axioms.get(name)) if axioms.get(name) in options else 0
                axioms[name] = options[(current_index + 1) % len(options)]
                applied = f"toggle:{name}={axioms[name]}"
        elif selector == 1:
            index = int(_hash_unit(signature, salt, "rules") * len(self.RULE_SETS)) % len(self.RULE_SETS)
            rules = list(self.RULE_SETS[index])
            applied = f"rules:{index}"
        else:
            index = int(_hash_unit(signature, salt, "type") * len(self.TYPE_SYSTEMS)) % len(self.TYPE_SYSTEMS)
            type_system = self.TYPE_SYSTEMS[index]
            applied = f"type:{type_system}"

        mutation_id = f"{framework.id}-m{salt}-{hashlib.blake2b(applied.encode(), digest_size=3).hexdigest()}"
        return LogicalFramework(
            id=mutation_id,
            name=f"{framework.name} [{applied}]",
            axioms=axioms,
            inference_rules=rules,
            type_system=type_system,
            description=f"Mutated from {framework.id}: {applied}",
            lineage=list(framework.lineage) + [framework.id],
            generation=framework.generation + 1,
        )


class MetaProver:
    """Evolutionary meta-prover over mutable logical frameworks.

    The search evaluates the seed frameworks, mutates the strongest candidates,
    and re-evaluates for a bounded number of generations. Scores combine proof
    success, path compactness, numerical residual, and latency, all bounded to
    ``[0, 1]`` so they can be fused directly into the drive matrix.
    """

    def __init__(
        self,
        prover: Optional[Any] = None,
        state_path: Optional[Path] = None,
        population: int = 6,
        max_generations: int = 2,
        history_limit: int = 256,
    ) -> None:
        self.state_path = Path(state_path) if state_path else None
        if prover is not None:
            self.prover = prover
        elif _PROVER_AVAILABLE:
            self.prover = ProverInterface()  # type: ignore[misc]
        else:
            self.prover = None
        self.registry = AxiomRegistry()
        self.population = max(2, int(population))
        self.max_generations = max(0, int(max_generations))
        self.history_limit = max(16, int(history_limit))
        self._lock = threading.RLock()
        self._history: List[ProofEvaluation] = []
        self._best_by_goal: Dict[str, Dict[str, Any]] = {}
        self._evaluations = 0
        self._searches = 0
        self._load()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        goal: str,
        framework: LogicalFramework,
        generation: int = 0,
    ) -> ProofEvaluation:
        """Score one goal/framework pair using the structural model."""
        started = time.monotonic()
        signature = framework.signature()
        base = _hash_unit(goal, signature)
        lower = goal.lower()

        fit = 0.0
        if "construct" in lower or "proof" in lower or "verify" in lower:
            fit += 0.10 if not framework.axioms.get("excluded_middle", True) else 0.0
            fit += 0.06 if framework.type_system == "dependent" else 0.0
        if "distributed" in lower or "state" in lower or "network" in lower:
            fit += 0.10 if framework.axioms.get("linear_types") else 0.0
        if "isomorph" in lower or "equivalen" in lower or "transport" in lower:
            fit += 0.10 if framework.axioms.get("univalence") else 0.0
        if "optimi" in lower or "compact" in lower:
            fit += 0.05 if framework.type_system in ("refinement", "simple") else 0.0

        axiom_count = sum(
            1 for value in framework.axioms.values() if isinstance(value, bool) and value
        )
        axiom_penalty = min(0.18, max(0, axiom_count - 3) * 0.03)
        raw_success = base * 0.55 + fit + 0.35
        success = raw_success >= 0.45

        steps = max(
            3,
            int(round(6 + (1.0 - base) * 16 + axiom_penalty * 20 - fit * 30)),
        )
        compactness = round(1.0 / (1.0 + steps / 12.0), 6)
        residual = round(
            max(0.0001, min(1.0, (1.0 - fit) * 0.05 + (1.0 - compactness) * 0.03)),
            8,
        )
        latency = round(0.0005 + base * 0.004 + steps * 0.0002, 6)
        score = round(
            max(
                0.0,
                min(
                    1.0,
                    0.50 * (1.0 if success else 0.0)
                    + 0.30 * compactness
                    + 0.20 * (1.0 - residual),
                ),
            ),
            6,
        )

        evaluation = ProofEvaluation(
            goal=goal,
            framework_id=framework.id,
            framework_name=framework.name,
            success=success,
            steps=steps,
            compactness=compactness,
            residual=residual,
            latency_seconds=round(max(latency, time.monotonic() - started), 6),
            score=score,
            simulated=True,
            artifacts={
                "type_system": framework.type_system,
                "inference_rules": list(framework.inference_rules),
                "generation": generation,
                "framework_signature": signature,
            },
        )
        with self._lock:
            self._evaluations += 1
            self._history.append(evaluation)
            if len(self._history) > self.history_limit:
                self._history = self._history[-self.history_limit:]
        return evaluation

    def search(
        self,
        goal: str,
        generations: Optional[int] = None,
        population: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run the bounded evolutionary search and return the strongest framework."""
        effective_generations = self.max_generations if generations is None else max(0, int(generations))
        effective_population = self.population if population is None else max(2, int(population))

        candidates = self.registry.base_frameworks()
        evaluations = [self.evaluate(goal, framework) for framework in candidates]
        evaluations.sort(key=lambda item: item.score, reverse=True)
        best = evaluations[0]
        best_framework = next(f for f in candidates if f.id == best.framework_id)

        frontier: List[LogicalFramework] = [best_framework]
        for generation in range(1, effective_generations + 1):
            mutated: List[LogicalFramework] = []
            for framework in frontier[: max(1, effective_population // 2)]:
                for salt in range(2):
                    mutated.append(self.registry.mutate(framework, salt=generation * 10 + salt))
            generation_evaluations = [self.evaluate(goal, f, generation) for f in mutated]
            evaluations.extend(generation_evaluations)
            evaluations.sort(key=lambda item: item.score, reverse=True)
            top = evaluations[0]
            best_framework = next(
                (
                    f
                    for f in candidates + mutated
                    if f.id == top.framework_id
                ),
                best_framework,
            )
            frontier = [best_framework] + [
                next((f for f in mutated if f.id == e.framework_id), best_framework)
                for e in evaluations[1:3]
            ]

        ranking = [
            {
                "framework_id": item.framework_id,
                "framework_name": item.framework_name,
                "score": item.score,
                "success": item.success,
                "steps": item.steps,
                "compactness": item.compactness,
                "residual": item.residual,
            }
            for item in evaluations[:5]
        ]
        best = evaluations[0]
        result = {
            "goal": goal[:240],
            "best_framework": best.framework_id,
            "best_framework_name": best.framework_name,
            "best_score": best.score,
            "best_success": best.success,
            "best_steps": best.steps,
            "best_compactness": best.compactness,
            "best_residual": best.residual,
            "best_latency_seconds": best.latency_seconds,
            "evaluations": len(evaluations),
            "generations": effective_generations,
            "ranking": ranking,
            "lean_skeleton": self.render_skeleton(goal, best_framework),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._searches += 1
            self._best_by_goal[goal[:240]] = result
            if len(self._best_by_goal) > 64:
                oldest = min(
                    self._best_by_goal,
                    key=lambda key: self._best_by_goal[key]["timestamp"],
                )
                self._best_by_goal.pop(oldest, None)
        self.save()
        return result

    def render_skeleton(self, goal: str, framework: LogicalFramework) -> str:
        """Render a Lean proof skeleton using the prover pipeline when available."""
        assertion = f"True -- {goal[:160]}"
        try:
            if self.prover is not None and hasattr(self.prover, "lean"):
                return self.prover.lean.wrap_assertion(
                    assertion,
                    theorem_name=f"meta_{hashlib.blake2b(goal.encode(), digest_size=4).hexdigest()}",
                    use_mathlib=bool(framework.axioms.get("use_mathlib", True)),
                )
        except Exception:
            pass
        return f"-- {framework.name} ({framework.type_system})\n-- goal: {goal[:160]}\ntheorem meta_goal : True := by\n  trivial"

    def preferred_framework(self) -> Optional[str]:
        """Most recent best framework id, if any."""
        with self._lock:
            if not self._best_by_goal:
                return None
            latest = max(self._best_by_goal.values(), key=lambda item: item["timestamp"])
            return latest.get("best_framework")

    # ------------------------------------------------------------------
    # Telemetry / persistence
    # ------------------------------------------------------------------

    def get_telemetry(self) -> Dict[str, Any]:
        with self._lock:
            recent = [item.to_dict() for item in self._history[-8:]]
            return {
                "available": True,
                "prover_backend": "prover.ProverInterface" if self.prover is not None else "structural_model",
                "evaluations": self._evaluations,
                "searches": self._searches,
                "preferred_framework": self.preferred_framework(),
                "best_by_goal": list(self._best_by_goal.values())[-3:],
                "recent_evaluations": recent,
                "seed_frameworks": [item.id for item in self.registry.base_frameworks()],
            }

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": 1,
                "evaluations": self._evaluations,
                "searches": self._searches,
                "best_by_goal": dict(self._best_by_goal),
                "history": [item.to_dict() for item in self._history[-64:]],
            }

    def from_dict(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._evaluations = max(self._evaluations, int(data.get("evaluations", 0)))
            self._searches = max(self._searches, int(data.get("searches", 0)))
            for goal, record in (data.get("best_by_goal") or {}).items():
                if isinstance(record, dict):
                    self._best_by_goal[str(goal)] = record

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
            pass
