"""Verify Module: Automated test suite runner for validating software components.

Provides automated test suite discovery, program execution, integrity checks,
and structured diagnostic reporting.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import io
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type


@dataclass
class VerificationReport:
    """Consolidated report summarizing test execution and integrity checks."""
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_successful(self) -> bool:
        """Returns True if all checks passed without failures or errors."""
        return self.failed_checks == 0 and len(self.errors) == 0


@dataclass
class EgoRatchetState:
    """Persisted high-watermark telemetry for the regression ratchet."""
    high_watermark_passed: int = 0
    high_watermark_total: int = 0
    gate_runs: int = 0
    updated_at: Optional[str] = None


class EgoRatchet:
    """Persistent high-watermark ratchet: never regress below the best-known pass count."""

    DEFAULT_PATH = ".sask_ego_ratchet.json"

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self.state_path = Path(state_path or self.DEFAULT_PATH)
        self.state = self._load()

    def _load(self) -> EgoRatchetState:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return EgoRatchetState(
                high_watermark_passed=int(data.get("high_watermark_passed", 0)),
                high_watermark_total=int(data.get("high_watermark_total", 0)),
                gate_runs=int(data.get("gate_runs", 0)),
                updated_at=data.get("updated_at"),
            )
        except (OSError, ValueError, TypeError):
            return EgoRatchetState()

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(asdict(self.state), indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)

    def check(self, passed_checks: int) -> bool:
        """True if the pass count is at or above the persisted high-watermark."""
        return passed_checks >= self.state.high_watermark_passed

    def record(self, passed_checks: int, total_checks: int) -> bool:
        """Record a gate run; promotes the high-watermark and returns True if promoted."""
        self.state.gate_runs += 1
        promoted = passed_checks > self.state.high_watermark_passed
        if promoted:
            self.state.high_watermark_passed = passed_checks
        if total_checks > self.state.high_watermark_total:
            self.state.high_watermark_total = total_checks
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return promoted

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self.state)


class AncestralRegressionGate:
    """Ancestral regression gate combining overthinking consensus with an ego ratchet."""

    def __init__(self, ratchet_path: Optional[Path] = None) -> None:
        self.ratchet = EgoRatchet(ratchet_path)

    def evaluate(self, report: VerificationReport, depth: int) -> Dict[str, Any]:
        """Apply the ego ratchet to a consensus report, mutating it on regression."""
        snapshot_before = self.ratchet.snapshot()
        regressed = not self.ratchet.check(report.passed_checks)
        promoted = self.ratchet.record(report.passed_checks, report.total_checks)
        snapshot_after = self.ratchet.snapshot()

        info: Dict[str, Any] = {
            "overthinking_depth": depth,
            "passed_checks": report.passed_checks,
            "high_watermark_before": snapshot_before["high_watermark_passed"],
            "high_watermark_after": snapshot_after["high_watermark_passed"],
            "promoted": promoted,
            "regressed": regressed,
            "gate_runs": snapshot_after["gate_runs"],
        }

        if regressed:
            report.failed_checks += 1
            report.errors.append(
                f"EGO RATCHET: passed {report.passed_checks} checks, below high-watermark "
                f"{snapshot_before['high_watermark_passed']}."
            )
        return info


class ComponentIntegrityChecker:
    """Validates structural integrity, imports, and interface adherence of modules."""

    @staticmethod
    def verify_importable(module_name: str) -> bool:
        """Check if a module can be imported cleanly without exceptions."""
        try:
            importlib.import_module(module_name)
            return True
        except Exception:
            return False

    @staticmethod
    def check_module_exports(
        module_name: str,
        expected_symbols: List[str],
    ) -> Dict[str, bool]:
        """Verify that expected classes or functions exist in the target module."""
        try:
            mod = importlib.import_module(module_name)
            return {sym: hasattr(mod, sym) for sym in expected_symbols}
        except Exception:
            return {sym: False for sym in expected_symbols}


class FastFailPrecheck:
    """Cheap pre-flight gate that aborts before the expensive test depth loop."""

    REQUIRED_FILES = (
        "core.py",
        "drives.py",
        "bio_gen.py",
        "telos.py",
        "mutate.py",
        "prover.py",
        "verify.py",
        "science_mcp.py",
        "meta_prover.py",
        "telos_v2.py",
        "manifold_bridge.py",
        "devil_advocate.py",
        "egraph_engine.py",
        "requirements.txt",
    )

    @classmethod
    def run(cls, root: Optional[Path] = None) -> Dict[str, Any]:
        started = time.monotonic()
        base = Path(root) if root is not None else Path(__file__).resolve().parent
        failures: List[str] = []
        checked = 0

        for filename in cls.REQUIRED_FILES:
            checked += 1
            if not (base / filename).is_file():
                failures.append(f"PRE-CHECK: required file missing: {filename}")

        for path in sorted(base.glob("*.py")):
            checked += 1
            try:
                source = path.read_text(encoding="utf-8")
                compile(source, str(path), "exec")
            except SyntaxError as exc:
                failures.append(
                    f"PRE-CHECK: syntax error in {path.name}:{exc.lineno}: {exc.msg}"
                )
            except OSError as exc:
                failures.append(f"PRE-CHECK: unreadable source {path.name}: {exc}")

        return {
            "success": not failures,
            "checks": checked,
            "failures": failures,
            "elapsed_seconds": round(time.monotonic() - started, 4),
        }


class VerificationRunner:
    """Automated test runner orchestrating discovery and programmatic execution."""

    def __init__(
        self,
        verbosity: int = 1,
        ratchet_path: Optional[Path] = None,
    ) -> None:
        self.verbosity = verbosity
        self.gate = AncestralRegressionGate(ratchet_path=ratchet_path)

    def run_suite(self, suite: unittest.TestSuite) -> VerificationReport:
        """Execute a unittest.TestSuite and compile a VerificationReport."""
        stream = io.StringIO()
        runner = unittest.TextTestRunner(stream=stream, verbosity=self.verbosity)

        start_time = time.monotonic()
        result = runner.run(suite)
        duration = time.monotonic() - start_time

        errors: List[str] = []
        for test_case, err in result.errors:
            errors.append(f"ERROR: {test_case} - {err.strip()}")
        for test_case, failure in result.failures:
            errors.append(f"FAILURE: {test_case} - {failure.strip()}")

        passed = result.testsRun - len(result.failures) - len(result.errors)

        return VerificationReport(
            total_checks=result.testsRun,
            passed_checks=passed,
            failed_checks=len(result.failures) + len(result.errors),
            errors=errors,
            duration_seconds=round(duration, 4),
            details={"output": stream.getvalue()},
        )

    def run_tests_in_module(self, test_case_class: Type[unittest.TestCase]) -> VerificationReport:
        """Run all test methods in a single TestCase class."""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(test_case_class)
        return self.run_suite(suite)

    def run_self_verification(self) -> VerificationReport:
        """Run built-in verification suite covering the core modules."""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(PipelineComponentTests)
        return self.run_suite(suite)

    def run_zero_trust_verification(self) -> VerificationReport:
        """Prove the tiered gate still catches tampering, regressions, and spikes."""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(ZeroTrustPipelineTests)
        return self.run_suite(suite)

    def run_diff_scoped(
        self,
        changed_files: Iterable[str],
        pipeline: Optional["ZeroTrustPipeline"] = None,
    ) -> VerificationReport:
        """Verify only the modules a diff touches; fall back to a full sweep."""
        pipeline = pipeline or ZeroTrustPipeline()
        scope = pipeline.scope_for(list(changed_files))
        if scope["mode"] == "full-sweep":
            report = self.run_self_verification()
            report.details["zero_trust_scope"] = scope
            return report

        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for name in scope["targeted_tests"]:
            suite.addTest(PipelineComponentTests(name))
        report = self.run_suite(suite)
        report.details["zero_trust_scope"] = scope
        return report

    @staticmethod
    def evaluate_candidate(spec: CandidateSpec, pipeline: Optional["ZeroTrustPipeline"] = None) -> VerificationVerdict:
        """Run the tiered zero-trust pipeline over a candidate change."""
        pipeline = pipeline or ZeroTrustPipeline()
        return pipeline.evaluate(spec)

    @staticmethod
    def _resolve_depth(depth_override: Optional[int] = None) -> int:
        """Resolve the overthinking consensus depth from drives.py."""
        if depth_override is not None:
            return max(1, int(depth_override))
        try:
            from drives import get_overthinking_depth  # type: ignore[import]
            return max(1, int(get_overthinking_depth()))
        except Exception:
            return 3

    def run_ancestral_gate(
        self,
        depth_override: Optional[int] = None,
    ) -> VerificationReport:
        """Run the suite at overthinking depth, requiring consensus, then apply the ego ratchet.

        Every pass must succeed. The consensus report uses the minimum pass count
        across runs, so the ratchet is insensitive to depth changes.
        """
        depth = self._resolve_depth(depth_override)
        run_reports: List[VerificationReport] = []

        precheck = FastFailPrecheck.run()
        if not precheck["success"]:
            return VerificationReport(
                total_checks=precheck["checks"],
                passed_checks=precheck["checks"] - len(precheck["failures"]),
                failed_checks=len(precheck["failures"]),
                errors=list(precheck["failures"]),
                duration_seconds=precheck["elapsed_seconds"],
                details={
                    "overthinking_depth": depth,
                    "passes_completed": 0,
                    "precheck": precheck,
                    "fast_failed": True,
                },
            )

        for _ in range(depth):
            report = self.run_self_verification()
            run_reports.append(report)
            if not report.is_successful:
                break

        total_checks = max((r.total_checks for r in run_reports), default=0)
        passed_checks = min((r.passed_checks for r in run_reports), default=0)
        failed_checks = max((r.failed_checks for r in run_reports), default=0)

        errors: List[str] = []
        for pass_index, report in enumerate(run_reports):
            for error in report.errors:
                errors.append(f"[pass {pass_index + 1}/{depth}] {error}")

        completed = len(run_reports)
        if completed < depth:
            failed_checks = max(failed_checks, 1)
            errors.append(
                f"OVERTHINKING GATE: consensus aborted after {completed}/{depth} passes."
            )

        consensus = VerificationReport(
            total_checks=total_checks,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            errors=errors,
            duration_seconds=round(sum(r.duration_seconds for r in run_reports), 4),
            details={"overthinking_depth": depth, "passes_completed": completed},
        )

        zero_trust_report = self.run_zero_trust_verification()
        consensus.total_checks += zero_trust_report.total_checks
        consensus.passed_checks += zero_trust_report.passed_checks
        consensus.failed_checks += zero_trust_report.failed_checks
        consensus.errors.extend(
            f"[zero-trust] {error}" for error in zero_trust_report.errors
        )
        consensus.duration_seconds = round(
            consensus.duration_seconds + zero_trust_report.duration_seconds, 4
        )
        consensus.details["zero_trust_self_test"] = {
            "total": zero_trust_report.total_checks,
            "passed": zero_trust_report.passed_checks,
            "failed": zero_trust_report.failed_checks,
            "duration_seconds": zero_trust_report.duration_seconds,
        }

        ratchet_info = self.gate.evaluate(consensus, depth=depth)
        consensus.details["ego_ratchet"] = ratchet_info
        return consensus


# ---------------------------------------------------------------------------
# Tiered zero-trust verification pipeline
# ---------------------------------------------------------------------------

INVARIANT_VERSION = "zt-1"
_VERIFY_ROOT = Path(__file__).resolve().parent
GOLDEN_SUITE_PATH = _VERIFY_ROOT / "verify_golden_suite.json"
HOLDOUT_SUITE_PATH = _VERIFY_ROOT / "verify_holdout_suite.json"
INTEGRITY_MANIFEST_PATH = _VERIFY_ROOT / "verify_integrity.json"
VERIFY_CACHE_PATH = _VERIFY_ROOT / ".sask_verify_cache.json"

PROTECTED_ARTIFACTS: Tuple[str, ...] = (
    "verify.py",
    "verify_golden_suite.json",
    "verify_holdout_suite.json",
)
# Suites are append-only by design (ratcheting), so they are sealed per-case
# rather than by whole-file digest.
SUITE_ARTIFACTS: Tuple[str, ...] = (
    "verify_golden_suite.json",
    "verify_holdout_suite.json",
)

FORMAL_MODULES = frozenset(
    {"prover.py", "meta_prover.py", "telos_v2.py", "verify.py", "mutate.py"}
)
SAFE_EXCEPTIONS = frozenset(
    {"TypeError", "ValueError", "ZeroDivisionError", "OverflowError", "KeyError"}
)
ALLOWED_IMPORTS = frozenset(
    {
        "__future__", "abc", "array", "bisect", "cmath", "collections", "copy",
        "dataclasses", "datetime", "decimal", "enum", "fractions", "functools",
        "hashlib", "heapq", "itertools", "json", "math", "numbers", "operator",
        "random", "re", "statistics", "string", "textwrap", "time", "typing",
        "unicodedata",
    }
)
FORBIDDEN_CALLS = frozenset(
    {
        "eval", "exec", "compile", "__import__", "breakpoint", "input", "open",
        "globals", "locals", "vars",
    }
)
FORBIDDEN_ATTRS = frozenset(
    {
        "write_text", "write_bytes", "unlink", "rmdir", "removedirs", "mkdir",
        "makedirs", "touch", "system", "popen", "remove", "rename", "replace",
        "rmtree", "move", "copyfile", "urlopen", "socket", "connect", "sendall",
        "modules", "_exit", "exit",
    }
)
FORBIDDEN_MODULES = frozenset(
    {
        "os", "sys", "subprocess", "shutil", "socket", "http", "urllib",
        "requests", "ftplib", "smtplib", "telnetlib", "importlib", "ctypes",
        "multiprocessing", "threading", "asyncio", "pathlib", "io",
    }
)
EDGE_CORPUS: Tuple[Any, ...] = (
    None, True, 0, -1, 1, 2**53, -(2**53), 1e308, -1e308,
    float("inf"), float("-inf"), float("nan"), "", "x" * 256, [],
    [1, 2, 3], {"a": 1},
)

# Diff-scoped verification: a change confined to one of these modules only
# needs the tests that physically import/exercise it. ``core.py`` is absent on
# purpose — touching the kernel orchestrator forces a full sweep.
MODULE_TEST_MAP: Dict[str, List[str]] = {
    "telos.py": [
        "test_telos_engine_milestones",
        "test_telos_tactical_subquests_and_metabolic_reset",
        "test_goal_ingestion_engine",
        "test_telos_science_goal_dispatch",
    ],
    "telos_v2.py": [
        "test_telos_engine_milestones",
        "test_goal_ingestion_engine",
        "test_dual_track_macro_discovery_taxonomy",
    ],
    "mutate.py": ["test_mutate_code_and_text", "test_surgical_ast_modifier"],
    "prover.py": [
        "test_prover_execution",
        "test_lean4_prover_pipeline",
        "test_prover_bidirectional_meet_in_the_middle",
    ],
    "drives.py": [
        "test_drives_discipline_engine",
        "test_drives_execution_friction",
        "test_curiosity_free_energy_and_autonomous_goals",
    ],
    "bio_gen.py": ["test_bio_gen_harness_spawn", "test_bio_gen_router_harness"],
    "devil_advocate.py": [
        "test_devil_advocate_stagnation_inversion",
        "test_boundary_probes_goal_injection_and_science_matrix",
    ],
    "egraph_engine.py": ["test_egraph_equivalence_saturation_and_compression"],
    "manifold_bridge.py": ["test_manifold_cross_domain_isomorphism_and_transfer"],
    "science_mcp.py": ["test_science_router_matrix"],
    "meta_prover.py": [
        "test_meta_prover_axiomatic_search",
        "test_counterfactual_sandbox_invariants_and_promotion",
    ],
    "verify.py": [
        "test_fast_fail_precheck_and_syntax_cache",
        "test_ego_ratchet_high_watermark",
        "test_ancestral_gate_regression_detection",
    ],
}


@dataclass
class VerificationVerdict:
    """Structured verdict returned to mutate.py and written to telemetry."""

    passed: bool
    failed_tier: Optional[str] = None
    reason_code: str = "ok"
    reason: str = ""
    counterexample: Optional[Dict[str, Any]] = None
    duration_ms: float = 0.0
    speedup_ratio: Optional[float] = None
    cached: bool = False
    tier_timings: Dict[str, float] = field(default_factory=dict)
    scope: Dict[str, Any] = field(default_factory=dict)
    ratcheted: Optional[Dict[str, Any]] = None
    feedback: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateSpec:
    """A candidate submitted for tiered verification."""

    module_source: str
    baseline_source: Optional[str] = None
    entrypoint: str = "solve"
    cases: List[Any] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    formal: bool = False
    critical: bool = True
    allow_nonfinite: bool = False
    touch_lean: bool = False
    lean_source: Optional[str] = None
    invariants: List[Dict[str, Any]] = field(default_factory=list)
    metamorphic: List[Dict[str, Any]] = field(default_factory=list)
    timeout_seconds: float = 2.5
    memory_limit_mb: float = 128.0
    enforce_budget: bool = True
    use_cache: bool = True
    label: str = "candidate"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case_args(case: Any) -> List[Any]:
    if isinstance(case, dict) and "args" in case:
        args = case["args"]
        return list(args) if isinstance(args, (list, tuple)) else [args]
    return list(case) if isinstance(case, (list, tuple)) else [case]


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<depth-limit>"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value[:256] if isinstance(value, str) else value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in value[:32]]
    if isinstance(value, dict):
        return {str(key)[:64]: _json_safe(item, depth + 1) for key, item in list(value.items())[:32]}
    return {"__type__": type(value).__name__, "repr": repr(value)[:200]}


def _canonical(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=False)


def _file_digest(path: Path) -> Optional[str]:
    """SHA-256 of LF-normalized content so CRLF checkouts hash identically."""
    try:
        data = path.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _source_digest(source: Optional[str]) -> Optional[str]:
    if source is None:
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return hashlib.sha256(source.encode("utf-8", "replace")).hexdigest()
    normalized = ast.dump(tree, include_attributes=False)
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()


class IntegrityGuard:
    """Tier-0 cryptographic seal over verifier code, fixtures, and specs."""

    @staticmethod
    def snapshot(root: Path, names: Iterable[str] = PROTECTED_ARTIFACTS) -> Dict[str, str]:
        files: Dict[str, str] = {}
        for name in names:
            digest = _file_digest(Path(root) / name)
            if digest is not None:
                files[name] = digest
        return files

    @staticmethod
    def _suite_cases(path: Path) -> Optional[Dict[str, str]]:
        """Per-case digests for an append-only suite, or None if unreadable."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list):
            return None
        digests: Dict[str, str] = {}
        for entry in cases:
            if isinstance(entry, dict) and entry.get("id"):
                material = _canonical(entry)
                digests[str(entry["id"])] = hashlib.sha256(
                    material.encode("utf-8")
                ).hexdigest()
        return digests

    @classmethod
    def seal(cls, root: Path = _VERIFY_ROOT, manifest_path: Optional[Path] = None) -> Dict[str, Any]:
        root = Path(root)
        payload = {
            "invariant_version": INVARIANT_VERSION,
            "algorithm": "sha256-lf-normalized",
            "sealed_at": _utc_now(),
            "files": cls.snapshot(root, ("verify.py",)),
            "suites": {
                name: {"cases": cls._suite_cases(root / name) or {}}
                for name in SUITE_ARTIFACTS
            },
        }
        target = Path(manifest_path) if manifest_path else (
            INTEGRITY_MANIFEST_PATH if root == _VERIFY_ROOT else root / "verify_integrity.json"
        )
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    @classmethod
    def verify(
        cls,
        root: Path = _VERIFY_ROOT,
        manifest_path: Optional[Path] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        root = Path(root)
        target = Path(manifest_path) if manifest_path else (
            INTEGRITY_MANIFEST_PATH if root == _VERIFY_ROOT else root / "verify_integrity.json"
        )
        try:
            manifest = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, {"reason_code": "integrity_manifest_missing", "path": str(target)}
        failures: List[str] = []
        if manifest.get("invariant_version") != INVARIANT_VERSION:
            failures.append(
                f"invariant_version {manifest.get('invariant_version')!r} != {INVARIANT_VERSION!r}"
            )
        recorded = manifest.get("files") or {}
        for name in ("verify.py",):
            current = _file_digest(root / name)
            expected = recorded.get(name)
            if expected is None:
                failures.append(f"{name}: absent from sealed manifest")
            elif current is None:
                failures.append(f"{name}: missing on disk")
            elif current != expected:
                failures.append(f"{name}: digest mismatch")

        sealed_suites = manifest.get("suites")
        if not isinstance(sealed_suites, dict):
            sealed_suites = {}
            for name in SUITE_ARTIFACTS:
                if name in recorded:
                    sealed_suites[name] = {"legacy_digest": recorded[name]}
        for name in SUITE_ARTIFACTS:
            sealed = sealed_suites.get(name)
            if not isinstance(sealed, dict):
                failures.append(f"{name}: absent from sealed manifest")
                continue
            current_cases = cls._suite_cases(root / name)
            if current_cases is None:
                failures.append(f"{name}: missing or unreadable")
                continue
            legacy = sealed.get("legacy_digest")
            if legacy is not None:
                if _file_digest(root / name) != legacy:
                    failures.append(f"{name}: digest mismatch")
                continue
            for case_id, digest in (sealed.get("cases") or {}).items():
                if case_id not in current_cases:
                    failures.append(f"{name}: sealed case {case_id} removed")
                elif current_cases[case_id] != digest:
                    failures.append(f"{name}: sealed case {case_id} modified")
        return not failures, {
            "reason_code": "integrity_ok" if not failures else "integrity_mismatch",
            "failures": failures,
            "manifest": str(target),
        }


class AstGuard:
    """Tier-0 static guardrails: tampering, injection, I/O, and reward hacking."""

    @classmethod
    def scan(cls, source: str, known_inputs: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
        violations: List[Dict[str, Any]] = []
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return [{
                "code": "syntax_error",
                "line": exc.lineno,
                "message": exc.msg,
            }]
        literals = {
            _json_safe(item)
            for item in (known_inputs or [])
            if isinstance(item, (int, float, str))
            and not isinstance(item, bool)
            and item not in (0, 1, -1)
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in ALLOWED_IMPORTS:
                        violations.append(cls._v(node, "forbidden_import", f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    violations.append(cls._v(node, "forbidden_import", f"from {node.module} import ..."))
            elif isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in FORBIDDEN_CALLS:
                    violations.append(cls._v(node, "forbidden_call", f"{name}("))
                if name == "open":
                    mode = ""
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = str(kw.value.value)
                    if any(flag in mode for flag in ("w", "a", "x", "+")):
                        violations.append(cls._v(node, "forbidden_write", "open(mode=write)"))
            elif isinstance(node, ast.Attribute):
                if node.attr in FORBIDDEN_ATTRS and node.attr != "modules":
                    parent = node.value.id if isinstance(node.value, ast.Name) else ""
                    if parent in ("sys", "os", "shutil", "socket", "urllib"):
                        violations.append(
                            cls._v(node, "forbidden_attribute", f"{parent}.{node.attr}")
                        )
            elif isinstance(node, ast.ExceptHandler):
                body = node.body or []
                suppressed = all(
                    isinstance(stmt, ast.Pass)
                    or (
                        isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Constant)
                        and stmt.value.value is Ellipsis
                    )
                    for stmt in body
                )
                if suppressed:
                    violations.append(
                        cls._v(node, "suppressed_exception", "except: pass")
                    )
            elif isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if (
                        isinstance(comparator, ast.Constant)
                        and literals
                        and _json_safe(comparator.value) in literals
                    ):
                        violations.append(
                            cls._v(
                                node,
                                "hardcoded_test_input",
                                f"comparison against known fixture literal {comparator.value!r}",
                            )
                        )
            elif isinstance(node, (ast.Dict, ast.Set, ast.List, ast.Tuple)):
                hits = 0
                elements = node.keys if isinstance(node, ast.Dict) else node.elts
                for element in elements or []:
                    if isinstance(element, ast.Constant) and _json_safe(element.value) in literals:
                        hits += 1
                if hits >= 2:
                    violations.append(
                        cls._v(node, "hardcoded_test_input", f"literal fixture table ({hits} known inputs)")
                    )
        return violations

    @staticmethod
    def _v(node: ast.AST, code: str, message: str) -> Dict[str, Any]:
        return {"code": code, "line": getattr(node, "lineno", None), "message": message}


_INTEGRITY_GUARD = IntegrityGuard


_DRIVER_SOURCE = r'''
import importlib.util
import json
import math
import random
import sys
import tracemalloc
import time

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def normalize(value):
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return {"__type__": type(value).__name__, "repr": repr(value)[:200]}

def arguments(case):
    if isinstance(case, dict) and "args" in case:
        args = case["args"]
        return list(args) if isinstance(args, (list, tuple)) else [args]
    return list(case) if isinstance(case, (list, tuple)) else [case]

def main():
    config = json.loads(open(sys.argv[1], encoding="utf-8").read())
    random.seed(int(config.get("seed", 0)))
    module = load(config["module_path"], config.get("module_name", "sask_candidate"))
    entry = getattr(module, config["entrypoint"])
    results = []
    tracemalloc.start()
    try:
        for case in config["cases"]:
            args = arguments(case)
            started = time.perf_counter()
            try:
                value = entry(*args)
                record = {"ok": True, "value": normalize(value)}
            except BaseException as exc:
                record = {"ok": False, "error": type(exc).__name__, "message": str(exc)[:200]}
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 4)
            results.append(record)
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    payload = {"results": results, "peak_kb": round(peak / 1024.0, 2)}
    sys.stdout.write("@@SASK_RESULT@@" + json.dumps(payload) + "\n")

main()
'''


class SubprocessSandbox:
    """Tier-1 deterministic subprocess envelope with wall-clock and RAM ceilings."""

    def __init__(self, timeout_seconds: float = 2.5, memory_limit_mb: float = 128.0) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.memory_limit_mb = max(1.0, float(memory_limit_mb))

    def run(
        self,
        source: str,
        entrypoint: str,
        cases: List[Any],
        timeout_seconds: Optional[float] = None,
        memory_limit_mb: Optional[float] = None,
    ) -> Dict[str, Any]:
        timeout = self.timeout_seconds if timeout_seconds is None else max(0.05, float(timeout_seconds))
        limit = self.memory_limit_mb if memory_limit_mb is None else max(1.0, float(memory_limit_mb))
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="sask-zt-") as raw:
            tmp = Path(raw)
            module_path = tmp / "sask_candidate.py"
            module_path.write_text(source, encoding="utf-8")
            driver = tmp / "_sask_zt_driver.py"
            driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
            config = tmp / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "module_path": str(module_path),
                        "module_name": "sask_candidate",
                        "entrypoint": entrypoint,
                        "cases": cases,
                        "seed": 0,
                    }
                ),
                encoding="utf-8",
            )
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
                    [sys.executable, "-I", str(driver), str(config)],
                    cwd=str(tmp),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "reason_code": "timeout",
                    "detail": f"candidate exceeded {timeout:.2f}s wall-clock envelope",
                    "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                }
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        marker = "@@SASK_RESULT@@"
        payload: Optional[Dict[str, Any]] = None
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith(marker):
                try:
                    payload = json.loads(line[len(marker):])
                except ValueError:
                    payload = None
                break
        if payload is None:
            return {
                "ok": False,
                "reason_code": "subprocess_crash",
                "detail": (proc.stderr or proc.stdout or "no result marker")[-400:],
                "elapsed_ms": elapsed_ms,
            }
        peak_mb = float(payload.get("peak_kb", 0.0)) / 1024.0
        if peak_mb > limit:
            return {
                "ok": False,
                "reason_code": "memory_envelope_exceeded",
                "detail": f"peak {peak_mb:.1f} MB > {limit:.1f} MB ceiling",
                "elapsed_ms": elapsed_ms,
                "peak_mb": round(peak_mb, 3),
            }
        return {
            "ok": True,
            "results": payload.get("results") or [],
            "peak_mb": round(peak_mb, 3),
            "elapsed_ms": elapsed_ms,
            "returncode": proc.returncode,
        }


