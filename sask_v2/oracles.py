"""Automated verification-oracle synthesis and differential consensus.

For a queued goal without a pre-written test, ``OracleSynthesizer`` builds a
multi-angle harness -- deterministic property fuzzing, boundary values,
declared invariants (conservation/dimensional checks), and an optional inverse
round-trip -- and runs it in an isolated subprocess. The candidate source is
statically audited first; harnesses that import dangerous modules or call
sensitive builtins are rejected before execution.

``differential_validate`` cross-checks a candidate against independent
numerical solvers and promotes only on consensus. ``multi_axis_promote``
prevents specification gaming by requiring gains on at least two independent
axes without regressions elsewhere.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import curriculum

MARKER = "@@SASK_ORACLE@@"
MAX_CASES = 64

ALLOWED_MODULES = {
    "math",
    "cmath",
    "statistics",
    "itertools",
    "functools",
    "collections",
    "decimal",
    "fractions",
    "random",
    "json",
    "re",
}
BANNED_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "input",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
}
BANNED_ATTRS = {
    "system",
    "popen",
    "spawn",
    "fork",
    "kill",
    "remove",
    "unlink",
    "rmtree",
    "chmod",
    "write_text",
    "write_bytes",
    "urlopen",
    "request",
    "connect",
}


class OracleError(RuntimeError):
    """Raised when harness synthesis cannot proceed."""


# ---------------------------------------------------------------------------
# Static adversarial audit
# ---------------------------------------------------------------------------


def audit_source(source: str) -> Dict[str, Any]:
    """Reject candidates that reach outside pure computation."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"ok": False, "violations": [f"syntax_error:{exc.msg}"]}
    violations: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_MODULES:
                    violations.append({"line": node.lineno, "kind": "import", "name": alias.name})
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_MODULES:
                violations.append({"line": node.lineno, "kind": "import", "name": node.module})
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
                violations.append({"line": node.lineno, "kind": "call", "name": func.id})
            elif isinstance(func, ast.Attribute) and func.attr in BANNED_ATTRS:
                violations.append({"line": node.lineno, "kind": "attribute", "name": func.attr})
    return {"ok": not violations, "violations": violations[:10]}


def devil_metrics_audit(metrics: Dict[str, float]) -> Dict[str, Any]:
    """Optional bridge to ``devil_advocate`` for stagnation detection."""
    try:
        import devil_advocate  # type: ignore[import]

        engine = devil_advocate.DevilAdvocate()
        assessment = engine.assess(dict(metrics))
        return {
            "available": True,
            "stagnation": bool(assessment.get("stagnation")),
            "cycles_observed": int(assessment.get("cycles_observed", 0) or 0),
        }
    except Exception as exc:  # pragma: no cover - optional dependency
        return {"available": False, "detail": type(exc).__name__}


# ---------------------------------------------------------------------------
# Case generation
# ---------------------------------------------------------------------------


def build_cases(goal: curriculum.GoalSpec, solution_source: str) -> List[Dict[str, Any]]:
    seed = int(hashlib.sha256((goal.goal_id + solution_source).encode("utf-8")).hexdigest()[:12], 16)
    names = list(goal.inputs)
    cases: List[Dict[str, Any]] = []
    seen: set = set()

    def add(kind: str, args: Dict[str, Any]) -> None:
        key = json.dumps(args, sort_keys=True, default=str)
        if key in seen or len(cases) >= MAX_CASES:
            return
        seen.add(key)
        cases.append({"kind": kind, "args": args})

    # Boundary sweep: vary one input at a time while holding the others.
    anchors = {
        name: curriculum.sample_values(goal.inputs[name], count=4, seed=seed + index)[0]
        for index, name in enumerate(names)
    }
    for index, name in enumerate(names):
        values = curriculum.sample_values(goal.inputs[name], count=4, seed=seed + 100 + index)
        for value in values:
            args = dict(anchors)
            args[name] = value
            add("boundary", args)

    # Deterministic property fuzz.
    columns = {
        name: curriculum.sample_values(goal.inputs[name], count=24, seed=seed + 200 + index)
        for index, name in enumerate(names)
    }
    for draw in range(24):
        add("property", {name: columns[name][draw] for name in names})
    return cases


# ---------------------------------------------------------------------------
# Harness execution
# ---------------------------------------------------------------------------

