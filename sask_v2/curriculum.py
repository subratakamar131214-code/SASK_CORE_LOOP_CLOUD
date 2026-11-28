"""Self-regulating goal and curriculum engine for SASK v2.0.

The engine converts unresolved signals (devil-advocate counterexamples,
cross-domain transfers, proof/rewrite frontier stalls) into *falsifiable*
sub-goals. A goal is only queued if it declares explicit inputs, invariants, a
compute budget, and a machine-checkable predicate that returns a bool on
sampled inputs. Generation is deterministic and template-based: no language
model is consulted, so the same source signal always yields the same goal.

When the rolling pass rate saturates, the engine emits frontier tasks whose
target difficulty lies in the 30%-70% band, plus compression tasks that try to
collapse similar accumulated rules into a tighter general principle.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

MIN_PASS_BAND = 0.30
MAX_PASS_BAND = 0.70
SATURATION_DEFAULT = 0.80
QUEUE_LIMIT = 256

_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "sum": sum,
}
_PREDICATE_GLOBALS = {"__builtins__": {}, "isfinite": math.isfinite, **_SAFE_BUILTINS}

_IDENT = re.compile(r"[^A-Za-z0-9_]+")


class FalsifiabilityError(ValueError):
    """A queued goal failed the strict falsifiability gate."""


def _identifier(text: str, fallback: str = "value") -> str:
    cleaned = _IDENT.sub("_", str(text)).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"g_{cleaned}"
    return cleaned[:40]


def _stable_id(*parts: Any) -> str:
    material = "|".join(str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def compile_predicate(source: str) -> Any:
    """Compile a predicate expression in a restrictive namespace."""
    if not isinstance(source, str) or not source.strip():
        raise FalsifiabilityError("predicate is empty")
    try:
        code = compile(source.strip(), "<goal-predicate>", "eval")
        fn = eval(code, dict(_PREDICATE_GLOBALS))  # noqa: S307 - restricted globals
    except Exception as exc:  # pragma: no cover - defensive
        raise FalsifiabilityError(f"predicate does not compile: {type(exc).__name__}") from exc
    if not callable(fn):
        raise FalsifiabilityError("predicate is not callable")
    return fn


def sample_values(spec: str, count: int = 4, seed: int = 0) -> List[Any]:
    """Generate deterministic values from a compact type grammar."""
    rng = random.Random(seed)
    text = str(spec).strip()
    if text.startswith("int:"):
        _, lo, hi = text.split(":", 2)
        low, high = int(float(lo)), int(float(hi))
        low, high = min(low, high), max(low, high)
        values = [low, high, (low + high) // 2]
        values += [rng.randint(low, high) for _ in range(max(0, count - len(values)))]
        return values[:count]
    if text.startswith("float:"):
        _, lo, hi = text.split(":", 2)
        low, high = float(lo), float(hi)
        low, high = min(low, high), max(low, high)
        values = [low, high, (low + high) / 2.0]
        values += [rng.uniform(low, high) for _ in range(max(0, count - len(values)))]
        return values[:count]
    if text == "bool":
        return [True, False, True, False][:count]
    if text.startswith("choice:"):
        options = [item for item in text[len("choice:"):].split("|") if item]
        if not options:
            raise FalsifiabilityError("empty choice domain")
        return [options[index % len(options)] for index in range(count)]
    if text == "int":
        return [0, 1, -1, 2][:count]
    if text == "float":
        return [0.0, 1.0, -1.0, 0.5][:count]
    raise FalsifiabilityError(f"unsupported input grammar: {spec!r}")


@dataclass(frozen=True)
class GoalSpec:
    goal_id: str
    statement: str
    source: str
    inputs: Dict[str, str]
    invariants: Tuple[str, ...]
    budget: Dict[str, float]
    predicate_src: str
    difficulty: float = 0.5
    expected_gain: float = 0.5
    inverse_src: str = ""
    status: str = "queued"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "statement": self.statement,
            "source": self.source,
            "inputs": dict(self.inputs),
            "invariants": list(self.invariants),
            "budget": dict(self.budget),
            "predicate_src": self.predicate_src,
            "difficulty": self.difficulty,
            "expected_gain": self.expected_gain,
            "inverse_src": self.inverse_src,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GoalSpec":
        return cls(
            goal_id=str(payload["goal_id"]),
            statement=str(payload["statement"]),
            source=str(payload.get("source", "unknown")),
            inputs={str(k): str(v) for k, v in (payload.get("inputs") or {}).items()},
            invariants=tuple(str(item) for item in (payload.get("invariants") or ())),
            budget={str(k): float(v) for k, v in (payload.get("budget") or {}).items()},
            predicate_src=str(payload["predicate_src"]),
            difficulty=float(payload.get("difficulty", 0.5)),
            expected_gain=float(payload.get("expected_gain", 0.5)),
            inverse_src=str(payload.get("inverse_src", "")),
            status=str(payload.get("status", "queued")),
        )

    def cost(self) -> float:
        steps = float(self.budget.get("max_steps", 0) or 0)
        seconds = float(self.budget.get("seconds", 0) or 0)
        return max(1.0, steps / 100.0 + seconds)

    def score(self) -> float:
        return round(float(self.expected_gain) / self.cost(), 6)


def validate_goal(goal: GoalSpec, seed: int = 0) -> GoalSpec:
    """Enforce the falsifiability gate; return the goal or raise."""
    problems: List[str] = []
    if not goal.statement.strip():
        problems.append("missing statement")
    if not goal.inputs:
        problems.append("missing inputs")
    for name, grammar in goal.inputs.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            problems.append(f"invalid input name {name!r}")
            continue
        try:
            sample_values(grammar, count=1, seed=seed)
        except FalsifiabilityError as exc:
            problems.append(str(exc))
    if not goal.invariants:
        problems.append("missing invariants")
    if not goal.budget or not (
        float(goal.budget.get("max_steps", 0) or 0) > 0
        or float(goal.budget.get("seconds", 0) or 0) > 0
    ):
        problems.append("missing compute budget (max_steps/seconds)")
    if not 0.0 <= float(goal.difficulty) <= 1.0:
        problems.append("difficulty outside [0,1]")
    predicate = None
    if not goal.predicate_src.strip():
        problems.append("missing predicate")
    else:
        try:
            predicate = compile_predicate(goal.predicate_src)
        except FalsifiabilityError as exc:
            problems.append(str(exc))
    if goal.inverse_src.strip():
        try:
            compile_predicate(goal.inverse_src)
        except FalsifiabilityError as exc:
            problems.append(f"inverse: {exc}")

    if problems:
        raise FalsifiabilityError("; ".join(problems))

    if predicate is not None:
        names = list(goal.inputs)
        first = {name: sample_values(goal.inputs[name], count=1, seed=seed)[0] for name in names}
        try:
            verdict = predicate(**first)
        except Exception as exc:
            raise FalsifiabilityError(f"predicate failed on sample inputs: {type(exc).__name__}") from exc
        if not isinstance(verdict, bool):
            raise FalsifiabilityError("predicate must return bool")
    return goal


class CurriculumEngine:
    """Deterministic replenishment, saturation tracking, frontier and compression."""

    def __init__(self, queue_limit: int = QUEUE_LIMIT) -> None:
        self.queue_limit = max(8, int(queue_limit))
        self._queue: List[GoalSpec] = []
        self._seen: set = set()
        self._passes: Deque[bool] = deque(maxlen=40)
        self._observations: Dict[str, Deque[bool]] = {}
        self._saturation_events = 0
        self._frontier_emitted = 0
        self._compression_emitted = 0

    # -- ingestion ---------------------------------------------------------

    def _push(self, goal: GoalSpec) -> Optional[GoalSpec]:
        if goal.goal_id in self._seen or len(self._queue) >= self.queue_limit:
            return None
        try:
            validate_goal(goal)
        except FalsifiabilityError:
            return None
        self._seen.add(goal.goal_id)
        self._queue.append(goal)
        return goal

    def add_counterexample(self, item: Dict[str, Any]) -> Optional[GoalSpec]:
        metric = _identifier(item.get("metric", "metric"))
        try:
            target = float(item.get("inverted_target", item.get("observed_value", 0.0)))
        except (TypeError, ValueError):
            target = 0.0
        tolerance = 0.05
        statement = f"Disprove the entrenched assumption on {metric} near target {target:.4f}."
        predicate = f"lambda {metric}: abs(float({metric}) - {target:.6f}) <= {tolerance:.6f}"
        goal = GoalSpec(
            goal_id=_stable_id("counterexample", metric, target),
            statement=statement,
            source="devil_advocate",
            inputs={metric: "float:-10:10"},
            invariants=(f"isfinite({metric})", f"abs({metric} - {target:.6f}) >= 0"),
            budget={"max_steps": 200, "seconds": 60.0, "ram_mb": 256.0},
            predicate_src=predicate,
            difficulty=0.55,
            expected_gain=1.0,
        )
        return self._push(goal)

    def add_transfer(self, item: Dict[str, Any]) -> Optional[GoalSpec]:
        confidence = float(item.get("confidence", 0.0) or 0.0)
        source_domain = _identifier(item.get("source_domain", "source"))
        target_domain = _identifier(item.get("target_domain", "target"))
        statement = (
            f"Re-derive the {source_domain}->{target_domain} transfer and reach "
            f"confidence >= {min(1.0, max(0.0, confidence)):.3f}."
        )
        goal = GoalSpec(
            goal_id=_stable_id("transfer", source_domain, target_domain, confidence),
            statement=statement,
            source="manifold_bridge",
            inputs={"score": "float:0:1"},
            invariants=("isfinite(score)", "score >= 0"),
            budget={"max_steps": 250, "seconds": 90.0, "ram_mb": 256.0},
            predicate_src=f"lambda score: float(score) >= {min(1.0, max(0.0, confidence)):.6f}",
            difficulty=0.5,
            expected_gain=0.8,
        )
        return self._push(goal)

    def add_frontier(self, item: Dict[str, Any]) -> Optional[GoalSpec]:
        source = str(item.get("source", "egraph_engine"))
        try:
            cost = float(item.get("cost", item.get("verified_cost", 0.0)))
        except (TypeError, ValueError):
            cost = 0.0
        target = max(0.0, cost * 0.8)
        goal = GoalSpec(
            goal_id=_stable_id("frontier", source, cost),
            statement=f"Reduce the verified {source} frontier cost below {target:.4f}.",
            source=source if source in {"egraph_engine", "prover", "meta_prover"} else "egraph_engine",
            inputs={"cost": "float:0:100"},
            invariants=("isfinite(cost)", "cost >= 0"),
            budget={"max_steps": 300, "seconds": 120.0, "ram_mb": 384.0},
            predicate_src=f"lambda cost: float(cost) <= {target:.6f}",
            difficulty=0.65,
            expected_gain=0.7,
        )
        return self._push(goal)

    def replenish(
        self,
        counterexamples: Iterable[Dict[str, Any]] = (),
        transfers: Iterable[Dict[str, Any]] = (),
        frontiers: Iterable[Dict[str, Any]] = (),
    ) -> List[GoalSpec]:
        created: List[GoalSpec] = []
        for item in counterexamples:
            goal = self.add_counterexample(dict(item))
            if goal is not None:
                created.append(goal)
        for item in transfers:
            goal = self.add_transfer(dict(item))
            if goal is not None:
                created.append(goal)
        for item in frontiers:
            goal = self.add_frontier(dict(item))
            if goal is not None:
                created.append(goal)
        self._queue.sort(key=lambda goal: (-goal.score(), goal.goal_id))
        return created

    # -- execution feedback ------------------------------------------------

    def observe(self, goal_id: str, passed: bool) -> Dict[str, Any]:
        self._passes.append(bool(passed))
        window = self._observations.setdefault(goal_id, deque(maxlen=16))
        window.append(bool(passed))
        return self.status()

    def pass_rate(self) -> Optional[float]:
        if not self._passes:
            return None
        return sum(1 for item in self._passes if item) / len(self._passes)

    def is_saturated(self, threshold: float = SATURATION_DEFAULT, min_samples: int = 8) -> bool:
        rate = self.pass_rate()
        return rate is not None and len(self._passes) >= min_samples and rate >= threshold

    def status(self) -> Dict[str, Any]:
        return {
            "queued": len(self._queue),
            "pass_rate": None if self.pass_rate() is None else round(float(self.pass_rate()), 4),
            "samples": len(self._passes),
            "saturated": self.is_saturated(),
            "saturation_events": self._saturation_events,
            "frontier_emitted": self._frontier_emitted,
            "compression_emitted": self._compression_emitted,
        }

    # -- curriculum expansion ----------------------------------------------

    def frontier_tasks(self, count: int = 3) -> List[GoalSpec]:
        count = max(0, int(count))
        if count == 0:
            return []
        self._saturation_events += 1
        created: List[GoalSpec] = []
        for index in range(count):
            target = MIN_PASS_BAND + (MAX_PASS_BAND - MIN_PASS_BAND) * (index + 1) / (count + 1)
            statement = (
                f"Frontier probe {index + 1}: solve a task calibrated to a {target:.2f} "
                "expected pass rate at the current capability boundary."
            )
            goal = GoalSpec(
                goal_id=_stable_id("curriculum-frontier", target, self._saturation_events),
                statement=statement,
                source="curriculum_frontier",
                inputs={"score": "float:0:1"},
                invariants=("isfinite(score)",),
                budget={"max_steps": 400, "seconds": 180.0, "ram_mb": 512.0},
                predicate_src=f"lambda score: abs(float(score) - {target:.6f}) <= 0.2",
                difficulty=round(target, 4),
                expected_gain=0.6,
            )
            placed = self._push(goal)
            if placed is not None:
                created.append(placed)
                self._frontier_emitted += 1
        return created

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        left_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9.]+", left))
        right_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9.]+", right))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def compression_tasks(self, rules: Sequence[Dict[str, Any]], limit: int = 3) -> List[GoalSpec]:
        created: List[GoalSpec] = []
        for i in range(len(rules)):
            for j in range(i + 1, len(rules)):
                if len(created) >= max(0, int(limit)):
                    return created
                left, right = rules[i], rules[j]
                similarity = self._similarity(str(left.get("lhs", "")), str(right.get("lhs", "")))
                if similarity < 0.5:
                    continue
                left_sig = _stable_id("rule", left.get("signature", left.get("lhs", "")))
                right_sig = _stable_id("rule", right.get("signature", right.get("lhs", "")))
                goal = GoalSpec(
                    goal_id=_stable_id("compression", left_sig, right_sig),
                    statement=(
                        f"Collapse rewrite rules {left_sig[:8]} and {right_sig[:8]} "
                        f"(similarity {similarity:.2f}) into one general principle."
                    ),
                    source="compression",
                    inputs={"holds": "bool"},
                    invariants=("merged_rule_preserves_equivalence",),
                    budget={"max_steps": 300, "seconds": 150.0, "ram_mb": 384.0},
                    predicate_src="lambda holds: bool(holds)",
                    difficulty=0.6,
                    expected_gain=0.7,
                )
                placed = self._push(goal)
                if placed is not None:
                    created.append(placed)
                    self._compression_emitted += 1
        return created

    def maybe_expand(self, rules: Sequence[Dict[str, Any]] = ()) -> Dict[str, List[GoalSpec]]:
        """Emit frontier/compression work when the pass window is saturated."""
        if not self.is_saturated():
            return {"frontier": [], "compression": []}
        return {
            "frontier": self.frontier_tasks(3),
            "compression": self.compression_tasks(rules, 2),
        }

    # -- queue access ------------------------------------------------------

    def next_goals(self, limit: int = 5) -> List[GoalSpec]:
        ordered = sorted(self._queue, key=lambda goal: (-goal.score(), goal.goal_id))
        return ordered[: max(0, int(limit))]

    def pop_next(self) -> Optional[GoalSpec]:
        goals = self.next_goals(1)
        if not goals:
            return None
        goal = goals[0]
        self._queue = [item for item in self._queue if item.goal_id != goal.goal_id]
        return goal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queue": [goal.to_dict() for goal in self._queue],
            "passes": list(self._passes),
            "saturation_events": self._saturation_events,
            "frontier_emitted": self._frontier_emitted,
            "compression_emitted": self._compression_emitted,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CurriculumEngine":
        engine = cls()
        for item in payload.get("queue") or ():
            try:
                goal = GoalSpec.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            engine._seen.add(goal.goal_id)
            engine._queue.append(goal)
        engine._passes.extend(bool(item) for item in payload.get("passes") or ())
        engine._saturation_events = int(payload.get("saturation_events", 0) or 0)
        engine._frontier_emitted = int(payload.get("frontier_emitted", 0) or 0)
        engine._compression_emitted = int(payload.get("compression_emitted", 0) or 0)
        return engine
