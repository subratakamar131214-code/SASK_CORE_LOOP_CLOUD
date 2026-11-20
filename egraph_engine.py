"""Equivalence-class superposition engine (e-graphs) for the SASK kernel.

Transformation pipelines traditionally collapse each rewrite into a single
"current" form, discarding the alternatives that were explored on the way.
This module keeps every equivalent form alive at once:

  * a hash-consed e-graph with union-find + congruence closure,
  * bidirectional rewrite rules saturated to a fixpoint under strict budgets,
  * cost/latency-driven extraction of the best representative form,
  * numeric witness verification before a form is allowed to be promoted.

The same machinery serves three domains requested by the kernel:

  * expressions  — algebraic equals are merged instead of thrown away,
  * code         — arithmetic fragments extracted from real source files,
  * solver steps — traces collapsed to one representative per equivalence class.
"""

from __future__ import annotations

import ast
import itertools
import json
import logging
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("SASK.EGraph")

_ATOM_LIMIT = 5
_BINARY_PRECEDENCE = {
    "add": 1,
    "sub": 1,
    "mul": 2,
    "div": 2,
    "pow": 4,
}
_OP_LATENCY = {
    "add": 1.0,
    "sub": 1.0,
    "mul": 1.7,
    "div": 3.2,
    "pow": 6.0,
    "neg": 0.6,
    "num": 0.2,
    "var": 0.2,
}


# ---------------------------------------------------------------------------
# Expression AST helpers
# ---------------------------------------------------------------------------

Expr = Tuple[Any, ...]