_HARNESS = '''\
import json
import math

{solution}

_CASES = {cases}
_PREDICATE_SRC = {predicate}
_INVERSE_SRC = {inverse}
_INVARIANTS = {invariants}

_SAFE = {{
    "__builtins__": {{}},
    "isfinite": math.isfinite,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "float": float,
    "int": int,
    "bool": bool,
    "len": len,
    "all": all,
    "any": any,
    "sum": sum,
    "solve": solve,
}}


def _eval_expr(expr, env=None):
    return eval(compile(expr, "<oracle>", "eval"), dict(_SAFE), env or {{}})


def main():
    predicate = _eval_expr(_PREDICATE_SRC) if _PREDICATE_SRC else None
    inverse = _eval_expr(_INVERSE_SRC) if _INVERSE_SRC else None
    failures = []
    passed = 0
    skipped = 0
    for case in _CASES:
        args = case.get("args") or {{}}
        kind = case.get("kind", "property")
        problems = []
        try:
            out = solve(**args)
        except Exception as exc:
            failures.append({{"kind": kind, "args": args, "errors": ["crash:" + type(exc).__name__]}})
            continue
        if isinstance(out, float) and not math.isfinite(out):
            failures.append({{"kind": kind, "args": args, "errors": ["nonfinite_output"]}})
            continue
        if predicate is not None:
            try:
                verdict = predicate(**args)
            except Exception as exc:
                problems.append("predicate_raised:" + type(exc).__name__)
            else:
                if not isinstance(verdict, bool):
                    problems.append("predicate_non_bool")
                elif not verdict:
                    problems.append("predicate_false")
        for invariant in _INVARIANTS:
            env = dict(args)
            env["result"] = out
            try:
                value = _eval_expr(invariant, env)
            except NameError:
                skipped += 1
                continue
            except Exception as exc:
                problems.append("invariant_raised:" + type(exc).__name__)
                break
            if not bool(value):
                problems.append("invariant_false")
                break
        if not problems and inverse is not None and len(args) == 1:
            try:
                restored = inverse(out)
                reference = list(args.values())[0]
                if abs(float(restored) - float(reference)) > 1e-6:
                    problems.append("inverse_mismatch")
            except Exception:
                problems.append("inverse_raised")
        if problems:
            failures.append({{"kind": kind, "args": args, "errors": problems}})
        else:
            passed += 1
    print(
        "{marker}"
        + json.dumps(
            {{
                "executed": len(_CASES),
                "passed": passed,
                "skipped_invariants": skipped,
                "failures": failures[:20],
            }}
        )
    )


if __name__ == "__main__":
    main()
'''


def run_harness(
    solution_source: str,
    cases: Sequence[Dict[str, Any]],
    predicate_src: str,
    invariants: Sequence[str],
    inverse_src: str = "",
    timeout: float = 15.0,
) -> Dict[str, Any]:
    source = _HARNESS.format(
        solution=solution_source,
        cases=repr(list(cases)),
        predicate=repr(str(predicate_src or "")),
        inverse=repr(str(inverse_src or "")),
        invariants=repr(list(invariants or ())),
        marker=MARKER,
    )
    with tempfile.TemporaryDirectory(prefix="sask-oracle-") as raw:
        path = Path(raw) / "oracle_harness.py"
        path.write_text(source, encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "TZ": "UTC",
                "SASK_DETERMINISTIC": "1",
            }
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(path)],
                cwd=raw,
                env=env,
                capture_output=True,
                text=True,
                timeout=max(0.5, float(timeout)),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "harness_timeout", "detail": f"exceeded {timeout:.1f}s"}
    payload: Optional[Dict[str, Any]] = None
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(MARKER):
            try:
                payload = json.loads(line[len(MARKER):])
            except ValueError:
                payload = None
            break
    if payload is None:
        return {
            "ok": False,
            "reason": "harness_crash",
            "detail": (proc.stderr or proc.stdout or "no result marker")[-500:],
        }
    executed = int(payload.get("executed", 0) or 0)
    passed = int(payload.get("passed", 0) or 0)
    return {
        "ok": executed > 0 and passed == executed,
        "executed": executed,
        "passed": passed,
        "failed": executed - passed,
        "skipped_invariants": int(payload.get("skipped_invariants", 0) or 0),
        "failures": payload.get("failures") or [],
    }


