"""Abstraction synthesis and multi-encoding reformulation for SASK v2.0.

Two deterministic facilities live here:

``LemmaExtractor``
    Given a solved rewrite (the same program before and after a verified
    transformation), generalize the changed constants into parameters and emit
    a reusable rewrite rule. ``MacroDetector`` finds repeated call sequences and
    proposes macro-operators. Both are persisted through the encrypted
    ``KnowledgeStore`` so proven abstractions compound across cycles.

``MultiEncodingRouter``
    When search stalls under one encoding, retry the problem under the next
    independent encoding (symbolic, constraint/SAT-style backtracking, numeric
    optimization). A candidate is accepted only if the caller's verifier
    accepts it; every attempt is recorded for telemetry.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .knowledge import KnowledgeStore


def _signature(payload: str) -> str:
    return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()


class _ConstantGeneralizer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.constants: List[Any] = []

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float, str)):
            return node
        index = len(self.constants)
        self.constants.append(node.value)
        replacement = ast.Name(id=f"C{index}", ctx=ast.Load())
        return ast.copy_location(replacement, node)


@dataclass(frozen=True)
class RewriteRule:
    kind: str
    lhs: str
    rhs: str
    signature: str
    parameters: Tuple[Tuple[Any, Any], ...] = ()
    generalized_dump: str = ""

    def to_record(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "signature": self.signature,
            "lhs": self.lhs,
            "rhs": self.rhs,
            "parameters": [list(pair) for pair in self.parameters],
        }


@dataclass(frozen=True)
class MacroOperator:
    name: str
    calls: int
    savings: int
    signature: str

    def to_record(self) -> Dict[str, Any]:
        return {
            "kind": "macro_operator",
            "signature": self.signature,
            "name": self.name,
            "calls": self.calls,
            "savings": self.savings,
        }


class LemmaExtractor:
    """Extract a generalized rewrite from a verified before/after pair."""

    def extract(self, before: str, after: str) -> Optional[RewriteRule]:
        try:
            before_tree = ast.parse(before)
            after_tree = ast.parse(after)
            before_text = ast.unparse(before_tree)
            after_text = ast.unparse(after_tree)
        except SyntaxError:
            return None
        left = _ConstantGeneralizer()
        right = _ConstantGeneralizer()
        left_tree = ast.fix_missing_locations(left.visit(before_tree))
        right_tree = ast.fix_missing_locations(right.visit(after_tree))
        left_dump = ast.dump(left_tree, include_attributes=False)
        right_dump = ast.dump(right_tree, include_attributes=False)
        if left_dump != right_dump:
            return None
        pairs = tuple(
            (lvalue, rvalue)
            for lvalue, rvalue in zip(left.constants, right.constants)
            if lvalue != rvalue
        )
        if not pairs:
            return None
        return RewriteRule(
            kind="lemma",
            lhs=before_text,
            rhs=after_text,
            signature=_signature(left_dump),
            parameters=pairs,
            generalized_dump=left_dump,
        )


class MacroDetector:
    """Find repeated call runs inside function bodies."""

    def detect(self, source: str) -> List[MacroOperator]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        operators: List[MacroOperator] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.For, ast.If, ast.While)):
                continue
            run: List[str] = []
            for statement in getattr(node, "body", []):
                name = self._call_name(statement)
                if name is None:
                    if len(run) >= 2:
                        operators.append(self._operator(run))
                    run = []
                    continue
                if run and run[-1] != name:
                    if len(run) >= 2:
                        operators.append(self._operator(run))
                    run = []
                run.append(name)
            if len(run) >= 2:
                operators.append(self._operator(run))
        unique: Dict[str, MacroOperator] = {}
        for operator in operators:
            unique.setdefault(operator.signature, operator)
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _operator(run: Sequence[str]) -> MacroOperator:
        name = run[0]
        return MacroOperator(
            name=f"macro_{name}_x{len(run)}",
            calls=len(run),
            savings=len(run) - 1,
            signature=_signature(f"{name}|{'|'.join(run)}"),
        )

    @staticmethod
    def _call_name(statement: ast.stmt) -> Optional[str]:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            func = statement.value.func
            if isinstance(func, ast.Name):
                return func.id
            if isinstance(func, ast.Attribute):
                return func.attr
        return None


def persist_rules(store: KnowledgeStore, rules: Sequence[Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for rule in rules:
        record = rule.to_record() if hasattr(rule, "to_record") else dict(rule)
        results.append(store.upsert(record, signature_from=json.dumps(record, sort_keys=True, default=str)))
    return results


@dataclass
class EncodingAttempt:
    name: str
    status: str
    steps: int = 0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "status": self.status, "steps": self.steps, "detail": self.detail}


class MultiEncodingRouter:
    """Retry a stalled search under independent encodings."""

    DEFAULT_ORDER = ("symbolic", "constraint", "numeric")

    def __init__(
        self,
        encoders: Mapping[str, Callable[[int], Any]],
        verify: Callable[[Any], bool],
        budget_steps: int = 50,
        order: Optional[Sequence[str]] = None,
    ) -> None:
        self.encoders = dict(encoders)
        self.verify = verify
        self.budget_steps = max(1, int(budget_steps))
        self.order = [name for name in (order or self.DEFAULT_ORDER) if name in self.encoders]
        for name in self.encoders:
            if name not in self.order:
                self.order.append(name)

    def solve(self) -> Dict[str, Any]:
        attempts: List[EncodingAttempt] = []
        for name in self.order:
            encoder = self.encoders[name]
            try:
                candidate = encoder(self.budget_steps)
            except Exception as exc:
                attempts.append(EncodingAttempt(name, "error", self.budget_steps, type(exc).__name__))
                continue
            if candidate is None:
                attempts.append(EncodingAttempt(name, "stalled", self.budget_steps, "no_candidate"))
                continue
            try:
                accepted = bool(self.verify(candidate))
            except Exception as exc:
                attempts.append(EncodingAttempt(name, "error", 0, f"verify:{type(exc).__name__}"))
                continue
            if not accepted:
                attempts.append(EncodingAttempt(name, "rejected", 0, "verifier_rejected"))
                continue
            attempts.append(EncodingAttempt(name, "verified", 0, "accepted"))
            return {
                "solved": True,
                "encoding": name,
                "candidate": candidate,
                "attempts": [attempt.to_dict() for attempt in attempts],
            }
        return {"solved": False, "encoding": None, "candidate": None, "attempts": [attempt.to_dict() for attempt in attempts]}