def parse_expression(text: str) -> Expr:
    """Parse a Python arithmetic expression into the canonical node tuples."""
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - defensive boundary
        raise ValueError(f"Unparseable expression: {text!r}") from exc

    binary = {
        ast.Add: "add",
        ast.Sub: "sub",
        ast.Mult: "mul",
        ast.Div: "div",
        ast.Pow: "pow",
    }

    def convert(node: ast.AST) -> Expr:
        if isinstance(node, ast.Expression):
            return convert(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(f"Unsupported constant: {node.value!r}")
            return ("num", float(node.value))
        if isinstance(node, ast.Name):
            return ("var", node.id)
        if isinstance(node, ast.BinOp):
            op = binary.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")
            return (op, convert(node.left), convert(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return ("neg", convert(node.operand))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            return convert(node.operand)
        raise ValueError(f"Unsupported syntax node: {type(node).__name__}")

    return convert(tree)


def _format_constant(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def render(expr: Expr, parent_precedence: int = 0) -> str:
    """Render an expression tuple with precedence-aware parentheses."""
    head = expr[0]
    if head == "num":
        return _format_constant(expr[1])
    if head == "var":
        return str(expr[1])
    if head == "neg":
        inner = render(expr[1], _ATOM_LIMIT)
        text = f"-{inner}"
        return f"({text})" if parent_precedence > 3 else text
    if head not in _BINARY_PRECEDENCE:
        raise ValueError(f"Cannot render operator {head!r}")

    precedence = _BINARY_PRECEDENCE[head]
    left = render(expr[1], precedence)
    right = render(
        expr[2],
        precedence if head in ("add", "mul") else precedence + 1,
    )
    symbol = {"add": "+", "sub": "-", "mul": "*", "div": "/", "pow": "**"}[head]
    text = f"{left} {symbol} {right}"
    if precedence < parent_precedence:
        return f"({text})"
    return text


def expression_variables(expr: Expr) -> List[str]:
    """Collect variable names in deterministic first-seen order."""
    found: List[str] = []

    def walk(node: Expr) -> None:
        if node[0] == "var":
            if node[1] not in found:
                found.append(str(node[1]))
            return
        for child in node[1:]:
            if isinstance(child, tuple):
                walk(child)

    walk(expr)
    return found


def count_nodes(expr: Expr) -> int:
    if expr[0] in ("num", "var"):
        return 1
    return 1 + sum(count_nodes(child) for child in expr[1:] if isinstance(child, tuple))


def evaluate(expr: Expr, env: Dict[str, float]) -> float:
    """Evaluate directly on the node tuple (no ``eval`` of strings)."""
    head = expr[0]
    if head == "num":
        return float(expr[1])
    if head == "var":
        return float(env[str(expr[1])])
    if head == "neg":
        return -evaluate(expr[1], env)
    left = evaluate(expr[1], env)
    right = evaluate(expr[2], env)
    if head == "add":
        return left + right
    if head == "sub":
        return left - right
    if head == "mul":
        return left * right
    if head == "div":
        if right == 0:
            raise ZeroDivisionError("division by zero")
        return left / right
    if head == "pow":
        value = left ** right
        if isinstance(value, complex):
            raise ValueError("complex result")
        return float(value)
    raise ValueError(f"Unknown operator {head!r}")


# ---------------------------------------------------------------------------
# E-graph core
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ENode:
    """A hash-consed node inside an equivalence class."""

    op: str
    children: Tuple[int, ...] = ()
    value: Optional[float] = None


class EGraph:
    """Non-destructive equivalence graph with union-find and congruence closure."""

    def __init__(self) -> None:
        self._parent: Dict[int, int] = {}
        self._rank: Dict[int, int] = {}
        self._hashcons: Dict[Tuple[str, Tuple[int, ...], Optional[float]], int] = {}
        self._next_id = 0
        self._class_index_cache: Optional[Dict[int, List[ENode]]] = None

    # -- construction ---------------------------------------------------
    def _add(self, op: str, children: Tuple[int, ...], value: Optional[float]) -> int:
        key = (op, children, value)
        existing = self._hashcons.get(key)
        if existing is not None:
            return self.find(existing)
        cid = self._next_id
        self._next_id += 1
        self._parent[cid] = cid
        self._rank[cid] = 0
        self._hashcons[key] = cid
        self._class_index_cache = None
        return cid

    def add_leaf(self, op: str, value: Any = None) -> int:
        numeric = None if value is None else float(value)
        return self._add(op, (), numeric)

    def add(self, op: str, children: Sequence[int]) -> int:
        return self._add(op, tuple(self.find(child) for child in children), None)

    def add_expression(self, expr: Expr) -> int:
        head = expr[0]
        if head == "num":
            return self.add_leaf("num", expr[1])
        if head == "var":
            return self._add_leaf_named(str(expr[1]))
        return self.add(head, [self.add_expression(child) for child in expr[1:]])

    def _add_leaf_named(self, name: str) -> int:
        # Variables are keyed by name through a distinct op tag.
        return self._add(f"var:{name}", (), None)

    # -- union-find -----------------------------------------------------
    def find(self, cid: int) -> int:
        parent = self._parent[cid]
        if parent == cid:
            return cid
        root = self.find(parent)
        self._parent[cid] = root
        return root

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        self._class_index_cache = None
        return True

    # -- congruence closure ---------------------------------------------
    def rebuild(self) -> int:
        """Recompute hash-consing keys under the current equivalence relation."""
        merges = 0
        while True:
            fresh: Dict[Tuple[str, Tuple[int, ...], Optional[float]], int] = {}
            collisions: List[Tuple[int, int]] = []
            for (op, children, value), cid in list(self._hashcons.items()):
                root = self.find(cid)
                canonical_children = tuple(self.find(child) for child in children)
                key = (op, canonical_children, value)
                other = fresh.get(key)
                if other is None:
                    fresh[key] = root
                elif self.find(other) != root:
                    collisions.append((other, root))
            self._hashcons = fresh
            if not collisions:
                break
            merged = False
            for a, b in collisions:
                if self.union(a, b):
                    merged = True
                    merges += 1
            if not merged:
                break
        self._class_index_cache = None
        return merges

    # -- views ----------------------------------------------------------
    def node_count(self) -> int:
        return len(self._hashcons)

    def class_count(self) -> int:
        return len({self.find(cid) for cid in self._hashcons.values()})

    def class_index(self) -> Dict[int, List[ENode]]:
        """Map every class root to the list of e-nodes it contains."""
        if self._class_index_cache is not None:
            return self._class_index_cache
        index: Dict[int, List[ENode]] = {}
        for (op, children, value), cid in self._hashcons.items():
            root = self.find(cid)
            index.setdefault(root, []).append(
                ENode(op=op, children=tuple(self.find(c) for c in children), value=value)
            )
        self._class_index_cache = index
        return index

    # -- e-matching -----------------------------------------------------
    def _match(
        self,
        pattern: Expr,
        cid: int,
        subst: Dict[str, int],
        index: Dict[int, List[ENode]],
    ) -> List[Dict[str, int]]:
        head = pattern[0]
        root = self.find(cid)

        if head == "?":
            name = str(pattern[1])
            bound = subst.get(name)
            if bound is not None:
                return [subst] if self.find(bound) == root else []
            extended = dict(subst)
            extended[name] = root
            return [extended]

        if head == "var":
            target = f"var:{pattern[1]}"
            return [subst] if any(node.op == target for node in index.get(root, [])) else []

        results: List[Dict[str, int]] = []
        for node in index.get(root, []):
            if node.op != head:
                continue
            if head == "num":
                if node.value is not None and abs(node.value - float(pattern[1])) <= 1e-12:
                    results.append(subst)
                continue
            arity = len(pattern) - 1
            if len(node.children) != arity:
                continue
            partial: List[Dict[str, int]] = [subst]
            for sub_pattern, child in zip(pattern[1:], node.children):
                stepped: List[Dict[str, int]] = []
                for mapping in partial:
                    stepped.extend(self._match(sub_pattern, child, mapping, index))
                partial = stepped
                if not partial:
                    break
            results.extend(partial)
        return results

    def _instantiate(self, pattern: Expr, subst: Dict[str, int]) -> int:
        head = pattern[0]
        if head == "?":
            return self.find(subst[str(pattern[1])])
        if head == "num":
            return self.add_leaf("num", pattern[1])
        if head == "var":
            return self._add_leaf_named(str(pattern[1]))
        child_ids = [self._instantiate(child, subst) for child in pattern[1:]]
        return self.add(head, child_ids)

    # -- saturation -----------------------------------------------------
    def saturate(
        self,
        rules: Sequence["RewriteRule"],
        max_iterations: int = 12,
        node_budget: int = 4000,
        match_budget: int = 4000,
        time_budget: float = 3.0,
    ) -> Dict[str, Any]:
        started = time.monotonic()
        stats: Dict[str, Any] = {
            "iterations": 0,
            "rewrites": 0,
            "matches": 0,
            "congruence_merges": 0,
            "stopped": "fixpoint",
            "elapsed_seconds": 0.0,
        }

        for iteration in range(max(0, int(max_iterations))):
            if time.monotonic() - started >= time_budget:
                stats["stopped"] = "time_budget"
                break
            if self.node_count() >= node_budget:
                stats["stopped"] = "node_budget"
                break

            index = self.class_index()
            added = 0
            matches = 0
            for rule in rules:
                for root, nodes in index.items():
                    if matches >= match_budget:
                        break
                    for node in nodes:
                        if node.op != rule.lhs[0]:
                            continue
                        if matches >= match_budget:
                            break
                        for subst in self._match(rule.lhs, root, {}, index):
                            matches += 1
                            rhs_id = self._instantiate(rule.rhs, subst)
                            if self.union(root, rhs_id):
                                added += 1
                            if matches >= match_budget:
                                break
                if matches >= match_budget:
                    break

            stats["iterations"] = iteration + 1
            stats["matches"] += matches
            stats["rewrites"] += added
            stats["congruence_merges"] += self.rebuild()
            if added == 0:
                stats["stopped"] = "fixpoint"
                break
        else:
            stats["stopped"] = "iteration_budget"

        stats["elapsed_seconds"] = round(time.monotonic() - started, 4)
        stats["nodes"] = self.node_count()
        stats["classes"] = self.class_count()
        return stats

    # -- extraction -----------------------------------------------------
    def _leaf_expr(self, node: ENode) -> Expr:
        if node.op == "num":
            return ("num", node.value if node.value is not None else 0.0)
        if node.op.startswith("var:"):
            return ("var", node.op[4:])
        return ("var", node.op)

    def extract_best(
        self, root: int, model: "CostModel"
    ) -> Tuple[Optional[Expr], float, float]:
        """Relaxation to the cheapest well-founded form of an equivalence class."""
        index = self.class_index()
        costs: Dict[int, float] = {}
        latencies: Dict[int, float] = {}
        best_node: Dict[int, ENode] = {}
        for _ in range(len(index) + 2):
            changed = False
            for cls, nodes in index.items():
                current = costs.get(cls)
                for node in nodes:
                    if node.op in ("num",) or node.op.startswith("var:") or node.op == "var":
                        cost = model.leaf_cost
                        latency = _OP_LATENCY.get("var", 0.2)
                    else:
                        child_roots = [self.find(c) for c in node.children]
                        if any(child not in costs for child in child_roots):
                            continue
                        cost = model.operator_cost(node.op) + sum(costs[c] for c in child_roots)
                        latency = _OP_LATENCY.get(node.op, 1.0) + sum(
                            latencies.get(c, 0.0) for c in child_roots
                        )
                    if current is None or cost < current - 1e-12:
                        costs[cls] = cost
                        latencies[cls] = latency
                        best_node[cls] = node
                        current = cost
                        changed = True
            if not changed:
                break

        root = self.find(root)
        if root not in best_node:
            return None, math.inf, math.inf

        def materialize(cls: int, depth: int = 0) -> Optional[Expr]:
            node = best_node.get(cls)
            if node is None or depth > len(index) + 2:
                return None
            if node.op in ("num",) or node.op.startswith("var:"):
                return self._leaf_expr(node)
            children: List[Expr] = []
            for child in node.children:
                child_expr = materialize(self.find(child), depth + 1)
                if child_expr is None:
                    return None
                children.append(child_expr)
            return (node.op, *children)

        expr = materialize(root)
        if expr is None:
            return None, math.inf, math.inf
        return expr, costs.get(root, math.inf), latencies.get(root, math.inf)

    def extract_forms(
        self, root: int, limit: int = 6, max_depth: int = 5
    ) -> List[Expr]:
        """Enumerate distinct equivalent forms up to the requested depth."""
        index = self.class_index()
        memo: Dict[Tuple[int, int], List[Expr]] = {}

        def enumerate_class(cid: int, depth: int) -> List[Expr]:
            cls = self.find(cid)
            key = (cls, depth)
            if key in memo:
                return memo[key]
            if depth <= 0:
                return []
            forms: List[Expr] = []
            seen: set = set()
            for node in index.get(cls, []):
                if node.op == "num" or node.op.startswith("var:") or node.op == "var":
                    expr = self._leaf_expr(node)
                else:
                    per_child = [
                        enumerate_class(self.find(child), depth - 1)
                        for child in node.children
                    ]
                    if any(not options for options in per_child):
                        continue
                    produced = 0
                    for combo in itertools.product(*per_child):
                        candidate = (node.op, *combo)
                        marker = render(candidate)
                        if marker in seen:
                            continue
                        seen.add(marker)
                        forms.append(candidate)
                        produced += 1
                        if produced >= limit:
                            break
                    if len(forms) >= limit:
                        break
                    continue
                marker = render(expr)
                if marker not in seen:
                    seen.add(marker)
                    forms.append(expr)
                if len(forms) >= limit:
                    break
            forms.sort(key=count_nodes)
            memo[key] = forms[:limit]
            return memo[key]

        root = self.find(root)
        collected: Dict[str, Expr] = {}
        for depth in range(1, max_depth + 1):
            for expr in enumerate_class(root, depth):
                collected.setdefault(render(expr), expr)
            if len(collected) >= limit:
                break
        ordered = sorted(collected.values(), key=count_nodes)
        return ordered[:limit]


# ---------------------------------------------------------------------------
# Rewrite rules
# ---------------------------------------------------------------------------


def _v(name: str) -> Expr:
    return ("?", name)


@dataclass(frozen=True)
class RewriteRule:
    """A directional rewrite; the e-graph unions both sides regardless."""

    name: str
    lhs: Expr
    rhs: Expr
    category: str = "algebra"
    description: str = ""


def default_rules() -> List[RewriteRule]:
    """Commutative-ring safe rewrites plus cheap simplification identities."""
    x, y, z = _v("x"), _v("y"), _v("z")
    zero = ("num", 0.0)
    one = ("num", 1.0)
    return [
        RewriteRule("add_zero", ("add", x, zero), x, "identity", "x + 0 = x"),
        RewriteRule("zero_add", ("add", zero, x), x, "identity", "0 + x = x"),
        RewriteRule("mul_one", ("mul", x, one), x, "identity", "x * 1 = x"),
        RewriteRule("one_mul", ("mul", one, x), x, "identity", "1 * x = x"),
        RewriteRule("mul_zero", ("mul", x, zero), zero, "annihilator", "x * 0 = 0"),
        RewriteRule("zero_mul", ("mul", zero, x), zero, "annihilator", "0 * x = 0"),
        RewriteRule("add_comm", ("add", x, y), ("add", y, x), "commutativity", "x + y = y + x"),
        RewriteRule("mul_comm", ("mul", x, y), ("mul", y, x), "commutativity", "x * y = y * x"),
        RewriteRule(
            "add_assoc",
            ("add", ("add", x, y), z),
            ("add", x, ("add", y, z)),
            "associativity",
            "(x + y) + z = x + (y + z)",
        ),
        RewriteRule(
            "mul_assoc",
            ("mul", ("mul", x, y), z),
            ("mul", x, ("mul", y, z)),
            "associativity",
            "(x * y) * z = x * (y * z)",
        ),
        RewriteRule(
            "distribute",
            ("mul", x, ("add", y, z)),
            ("add", ("mul", x, y), ("mul", x, z)),
            "distributivity",
            "x * (y + z) = x*y + x*z",
        ),
        RewriteRule("sub_self", ("sub", x, x), zero, "simplify", "x - x = 0"),
        RewriteRule("sub_to_add_neg", ("sub", x, y), ("add", x, ("neg", y)), "normal_form"),
        RewriteRule("neg_neg", ("neg", ("neg", x)), x, "simplify", "-(-x) = x"),
        RewriteRule("add_inverse", ("add", x, ("neg", x)), zero, "simplify", "x + (-x) = 0"),
        RewriteRule("double", ("add", x, x), ("mul", ("num", 2.0), x), "normal_form"),
        RewriteRule("div_one", ("div", x, one), x, "identity", "x / 1 = x"),
        RewriteRule("pow_one", ("pow", x, one), x, "identity", "x^1 = x"),
        RewriteRule("pow_zero", ("pow", x, zero), one, "identity", "x^0 = 1"),
        RewriteRule("pow_two", ("pow", x, ("num", 2.0)), ("mul", x, x), "expand"),
    ]


# ---------------------------------------------------------------------------
# Cost model & verification
# ---------------------------------------------------------------------------


@dataclass
class CostModel:
    """Explicit quality / latency / structural cost weights for extraction."""

    op_costs: Dict[str, float] = field(
        default_factory=lambda: {
            "add": 1.0,
            "sub": 1.0,
            "mul": 2.0,
            "div": 3.0,
            "pow": 5.0,
            "neg": 0.8,
        }
    )
    leaf_cost: float = 1.0
    latency_weight: float = 0.5
    quality_weight: float = 2.0

    def operator_cost(self, op: str) -> float:
        return self.op_costs.get(op, 1.0)

    def score(self, cost: float, latency: float, verified: bool) -> float:
        # Lower is better; unverified forms receive an explicit penalty.
        penalty = 0.0 if verified else self.quality_weight
        return cost + self.latency_weight * latency + penalty


@dataclass
class ExtractedForm:
    expression: str
    cost: float
    latency: float
    nodes: int
    score: float
    verified: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expression": self.expression,
            "cost": round(self.cost, 4),
            "latency": round(self.latency, 4),
            "nodes": self.nodes,
            "score": round(self.score, 4),
            "verified": self.verified,
        }


def verify_equivalence(
    left: Expr,
    right: Expr,
    samples: int = 10,
    seed: int = 0,
    tolerance: float = 1e-9,
) -> Dict[str, Any]:
    """Numeric witness check: two forms must agree on sampled environments."""
    variables = list(dict.fromkeys(expression_variables(left) + expression_variables(right)))
    rng = random.Random(seed)
    checks = 0
    failures = 0
    witness: Optional[Dict[str, float]] = None

    for index in range(samples):
        env = {
            name: rng.choice([-2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 3.0, 4.5])
            for name in variables
        }
        try:
            lval = evaluate(left, env)
            rval = evaluate(right, env)
        except (ZeroDivisionError, ValueError, OverflowError):
            continue
        if not (math.isfinite(lval) and math.isfinite(rval)):
            continue
        checks += 1
        delta = abs(lval - rval)
        if delta > tolerance * max(1.0, abs(lval), abs(rval)):
            failures += 1
            witness = {**env, "left": lval, "right": rval, "delta": delta}
            break

    return {
        "verified": failures == 0 and checks > 0,
        "checks": checks,
        "failures": failures,
        "variables": variables,
        "witness": witness,
    }


# ---------------------------------------------------------------------------
# Engine facade
# ---------------------------------------------------------------------------


class EquivalenceSaturationEngine:
    """Persistent facade that superposes expressions, code fragments and traces."""

    def __init__(
        self,
        state_file: str = ".sask_egraph.json",
        rules: Optional[Sequence[RewriteRule]] = None,
        cost_model: Optional[CostModel] = None,
        max_iterations: int = 10,
        node_budget: int = 3500,
        match_budget: int = 3500,
        time_budget: float = 2.5,
    ) -> None:
        self.state_file = Path(state_file)
        self.rules: List[RewriteRule] = list(rules or default_rules())
        self.cost_model = cost_model or CostModel()
        self.max_iterations = max_iterations
        self.node_budget = node_budget
        self.match_budget = match_budget
        self.time_budget = time_budget
        self._superpositions = 0
        self._rewrites = 0
        self._promotions = 0
        self._rejections = 0
        self._last: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []
        self.load()

    # -- public API -----------------------------------------------------
    def superpose(
        self,
        expressions: Iterable[str],
        goal: Optional[str] = None,
        verify: bool = True,
        form_limit: int = 4,
        origin: str = "expressions",
    ) -> Dict[str, Any]:
        """Saturate all inputs together and rank their cheapest verified forms."""
        unique: Dict[str, Expr] = {}
        rejected: List[Dict[str, str]] = []
        for raw in expressions:
            text = str(raw).strip()
            if not text or text in unique:
                continue
            try:
                unique[text] = parse_expression(text)
            except ValueError as exc:
                rejected.append({"expression": text, "error": str(exc)})

        graph = EGraph()
        roots: Dict[str, int] = {}
        for text, expr in unique.items():
            roots[text] = graph.add_expression(expr)

        goal_root: Optional[int] = None
        if goal:
            try:
                goal_root = graph.add_expression(parse_expression(goal))
            except ValueError as exc:
                rejected.append({"expression": str(goal), "error": str(exc), "goal": "1"})

        stats = graph.saturate(
            self.rules,
            max_iterations=self.max_iterations,
            node_budget=self.node_budget,
            match_budget=self.match_budget,
            time_budget=self.time_budget,
        )

        results: List[Dict[str, Any]] = []
        for text, root in roots.items():
            original = unique[text]
            best, best_cost, best_latency = graph.extract_best(root, self.cost_model)
            forms: List[ExtractedForm] = []
            if best is not None:
                candidates = [best] + graph.extract_forms(root, limit=max(1, form_limit))
                seen: set = set()
                for candidate in candidates:
                    rendered = render(candidate)
                    if rendered in seen:
                        continue
                    seen.add(rendered)
                    if verify:
                        proof = verify_equivalence(original, candidate)
                        verified = bool(proof["verified"])
                    else:
                        verified = True
                    cost = sum(
                        self.cost_model.operator_cost(node[0])
                        if node[0] in self.cost_model.op_costs
                        else self.cost_model.leaf_cost
                        for node in _flatten(candidate)
                    )
                    latency = sum(
                        _OP_LATENCY.get(node[0], 1.0) for node in _flatten(candidate)
                    )
                    forms.append(
                        ExtractedForm(
                            expression=rendered,
                            cost=cost,
                            latency=latency,
                            nodes=count_nodes(candidate),
                            score=self.cost_model.score(cost, latency, verified),
                            verified=verified,
                        )
                    )
                    if len(forms) >= max(1, form_limit):
                        break
                forms.sort(key=lambda item: (item.score, item.nodes))
            best_form = forms[0] if forms else None
            if best_form is not None and best_form.verified:
                self._promotions += 1
            elif best_form is not None:
                self._rejections += 1
            results.append(
                {
                    "input": text,
                    "class": graph.find(root),
                    "canonical_forms": [form.to_dict() for form in forms],
                    "best": best_form.to_dict() if best_form else None,
                    "structural_cost": round(best_cost, 4),
                    "latency": round(best_latency, 4),
                    "class_size": len(graph.class_index().get(graph.find(root), [])),
                }
            )

        groups: Dict[int, List[str]] = {}
        for text, root in roots.items():
            groups.setdefault(graph.find(root), []).append(text)
        merged = [
            {"class": cls, "members": members, "size": len(members)}
            for cls, members in groups.items()
            if len(members) > 1
        ]

        goal_result: Dict[str, Any] = {"provided": bool(goal), "matched": False}
        if goal_root is not None:
            goal_class = graph.find(goal_root)
            matched_inputs = [text for text, root in roots.items() if graph.find(root) == goal_class]
            goal_result = {
                "provided": True,
                "matched": bool(matched_inputs),
                "class": goal_class,
                "proved_from": matched_inputs,
                "best": render(
                    graph.extract_best(goal_root, self.cost_model)[0]
                    or parse_expression(goal)
                ),
            }

        self._superpositions += 1
        self._rewrites += int(stats.get("rewrites", 0))
        result = {
            "origin": origin,
            "expressions": len(unique),
            "rejected": rejected,
            "saturation": stats,
            "results": results,
            "merged_classes": merged,
            "goal": goal_result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._last = {
            "origin": origin,
            "expressions": len(unique),
            "merged_classes": len(merged),
            "rewrites": stats.get("rewrites", 0),
            "elapsed_seconds": stats.get("elapsed_seconds", 0.0),
            "goal_matched": goal_result["matched"],
        }
        self._history.append(self._last)
        self._history = self._history[-24:]
        self.save()
        return result

    def compress_solver_trace(self, steps: Sequence[str]) -> Dict[str, Any]:
        """Collapse consecutive equivalences in a trace to one representative."""
        if not steps:
            return {"input_steps": 0, "output_steps": [], "compression_ratio": 1.0}
        report = self.superpose(steps, origin="solver_trace")
        best_by_text = {
            entry["input"]: entry["best"]["expression"]
            for entry in report["results"]
            if entry.get("best")
        }
        replaced = [best_by_text.get(step, step) for step in steps]
        collapsed = [replaced[0]]
        for step in replaced[1:]:
            if step != collapsed[-1]:
                collapsed.append(step)
        return {
            "input_steps": len(steps),
            "output_steps": collapsed,
            "compression_ratio": round(len(collapsed) / max(1, len(steps)), 4),
            "saturation": report["saturation"],
        }

    def superpose_source_fragments(
        self, sources: Dict[str, str], max_fragments: int = 24
    ) -> Dict[str, Any]:
        """Collect arithmetic fragments from real source and superpose them."""
        fragments: List[str] = []
        origins: Dict[str, str] = {}
        for name, source in sources.items():
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp):
                    try:
                        text = ast.unparse(node)
                    except Exception:  # pragma: no cover - old interpreters
                        continue
                    if len(text) > 160:
                        continue
                    try:
                        parse_expression(text)
                    except ValueError:
                        continue
                    if text not in origins:
                        origins[text] = name
                        fragments.append(text)
                    if len(fragments) >= max_fragments:
                        break
            if len(fragments) >= max_fragments:
                break

        report = self.superpose(fragments, origin="source_fragments")
        report["origins"] = origins
        return report

    # -- telemetry / persistence ---------------------------------------
    def get_telemetry(self) -> Dict[str, Any]:
        return {
            "available": True,
            "engine": "equivalence_saturation",
            "rules": [rule.name for rule in self.rules],
            "superpositions": self._superpositions,
            "rewrites": self._rewrites,
            "promotions": self._promotions,
            "rejections": self._rejections,
            "last": dict(self._last),
            "history": self._history[-6:],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "superpositions": self._superpositions,
            "rewrites": self._rewrites,
            "promotions": self._promotions,
            "rejections": self._rejections,
            "last": self._last,
            "history": self._history,
        }

    def from_dict(self, payload: Dict[str, Any]) -> None:
        self._superpositions = int(payload.get("superpositions", 0))
        self._rewrites = int(payload.get("rewrites", 0))
        self._promotions = int(payload.get("promotions", 0))
        self._rejections = int(payload.get("rejections", 0))
        self._last = dict(payload.get("last", {}))
        self._history = list(payload.get("history", []))[-24:]

    def load(self) -> None:
        try:
            if self.state_file.exists():
                payload = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.from_dict(payload if isinstance(payload, dict) else {})
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load e-graph state: %s", exc)

    def save(self) -> None:
        try:
            payload = json.dumps(self.to_dict(), indent=2)
            temp = self.state_file.with_suffix(".tmp")
            temp.write_text(payload, encoding="utf-8")
            temp.replace(self.state_file)
        except OSError as exc:
            logger.warning("Could not persist e-graph state: %s", exc)


def _flatten(expr: Expr) -> Iterable[Expr]:
    yield expr
    for child in expr[1:]:
        if isinstance(child, tuple):
            yield from _flatten(child)


__all__ = [
    "CostModel",
    "ENode",
    "EGraph",
    "EquivalenceSaturationEngine",
    "ExtractedForm",
    "RewriteRule",
    "count_nodes",
    "default_rules",
    "evaluate",
    "expression_variables",
    "parse_expression",
    "render",
    "verify_equivalence",
]