class OracleSynthesizer:
    """Build, audit, and execute a multi-angle verification harness."""

    def synthesize(
        self,
        goal: curriculum.GoalSpec,
        solution_source: str,
        timeout: float = 15.0,
    ) -> Dict[str, Any]:
        audit = audit_source(solution_source)
        if not audit["ok"]:
            return {"ok": False, "reason": "audit_rejected", "audit": audit, "checks": {}}
        try:
            cases = build_cases(goal, solution_source)
        except curriculum.FalsifiabilityError as exc:
            return {"ok": False, "reason": "case_generation_failed", "detail": str(exc), "audit": audit}
        result = run_harness(
            solution_source=solution_source,
            cases=cases,
            predicate_src=goal.predicate_src,
            invariants=goal.invariants,
            inverse_src=goal.inverse_src,
            timeout=timeout,
        )
        kinds: Dict[str, Dict[str, int]] = {}
        for case in cases:
            bucket = kinds.setdefault(case["kind"], {"executed": 0, "failed": 0})
            bucket["executed"] += 1
        for failure in result.get("failures") or ():
            bucket = kinds.get(str(failure.get("kind")))
            if bucket is not None:
                bucket["failed"] += 1
        checks = {
            name: {"executed": stats["executed"], "ok": stats["failed"] == 0}
            for name, stats in kinds.items()
        }
        checks["invariants"] = {
            "declared": len(goal.invariants),
            "skipped": result.get("skipped_invariants", 0),
            "ok": bool(result.get("ok")),
        }
        return {
            "ok": bool(result.get("ok")),
            "audit": audit,
            "checks": checks,
            "executed": result.get("executed", 0),
            "passed": result.get("passed", 0),
            "failures": result.get("failures") or [],
            "reason": result.get("reason"),
        }


# ---------------------------------------------------------------------------
# Differential validation
# ---------------------------------------------------------------------------


def _exact_decay(t: float, y0: float = 1.0, k: float = 1.0) -> float:
    return y0 * math.exp(-k * t)


def _rk4_decay(t: float, y0: float = 1.0, k: float = 1.0, steps: int = 200) -> float:
    h = t / steps
    y = y0
    for _ in range(steps):
        k1 = -k * y
        k2 = -k * (y + 0.5 * h * k1)
        k3 = -k * (y + 0.5 * h * k2)
        k4 = -k * (y + h * k3)
        y += (h / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
    return y


def _euler_decay(t: float, y0: float = 1.0, k: float = 1.0, steps: int = 4000) -> float:
    h = t / steps
    y = y0
    for _ in range(steps):
        y += h * (-k * y)
    return y


SOLVERS: Dict[str, Callable[..., float]] = {
    "exact": _exact_decay,
    "rk4": _rk4_decay,
    "euler": _euler_decay,
}


def differential_validate(
    candidate: Callable[[float], float],
    t: float = 1.0,
    params: Optional[Dict[str, float]] = None,
    rtol: float = 1e-3,
) -> Dict[str, Any]:
    """Promote only when >=2 independent solvers agree and the candidate joins them."""
    kwargs = dict(params or {})
    outputs: Dict[str, float] = {}
    for name, solver in SOLVERS.items():
        try:
            outputs[name] = float(solver(t, **kwargs))
        except Exception:  # pragma: no cover - defensive
            continue
    if len(outputs) < 2:
        return {"promoted": False, "reason": "insufficient_solvers", "outputs": outputs}
    values = sorted(outputs.values())
    best_group: List[str] = []
    for name, value in outputs.items():
        group = [other for other, candidate_value in outputs.items() if abs(candidate_value - value) <= rtol * max(1.0, abs(value))]
        if len(group) > len(best_group):
            best_group = group
    consensus = sum(outputs[name] for name in best_group) / len(best_group)
    try:
        cand = float(candidate(t))
    except Exception as exc:
        return {"promoted": False, "reason": f"candidate_raised:{type(exc).__name__}", "outputs": outputs}
    delta = abs(cand - consensus)
    agrees = delta <= rtol * max(1.0, abs(consensus))
    promoted = len(best_group) >= 2 and agrees
    return {
        "promoted": promoted,
        "reason": "consensus" if promoted else ("candidate_diverged" if len(best_group) >= 2 else "no_consensus"),
        "outputs": {name: round(value, 8) for name, value in outputs.items()},
        "consensus": round(consensus, 8),
        "consensus_group": best_group,
        "candidate": round(cand, 8),
        "delta": round(delta, 10),
    }


def multi_axis_promote(
    candidate: Dict[str, float],
    baseline: Dict[str, float],
    axes: Sequence[str] = ("compression", "ood_generalization"),
    min_improved: int = 2,
    tol: float = 1e-9,
) -> Dict[str, Any]:
    """Require independent-axis gains, with no regressions on shared axes."""
    improved: List[str] = []
    regressed: List[str] = []
    unchanged: List[str] = []
    for axis in axes:
        if axis not in candidate or axis not in baseline:
            continue
        delta = float(candidate[axis]) - float(baseline[axis])
        if delta > tol:
            improved.append(axis)
        elif delta < -tol:
            regressed.append(axis)
        else:
            unchanged.append(axis)
    promoted = len(improved) >= max(1, int(min_improved)) and not regressed
    return {
        "promoted": promoted,
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "reason": "multi_axis_gain" if promoted else ("regression" if regressed else "insufficient_axes"),
    }