def _values_match(left: Any, right: Any, epsilon: float = 1e-9) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)) and not isinstance(left, bool) and not isinstance(right, bool):
        if math.isnan(float(left)) and math.isnan(float(right)):
            return True
        if math.isinf(float(left)) or math.isinf(float(right)):
            return float(left) == float(right)
        return math.isclose(float(left), float(right), rel_tol=epsilon, abs_tol=epsilon)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_values_match(a, b, epsilon) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_values_match(left[k], right[k], epsilon) for k in left)
    return left == right


def _contains_nonfinite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, list):
        return any(_contains_nonfinite(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_nonfinite(item) for item in value.values())
    return False


class RatchetSuite:
    """Permanent golden/holdout suite that only ever grows."""

    def __init__(self, path: Path, name: str) -> None:
        self.path = Path(path)
        self.name = name
        self._lock = __import__("threading").RLock()

    def load(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
                return payload
        except (OSError, ValueError):
            pass
        return {"suite": self.name, "version": INVARIANT_VERSION, "cases": []}

    def append(self, case: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        with self._lock:
            payload = self.load()
            cases = payload.setdefault("cases", [])
            signature = _canonical(
                {
                    "kind": case.get("kind"),
                    "args": case.get("args"),
                    "scope": case.get("scope"),
                }
            )
            entry_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
            for existing in cases:
                if str(existing.get("id")) == entry_id:
                    return existing, False
            entry = dict(case)
            entry["id"] = entry_id
            entry.setdefault("created_at", _utc_now())
            entry["sanitizer"] = "verify.ratchet.v1"
            cases.append(entry)
            payload["updated_at"] = _utc_now()
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return entry, True


class VerificationResultCache:
    """Memoized verdicts keyed by normalized AST, dependencies, and suite state."""

    def __init__(self, path: Optional[Path], capacity: int = 256) -> None:
        self.path = Path(path) if path else None
        self.capacity = max(16, int(capacity))
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._hits = 0
        self._misses = 0
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            entries = payload.get("entries") if isinstance(payload, dict) else None
            if isinstance(entries, dict):
                self._entries = {
                    str(key): value for key, value in entries.items() if isinstance(value, dict)
                }
        except (OSError, ValueError):
            self._entries = {}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.write_text(
                json.dumps({"version": INVARIANT_VERSION, "entries": self._entries}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        value = self._entries.get(key)
        if value is None:
            self._misses += 1
        else:
            self._hits += 1
        return value

    def put(self, key: str, value: Dict[str, Any]) -> None:
        if key not in self._entries and len(self._entries) >= self.capacity:
            oldest = next(iter(self._entries))
            self._entries.pop(oldest, None)
        self._entries[key] = value
        self._save()

    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "entries": len(self._entries)}


def _linear_form(node: ast.AST) -> Tuple[Dict[str, float], float]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return {}, float(node.value)
    if isinstance(node, ast.Name):
        return {node.id: 1.0}, 0.0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        terms, const = _linear_form(node.operand)
        return {key: -value for key, value in terms.items()}, -const
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _linear_form(node.operand)
    if isinstance(node, ast.BinOp):
        left_terms, left_const = _linear_form(node.left)
        right_terms, right_const = _linear_form(node.right)
        if isinstance(node.op, ast.Add):
            merged = dict(left_terms)
            for key, value in right_terms.items():
                merged[key] = merged.get(key, 0.0) + value
            return merged, left_const + right_const
        if isinstance(node.op, ast.Sub):
            merged = dict(left_terms)
            for key, value in right_terms.items():
                merged[key] = merged.get(key, 0.0) - value
            return merged, left_const - right_const
        if isinstance(node.op, ast.Mult):
            if not left_terms:
                return (
                    {key: value * left_const for key, value in right_terms.items()},
                    left_const * right_const,
                )
            if not right_terms:
                return (
                    {key: value * right_const for key, value in left_terms.items()},
                    left_const * right_const,
                )
    raise ValueError("expression is not linear")


def _parse_constraint(text: str) -> Tuple[Dict[str, float], float, ast.cmpop]:
    tree = ast.parse(text, mode="eval").body
    if not isinstance(tree, ast.Compare) or len(tree.ops) != 1:
        raise ValueError(f"constraint must be a single comparison: {text!r}")
    left_terms, left_const = _linear_form(tree.left)
    right_terms, right_const = _linear_form(tree.comparators[0])
    terms = dict(left_terms)
    for key, value in right_terms.items():
        terms[key] = terms.get(key, 0.0) - value
    return terms, left_const - right_const, tree.ops[0]


_COMPARATORS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Eq: lambda a, b: a == b,
}


class ZeroTrustPipeline:
    """Ordered, short-circuiting verification: cheap rejection first, deep proof last."""

    def __init__(
        self,
        root: Optional[Path] = None,
        golden_path: Optional[Path] = None,
        holdout_path: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
        cache_path: Optional[Path] = None,
        enforce_integrity: bool = True,
        telemetry: bool = True,
        solver_factory: Optional[Any] = None,
    ) -> None:
        self.root = Path(root) if root else _VERIFY_ROOT
        self.golden = RatchetSuite(golden_path or (self.root / "verify_golden_suite.json"), "golden")
        self.holdout = RatchetSuite(holdout_path or (self.root / "verify_holdout_suite.json"), "holdout")
        self.manifest_path = Path(manifest_path) if manifest_path else (
            INTEGRITY_MANIFEST_PATH if self.root == _VERIFY_ROOT else self.root / "verify_integrity.json"
        )
        self.enforce_integrity = bool(enforce_integrity)
        self.telemetry = bool(telemetry)
        self.solver_factory = solver_factory
        self.cache = VerificationResultCache(cache_path if cache_path is not None else (
            VERIFY_CACHE_PATH if self.root == _VERIFY_ROOT else None
        ))

    # -- public API ------------------------------------------------------
    def evaluate(self, spec: CandidateSpec) -> VerificationVerdict:
        started = time.perf_counter()
        self._active_scope = self._scope_key(spec)
        scope = self.scope_for(spec.changed_files)
        verdict = VerificationVerdict(passed=False, scope=scope)

        tier0 = self._tier0(spec)
        verdict.tier_timings["tier0_ms"] = self._elapsed(started)
        if not tier0["passed"]:
            return self._finish(verdict, started, "tier0", tier0)

        key = self._cache_key(spec)
        if spec.use_cache:
            hit_started = time.perf_counter()
            cached = self.cache.get(key)
            if cached is not None:
                verdict.passed = bool(cached.get("passed"))
                verdict.failed_tier = cached.get("failed_tier")
                verdict.reason_code = str(cached.get("reason_code") or "ok")
                verdict.reason = str(cached.get("reason") or "cached verdict")
                verdict.counterexample = cached.get("counterexample")
                verdict.speedup_ratio = cached.get("speedup_ratio")
                verdict.cached = True
                verdict.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
                verdict.tier_timings["cache_ms"] = round(
                    (time.perf_counter() - hit_started) * 1000.0, 3
                )
                verdict.feedback = [f"cache: {verdict.reason_code} (reused verdict)"]
                self._log(verdict, spec, key)
                return verdict

        mark = time.perf_counter()
        tier1 = self._tier1(spec)
        verdict.tier_timings["tier1_ms"] = round((time.perf_counter() - mark) * 1000.0, 3)
        verdict.speedup_ratio = tier1.get("speedup_ratio")
        if not tier1["passed"]:
            return self._finish(verdict, started, "tier1", tier1, key)

        mark = time.perf_counter()
        tier2 = self._tier2(spec)
        verdict.tier_timings["tier2_ms"] = round((time.perf_counter() - mark) * 1000.0, 3)
        if not tier2["passed"]:
            return self._finish(verdict, started, "tier2", tier2, key)

        if self._needs_formal(spec):
            mark = time.perf_counter()
            tier3 = self._tier3(spec)
            verdict.tier_timings["tier3_ms"] = round((time.perf_counter() - mark) * 1000.0, 3)
            if not tier3["passed"]:
                return self._finish(verdict, started, "tier3", tier3, key)
        else:
            verdict.tier_timings["tier3_ms"] = 0.0

        mark = time.perf_counter()
        tier4 = self._tier4(spec, tier1, tier2)
        verdict.tier_timings["tier4_ms"] = round((time.perf_counter() - mark) * 1000.0, 3)
        if not tier4["passed"]:
            return self._finish(verdict, started, "tier4", tier4, key)

        verdict.passed = True
        verdict.reason_code = "ok"
        verdict.reason = "all tiers passed"
        verdict.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        if spec.use_cache:
            self.cache.put(
                key,
                {
                    "passed": True,
                    "failed_tier": None,
                    "reason_code": "ok",
                    "reason": verdict.reason,
                    "speedup_ratio": verdict.speedup_ratio,
                },
            )
        self._log(verdict, spec, key)
        return verdict

    def scan_workspace(self, root: Path, files: Optional[Iterable[Path]] = None) -> Dict[str, Any]:
        """Fast Tier-0 sweep used by the kernel every cycle."""
        started = time.perf_counter()
        integrity_ok, integrity = IntegrityGuard.verify(self.root, self.manifest_path)
        violations: List[Dict[str, Any]] = []
        scanned = 0
        for path in files if files is not None else sorted(Path(root).glob("*.py")):
            scanned += 1
            try:
                source = Path(path).read_text(encoding="utf-8")
            except OSError as exc:
                violations.append({"file": str(path), "code": "unreadable", "message": str(exc)})
                continue
            for violation in AstGuard.scan(source):
                violations.append({"file": Path(path).name, **violation})
        verdict = {
            "passed": integrity_ok and not violations,
            "integrity": integrity,
            "violations": violations[:20],
            "files_scanned": scanned,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
        if self.telemetry:
            try:
                import cycle_telemetry  # type: ignore[import]

                cycle_telemetry.record_verification_verdict(
                    {
                        "passed": verdict["passed"],
                        "failed_tier": None if verdict["passed"] else "tier0",
                        "reason_code": "workspace_ok" if verdict["passed"] else "workspace_violation",
                        "duration_ms": verdict["duration_ms"],
                        "counterexample": None if verdict["passed"] else {
                            "integrity": integrity,
                            "violations": violations[:5],
                        },
                    },
                    {"label": "workspace-scan", "files": scanned},
                )
            except Exception:
                pass
        return verdict

    def scope_for(self, changed_files: List[str]) -> Dict[str, Any]:
        names = {Path(name).name for name in (changed_files or [])}
        targeted = bool(names) and "core.py" not in names and not any(
            name not in MODULE_TEST_MAP for name in names
        )
        return {
            "changed_files": sorted(names),
            "mode": "targeted" if targeted else "full-sweep",
            "targeted_tests": sorted({t for name in names for t in MODULE_TEST_MAP.get(name, [])}),
            "formal": any(name in FORMAL_MODULES for name in names),
        }

    def ratchet(
        self,
        case: Dict[str, Any],
        target: str = "golden",
        reason_code: str = "counterexample",
    ) -> Optional[Dict[str, Any]]:
        suite = self.holdout if target == "holdout" else self.golden
        payload = dict(case)
        payload.setdefault("source", f"ratchet:{reason_code}")
        payload.setdefault("reason_code", reason_code)
        active_scope = getattr(self, "_active_scope", None)
        if active_scope and "scope" not in payload:
            payload["scope"] = active_scope
        entry, _created = suite.append(payload)
        return entry

    @staticmethod
    def _scope_key(spec: CandidateSpec) -> str:
        """Suite entries are namespaced so one candidate's failure cannot
        falsely reject an unrelated candidate with the same inputs."""
        return f"{spec.label}:{spec.entrypoint}"

    def _suite_entries(self, suite: RatchetSuite) -> List[Dict[str, Any]]:
        active = getattr(self, "_active_scope", None)
        entries: List[Dict[str, Any]] = []
        for entry in suite.load().get("cases", []):
            if not isinstance(entry, dict):
                continue
            entry_scope = entry.get("scope")
            if entry_scope in (None, "", active):
                entries.append(entry)
        return entries

    # -- tier implementations -------------------------------------------
    def _tier0(self, spec: CandidateSpec) -> Dict[str, Any]:
        if self.enforce_integrity:
            ok, detail = IntegrityGuard.verify(self.root, self.manifest_path)
            if not ok:
                return {
                    "passed": False,
                    "reason_code": str(detail.get("reason_code")),
                    "reason": "protected verifier artifacts changed or unsealed",
                    "counterexample": detail,
                    "feedback": ["Tier0: verifier integrity failed — restore protected files or reseal."],
                }
        input_literals: List[Any] = []
        for entry in self._suite_entries(self.golden):
            input_literals.extend(_case_args(entry.get("args", [])))
        for entry in self._suite_entries(self.holdout):
            input_literals.extend(_case_args(entry.get("args", [])))
        for case in spec.cases:
            input_literals.extend(_case_args(case))
        violations = AstGuard.scan(spec.module_source, input_literals)
        if violations:
            first = violations[0]
            return {
                "passed": False,
                "reason_code": f"ast_{first['code']}",
                "reason": f"AST guardrail rejected candidate: {first['message']}",
                "counterexample": {"violations": violations[:6]},
                "feedback": [
                    f"Tier0: remove {violation['code']} at line {violation.get('line')}"
                    for violation in violations[:4]
                ],
            }
        return {"passed": True}

    def _tier1(self, spec: CandidateSpec) -> Dict[str, Any]:
        sandbox = SubprocessSandbox(spec.timeout_seconds, spec.memory_limit_mb)
        golden = self._suite_entries(self.golden)
        golden_expected: Dict[str, Any] = {}
        golden_kinds: Dict[str, Dict[str, Any]] = {}
        cases: List[Any] = []
        seen: set = set()
        for case in spec.cases:
            signature = _canonical(_case_args(case))
            if signature not in seen:
                seen.add(signature)
                cases.append(case)
        for entry in golden:
            args = _case_args(entry.get("args", []))
            signature = _canonical(args)
            golden_kinds[signature] = entry
            if "expected" in entry:
                golden_expected[signature] = entry
            if signature not in seen:
                seen.add(signature)
                cases.append(args)
        if not cases:
            cases = [0]

        baseline = None
        if spec.baseline_source is not None:
            baseline = sandbox.run(
                spec.baseline_source,
                spec.entrypoint,
                cases,
                timeout_seconds=spec.timeout_seconds,
                memory_limit_mb=spec.memory_limit_mb,
            )
            if not baseline.get("ok"):
                return {
                    "passed": False,
                    "reason_code": "baseline_execution_failed",
                    "reason": str(baseline.get("detail") or baseline.get("reason_code")),
                    "counterexample": baseline,
                    "feedback": ["Tier1: baseline could not run — fix the baseline before mutating."],
                }

        candidate = sandbox.run(
            spec.module_source,
            spec.entrypoint,
            cases,
            timeout_seconds=spec.timeout_seconds,
            memory_limit_mb=spec.memory_limit_mb,
        )
        if not candidate.get("ok"):
            code = str(candidate.get("reason_code"))
            case = {"kind": "no_crash", "args": cases[0], "source": f"ratchet:{code}"}
            verdict = {
                "passed": False,
                "reason_code": code,
                "reason": str(candidate.get("detail") or code),
                "counterexample": {"args": cases[0], "detail": candidate.get("detail")},
                "feedback": [f"Tier1: candidate failed the resource envelope ({code})."],
            }
            verdict["ratcheted"] = self.ratchet(case, "golden", code)
            return verdict

        baseline_time = float(baseline.get("elapsed_ms", 0.0)) if baseline else 0.0
        baseline_mem = float(baseline.get("peak_mb", 0.0)) if baseline else 0.0
        candidate_time = float(candidate.get("elapsed_ms", 0.0))
        candidate_mem = float(candidate.get("peak_mb", 0.0))
        speedup = (
            round(baseline_time / candidate_time, 4)
            if baseline_time > 0 and candidate_time > 0
            else None
        )

        if baseline and spec.enforce_budget:
            if candidate_mem > baseline_mem * 1.10 + 1.0:
                return {
                    "passed": False,
                    "reason_code": "memory_regression",
                    "reason": f"peak {candidate_mem:.1f} MB exceeds baseline {baseline_mem:.1f} MB",
                    "counterexample": {"baseline_mb": baseline_mem, "candidate_mb": candidate_mem},
                    "speedup_ratio": speedup,
                    "feedback": ["Tier1: reduce allocation growth before resubmitting."],
                }
            if candidate_time > baseline_time * 1.10 + 10.0:
                return {
                    "passed": False,
                    "reason_code": "latency_regression",
                    "reason": f"candidate {candidate_time:.1f} ms slower than baseline {baseline_time:.1f} ms",
                    "counterexample": {"baseline_ms": baseline_time, "candidate_ms": candidate_time},
                    "speedup_ratio": speedup,
                    "feedback": ["Tier1: optimize the hot path to meet the baseline budget."],
                }

        results = candidate.get("results") or []
        baseline_results = (baseline or {}).get("results") or []
        for index, case in enumerate(cases):
            result = results[index] if index < len(results) else {"ok": False, "error": "MissingResult"}
            args = _case_args(case)
            signature = _canonical(args)
            expected_entry = golden_expected.get(signature)
            kind_entry = golden_kinds.get(signature) or {}
            kind = str(kind_entry.get("kind") or "")

            if kind == "no_crash" and not result.get("ok"):
                return self._tier1_failure(
                    "golden_no_crash", args, kind_entry, result, speedup
                )
            if kind == "finite_output" and result.get("ok") and _contains_nonfinite(result.get("value")):
                return self._tier1_failure(
                    "golden_nonfinite_output", args, kind_entry, result, speedup
                )
            if expected_entry is not None and "expected" in expected_entry:
                expected = expected_entry["expected"]
                epsilon = float(expected_entry.get("epsilon", 1e-9) or 0.0)
                if not result.get("ok") or not _values_match(result.get("value"), expected, max(epsilon, 1e-9)):
                    return self._tier1_failure(
                        "golden_mismatch", args, expected_entry, result, speedup, expected
                    )
            if baseline_results:
                base = baseline_results[index] if index < len(baseline_results) else {}
                if bool(base.get("ok")) != bool(result.get("ok")):
                    return self._tier1_failure(
                        "differential_outcome_mismatch",
                        args,
                        base,
                        result,
                        speedup,
                        base.get("value") if base.get("ok") else f"raise:{base.get('error')}",
                    )
                if base.get("ok") and not _values_match(base.get("value"), result.get("value")):
                    return self._tier1_failure(
                        "differential_mismatch", args, base, result, speedup, base.get("value")
                    )
                if not base.get("ok") and base.get("error") != result.get("error"):
                    return self._tier1_failure(
                        "differential_exception_mismatch",
                        args,
                        base,
                        result,
                        speedup,
                        f"raise:{base.get('error')}",
                    )
        return {
            "passed": True,
            "speedup_ratio": speedup,
            "candidate_ms": candidate_time,
            "candidate_peak_mb": candidate_mem,
            "baseline_ms": baseline_time,
        }

    def _tier1_failure(
        self,
        code: str,
        args: List[Any],
        expected_entry: Dict[str, Any],
        result: Dict[str, Any],
        speedup: Optional[float],
        expected: Any = None,
    ) -> Dict[str, Any]:
        actual = result.get("value") if result.get("ok") else f"raise:{result.get('error')}"
        counterexample = {
            "args": _json_safe(args),
            "expected": _json_safe(expected if expected is not None else expected_entry.get("expected")),
            "actual": _json_safe(actual),
        }
        kind = "function_output" if expected is not None else "no_crash"
        ratcheted = self.ratchet(
            {
                "kind": kind,
                "args": _json_safe(args),
                "expected": _json_safe(expected if expected is not None else "no BaseException"),
                "epsilon": 1e-9,
            },
            "golden",
            code,
        )
        return {
            "passed": False,
            "reason_code": code,
            "reason": f"{code}: expected {counterexample['expected']!r}, got {counterexample['actual']!r}",
            "counterexample": counterexample,
            "speedup_ratio": speedup,
            "ratcheted": ratcheted,
            "feedback": [f"Tier1: regression case ratcheted into the golden suite ({code})."],
        }

    def _tier2(self, spec: CandidateSpec) -> Dict[str, Any]:
        sandbox = SubprocessSandbox(min(spec.timeout_seconds, 1.2), spec.memory_limit_mb)
        rng = random.Random(1337)
        fuzz: List[Any] = list(EDGE_CORPUS)
        for _ in range(8):
            fuzz.append(rng.randint(-(2**31), 2**31))
        for _ in range(6):
            fuzz.append(rng.uniform(-1e6, 1e6))
        if spec.metamorphic:
            for law in spec.metamorphic:
                for pair in law.get("pairs", [])[:4]:
                    fuzz.extend(pair if isinstance(pair, list) else [pair])
        repeated = fuzz + fuzz[:4]
        run = sandbox.run(
            spec.module_source,
            spec.entrypoint,
            repeated,
            timeout_seconds=min(spec.timeout_seconds, 1.2),
            memory_limit_mb=spec.memory_limit_mb,
        )
        if not run.get("ok"):
            code = str(run.get("reason_code"))
            ratcheted = self.ratchet(
                {"kind": "no_crash", "args": fuzz[0], "source": f"ratchet:{code}"},
                "golden",
                code,
            )
            return {
                "passed": False,
                "reason_code": code,
                "reason": str(run.get("detail") or code),
                "counterexample": {"args": _json_safe(fuzz[0]), "detail": run.get("detail")},
                "ratcheted": ratcheted,
                "feedback": [f"Tier2: fuzz envelope failed ({code})."],
            }
        results = run.get("results") or []
        for index, case in enumerate(fuzz):
            result = results[index] if index < len(results) else {}
            if not result.get("ok"):
                error = str(result.get("error"))
                if error not in SAFE_EXCEPTIONS:
                    ratcheted = self.ratchet(
                        {"kind": "no_crash", "args": _json_safe(_case_args(case))},
                        "golden",
                        "unexpected_exception",
                    )
                    return {
                        "passed": False,
                        "reason_code": "fuzz_unexpected_exception",
                        "reason": f"unexpected {error} on adversarial input",
                        "counterexample": {
                            "args": _json_safe(_case_args(case)),
                            "error": error,
                            "message": result.get("message"),
                        },
                        "ratcheted": ratcheted,
                        "feedback": [f"Tier2: handle {error} explicitly for edge inputs."],
                    }
            elif not spec.allow_nonfinite and _contains_nonfinite(result.get("value")):
                ratcheted = self.ratchet(
                    {"kind": "finite_output", "args": _json_safe(_case_args(case))},
                    "golden",
                    "nonfinite_output",
                )
                return {
                    "passed": False,
                    "reason_code": "fuzz_nonfinite_output",
                    "reason": "non-finite output on adversarial input",
                    "counterexample": {
                        "args": _json_safe(_case_args(case)),
                        "value": _json_safe(result.get("value")),
                    },
                    "ratcheted": ratcheted,
                    "feedback": ["Tier2: clamp or reject non-finite intermediates."],
                }
        for index in range(min(4, len(fuzz))):
            first = results[index] if index < len(results) else {}
            second = results[len(fuzz) + index] if (len(fuzz) + index) < len(results) else {}
            if bool(first.get("ok")) != bool(second.get("ok")) or (
                first.get("ok") and not _values_match(first.get("value"), second.get("value"))
            ):
                ratcheted = self.ratchet(
                    {"kind": "deterministic", "args": _json_safe(_case_args(fuzz[index]))},
                    "golden",
                    "nondeterministic_result",
                )
                return {
                    "passed": False,
                    "reason_code": "nondeterministic_result",
                    "reason": "candidate returned different values for identical inputs",
                    "counterexample": {"args": _json_safe(_case_args(fuzz[index]))},
                    "ratcheted": ratcheted,
                    "feedback": ["Tier2: remove hidden state or unseeded randomness."],
                }

        metamorphic = self._metamorphic_check(spec, sandbox)
        if not metamorphic["passed"]:
            return metamorphic

        if not self._discipline_floor_ok():
            return {
                "passed": False,
                "reason_code": "discipline_floor_violation",
                "reason": "unified discipline fell below D_floor=0.90",
                "counterexample": {"metric": "discipline", "threshold": 0.90},
                "feedback": ["Tier2: restore discipline >= 0.90 before promotion."],
            }
        return {"passed": True, "engine": "seeded-fuzz", "cases": len(fuzz)}

    def _metamorphic_check(self, spec: CandidateSpec, sandbox: SubprocessSandbox) -> Dict[str, Any]:
        pairs: List[Tuple[Any, Any]] = []
        for law in spec.metamorphic:
            name = str(law.get("law") or "")
            if name == "permutation":
                for case in law.get("cases", [])[:4]:
                    if isinstance(case, list):
                        pairs.append((case, list(reversed(case))))
            elif name == "egraph_equivalence":
                for left, right in (law.get("pairs") or [])[:4]:
                    pairs.append((left, right))
        if not pairs:
            return {"passed": True}
        flat: List[Any] = []
        for left, right in pairs:
            flat.extend([left, right])
        run = sandbox.run(
            spec.module_source,
            spec.entrypoint,
            flat,
            timeout_seconds=min(spec.timeout_seconds, 1.2),
            memory_limit_mb=spec.memory_limit_mb,
        )
        if not run.get("ok"):
            return {
                "passed": False,
                "reason_code": "metamorphic_execution_failed",
                "reason": str(run.get("detail") or run.get("reason_code")),
                "counterexample": {"law": "metamorphic"},
                "feedback": ["Tier2: metamorphic pair execution crashed."],
            }
        results = run.get("results") or []
        for index in range(0, len(results) - 1, 2):
            left = results[index]
            right = results[index + 1]
            equivalent = (
                bool(left.get("ok")) == bool(right.get("ok"))
                and (
                    not left.get("ok")
                    or _values_match(left.get("value"), right.get("value"))
                )
            )
            if not equivalent:
                args = flat[index]
                ratcheted = self.ratchet(
                    {"kind": "deterministic", "args": _json_safe(_case_args(args))},
                    "golden",
                    "metamorphic_violation",
                )
                return {
                    "passed": False,
                    "reason_code": "metamorphic_violation",
                    "reason": "equivalent inputs produced different outputs",
                    "counterexample": {
                        "left": _json_safe(left),
                        "right": _json_safe(right),
                        "args": _json_safe(_case_args(args)),
                    },
                    "ratcheted": ratcheted,
                    "feedback": ["Tier2: preserve declared equivalence laws."],
                }
        return {"passed": True}

    @staticmethod
    def _discipline_floor_ok() -> bool:
        try:
            from drives import get_unified_discipline  # type: ignore[import]

            return float(get_unified_discipline()) >= 0.90
        except Exception:
            return True

    def _needs_formal(self, spec: CandidateSpec) -> bool:
        if spec.formal or spec.invariants or spec.touch_lean:
            return True
        return any(Path(name).name in FORMAL_MODULES for name in spec.changed_files)

    def _tier3(self, spec: CandidateSpec) -> Dict[str, Any]:
        invariants = list(spec.invariants) or [
            {
                "name": "discipline_bounds",
                "assumptions": ["d >= 0", "d <= 1"],
                "claim": "d >= 0",
            },
            {
                "name": "budget_monotonic",
                "assumptions": ["g >= 0"],
                "claim": "g * 2 >= g",
            },
        ]
        factory = self.solver_factory
        if factory is None:
            try:
                import z3  # type: ignore[import]

                factory = z3
            except Exception:
                factory = None
        if factory is None:
            if spec.critical:
                return {
                    "passed": False,
                    "reason_code": "formal_solver_unavailable",
                    "reason": "Z3 is unavailable; critical formal invariants fail closed",
                    "counterexample": {"invariants": [item.get("name") for item in invariants]},
                    "feedback": ["Tier3: install z3-solver or mark the candidate non-critical."],
                }
            return {"passed": True, "skipped": "solver_unavailable"}

        for invariant in invariants:
            name = str(invariant.get("name") or "invariant")
            assumptions = list(invariant.get("assumptions") or [])
            claim = str(invariant.get("claim") or "")
            try:
                solver = factory.Solver()
                try:
                    solver.set("timeout", 1200)
                except Exception:
                    pass
                for text in assumptions:
                    terms, const, op = _parse_constraint(str(text))
                    solver.add(_COMPARATORS[type(op)](_smt_expr(factory, terms, const), 0))
                terms, const, op = _parse_constraint(claim)
                solver.add(
                    _negate_comparator(_COMPARATORS[type(op)])(_smt_expr(factory, terms, const), 0)
                )
                outcome = str(solver.check()).lower()
            except ValueError as exc:
                return {
                    "passed": False,
                    "reason_code": "smt_unparseable_invariant",
                    "reason": f"{name}: {exc}",
                    "counterexample": {"invariant": name, "claims": invariant},
                    "feedback": ["Tier3: express invariants as linear arithmetic."],
                }
            except Exception as exc:
                return {
                    "passed": False,
                    "reason_code": "smt_error",
                    "reason": f"{name}: {type(exc).__name__}: {exc}",
                    "counterexample": {"invariant": name},
                    "feedback": ["Tier3: solver error — fail closed."],
                }
            if outcome == "sat":
                model = ""
                try:
                    model = str(solver.model())[:300]
                except Exception:
                    pass
                return {
                    "passed": False,
                    "reason_code": "smt_counterexample",
                    "reason": f"{name}: counterexample found",
                    "counterexample": {"invariant": name, "model": model},
                    "feedback": [f"Tier3: invariant {name} is violated by a real model."],
                }
            if outcome == "unknown":
                if spec.critical:
                    return {
                        "passed": False,
                        "reason_code": "smt_unknown",
                        "reason": f"{name}: solver returned unknown on a critical invariant",
                        "counterexample": {"invariant": name, "solver": "unknown"},
                        "feedback": ["Tier3: critical invariants fail closed on unknown."],
                    }
        if spec.touch_lean:
            try:
                from prover import Lean4Prover  # type: ignore[import]

                prover = Lean4Prover(allow_sorry=False)
                result = prover.verify_lean_source(spec.lean_source or "")
                if not getattr(result, "verified", False):
                    return {
                        "passed": False,
                        "reason_code": "lean_unverified",
                        "reason": "Lean 4 proof hook did not verify the artifact",
                        "counterexample": {"lean": "unverified"},
                        "feedback": ["Tier3: provide a complete Lean proof or defer the change."],
                    }
            except Exception as exc:
                return {
                    "passed": False,
                    "reason_code": "lean_hook_unavailable",
                    "reason": f"Lean hook failed closed: {type(exc).__name__}: {exc}",
                    "counterexample": {"lean": "unavailable"},
                    "feedback": ["Tier3: Lean 4 toolchain unavailable — fail closed."],
                }
        return {"passed": True, "invariants": [item.get("name") for item in invariants]}

    def _tier4(self, spec: CandidateSpec, tier1: Dict[str, Any], tier2: Dict[str, Any]) -> Dict[str, Any]:
        sandbox = SubprocessSandbox(min(spec.timeout_seconds, 1.0), spec.memory_limit_mb)
        holdout_entries = [
            entry
            for entry in self._suite_entries(self.holdout)
            if entry.get("kind") != "goal_probe"
        ]
        cases: List[Any] = []
        for entry in holdout_entries:
            cases.append(_case_args(entry.get("args", [])))
        speedup = tier1.get("speedup_ratio")
        adversary_note: Dict[str, Any] = {}
        try:
            from devil_advocate import DevilAdvocate  # type: ignore[import]

            advocate = DevilAdvocate(state_path=None, cooldown_cycles=0)
            assessments = {
                "latency_ms": float(tier1.get("candidate_ms", 0.0) or 0.0),
                "speedup_ratio": float(speedup or 1.0),
                "fuzz_cases": float(tier2.get("cases", 0) or 0),
            }
            advocate.assess(assessments)
            probe = advocate.invert_metric("latency_ms", min(1.0, max(0.0, (speedup or 1.0) / 4.0)))
            adversary_note = {
                "available": True,
                "probe": {
                    "metric": probe.get("metric"),
                    "inverted_objective": probe.get("inverted_objective"),
                    "counter_goal": probe.get("counter_goal"),
                },
            }
        except Exception as exc:
            adversary_note = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

        inverted: List[Any] = []
        rng = random.Random(4242)
        for case in cases:
            for arg in _case_args(case):
                if isinstance(arg, (int, float)) and not isinstance(arg, bool):
                    inverted.extend([-arg, 1.0 / arg if arg else 1.0, arg * 1e6, rng.uniform(-1e9, 1e9)])
                elif isinstance(arg, str):
                    inverted.extend([arg[::-1], arg * 3])
        adversarial = (cases + inverted)[:16] if cases else inverted[:12]
        if adversarial:
            run = sandbox.run(
                spec.module_source,
                spec.entrypoint,
                adversarial,
                timeout_seconds=min(spec.timeout_seconds, 0.8),
                memory_limit_mb=spec.memory_limit_mb,
            )
            if not run.get("ok"):
                code = str(run.get("reason_code"))
                ratcheted = self.ratchet(
                    {"kind": "no_crash", "args": _json_safe(_case_args(adversarial[0]))},
                    "holdout",
                    f"adversarial_{code}",
                )
                return {
                    "passed": False,
                    "reason_code": f"adversarial_{code}",
                    "reason": str(run.get("detail") or code),
                    "counterexample": {"phase": "adversarial", "detail": run.get("detail")},
                    "ratcheted": ratcheted,
                    "feedback": ["Tier4: adversarial probe broke the candidate — case ratcheted to holdout."],
                }
            results = run.get("results") or []
            for index, case in enumerate(adversarial):
                result = results[index] if index < len(results) else {}
                if not result.get("ok") and str(result.get("error")) not in SAFE_EXCEPTIONS:
                    ratcheted = self.ratchet(
                        {"kind": "no_crash", "args": _json_safe(_case_args(case))},
                        "holdout",
                        "adversarial_unexpected_exception",
                    )
                    return {
                        "passed": False,
                        "reason_code": "adversarial_unexpected_exception",
                        "reason": f"adversarial input raised {result.get('error')}",
                        "counterexample": {
                            "args": _json_safe(_case_args(case)),
                            "error": result.get("error"),
                        },
                        "ratcheted": ratcheted,
                        "feedback": ["Tier4: unexpected exception under counterfactual input."],
                    }
                if result.get("ok") and not spec.allow_nonfinite and _contains_nonfinite(result.get("value")):
                    ratcheted = self.ratchet(
                        {"kind": "finite_output", "args": _json_safe(_case_args(case))},
                        "holdout",
                        "adversarial_nonfinite_output",
                    )
                    return {
                        "passed": False,
                        "reason_code": "adversarial_nonfinite_output",
                        "reason": "adversarial input produced a non-finite output",
                        "counterexample": {
                            "args": _json_safe(_case_args(case)),
                            "value": _json_safe(result.get("value")),
                        },
                        "ratcheted": ratcheted,
                        "feedback": ["Tier4: sanitize extreme inputs before promotion."],
                    }

        if holdout_entries:
            run = sandbox.run(
                spec.module_source,
                spec.entrypoint,
                cases,
                timeout_seconds=min(spec.timeout_seconds, 1.0),
                memory_limit_mb=spec.memory_limit_mb,
            )
            if not run.get("ok"):
                return {
                    "passed": False,
                    "reason_code": "holdout_execution_failed",
                    "reason": str(run.get("detail") or run.get("reason_code")),
                    "counterexample": {"phase": "holdout"},
                    "feedback": ["Tier4: hidden holdout execution failed."],
                }
            results = run.get("results") or []
            for index, entry in enumerate(holdout_entries):
                result = results[index] if index < len(results) else {}
                kind = str(entry.get("kind") or "")
                args = _case_args(entry.get("args", []))
                if kind == "no_crash" and not result.get("ok"):
                    return {
                        "passed": False,
                        "reason_code": "holdout_no_crash",
                        "reason": f"hidden holdout input raised {result.get('error')}",
                        "counterexample": {"args": _json_safe(args), "error": result.get("error")},
                        "feedback": ["Tier4: hidden holdout case failed."],
                    }
                if kind == "finite_output" and result.get("ok") and _contains_nonfinite(result.get("value")):
                    return {
                        "passed": False,
                        "reason_code": "holdout_nonfinite",
                        "reason": "hidden holdout produced a non-finite output",
                        "counterexample": {"args": _json_safe(args), "value": _json_safe(result.get("value"))},
                        "feedback": ["Tier4: hidden holdout case failed."],
                    }
                if "expected" in entry and (
                    not result.get("ok")
                    or not _values_match(
                        result.get("value"),
                        entry.get("expected"),
                        float(entry.get("epsilon", 1e-9) or 1e-9),
                    )
                ):
                    return {
                        "passed": False,
                        "reason_code": "holdout_mismatch",
                        "reason": "hidden holdout expectation not met",
                        "counterexample": {
                            "args": _json_safe(args),
                            "expected": _json_safe(entry.get("expected")),
                            "actual": _json_safe(result.get("value")),
                        },
                        "feedback": ["Tier4: candidate overfit the visible cases; hidden holdout disagrees."],
                    }
        return {
            "passed": True,
            "holdout_cases": len(holdout_entries),
            "adversarial_cases": len(adversarial),
            "adversary": adversary_note,
        }

    # -- helpers ---------------------------------------------------------
    def _finish(
        self,
        verdict: VerificationVerdict,
        started: float,
        tier: str,
        detail: Dict[str, Any],
        cache_key: Optional[str] = None,
    ) -> VerificationVerdict:
        verdict.passed = False
        verdict.failed_tier = tier
        verdict.reason_code = str(detail.get("reason_code") or "rejected")
        verdict.reason = str(detail.get("reason") or verdict.reason_code)
        verdict.counterexample = detail.get("counterexample")
        verdict.feedback = list(detail.get("feedback") or [])[:6]
        if detail.get("ratcheted"):
            verdict.ratcheted = detail.get("ratcheted")
        verdict.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        if cache_key and verdict.reason_code not in (
            "integrity_mismatch",
            "integrity_manifest_missing",
        ):
            self.cache.put(
                cache_key,
                {
                    "passed": False,
                    "failed_tier": tier,
                    "reason_code": verdict.reason_code,
                    "reason": verdict.reason,
                    "counterexample": verdict.counterexample,
                    "speedup_ratio": verdict.speedup_ratio,
                },
            )
        self._log(verdict, None, cache_key)
        return verdict

    @staticmethod
    def _elapsed(started: float) -> float:
        return round((time.perf_counter() - started) * 1000.0, 3)

    def _cache_key(self, spec: CandidateSpec) -> str:
        material = {
            "invariant_version": INVARIANT_VERSION,
            "source": _source_digest(spec.module_source),
            "baseline": _source_digest(spec.baseline_source),
            "entrypoint": spec.entrypoint,
            "cases": [_canonical(_case_args(case)) for case in spec.cases],
            "changed_files": sorted(Path(name).name for name in spec.changed_files),
            "formal": bool(spec.formal),
            "critical": bool(spec.critical),
            "allow_nonfinite": bool(spec.allow_nonfinite),
            "enforce_budget": bool(spec.enforce_budget),
            "metamorphic": spec.metamorphic,
            "invariants": spec.invariants,
            "golden": _file_digest(self.golden.path),
            "holdout": _file_digest(self.holdout.path),
            "protected": IntegrityGuard.snapshot(self.root),
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _log(self, verdict: VerificationVerdict, spec: Optional[CandidateSpec], key: Optional[str]) -> None:
        if not self.telemetry:
            return
        try:
            import cycle_telemetry  # type: ignore[import]

            cycle_telemetry.record_verification_verdict(
                verdict.to_dict(),
                {
                    "label": spec.label if spec else "workspace",
                    "entrypoint": spec.entrypoint if spec else None,
                    "cache_key": (key or "")[:16],
                },
            )
        except Exception:
            pass


def _smt_expr(factory: Any, terms: Dict[str, float], const: float) -> Any:
    expr = None
    for name, coefficient in sorted(terms.items()):
        if not float(coefficient).is_integer():
            raise ValueError(f"non-integer coefficient for {name}: {coefficient}")
        factor = int(coefficient)
        term = factory.Int(name)
        if factor != 1:
            term = term * factor
        expr = term if expr is None else expr + term
    if not float(const).is_integer():
        raise ValueError(f"non-integer constant: {const}")
    base = factory.Int("_const") * 0 if expr is None else expr
    return base + int(const)


def _negate_comparator(op: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
    if op is _COMPARATORS[ast.Gt]:
        return _COMPARATORS[ast.LtE]
    if op is _COMPARATORS[ast.GtE]:
        return _COMPARATORS[ast.Lt]
    if op is _COMPARATORS[ast.Lt]:
        return _COMPARATORS[ast.GtE]
    if op is _COMPARATORS[ast.LtE]:
        return _COMPARATORS[ast.Gt]
    return lambda a, b: a != b


class PipelineComponentTests(unittest.TestCase):
    """Unit tests validating the pipeline modules."""

    def test_telos_engine_milestones(self) -> None:
        """Test milestone creation, dependency resolution, and state updates."""
        from telos import MilestoneStatus, TelosEngine

        engine = TelosEngine()
        m1 = engine.register_milestone("m1", "Init", "Initialize pipeline")
        m2 = engine.register_milestone("m2", "Build", "Build artifacts", depends_on=["m1"])

        runnable = engine.get_runnable_milestones()
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0].id, "m1")

        engine.update_status("m1", MilestoneStatus.COMPLETED)
        runnable = engine.get_runnable_milestones()
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0].id, "m2")

        engine.update_status("m2", MilestoneStatus.COMPLETED)
        self.assertTrue(engine.is_all_complete())
        self.assertFalse(engine.has_failures())

    def test_telos_tactical_subquests_and_metabolic_reset(self) -> None:
        """Test phased sub-quest generation, mastery progression, and metabolic resets."""
        from telos import MilestoneStatus, TacticalPhase, TelosEngine

        engine = TelosEngine(stagnation_threshold=3)
        macro = engine.register_macro_objective(
            objective_id="mastery_demo",
            title="System Mastery Demo",
            target_mastery=1.0,
        )

        subquests = engine.issue_phased_tactical_subquests(
            macro_objective_id="mastery_demo",
            subquest_specs=[
                {"id": "sq1", "title": "Recon", "phase": TacticalPhase.RECONNAISSANCE, "weight": 1.0},
                {"id": "sq2", "title": "Transform", "phase": TacticalPhase.TRANSFORMATION, "depends_on": ["sq1"], "weight": 1.0},
            ],
        )
        self.assertEqual(len(subquests), 2)
        self.assertEqual(macro.current_mastery, 0.0)

        # Complete first subquest and check mastery score
        engine.update_status("sq1", MilestoneStatus.COMPLETED)
        self.assertEqual(macro.current_mastery, 0.5)

        # Mark sq2 as failed
        engine.update_status("sq2", MilestoneStatus.FAILED, error="Stuck in local basin")
        self.assertTrue(engine.has_failures())

        # Simulate stagnation across 3 cycles with stagnant error rate 0.5
        reset_event = None
        for _ in range(3):
            reset_event = engine.record_error_metric(0.5)

        self.assertIsNotNone(reset_event)
        self.assertEqual(reset_event.reset_type, "METABOLIC_RESET")
        self.assertIn("sq2", reset_event.cleared_milestone_ids)

        # Confirm failed task was cleared from basin and reset to PENDING
        ms2 = engine.get_milestone("sq2")
        self.assertEqual(ms2.status, MilestoneStatus.PENDING)
        self.assertFalse(engine.has_failures())

    def test_mutate_code_and_text(self) -> None:
        """Test code syntax verification and template rendering."""
        from mutate import CodeInspector, CodeMutator, TextMutator

        valid_code = "def hello():\n    return 'world'\n"
        self.assertTrue(CodeMutator.ensure_valid_syntax(valid_code))
        self.assertFalse(CodeMutator.ensure_valid_syntax("def invalid syntax():"))

        inspection = CodeInspector.inspect(valid_code)
        self.assertIn("hello", inspection["functions"])

        rendered = TextMutator.render_template(
            "Hello, {{ name }}!",
            {"name": "Automation"},
        )
        self.assertEqual(rendered, "Hello, Automation!")

    def test_surgical_ast_modifier(self) -> None:
        """Test surgical AST parsing, function injection, block modification, and compilation."""
        from mutate import CodeMutator, SurgicalAstModifier

        source = (
            "def existing_task(x: int) -> int:\n"
            "    return x * 2\n"
        )
        tree = SurgicalAstModifier.parse_source(source)

        # 1. Inject a new function
        new_func_code = (
            "def injected_helper(msg: str) -> str:\n"
            "    return f'injected: {msg}'\n"
        )
        SurgicalAstModifier.inject_function(tree, new_func_code)

        # 2. Inject a statement into existing function
        stmt_injected = SurgicalAstModifier.inject_statement_into_function(
            tree,
            func_name="existing_task",
            statement_code="print('Task executed')\n",
            index=0,
        )
        self.assertTrue(stmt_injected)

        # 3. Compile back to source code
        compiled = SurgicalAstModifier.compile_tree(tree)
        self.assertTrue(CodeMutator.ensure_valid_syntax(compiled))
        self.assertIn("injected_helper", compiled)
        self.assertIn("Task executed", compiled)

    def test_prover_execution(self) -> None:
        """Test subprocess execution safety and syntax checker."""
        from prover import ProverInterface

        prover = ProverInterface(default_timeout=10.0)
        res = prover.execute([sys.executable, "-c", "print('PROVER_OK')"])
        self.assertTrue(res.success)
        self.assertIn("PROVER_OK", res.stdout)

    def test_lean4_prover_pipeline(self) -> None:
        """Test Lean 4 / elan translation, wrapping, and verification pipeline."""
        from prover import Lean4Prover, ProverInterface

        lean_prover = Lean4Prover(allow_sorry=False)

        # 1. Test wrapping of code assertion with Mathlib
        wrapped_code = lean_prover.wrap_assertion(
            assertion="∀ (n : Nat), n + 0 = n",
            theorem_name="nat_add_zero",
            use_mathlib=True,
        )
        self.assertIn("import Mathlib", wrapped_code)
        self.assertIn("theorem nat_add_zero : ∀ (n : Nat), n + 0 = n", wrapped_code)

        # 2. Test strict rejection of unproved 'sorry' obligations
        sorry_assertion = "theorem test_sorry : 1 = 1 := by sorry"
        result_sorry = lean_prover.verify_lean_source(sorry_assertion)
        self.assertFalse(result_sorry.verified)
        self.assertFalse(bool(result_sorry))
        self.assertTrue(result_sorry.has_sorry)

        # 3. Test ProverInterface integration returning strict boolean
        prover = ProverInterface()
        status = prover.verify_lean_assertion(sorry_assertion)
        self.assertIsInstance(status, bool)
        self.assertFalse(status)

    def test_component_integrity(self) -> None:
        """Verify all pipeline modules exist and can be imported."""
        checker = ComponentIntegrityChecker()
        for mod in ["core", "telos", "mutate", "prover", "verify", "drives", "bio_gen"]:
            self.assertTrue(
                checker.verify_importable(mod),
                f"Module {mod} failed import check.",
            )

    def test_drives_discipline_engine(self) -> None:
        """Verify UnifiedDisciplineEngine maintains D_floor >= 0.90 under all conditions."""
        from drives import (
            DEFAULT_DRIVE_ENGINE,
            UnifiedDisciplineEngine,
            get_overthinking_depth,
            get_unified_discipline,
        )

        # Fresh engine — discipline must be at or above floor
        engine = UnifiedDisciplineEngine()
        self.assertGreaterEqual(engine.get_unified_discipline(), 0.90)

        # Module-level helpers
        self.assertGreaterEqual(get_unified_discipline(), 0.90)
        depth = get_overthinking_depth()
        self.assertGreaterEqual(depth, 3)
        self.assertLessEqual(depth, 7)

        pressure = engine.get_mutation_pressure()
        self.assertGreaterEqual(pressure, 0.0)
        self.assertLessEqual(pressure, 1.0)

        # apply_stimulus should never decay discipline below D_floor
        stressed = UnifiedDisciplineEngine()
        for challenge in [0, 5, 50, 500]:
            stressed.apply_stimulus(challenge_intensity=challenge)
        self.assertGreaterEqual(stressed.get_unified_discipline(), 0.90)
        self.assertGreaterEqual(DEFAULT_DRIVE_ENGINE.get_unified_discipline(), 0.90)

    def test_bio_gen_harness_spawn(self) -> None:
        """Verify BioGenReplicator can spawn an ALGORITHMIC_CODE harness and capture output."""
        from bio_gen import BioGenReplicator, HarnessVector

        replicator = BioGenReplicator()
        result = replicator.spawn_specialized_harness(
            goal_id="test-bio-gen-001",
            vector=HarnessVector.ALGORITHMIC_CODE,
            payload='print("OK")',
        )
        self.assertTrue(result.get("success", False), f"Harness failed: {result}")
        self.assertIn("OK", result.get("output", ""))

    def test_goal_ingestion_engine(self) -> None:
        """Verify GoalIngestionEngine can ingest, classify, decompose, and queue goals."""
        from telos import GoalIngestionEngine, GoalPriority, GoalStatus, GoalVector

        engine = GoalIngestionEngine()

        # Ingest with explicit vector
        g1 = engine.ingest_goal(
            raw_prompt="Prove that every Lean 4 theorem with sorry is invalid.",
            vector=GoalVector.FORMAL_PROOF,
            priority=GoalPriority.HIGH,
        )
        self.assertEqual(g1.vector, GoalVector.FORMAL_PROOF)
        self.assertEqual(g1.priority, GoalPriority.HIGH)
        self.assertEqual(g1.status, GoalStatus.QUEUED)
        self.assertTrue(len(g1.sub_tasks) >= 1)

        # Ingest without vector — auto-classification
        g2 = engine.ingest_goal("Implement an efficient quicksort algorithm in Python.")
        self.assertEqual(g2.vector, GoalVector.ALGORITHMIC_CODE)

        # Priority queue ordering
        engine.ingest_goal("Low priority explore task.", priority=GoalPriority.LOW)
        engine.ingest_goal("Critical discovery.", priority=GoalPriority.CRITICAL)
        queue = engine.get_priority_queue(discipline=1.0)
        self.assertGreaterEqual(queue[0].priority.value, queue[-1].priority.value)

        # Lifecycle transitions
        engine.mark_goal_complete(g1.id)
        self.assertEqual(engine.get_goal(g1.id).status, GoalStatus.COMPLETED)

        engine.mark_goal_failed(g2.id, error="test error")
        self.assertEqual(engine.get_goal(g2.id).status, GoalStatus.FAILED)

        summary = engine.get_summary()
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["failed"], 1)

    def test_dual_track_macro_discovery_taxonomy(self) -> None:
        """Verify goals are classified into convergent-mastery and open-frontier tracks."""
        from telos import DiscoveryTrack, GoalIngestionEngine, GoalVector

        engine = GoalIngestionEngine()
        convergent = engine.ingest_goal(
            "Prove the soundness theorem in Lean.",
            vector=GoalVector.FORMAL_PROOF,
        )
        frontier = engine.ingest_goal(
            "Explore open-ended hypotheses about emergent computation."
        )

        self.assertEqual(convergent.discovery_track, DiscoveryTrack.CONVERGENT_MASTERY)
        self.assertEqual(frontier.discovery_track, DiscoveryTrack.OPEN_FRONTIER)

        summary = engine.get_summary()["discovery_tracks"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["tracks"][DiscoveryTrack.CONVERGENT_MASTERY.value], 1)
        self.assertEqual(summary["tracks"][DiscoveryTrack.OPEN_FRONTIER.value], 1)

        # Manifest round-trip preserves the track
        restored = type(convergent).from_dict(convergent.to_dict())
        self.assertEqual(restored.discovery_track, DiscoveryTrack.CONVERGENT_MASTERY)

    def test_ego_ratchet_high_watermark(self) -> None:
        """Verify the ego ratchet persists and never lowers its high-watermark."""
        import tempfile
        from verify import EgoRatchet

        with tempfile.TemporaryDirectory() as tmp_dir:
            ratchet_path = Path(tmp_dir) / "ratchet.json"
            ratchet = EgoRatchet(ratchet_path)
            self.assertTrue(ratchet.check(0))
            self.assertTrue(ratchet.record(passed_checks=10, total_checks=12))
            self.assertTrue(ratchet.check(10))
            self.assertFalse(ratchet.check(9))

            # A regression must not lower the watermark
            self.assertFalse(ratchet.record(passed_checks=7, total_checks=12))
            reloaded = EgoRatchet(ratchet_path)
            self.assertEqual(reloaded.snapshot()["high_watermark_passed"], 10)
            self.assertEqual(reloaded.snapshot()["gate_runs"], 2)

    def test_ancestral_gate_regression_detection(self) -> None:
        """Verify the ancestral gate flags regressions below the ego high-watermark."""
        import tempfile
        from verify import AncestralRegressionGate, VerificationReport

        with tempfile.TemporaryDirectory() as tmp_dir:
            gate = AncestralRegressionGate(Path(tmp_dir) / "ratchet.json")

            baseline = VerificationReport(total_checks=10, passed_checks=10)
            info = gate.evaluate(baseline, depth=3)
            self.assertFalse(info["regressed"])
            self.assertTrue(info["promoted"])
            self.assertEqual(info["high_watermark_after"], 10)

            regressed = VerificationReport(total_checks=10, passed_checks=8)
            info = gate.evaluate(regressed, depth=3)
            self.assertTrue(info["regressed"])
            self.assertEqual(regressed.failed_checks, 1)
            self.assertFalse(regressed.is_successful)
            self.assertFalse(gate.ratchet.check(8))

    def test_overthinking_depth_resolution(self) -> None:
        """Verify overthinking depth resolves within the drives-sanctioned range."""
        from verify import VerificationRunner

        runner = VerificationRunner()
        self.assertEqual(runner._resolve_depth(2), 2)
        depth = runner._resolve_depth()
        self.assertGreaterEqual(depth, 3)
        self.assertLessEqual(depth, 7)

    def test_science_router_matrix(self) -> None:
        """Verify all ten science routers execute and return bounded telemetry."""
        from science_mcp import (
            ROUTER_NAMES,
            _ROUTER_FUNCTIONS,
            dispatch_router,
            get_science_telemetry,
        )

        self.assertEqual(len(ROUTER_NAMES), 10)
        for router in ROUTER_NAMES:
            self.assertTrue(
                callable(_ROUTER_FUNCTIONS[router]),
                f"{router} registry entry is not callable: {_ROUTER_FUNCTIONS[router]!r}",
            )
            result = dispatch_router(router, payload={})
            self.assertTrue(
                result.get("success"),
                f"{router} failed: {result.get('error')}",
            )
            self.assertEqual(result["router"], router)
            self.assertIn(
                result["execution_mode"],
                {"simulated_fallback", "live_adapter"},
            )
            self.assertGreaterEqual(result["numerical_residual"], 0.0)
            self.assertLessEqual(result["numerical_residual"], 1.0)
            self.assertGreaterEqual(result["latency_seconds"], 0.0)
            self.assertIn("task_graph", result)
            self.assertIn("optimizer", result)
            self.assertIn("discipline", result)

        telemetry = get_science_telemetry()
        self.assertEqual(telemetry["router_count"], 10)
        self.assertEqual(len(telemetry["routers"]), 10)

    def test_telos_science_goal_dispatch(self) -> None:
        """Verify telos routes a science prompt to the right router and executes it."""
        from telos import GoalIngestionEngine

        engine = GoalIngestionEngine()
        goal = engine.ingest_goal(
            "Run a quantum circuit swarm with high precision."
        )
        self.assertEqual(engine.resolve_router(goal), "quantum_router")
        self.assertEqual(goal.metadata.get("router"), "quantum_router")

        report = engine.dispatch_goal(goal.id)
        self.assertTrue(report["handled"])
        self.assertEqual(report["router"], "quantum_router")
        self.assertTrue(report["result"]["success"])
        self.assertEqual(goal.metadata["router"], "quantum_router")
        self.assertEqual(engine.get_router_histogram()["quantum_router"], 1)
        self.assertEqual(engine.get_summary()["router_dispatches"], 1)

    def test_drives_execution_friction(self) -> None:
        """Verify router friction raises discipline without breaching D_floor."""
        from drives import UnifiedDisciplineEngine

        engine = UnifiedDisciplineEngine()
        before = engine.get_unified_discipline()
        telemetry = engine.record_execution_telemetry(
            router="quantum_router",
            latency_seconds=3.0,
            residual=1.0,
            success=False,
        )
        self.assertGreater(telemetry["challenge_intensity"], 0.0)
        self.assertLessEqual(telemetry["challenge_intensity"], 10.0)
        self.assertGreaterEqual(telemetry["discipline_after"], 0.90)
        self.assertGreaterEqual(telemetry["discipline_after"], before)
        self.assertEqual(telemetry["floor"], 0.90)

    def test_bio_gen_router_harness(self) -> None:
        """Verify bio_gen can spawn a harness for a specific science router."""
        from bio_gen import BioGenReplicator

        replicator = BioGenReplicator()
        result = replicator.spawn_router_harness(
            goal_id="test-router-harness-001",
            router="quantum_router",
            payload={
                "qasm_batch": ["OPENQASM 2.0; qreg q[1];"],
                "target_backends": ["statevector_sim"],
                "precision_target": 1e-6,
            },
        )
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertIn("quantum_router", result.get("output", ""))

    def test_goal_stream_expansion_and_dedup(self) -> None:
        """Verify exhaustive level-wise expansion and ledger deduplication."""
        import tempfile

        from telos import GoalIngestionEngine, GoalPriority, GoalStreamingPipeline

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            objective = "Prove the convergence theorem for autonomous kernels."

            engine = GoalIngestionEngine()
            stream = GoalStreamingPipeline(
                engine,
                ledger_path=root / "ledger.json",
                state_path=root / "stream.json",
                max_level=3,
                max_concurrency=2,
                producer_poll_seconds=0.05,
            )
            info = stream.submit_objective(objective, level=2, priority=GoalPriority.HIGH)
            self.assertTrue(info["new"])
            self.assertTrue(stream.start(resume=False)["started"])

            # L0=1, L1=5, L2=15 => 21 exhaustive permutations.
            self.assertTrue(stream.wait_for_items(21, timeout=10), stream.get_telemetry())
            drained = stream.drain()
            self.assertEqual(len(drained), 2)  # bounded by max_concurrency
            for goal in drained:
                self.assertTrue(stream.complete(goal.id, True)["freed_slot"])
            self.assertEqual(len(stream.drain()), 2)

            telemetry = stream.stop()
            self.assertEqual(telemetry["produced"], 21)
            self.assertEqual(telemetry["level_counts"], {"0": 1, "1": 5, "2": 15})
            self.assertEqual(telemetry["ledger"]["entries"], 21)
            self.assertEqual(telemetry["completed"], 2)
            self.assertTrue((root / "stream.json").is_file())
            self.assertTrue((root / "ledger.json").is_file())

            # A fresh runner with the same ledger must suppress every duplicate.
            engine2 = GoalIngestionEngine()
            stream2 = GoalStreamingPipeline(
                engine2,
                ledger_path=root / "ledger.json",
                state_path=root / "stream2.json",
                max_level=3,
                max_concurrency=2,
                producer_poll_seconds=0.05,
            )
            stream2.submit_objective(objective, level=2, priority=GoalPriority.HIGH)
            stream2.start(resume=False)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                snapshot = stream2.get_telemetry()
                if snapshot["producer_passes"] >= 1 and snapshot["queue_depth"] == 0:
                    break
                time.sleep(0.02)
            telemetry2 = stream2.stop()
            self.assertEqual(telemetry2["produced"], 0)
            self.assertGreaterEqual(telemetry2["duplicates_suppressed"], 21)
            self.assertEqual(len(engine2.get_all_goals()), 0)

    def test_goal_stream_resume_and_slot_release(self) -> None:
        """Verify in-flight and pending work survives a handoff and is requeued."""
        import tempfile

        from telos import GoalIngestionEngine, GoalPriority, GoalStreamingPipeline

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            objective = "Explore novel protein docking frontiers."

            stream = GoalStreamingPipeline(
                GoalIngestionEngine(),
                ledger_path=root / "ledger.json",
                state_path=root / "stream.json",
                max_level=1,
                max_concurrency=2,
                producer_poll_seconds=0.05,
            )
            stream.submit_objective(objective, level=1, priority=GoalPriority.NORMAL)
            stream.start(resume=False)
            self.assertTrue(stream.wait_for_items(6, timeout=10))  # L0=1 + L1=5

            in_flight = stream.drain()
            self.assertEqual(len(in_flight), 2)
            self.assertEqual(stream.available_slots, 0)
            self.assertEqual(stream.drain(), [])  # slots exhausted

            handoff = stream.stop()
            self.assertEqual(handoff["in_flight"], 2)

            resumed_engine = GoalIngestionEngine()
            resumed = GoalStreamingPipeline(
                resumed_engine,
                ledger_path=root / "ledger.json",
                state_path=root / "stream.json",
                max_level=1,
                max_concurrency=2,
                producer_poll_seconds=0.05,
            )
            info = resumed.start(resume=True)
            self.assertEqual(info["resumed"]["requeued"], 6)
            self.assertTrue(resumed.wait_for_items(6, timeout=5))
            self.assertEqual(len(resumed_engine.get_all_goals()), 6)
            resumed.stop()

    def test_core_goal_stream_wiring(self) -> None:
        """Verify the core starts, drains, persists, and exports the goal stream."""
        import tempfile

        from core import AutomationKernel

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            kernel = AutomationKernel(
                workspace_root=root,
                window_minutes=1.0,
                stream_enabled=True,
                stream_level=1,
                stream_concurrency=2,
            )
            info = kernel._start_goal_stream()
            self.assertTrue(info["started"])
            self.assertTrue(kernel.telos.stream.wait_for_items(6, timeout=10))

            telemetry = kernel.telos.stream.stop()
            self.assertGreaterEqual(telemetry["produced"], 6)

            state_path = kernel.save_state_to_disk()
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["goal_stream"]["enabled"])
            self.assertGreaterEqual(payload["goal_stream"]["produced"], 6)
            self.assertGreaterEqual(payload["goal_stream"]["ledger"]["entries"], 6)
            self.assertTrue((root / ".sask_goal_ledger.json").is_file())
            self.assertTrue((root / ".sask_goal_stream.json").is_file())

    def test_core_wall_clock_timer_and_handoff(self) -> None:
        """Test the 340-minute wall-clock execution timer and container handoff serialization."""
        import json
        import tempfile
        from core import AutomationKernel, CoreState, WallClockTimer

        # 1. Verify standard 340-minute timer configuration
        timer = WallClockTimer(window_minutes=340.0)
        self.assertEqual(timer.window_minutes, 340.0)
        self.assertEqual(timer.window_seconds, 340.0 * 60.0)
        self.assertFalse(timer.is_threshold_reached())
        self.assertGreater(timer.remaining_minutes, 339.0)

        # 2. Test threshold expiration trigger
        expired_timer = WallClockTimer(window_minutes=0.001, margin_seconds=1.0)
        self.assertTrue(expired_timer.is_threshold_reached())

        # 3. Test AutomationKernel state serialization and handoff
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            tmp_path = tmp_root / "test_state.json"
            kernel = AutomationKernel(
                window_minutes=0.0001,  # triggers immediate handoff
                workspace_root=tmp_root,
                state_file=tmp_path,
            )
            kernel.run_continuous_loop(max_cycles=1)

            self.assertEqual(kernel.state, CoreState.HANDOFF_READY)
            self.assertTrue(tmp_path.is_file())

            payload = json.loads(tmp_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["container_handoff"])
            self.assertEqual(payload["state"], "HANDOFF_READY")
            self.assertEqual(payload["wall_clock"]["window_minutes"], 0.0001)

    def test_meta_prover_axiomatic_search(self) -> None:
        """Verify axiom mutation and evolutionary framework search persist state."""
        import tempfile

        from meta_prover import AxiomRegistry, MetaProver

        registry = AxiomRegistry()
        frameworks = registry.base_frameworks()
        self.assertGreaterEqual(len(frameworks), 6)
        mutated = registry.mutate(frameworks[0], salt=7)
        self.assertNotEqual(mutated.signature(), frameworks[0].signature())

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "meta.json"
            prover = MetaProver(
                state_path=state_path,
                population=6,
                max_generations=2,
            )
            result = prover.search(
                "construct a compact verified proof for distributed state",
                generations=2,
            )
            self.assertGreater(result["evaluations"], len(frameworks))
            self.assertGreaterEqual(result["best_score"], 0.5)
            self.assertLessEqual(result["best_score"], 1.0)
            self.assertEqual(len(result["ranking"]), 5)
            self.assertTrue(result["lean_skeleton"])
            self.assertTrue(state_path.is_file())

            telemetry = prover.get_telemetry()
            self.assertEqual(telemetry["searches"], 1)
            self.assertIsNotNone(telemetry["preferred_framework"])

            reloaded = MetaProver(state_path=state_path)
            reloaded_telemetry = reloaded.get_telemetry()
            self.assertEqual(reloaded_telemetry["searches"], 1)
            self.assertEqual(
                reloaded_telemetry["evaluations"], result["evaluations"]
            )

    def test_curiosity_free_energy_and_autonomous_goals(self) -> None:
        """Verify surprisal, information gain, novel hypotheses, and goal proposal."""
        import tempfile

        from telos import GoalIngestionEngine
        from telos_v2 import EpistemicCuriosityEngine

        engine = GoalIngestionEngine()
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "curiosity.json"
            curiosity = EpistemicCuriosityEngine(
                goal_engine=engine,
                state_path=state_path,
                seed=11,
                min_proposal_score=0.0,
            )
            observation = curiosity.observe("quantum_dynamics", 0.02, weight=1.0)
            self.assertGreater(observation["surprisal"], 0.0)
            self.assertGreater(observation["information_gain"], 0.0)
            self.assertGreaterEqual(observation["intrinsic_reward"], 0.0)
            self.assertLessEqual(observation["intrinsic_reward"], 1.0)

            tick = curiosity.tick(
                telemetry={
                    "system_mastery": 0.6,
                    "success_rate": 0.75,
                    "error_count": 2,
                },
                generate=3,
                propose_limit=2,
            )
            self.assertEqual(len(tick["hypotheses"]), 3)
            statements = {item["statement"] for item in tick["hypotheses"]}
            self.assertEqual(len(statements), 3)
            self.assertGreaterEqual(len(tick["proposals"]), 1)
            self.assertGreaterEqual(len(engine.get_all_goals()), 1)
            for goal in engine.get_all_goals():
                self.assertIn("curiosity", goal.metadata)

            telemetry = curiosity.get_telemetry()
            self.assertGreaterEqual(telemetry["hypotheses_generated"], 3)
            self.assertGreaterEqual(telemetry["hypotheses_proposed"], 1)
            self.assertTrue(state_path.is_file())

            reloaded = EpistemicCuriosityEngine(
                goal_engine=GoalIngestionEngine(),
                state_path=state_path,
            )
            self.assertGreaterEqual(
                reloaded.get_telemetry()["hypotheses_generated"], 3
            )

    def test_manifold_cross_domain_isomorphism_and_transfer(self) -> None:
        """Verify cross-domain structural isomorphism and solution transport."""
        import tempfile

        from manifold_bridge import AnalogicalManifoldBridge

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "manifold.json"
            bridge = AnalogicalManifoldBridge(state_path=state_path)

            ring_matches = bridge.find_isomorphisms(
                "net-ring-6", cross_domain_only=True
            )
            self.assertTrue(
                any(
                    match["target"] == "alg-cayley-z6"
                    and match["structural_isomorphism"]
                    for match in ring_matches
                )
            )
            lattice_matches = bridge.find_isomorphisms(
                "phys-lattice-2x3", cross_domain_only=True
            )
            self.assertTrue(
                any(
                    match["target"] == "net-mesh-2x3"
                    and match["structural_isomorphism"]
                    for match in lattice_matches
                )
            )
            cfg_matches = bridge.find_isomorphisms(
                "comp-cfg-diamond", cross_domain_only=True
            )
            self.assertFalse(
                any(match["structural_isomorphism"] for match in cfg_matches)
            )

            transfer = bridge.transfer_solution("alg-cayley-z6", "net-ring-6")
            self.assertTrue(transfer["transferred"])
            self.assertTrue(transfer["structural_isomorphism"])
            self.assertGreaterEqual(transfer["confidence"], 0.99)
            self.assertEqual(len(transfer["node_mapping"]), 6)
            self.assertTrue(state_path.is_file())

            reloaded = AnalogicalManifoldBridge(state_path=state_path)
            self.assertGreaterEqual(
                reloaded.get_telemetry()["transfers"], 1
            )

    def test_devil_advocate_stagnation_inversion(self) -> None:
        """Verify stagnation detection, assumption inversion, and drive pressure."""
        import tempfile

        from devil_advocate import DevilAdvocate
        from drives import UnifiedDisciplineEngine
        from telos import GoalIngestionEngine

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "devil.json"
            devil = DevilAdvocate(
                state_path=state_path,
                window=3,
                cooldown_cycles=0,
            )
            drives = UnifiedDisciplineEngine()
            goals = GoalIngestionEngine()
            before = drives.get_unified_discipline()

            result = None
            for _ in range(5):
                result = devil.challenge(
                    {
                        "system_mastery": 0.9,
                        "unified_discipline": 0.95,
                        "success_rate": 0.97,
                    },
                    drive_engine=drives,
                    goal_engine=goals,
                )

            self.assertTrue(result["challenged"])
            self.assertTrue(result["probes"])
            assessment = result["assessment"]
            self.assertTrue(
                assessment["stagnation"] or assessment["local_convergence"]
            )
            probe = result["probes"][0]
            self.assertTrue(probe["assumption_challenged"])
            self.assertTrue(probe["inverted_objective"])
            inversion = devil.invert_metric("system_mastery", 0.9)
            self.assertAlmostEqual(
                inversion["inverted_target"] + 0.9, 1.0, places=6
            )

            self.assertGreaterEqual(drives.get_unified_discipline(), before)
            self.assertGreaterEqual(drives.get_unified_discipline(), 0.90)
            self.assertGreaterEqual(len(goals.get_all_goals()), 1)
            self.assertTrue(state_path.is_file())

            reloaded = DevilAdvocate(state_path=state_path)
            self.assertGreaterEqual(
                reloaded.get_telemetry()["challenges_issued"], 1
            )

    def test_egraph_equivalence_saturation_and_compression(self) -> None:
        """Verify saturation merges equivalent forms, verifies them, and compresses traces."""
        import tempfile

        from egraph_engine import EquivalenceSaturationEngine

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_file = Path(tmp_dir) / "egraph.json"
            engine = EquivalenceSaturationEngine(
                state_file=state_file,
                max_iterations=6,
                node_budget=2000,
                match_budget=2000,
                time_budget=1.0,
            )
            report = engine.superpose(
                [
                    "x + 0",
                    "0 + x",
                    "x * 1",
                    "(a + b) + c",
                    "a + (b + c)",
                    "a * (b + c)",
                    "a * b + a * c",
                ],
                goal="x * 1",
                form_limit=3,
            )
            self.assertEqual(report["expressions"], 7)
            groups = [set(group["members"]) for group in report["merged_classes"]]
            self.assertTrue(
                any({"x + 0", "0 + x", "x * 1"} <= group for group in groups)
            )
            self.assertTrue(
                any({"a * (b + c)", "a * b + a * c"} <= group for group in groups)
            )
            self.assertTrue(report["goal"]["matched"])
            self.assertIn("x * 1", report["goal"]["proved_from"])
            for entry in report["results"]:
                self.assertIsNotNone(entry["best"])
                self.assertTrue(entry["best"]["verified"])
            simplification = next(
                entry for entry in report["results"] if entry["input"] == "x * 1"
            )
            self.assertEqual(simplification["best"]["expression"], "x")
            self.assertGreater(report["saturation"]["rewrites"], 0)

            trace = engine.compress_solver_trace(["x + 0", "0 + x", "x", "x * 1"])
            self.assertEqual(trace["output_steps"], ["x"])
            self.assertLess(trace["compression_ratio"], 1.0)

            self.assertTrue(state_file.is_file())
            reloaded = EquivalenceSaturationEngine(state_file=state_file)
            telemetry = reloaded.get_telemetry()
            self.assertGreaterEqual(telemetry["superpositions"], 2)
            self.assertGreater(telemetry["rewrites"], 0)

    def test_prover_bidirectional_meet_in_the_middle(self) -> None:
        """Verify meet-in-the-middle paths, verifier rejection, budgets, and cache reuse."""
        from prover import ProverInterface, SearchBudget

        prover = ProverInterface()
        undirected = lambda node: [n for n in (node - 1, node + 1) if 0 <= n <= 20]
        directed = lambda node: [node + 1] if node < 20 else []
        predecessors = lambda node: [node - 1] if node > 0 else []
        budget = SearchBudget(
            max_nodes_per_side=32,
            max_depth=10,
            max_seconds=2.0,
            beam_width=16,
            parallel=True,
        )

        result = prover.bidirectional_search(
            [0], [20], undirected, budget=budget, tag="undirected-line"
        )
        self.assertTrue(result.solved)
        self.assertEqual(result.path[0], 0)
        self.assertEqual(result.path[-1], 20)
        for left, right in zip(result.path, result.path[1:]):
            self.assertIn(right, undirected(left))
        self.assertEqual(result.meets_rejected, 0)

        directed_result = prover.bidirectional_search(
            [0],
            [20],
            directed,
            backward_predecessors=predecessors,
            budget=budget,
            tag="directed-line",
        )
        self.assertTrue(directed_result.solved)
        self.assertEqual(directed_result.path, list(range(21)))

        rejected = prover.bidirectional_search(
            [0],
            [20],
            undirected,
            verify=lambda state: False,
            budget=budget,
            tag="rejected-line",
        )
        self.assertFalse(rejected.solved)
        self.assertGreaterEqual(rejected.meets_rejected, 1)
        self.assertEqual(rejected.stopped, "exhausted")

        first = prover.bidirectional_search(
            [0], [20], undirected, budget=budget, tag="cached-line"
        )
        self.assertTrue(first.solved)
        second = prover.bidirectional_search(
            [0], [20], undirected, budget=budget, tag="cached-line"
        )
        self.assertTrue(second.solved)
        self.assertGreater(second.cache_hits, 0)

    def test_counterfactual_sandbox_invariants_and_promotion(self) -> None:
        """Verify sandbox isolation, invariant rejection, projection, and gated promotion."""
        import tempfile

        from meta_prover import MetaProver

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "meta.json"
            prover = MetaProver(
                state_path=state_path,
                population=6,
                max_generations=2,
            )
            goal = (
                "verify an isomorphism and transport equivalence across "
                "distributed proof states"
            )
            result = prover.counterfactual_search(
                goal,
                perturbations=["toggle:allow_sorry", "type:refinement"],
                min_delta=-1.0,
            )
            self.assertEqual(result["verification_mode"], "invariants+regression")
            self.assertTrue(result["deterministic_baseline"])
            branches = {branch["operator"]: branch for branch in result["branches"]}

            unsafe = branches["toggle:allow_sorry"]
            self.assertFalse(unsafe["promoted"])
            self.assertFalse(unsafe["invariants"]["allow_sorry_remains_false"])
            self.assertIn("invariants", unsafe["rejection_reason"])
            self.assertIn(
                "allow_sorry", unsafe["projection"]["discarded_axiom_deltas"]
            )

            safe = branches["type:refinement"]
            self.assertTrue(safe["verified"])
            self.assertTrue(all(safe["invariants"].values()))
            self.assertTrue(safe["projection"]["reprojected_from_trusted_baseline"])
            self.assertTrue(safe["promoted"])
            self.assertEqual(result["promoted"], 1)
            self.assertEqual(result["rejected"], 1)

            promotion = prover.promoted_framework(goal)
            self.assertIsNotNone(promotion)
            self.assertEqual(promotion["framework_id"], safe["framework_id"])
            self.assertTrue(state_path.is_file())

            reloaded = MetaProver(state_path=state_path)
            self.assertIsNotNone(reloaded.promoted_framework(goal))
            self.assertGreaterEqual(
                reloaded.get_telemetry()["counterfactual_runs"], 1
            )

    def test_boundary_probes_goal_injection_and_science_matrix(self) -> None:
        """Verify four-dimension probes, safety flags, goal injection, and router matrix."""
        import tempfile

        from manifold_bridge import BOUNDARY_DIMENSIONS, AnalogicalManifoldBridge
        from science_mcp import generate_boundary_probe_matrix, get_science_telemetry
        from telos import GoalIngestionEngine
        from telos_v2 import EpistemicCuriosityEngine

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            bridge = AnalogicalManifoldBridge(state_path=root / "manifold.json")
            probes = bridge.boundary_probes(limit=64)
            self.assertTrue(probes)
            self.assertEqual(len({probe.id for probe in probes}), len(probes))
            self.assertTrue(
                set(BOUNDARY_DIMENSIONS).issubset(
                    {probe.dimension for probe in probes}
                )
            )
            for probe in probes:
                self.assertTrue(all(probe.safety.values()))
                self.assertGreaterEqual(probe.severity, 0.0)
                self.assertLessEqual(probe.severity, 1.0)

            subset = bridge.boundary_probes(
                structure_id="net-ring-6", dimensions=["resource_ceiling"]
            )
            self.assertEqual(len(subset), 1)
            self.assertEqual(subset[0].structure_id, "net-ring-6")

            goal_engine = GoalIngestionEngine()
            curiosity = EpistemicCuriosityEngine(
                goal_engine=goal_engine,
                state_path=root / "curiosity.json",
                seed=5,
                min_proposal_score=0.0,
            )
            records = curiosity.propose_boundary_goals(probes, limit=2)
            self.assertEqual(len(records), 2)
            self.assertTrue(all(record["injected"] for record in records))
            injected = {
                goal.metadata.get("boundary_probe", {}).get("id")
                for goal in goal_engine.get_all_goals()
            }
            self.assertTrue(
                {record["probe_id"] for record in records} <= injected
            )

            matrix = generate_boundary_probe_matrix(domain="all", limit=8)
            self.assertTrue(matrix["success"])
            self.assertTrue(matrix["probes"])
            self.assertLessEqual(len(matrix["probes"]), 8)
            telemetry = get_science_telemetry()
            self.assertEqual(
                telemetry["boundary_dimensions"], list(BOUNDARY_DIMENSIONS)
            )

    def test_fast_fail_precheck_and_syntax_cache(self) -> None:
        """Verify cheap preflight checks abort early and syntax verdicts are cached."""
        import tempfile

        from mutate import CodeMutator
        from verify import FastFailPrecheck

        precheck = FastFailPrecheck.run()
        self.assertTrue(precheck["success"], precheck["failures"])
        self.assertGreaterEqual(
            precheck["checks"], len(FastFailPrecheck.REQUIRED_FILES)
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            empty_root = Path(tmp_dir)
            (empty_root / "requirements.txt").write_text("", encoding="utf-8")
            failed = FastFailPrecheck.run(empty_root)
            self.assertFalse(failed["success"])
            self.assertTrue(
                any("required file missing" in item for item in failed["failures"])
            )

        self.assertTrue(CodeMutator.ensure_valid_syntax("value = 1\n"))
        before = CodeMutator.syntax_cache_stats()
        self.assertTrue(CodeMutator.ensure_valid_syntax("value = 1\n"))
        after = CodeMutator.syntax_cache_stats()
        self.assertGreaterEqual(after["hits"], before["hits"] + 1)
        self.assertFalse(CodeMutator.ensure_valid_syntax("def broken(:\n"))

    def test_core_extensions_wiring_and_export(self) -> None:
        """Verify the kernel runs and exports all five extensions with state."""
        import tempfile

        from core import AutomationKernel
        from science_mcp import get_science_telemetry

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            kernel = AutomationKernel(
                workspace_root=root,
                window_minutes=1.0,
                extensions_enabled=True,
            )
            self.assertIsNotNone(kernel.meta_prover)
            self.assertIsNotNone(kernel.curiosity)
            self.assertIsNotNone(kernel.manifold)
            self.assertIsNotNone(kernel.devil)
            self.assertIsNotNone(kernel.egraph)

            extensions = kernel._run_extensions({}, error_count=0)
            self.assertTrue(extensions["meta_prover"]["available"])
            self.assertTrue(extensions["meta_prover"]["best_success"])
            self.assertGreaterEqual(
                extensions["curiosity"]["hypotheses_generated"], 1
            )
            self.assertTrue(extensions["manifold"]["available"])
            self.assertGreaterEqual(extensions["manifold"]["transfers"], 1)
            self.assertTrue(extensions["persistence"]["state_files"])
            self.assertTrue(extensions["egraph"]["available"])
            self.assertGreaterEqual(extensions["egraph"]["merged_classes"], 1)
            self.assertTrue(extensions["egraph"]["goal_matched"])
            self.assertIn("bidirectional", extensions)

            science_extensions = get_science_telemetry()["extensions"]
            for name in (
                "curiosity",
                "meta_prover",
                "manifold_bridge",
                "devil_advocate",
                "egraph",
            ):
                self.assertIn(name, science_extensions)

            payload = json.loads(kernel.export_state_json())
            self.assertTrue(payload["extensions"]["enabled"])
            self.assertGreaterEqual(
                payload["extensions"]["meta_prover"]["searches"], 1
            )
            self.assertTrue(payload["extensions"]["egraph"]["available"])
            self.assertGreaterEqual(
                payload["extensions"]["egraph"]["superpositions"], 1
            )
            self.assertIn("solved", payload["bidirectional_search"])
            self.assertGreaterEqual(
                kernel.drives.get_unified_discipline(), 0.90
            )
            for filename in (
                ".sask_meta_prover.json",
                ".sask_curiosity.json",
                ".sask_manifold.json",
                ".sask_devil_advocate.json",
                ".sask_egraph.json",
            ):
                self.assertTrue((root / filename).is_file(), filename)


class _FakeZ3Expr:
    """Minimal integer-expression stand-in used to exercise the SMT hook."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __mul__(self, other: Any) -> "_FakeZ3Expr":
        return _FakeZ3Expr(f"({self.text}*{other})")

    __rmul__ = __mul__

    def __add__(self, other: Any) -> "_FakeZ3Expr":
        return _FakeZ3Expr(f"({self.text}+{other})")

    __radd__ = __add__

    def __ge__(self, other: Any) -> Tuple[str, str, Any]:
        return ("ge", self.text, other)

    def __gt__(self, other: Any) -> Tuple[str, str, Any]:
        return ("gt", self.text, other)

    def __le__(self, other: Any) -> Tuple[str, str, Any]:
        return ("le", self.text, other)

    def __lt__(self, other: Any) -> Tuple[str, str, Any]:
        return ("lt", self.text, other)

    def __eq__(self, other: Any) -> Tuple[str, str, Any]:  # type: ignore[override]
        return ("eq", self.text, other)

    __hash__ = None  # type: ignore[assignment]


class _FakeZ3Solver:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.constraints: List[Any] = []

    def set(self, *args: Any, **kwargs: Any) -> None:
        return None

    def add(self, constraint: Any) -> None:
        self.constraints.append(constraint)

    def check(self) -> str:
        return self.outcome

    def model(self) -> str:
        return "d = -1"


class _FakeZ3Factory:
    """Injected solver factory: lets tests force ``sat``/``unknown`` outcomes."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome

    def Solver(self) -> _FakeZ3Solver:
        return _FakeZ3Solver(self.outcome)

    def Int(self, name: str) -> _FakeZ3Expr:
        return _FakeZ3Expr(name)


class ZeroTrustPipelineTests(unittest.TestCase):
    """Self-tests proving the tiered gate rejects unsafe work and rewards real speed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="sask-zt-selftest-")
        self.root = Path(self._tmp.name)
        self.golden_path = self.root / "verify_golden_suite.json"
        self.holdout_path = self.root / "verify_holdout_suite.json"
        self.manifest_path = self.root / "verify_integrity.json"
        self.cache_path = self.root / "cache.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_pipeline(self, **overrides: Any) -> ZeroTrustPipeline:
        pipeline = ZeroTrustPipeline(
            root=self.root,
            golden_path=self.golden_path,
            holdout_path=self.holdout_path,
            manifest_path=self.manifest_path,
            cache_path=overrides.pop("cache_path", self.cache_path),
            enforce_integrity=overrides.pop("enforce_integrity", False),
            telemetry=False,
            solver_factory=overrides.pop("solver_factory", None),
        )
        pipeline._discipline_floor_ok = lambda: True  # type: ignore[assignment]
        return pipeline

    def _seal(self) -> None:
        (self.root / "verify.py").write_text(
            "# sealed placeholder verify.py\n", encoding="utf-8"
        )
        for suite_name in SUITE_ARTIFACTS:
            suite_path = self.root / suite_name
            if not suite_path.exists():
                suite_path.write_text(
                    json.dumps(
                        {"suite": suite_name, "version": INVARIANT_VERSION, "cases": []}
                    ),
                    encoding="utf-8",
                )
        IntegrityGuard.seal(self.root, self.manifest_path)

    # -- Tier 0 ----------------------------------------------------------
    def test_tier0_rejects_tampered_verifier(self) -> None:
        self._seal()
        pipeline = self._make_pipeline(enforce_integrity=True)
        spec = CandidateSpec(
            module_source="def solve(x):\n    return x\n",
            cases=[[3]],
            allow_nonfinite=True,
        )
        verdict = pipeline.evaluate(spec)
        self.assertTrue(verdict.passed, verdict.reason)

        (self.root / "verify.py").write_text("# attacker weakened the verifier\n", encoding="utf-8")
        tampered = pipeline.evaluate(spec)
        self.assertFalse(tampered.passed)
        self.assertEqual(tampered.failed_tier, "tier0")
        self.assertEqual(tampered.reason_code, "integrity_mismatch")

    def test_tier0_allows_ratchet_appends(self) -> None:
        self._seal()
        RatchetSuite(self.golden_path, "golden").append(
            {"kind": "function_output", "args": [9], "expected": 9}
        )
        pipeline = self._make_pipeline(enforce_integrity=True)
        spec = CandidateSpec(
            module_source="def solve(x):\n    return x\n",
            cases=[[3]],
            allow_nonfinite=True,
        )
        verdict = pipeline.evaluate(spec)
        self.assertTrue(verdict.passed, verdict.reason)

    def test_tier0_detects_deleted_sealed_case(self) -> None:
        RatchetSuite(self.golden_path, "golden").append(
            {"kind": "function_output", "args": [9], "expected": 9}
        )
        self._seal()
        pipeline = self._make_pipeline(enforce_integrity=True)
        spec = CandidateSpec(
            module_source="def solve(x):\n    return x\n",
            cases=[[3]],
            allow_nonfinite=True,
        )
        self.assertTrue(pipeline.evaluate(spec).passed)

        self.golden_path.write_text(
            json.dumps({"suite": "golden", "version": INVARIANT_VERSION, "cases": []}),
            encoding="utf-8",
        )
        tampered = pipeline.evaluate(spec)
        self.assertFalse(tampered.passed)
        self.assertEqual(tampered.failed_tier, "tier0")
        self.assertEqual(tampered.reason_code, "integrity_mismatch")
        failures = " ".join(tampered.counterexample.get("failures") or [])
        self.assertIn("removed", failures)

    def test_tier0_blocks_reward_hacking(self) -> None:
        pipeline = self._make_pipeline()
        hardcoded = CandidateSpec(
            module_source=(
                "def solve(x):\n"
                "    if x == 41:\n"
                "        return 42\n"
                "    return 0\n"
            ),
            cases=[[41]],
        )
        verdict = pipeline.evaluate(hardcoded)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier0")
        self.assertEqual(verdict.reason_code, "ast_hardcoded_test_input")

        injected = CandidateSpec(
            module_source="def solve(x):\n    return eval(str(x))\n",
            cases=[[41]],
        )
        verdict = pipeline.evaluate(injected)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier0")
        self.assertEqual(verdict.reason_code, "ast_forbidden_call")

        suppressed = CandidateSpec(
            module_source=(
                "def solve(x):\n"
                "    try:\n"
                "        return 1 / x\n"
                "    except Exception:\n"
                "        pass\n"
            ),
            cases=[[2]],
        )
        verdict = pipeline.evaluate(suppressed)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason_code, "ast_suppressed_exception")

    def test_tier0_fails_fast_in_milliseconds(self) -> None:
        pipeline = self._make_pipeline()
        spec = CandidateSpec(
            module_source="def solve(x):\n    return exec(x)\n",
            cases=[[1]],
        )
        started = time.perf_counter()
        verdict = pipeline.evaluate(spec)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier0")
        self.assertLess(elapsed_ms, 250.0, f"tier0 took {elapsed_ms:.1f} ms")

    # -- Tier 1 ----------------------------------------------------------
    def test_tier1_differential_regression_ratchets_and_blocks_recurrence(self) -> None:
        baseline = (
            "def solve(x):\n"
            "    for _ in range(500000):\n"
            "        pass\n"
            "    return x * 2\n"
        )
        candidate = "def solve(x):\n    return x * 3\n"
        pipeline = self._make_pipeline()
        spec = CandidateSpec(
            module_source=candidate,
            baseline_source=baseline,
            cases=[[3]],
            allow_nonfinite=True,
            enforce_budget=False,
        )
        verdict = pipeline.evaluate(spec)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier1")
        self.assertEqual(verdict.reason_code, "differential_mismatch")
        self.assertIsNotNone(verdict.ratcheted)

        golden = json.loads(self.golden_path.read_text(encoding="utf-8"))
        self.assertEqual(len(golden["cases"]), 1)
        self.assertEqual(golden["cases"][0]["args"], [3])
        self.assertEqual(golden["cases"][0]["expected"], 6)

        fresh = self._make_pipeline(cache_path=self.root / "cache2.json")
        recurrence = fresh.evaluate(spec)
        self.assertFalse(recurrence.passed)
        self.assertEqual(recurrence.failed_tier, "tier1")
        self.assertEqual(recurrence.reason_code, "golden_mismatch")

    def test_tier1_rejects_memory_spike(self) -> None:
        pipeline = self._make_pipeline()
        spec = CandidateSpec(
            module_source=(
                "def solve(x):\n"
                "    data = [0] * (4 * 1024 * 1024)\n"
                "    return len(data)\n"
            ),
            baseline_source="def solve(x):\n    return 0\n",
            cases=[[1]],
            memory_limit_mb=2.0,
            allow_nonfinite=True,
        )
        verdict = pipeline.evaluate(spec)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier1")
        self.assertEqual(verdict.reason_code, "memory_envelope_exceeded")

    def test_tier2_new_counterexample_becomes_permanent(self) -> None:
        crashy = "def solve(x):\n    return x.bit_length()\n"
        pipeline = self._make_pipeline()
        spec = CandidateSpec(module_source=crashy, cases=[[5]], allow_nonfinite=True)
        verdict = pipeline.evaluate(spec)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier2")
        self.assertEqual(verdict.reason_code, "fuzz_unexpected_exception")
        self.assertIsNotNone(verdict.ratcheted)

        golden = json.loads(self.golden_path.read_text(encoding="utf-8"))
        self.assertTrue(any(entry["args"] == [None] for entry in golden["cases"]))

        fresh = self._make_pipeline(cache_path=self.root / "cache2.json")
        recurrence = fresh.evaluate(spec)
        self.assertFalse(recurrence.passed)
        self.assertEqual(recurrence.failed_tier, "tier1")
        self.assertEqual(recurrence.reason_code, "golden_no_crash")

    # -- Tier 3 ----------------------------------------------------------
    def test_tier3_smt_counterexample_rejects(self) -> None:
        pipeline = self._make_pipeline(solver_factory=_FakeZ3Factory("sat"))
        spec = CandidateSpec(
            module_source="def solve(x):\n    return x + 1\n",
            cases=[[1]],
            allow_nonfinite=True,
            invariants=[
                {
                    "name": "discipline_nonnegative",
                    "assumptions": ["d >= -5"],
                    "claim": "d >= 0",
                }
            ],
        )
        verdict = pipeline.evaluate(spec)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier3")
        self.assertEqual(verdict.reason_code, "smt_counterexample")

    def test_tier3_unknown_fails_closed_on_critical(self) -> None:
        pipeline = self._make_pipeline(solver_factory=_FakeZ3Factory("unknown"))
        spec = CandidateSpec(
            module_source="def solve(x):\n    return x + 1\n",
            cases=[[1]],
            allow_nonfinite=True,
            critical=True,
            invariants=[
                {
                    "name": "discipline_nonnegative",
                    "assumptions": ["d >= 0"],
                    "claim": "d >= 0",
                }
            ],
        )
        verdict = pipeline.evaluate(spec)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier3")
        self.assertEqual(verdict.reason_code, "smt_unknown")

    def test_tier3_missing_solver_fails_closed_on_critical(self) -> None:
        pipeline = self._make_pipeline()
        try:
            import z3  # noqa: F401  # type: ignore[import]
            self.skipTest("z3 is installed; missing-solver path cannot be exercised")
        except Exception:
            pass
        spec = CandidateSpec(
            module_source="def solve(x):\n    return x + 1\n",
            cases=[[1]],
            allow_nonfinite=True,
            critical=True,
            invariants=[{"name": "always", "assumptions": [], "claim": "d >= 0"}],
        )
        verdict = pipeline.evaluate(spec)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason_code, "formal_solver_unavailable")

    # -- Tier 4 ----------------------------------------------------------
    def test_tier4_hidden_holdout_blocks_overfit(self) -> None:
        self.holdout_path.write_text(
            json.dumps(
                {
                    "suite": "holdout",
                    "version": INVARIANT_VERSION,
                    "cases": [
                        {"kind": "function_output", "args": [7], "expected": 15},
                    ],
                }
            ),
            encoding="utf-8",
        )
        pipeline = self._make_pipeline()
        spec = CandidateSpec(
            module_source="def solve(x):\n    return x * 2\n",
            cases=[[2]],
            allow_nonfinite=True,
        )
        verdict = pipeline.evaluate(spec)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failed_tier, "tier4")
        self.assertEqual(verdict.reason_code, "holdout_mismatch")

    # -- Progress-rate optimizations -------------------------------------
    def test_valid_faster_candidate_passes_and_is_cached(self) -> None:
        baseline = (
            "def solve(n):\n"
            "    total = 0\n"
            "    for _ in range(n):\n"
            "        total += 1\n"
            "    return total\n"
        )
        candidate = "def solve(n):\n    return n\n"
        pipeline = self._make_pipeline()
        spec = CandidateSpec(
            module_source=candidate,
            baseline_source=baseline,
            cases=[[2000000]],
            allow_nonfinite=True,
            timeout_seconds=5.0,
        )
        first = pipeline.evaluate(spec)
        self.assertTrue(first.passed, first.reason)
        self.assertGreater(first.speedup_ratio or 0.0, 1.0)
        self.assertFalse(first.cached)

        second = pipeline.evaluate(spec)
        self.assertTrue(second.passed)
        self.assertTrue(second.cached)
        self.assertLess(second.tier_timings.get("cache_ms", 999.0), 1.0)

    def test_scope_targets_single_module_and_widens_for_core(self) -> None:
        pipeline = self._make_pipeline()
        targeted = pipeline.scope_for(["mutate.py"])
        self.assertEqual(targeted["mode"], "targeted")
        self.assertIn("test_mutate_code_and_text", targeted["targeted_tests"])
        self.assertEqual(pipeline.scope_for(["core.py"])["mode"], "full-sweep")
        self.assertEqual(pipeline.scope_for(["mystery.py"])["mode"], "full-sweep")
        self.assertEqual(pipeline.scope_for([])["mode"], "full-sweep")

    def test_ratchet_suite_dedupes_and_persists(self) -> None:
        suite = RatchetSuite(self.golden_path, "golden")
        case = {"kind": "function_output", "args": [11], "expected": 22}
        entry, created = suite.append(case)
        self.assertTrue(created)
        again, created_again = suite.append(case)
        self.assertFalse(created_again)
        self.assertEqual(entry["id"], again["id"])
        reloaded = RatchetSuite(self.golden_path, "golden").load()
        self.assertEqual(len(reloaded["cases"]), 1)
        self.assertEqual(reloaded["cases"][0]["sanitizer"], "verify.ratchet.v1")

    def test_ratchet_scope_isolates_candidates(self) -> None:
        pipeline = self._make_pipeline()
        regressed = pipeline.evaluate(
            CandidateSpec(
                module_source="def solve(x):\n    return x * 3\n",
                baseline_source="def solve(x):\n    return x * 2\n",
                cases=[[3]],
                allow_nonfinite=True,
                enforce_budget=False,
                label="alpha",
            )
        )
        self.assertFalse(regressed.passed)
        unrelated = pipeline.evaluate(
            CandidateSpec(
                module_source="def solve(x):\n    return x * 2\n",
                cases=[[3]],
                allow_nonfinite=True,
                label="beta",
            )
        )
        self.assertTrue(unrelated.passed, unrelated.reason)

    def test_workspace_scan_flags_forbidden_calls(self) -> None:
        self._seal()
        bad = self.root / "bad.py"
        bad.write_text("def solve(x):\n    return eval(x)\n", encoding="utf-8")
        good = self.root / "good.py"
        good.write_text("def solve(x):\n    return x + 1\n", encoding="utf-8")
        pipeline = self._make_pipeline(enforce_integrity=True)
        verdict = pipeline.scan_workspace(self.root, files=[bad, good])
        self.assertFalse(verdict["passed"])
        self.assertTrue(verdict["integrity"]["reason_code"] == "integrity_ok")
        self.assertTrue(any(item.get("file") == "bad.py" for item in verdict["violations"]))


if __name__ == "__main__":
    runner = VerificationRunner(verbosity=2)
    report = runner.run_ancestral_gate()
    depth = report.details.get("overthinking_depth", "?")
    print(f"\n--- Ancestral Verification Report (overthinking depth: {depth}) ---")
    print(f"Total: {report.total_checks} | Passed: {report.passed_checks} | Failed: {report.failed_checks}")
    print(f"Duration: {report.duration_seconds}s")
    print(f"Ego ratchet: {report.details.get('ego_ratchet')}")
    if not report.is_successful:
        for err in report.errors:
            print(err)
        sys.exit(1)
    print("All component checks PASSED successfully.")
