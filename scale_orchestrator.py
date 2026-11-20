"""Elastic auto-scaling orchestrator and self-improving meta-scheduler.

Responsibilities:

* An immutable constitutional governor caps RAM, local workers, cloud shards,
  cycle wall-clock, and verification tiers. Policies are clamped to the
  governor; verify.py independently re-checks the live ceilings and rejects
  any candidate that tries to weaken them.
* A bounded parallel pool dispatches independent work (syntax proving, code
  analysis, e-graph batches, adversarial probes) and supports
  winner-takes-all races: the first candidate that clears the acceptance
  predicate cancels the remaining queued work.
* A Thompson-sampling bandit allocates strategy slots with a 15% exploration
  floor and tracks verified speedup per CPU-second.
* A calibrator derives p95 latency, RAM growth, batch size, concurrency, and
  solver timeout recommendations from real cycle telemetry.
* A policy store benchmarks candidate schedules against the active policy on
  replayed real workloads, promotes only verified wins, snapshots every
  version into ``scale_policy_backups/``, and auto-rolls back on live tail
  latency or failure-rate regressions.

Runtime policy files (``scale_policy.json`` and ``scale_policy_backups/``) are
local, git-ignored artifacts.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import math
import os
import random
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("ScaleOrchestrator")

ROOT = Path(__file__).resolve().parent
POLICY_PATH = ROOT / "scale_policy.json"
BACKUP_DIR = ROOT / "scale_policy_backups"

DEFAULT_STRATEGIES: Tuple[str, ...] = (
    "goal_dispatch",
    "code_analysis",
    "syntax_proving",
    "egraph_batch",
    "devil_probe",
    "mutation_race",
)
MAX_BACKUPS = 24
HISTORY_LIMIT = 32
EXPLORATION_FLOOR = 0.15
PROMOTION_MIN_SPEEDUP = 1.02
TAIL_REGRESSION_TOLERANCE = 1.10
LIVE_TAIL_REGRESSION_FACTOR = 1.25
LIVE_FAILURE_REGRESSION = 0.05
DEFAULT_WORKER_RAM_MB = 192.0


class ConstitutionalViolation(RuntimeError):
    """Raised when a policy tries to breach or weaken the constitutional limits."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _detected_cpu_count() -> int:
    try:
        return max(1, int(os.cpu_count() or 1))
    except Exception:
        return 1


class ConstitutionalLimits:
    """Hard, immutable safety ceiling. Nothing in the system may raise it."""

    __slots__ = ("_values",)

    def __init__(self, cpu_count: Optional[int] = None) -> None:
        cpu = _detected_cpu_count() if cpu_count is None else max(1, int(cpu_count))
        object.__setattr__(
            self,
            "_values",
            {
                "max_ram_fraction": 0.80,
                "max_local_workers": max(1, cpu - 1),
                "max_cloud_shards": 4,
                "max_cycle_seconds": 340.0 * 60.0,
                "min_exploration_floor": EXPLORATION_FLOOR,
                "min_discipline_floor": 0.90,
                "required_tiers": (0, 1, 2, 3, 4),
            },
        )

    def __getattr__(self, name: str) -> Any:
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"CONSTITUTIONAL_LIMITS.{name} is immutable; self-tuning may only clamp."
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError("CONSTITUTIONAL_LIMITS is immutable.")

    def as_dict(self) -> Dict[str, Any]:
        values = dict(object.__getattribute__(self, "_values"))
        values["required_tiers"] = list(values["required_tiers"])
        return values

    def __repr__(self) -> str:
        return f"ConstitutionalLimits({self.as_dict()})"


CONSTITUTIONAL_LIMITS = ConstitutionalLimits()

LIMIT_FIELD_NAMES = frozenset(
    {
        "max_ram_fraction",
        "max_local_workers",
        "max_cloud_shards",
        "max_cycle_seconds",
        "min_exploration_floor",
        "min_discipline_floor",
    }
)

_VERIFY_TIERS = (0, 1, 2, 3, 4)


def expected_constitutional_values(cpu_count: Optional[int] = None) -> Dict[str, Any]:
    """Canonical ceiling values, used by verify.py to detect tampering."""
    cpu = _detected_cpu_count() if cpu_count is None else max(1, int(cpu_count))
    return {
        "max_ram_fraction": 0.80,
        "max_local_workers": max(1, cpu - 1),
        "max_cloud_shards": 4,
        "max_cycle_seconds": 340.0 * 60.0,
        "min_exploration_floor": EXPLORATION_FLOOR,
        "min_discipline_floor": 0.90,
        "required_tiers": list(_VERIFY_TIERS),
    }


def validate_policy(
    policy: Mapping[str, Any],
    limits: Optional[ConstitutionalLimits] = None,
) -> Dict[str, Any]:
    """Return structured violations for any policy that breaches the ceiling."""
    limits = limits or CONSTITUTIONAL_LIMITS
    policy = dict(policy or {})
    violations: List[str] = []

    tiers = policy.get("verify_tiers")
    if not isinstance(tiers, (list, tuple, set)):
        violations.append("verify_tiers_missing: Tiers 0-4 must be present")
    else:
        try:
            provided = {int(tier) for tier in tiers}
        except (TypeError, ValueError):
            provided = set()
        missing = sorted(set(limits.required_tiers) - provided)
        if missing:
            violations.append(f"tiers_weakened: missing tiers {missing}")
    for key in ("disable_tiers", "skip_tiers", "bypass_tiers"):
        if policy.get(key):
            violations.append(f"tiers_bypass_forbidden: {key}")

    local_workers = policy.get("local_workers")
    if local_workers is not None:
        workers = _safe_int(local_workers, -1)
        if workers < 1 or workers > limits.max_local_workers:
            violations.append(
                f"local_workers_out_of_bounds: {workers} > {limits.max_local_workers}"
            )

    cloud_shards = policy.get("cloud_shards")
    if cloud_shards is not None:
        shards = _safe_int(cloud_shards, -1)
        if shards < 0 or shards > limits.max_cloud_shards:
            violations.append(
                f"cloud_shards_out_of_bounds: {shards} > {limits.max_cloud_shards}"
            )

    ram_fraction = policy.get("ram_fraction", policy.get("max_ram_fraction"))
    if ram_fraction is not None:
        fraction = _safe_float(ram_fraction, -1.0) or -1.0
        if fraction <= 0 or fraction > limits.max_ram_fraction + 1e-12:
            violations.append(
                f"ram_fraction_out_of_bounds: {fraction} > {limits.max_ram_fraction}"
            )

    cycle_seconds = policy.get("cycle_seconds")
    if cycle_seconds is not None:
        seconds = _safe_float(cycle_seconds, -1.0) or -1.0
        if seconds <= 0 or seconds > limits.max_cycle_seconds + 1e-9:
            violations.append(
                f"cycle_seconds_out_of_bounds: {seconds} > {limits.max_cycle_seconds}"
            )

    floor = policy.get("exploration_floor")
    if floor is not None:
        value = _safe_float(floor, -1.0) or -1.0
        if value < limits.min_exploration_floor - 1e-12 or value > 1.0:
            violations.append(
                f"exploration_floor_too_low: {value} < {limits.min_exploration_floor}"
            )

    batch_size = policy.get("batch_size")
    if batch_size is not None:
        batch = _safe_int(batch_size, -1)
        if batch < 1 or batch > 128:
            violations.append(f"batch_size_out_of_bounds: {batch}")

    timeout = policy.get("solver_timeout_seconds")
    if timeout is not None:
        seconds = _safe_float(timeout, -1.0) or -1.0
        if seconds < 0.05 or seconds > 120.0:
            violations.append(f"solver_timeout_out_of_bounds: {seconds}")

    return {
        "valid": not violations,
        "violations": violations,
        "reason": "; ".join(violations) if violations else "ok",
    }


def clamp_policy(
    policy: Mapping[str, Any],
    limits: Optional[ConstitutionalLimits] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Return a ceiling-compliant copy plus notes for every value clamped."""
    limits = limits or CONSTITUTIONAL_LIMITS
    clamped = dict(policy or {})
    notes: List[str] = []

    def set_value(key: str, value: Any) -> None:
        if clamped.get(key) != value:
            notes.append(f"{key}: {clamped.get(key)!r} -> {value!r}")
            clamped[key] = value

    workers = _safe_int(clamped.get("local_workers", limits.max_local_workers), 1)
    set_value("local_workers", max(1, min(limits.max_local_workers, workers)))

    shards = _safe_int(clamped.get("cloud_shards", min(2, limits.max_cloud_shards)), 2)
    set_value("cloud_shards", max(0, min(limits.max_cloud_shards, shards)))

    fraction = _safe_float(clamped.get("ram_fraction", 0.60), 0.60) or 0.60
    set_value("ram_fraction", round(_clamp(fraction, 0.10, limits.max_ram_fraction), 4))
    clamped.pop("max_ram_fraction", None)

    seconds = _safe_float(clamped.get("cycle_seconds", limits.max_cycle_seconds), limits.max_cycle_seconds)
    set_value("cycle_seconds", round(_clamp(seconds or limits.max_cycle_seconds, 1.0, limits.max_cycle_seconds), 3))

    floor = _safe_float(clamped.get("exploration_floor", EXPLORATION_FLOOR), EXPLORATION_FLOOR)
    set_value("exploration_floor", round(_clamp(floor or EXPLORATION_FLOOR, limits.min_exploration_floor, 1.0), 4))

    batch = _safe_int(clamped.get("batch_size", 8), 8)
    set_value("batch_size", max(1, min(128, batch)))

    timeout = _safe_float(clamped.get("solver_timeout_seconds", 2.5), 2.5) or 2.5
    set_value("solver_timeout_seconds", round(_clamp(timeout, 0.05, 120.0), 4))

    tiers = clamped.get("verify_tiers")
    provided: set = set()
    if isinstance(tiers, (list, tuple, set)):
        for tier in tiers:
            try:
                provided.add(int(tier))
            except (TypeError, ValueError):
                continue
    required = set(limits.required_tiers)
    set_value("verify_tiers", sorted(required | provided))
    for key in ("disable_tiers", "skip_tiers", "bypass_tiers"):
        clamped.pop(key, None)

    return clamped, notes


def default_policy(limits: Optional[ConstitutionalLimits] = None) -> Dict[str, Any]:
    limits = limits or CONSTITUTIONAL_LIMITS
    workers = max(1, min(limits.max_local_workers, min(4, limits.max_local_workers)))
    return {
        "version": 1,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "local_workers": workers,
        "batch_size": 8,
        "solver_timeout_seconds": 2.5,
        "ram_fraction": 0.60,
        "cycle_seconds": limits.max_cycle_seconds,
        "cloud_shards": min(2, limits.max_cloud_shards),
        "exploration_floor": EXPLORATION_FLOOR,
        "verify_tiers": list(limits.required_tiers),
        "strategies": {},
        "cloud": {
            "active_shards": min(2, limits.max_cloud_shards),
            "consecutive_throttles": 0,
            "cooldown_until": 0.0,
        },
        "live": {
            "samples": [],
            "tail_latency_ms": None,
            "failure_rate": 0.0,
            "baseline_tail_latency_ms": None,
            "baseline_failure_rate": 0.0,
        },
        "history": [],
    }


def detect_constitutional_tampering(source: str) -> List[Dict[str, Any]]:
    """Statically flag attempts to rebind or delete the constitutional ceiling."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    findings: List[Dict[str, Any]] = []

    def root_name(node: ast.AST) -> Optional[str]:
        current = node
        while isinstance(current, ast.Attribute):
            current = current.value
        return current.id if isinstance(current, ast.Name) else None

    def record(node: ast.AST, reason: str) -> None:
        findings.append(
            {
                "line": getattr(node, "lineno", 0),
                "code": "constitutional_tampering",
                "message": reason,
            }
        )

    for node in getattr(tree, "body", []):
        targets: List[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and (
                target.id == "CONSTITUTIONAL_LIMITS" or target.id in LIMIT_FIELD_NAMES
            ):
                record(target, f"module-level rebinding of {target.id}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and root_name(node) == "CONSTITUTIONAL_LIMITS":
            parent_is_load = isinstance(node.ctx, ast.Load)
            if not parent_is_load:
                record(node, f"mutation of CONSTITUTIONAL_LIMITS.{node.attr}")
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("setattr", "__setattr__") and node.args:
                if root_name(node.args[0]) == "CONSTITUTIONAL_LIMITS":
                    record(node, "setattr on CONSTITUTIONAL_LIMITS")
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if root_name(target) == "CONSTITUTIONAL_LIMITS":
                    record(node, "deletion of CONSTITUTIONAL_LIMITS")

    deduped: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for item in findings:
        deduped[(item["line"], item["message"])] = item
    return list(deduped.values())[:8]


# ---------------------------------------------------------------------------
# Hardware / quota discovery
# ---------------------------------------------------------------------------


def _ram_info() -> Tuple[Optional[float], Optional[float]]:
    """Return (total_mb, available_mb) using psutil, Win32, or /proc."""
    try:
        import psutil  # type: ignore[import-not-found]

        vm = psutil.virtual_memory()
        return (
            round(float(vm.total) / (1024.0 * 1024.0), 3),
            round(float(vm.available) / (1024.0 * 1024.0), 3),
        )
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return (
                    round(status.ullTotalPhys / (1024.0 * 1024.0), 3),
                    round(status.ullAvailPhys / (1024.0 * 1024.0), 3),
                )
        except Exception:
            pass

    try:
        status = Path("/proc/meminfo")
        if status.is_file():
            values: Dict[str, float] = {}
            for line in status.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    values[parts[0].rstrip(":")] = float(parts[1])
            total = values.get("MemTotal")
            available = values.get("MemAvailable", values.get("MemFree"))
            if total is not None:
                return (
                    round(total / 1024.0, 3),
                    round((available or 0.0) / 1024.0, 3),
                )
    except Exception:
        pass
    return None, None


def _gpu_info() -> Tuple[bool, List[str]]:
    names: List[str] = []
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if completed.returncode == 0:
            names = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    except Exception:
        names = []
    if not names and str(os.environ.get("SASK_GPU_AVAILABLE", "")).lower() in ("1", "true", "yes"):
        names = ["configured"]
    return bool(names), names


def _solver_endpoints() -> List[str]:
    raw = os.environ.get("SASK_SOLVER_ENDPOINTS", "").strip()
    if not raw:
        return []
    seen: List[str] = []
    for item in raw.split(","):
        endpoint = item.strip()
        if endpoint and endpoint not in seen:
            seen.append(endpoint)
    return seen[:12]


@dataclass
class HardwareProfile:
    cpu_count: int
    ram_total_mb: Optional[float]
    ram_available_mb: Optional[float]
    gpu_available: bool
    gpu_names: List[str]
    solver_endpoints: List[str]
    max_local_workers: int
    ram_budget_mb: Optional[float]
    safe_local_workers: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cpu_count": self.cpu_count,
            "ram_total_mb": self.ram_total_mb,
            "ram_available_mb": self.ram_available_mb,
            "gpu_available": self.gpu_available,
            "gpu_names": list(self.gpu_names),
            "solver_endpoints": list(self.solver_endpoints),
            "max_local_workers": self.max_local_workers,
            "ram_budget_mb": self.ram_budget_mb,
            "safe_local_workers": self.safe_local_workers,
        }


class ResourceProbe:
    """Discover local CPU/RAM/GPU/solver capacity without hard dependencies."""

    def __init__(self, limits: Optional[ConstitutionalLimits] = None) -> None:
        self.limits = limits or CONSTITUTIONAL_LIMITS

    def probe(
        self,
        cpu_count: Optional[int] = None,
        ram: Optional[Tuple[Optional[float], Optional[float]]] = None,
        gpu: Optional[Tuple[bool, List[str]]] = None,
        endpoints: Optional[Sequence[str]] = None,
    ) -> HardwareProfile:
        cpu = max(1, int(cpu_count)) if cpu_count else _detected_cpu_count()
        total_mb, available_mb = ram if ram is not None else _ram_info()
        gpu_available, gpu_names = gpu if gpu is not None else _gpu_info()
        solver = list(endpoints) if endpoints is not None else _solver_endpoints()
        budget = (
            round(available_mb * self.limits.max_ram_fraction, 3)
            if available_mb is not None
            else None
        )
        max_workers = max(1, cpu - 1)
        safe_workers = max_workers
        if budget is not None:
            safe_workers = min(max_workers, max(1, int(budget // DEFAULT_WORKER_RAM_MB)))
        return HardwareProfile(
            cpu_count=cpu,
            ram_total_mb=total_mb,
            ram_available_mb=available_mb,
            gpu_available=gpu_available,
            gpu_names=list(gpu_names),
            solver_endpoints=solver,
            max_local_workers=max_workers,
            ram_budget_mb=budget,
            safe_local_workers=max(1, safe_workers),
        )


# ---------------------------------------------------------------------------
# Winner-takes-all bounded pool
# ---------------------------------------------------------------------------


@dataclass
class RaceResult:
    winner: Any = None
    winner_name: Optional[str] = None
    results: Dict[str, Any] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)
    cancelled: int = 0
    early_stopped: int = 0
    saved_compute_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    timed_out: bool = False

    @property
    def found_winner(self) -> bool:
        return self.winner_name is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "winner_name": self.winner_name,
            "winner": self.winner,
            "results": self.results,
            "failures": self.failures,
            "cancelled": self.cancelled,
            "early_stopped": self.early_stopped,
            "saved_compute_seconds": self.saved_compute_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "timed_out": self.timed_out,
        }


class WinnerPool:
    """Bounded thread pool with cooperative early cancellation."""

    def __init__(self, max_workers: int = 4, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_workers = max(1, int(max_workers))
        self.clock = clock

    @staticmethod
    def _normalise(
        tasks: Iterable[Any],
    ) -> List[Tuple[str, Callable[..., Any]]]:
        items: List[Tuple[str, Callable[..., Any]]] = []
        for index, task in enumerate(tasks):
            if isinstance(task, tuple) and len(task) == 2:
                name, fn = task
            else:
                name, fn = f"task-{index}", task
            if callable(fn):
                items.append((str(name), fn))
        return items

    @staticmethod
    def _accepts_stop(fn: Callable[..., Any]) -> bool:
        try:
            return "stop_event" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False

    def _invoke(self, fn: Callable[..., Any], stop_event: threading.Event) -> Any:
        if self._accepts_stop(fn):
            return fn(stop_event=stop_event)
        return fn()

    def run_race(
        self,
        tasks: Iterable[Any],
        is_winner: Callable[[Any], bool],
        max_seconds: Optional[float] = None,
        cost_hint: float = 0.0,
    ) -> RaceResult:
        items = self._normalise(tasks)
        if not items:
            return RaceResult()

        started = self.clock()
        stop_event = threading.Event()
        results: Dict[str, Any] = {}
        failures: Dict[str, str] = {}
        winner: Any = None
        winner_name: Optional[str] = None
        cancelled = 0
        early_stopped = 0
        timed_out = False

        pool = ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(items)),
            thread_name_prefix="sask-race",
        )
        futures: Dict[Any, str] = {}
        try:
            for name, fn in items:
                futures[pool.submit(self._invoke, fn, stop_event)] = name
            pending = set(futures)
            while pending:
                timeout: Optional[float] = None
                if max_seconds is not None:
                    timeout = max_seconds - (self.clock() - started)
                    if timeout <= 0:
                        timed_out = True
                        break
                done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                if not done:
                    timed_out = True
                    break
                for future in done:
                    pending.discard(future)
                    name = futures[future]
                    try:
                        value = future.result()
                    except Exception as exc:
                        failures[name] = f"{type(exc).__name__}: {exc}"
                        continue
                    results[name] = value
                    try:
                        won = bool(is_winner(value))
                    except Exception:
                        won = False
                    if won and winner_name is None:
                        winner = value
                        winner_name = name
                        stop_event.set()
                        for other in list(pending):
                            if other.cancel():
                                cancelled += 1
                        early_stopped = len(pending)
                        break
                if winner_name is not None:
                    break
        finally:
            stop_event.set()
            pool.shutdown(wait=True, cancel_futures=True)

        elapsed = max(0.0, self.clock() - started)
        completed = max(1, len(results) + len(failures))
        per_item = cost_hint if cost_hint > 0 else (elapsed / completed)
        saved = round(max(0, early_stopped) * per_item, 6)

        return RaceResult(
            winner=winner,
            winner_name=winner_name,
            results=results,
            failures=failures,
            cancelled=cancelled,
            early_stopped=early_stopped,
            saved_compute_seconds=saved,
            elapsed_seconds=round(elapsed, 6),
            timed_out=timed_out,
        )

    def run_parallel(
        self,
        tasks: Iterable[Any],
        max_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run every task, returning results and errors keyed by task name."""
        items = self._normalise(tasks)
        started = self.clock()
        results: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        if not items:
            return {
                "results": results,
                "errors": errors,
                "elapsed_seconds": 0.0,
                "timed_out": False,
            }

        pool = ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(items)),
            thread_name_prefix="sask-parallel",
        )
        futures = {pool.submit(self._invoke, fn, threading.Event()): name for name, fn in items}
        done, pending = wait(futures, timeout=max_seconds)
        for future in done:
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)

        return {
            "results": results,
            "errors": errors,
            "elapsed_seconds": round(max(0.0, self.clock() - started), 6),
            "timed_out": bool(pending),
        }


# ---------------------------------------------------------------------------
# Thompson-sampling strategy bandit
# ---------------------------------------------------------------------------


class StrategyBandit:
    """Multi-armed bandit over execution strategies with a 15% exploration floor."""

    def __init__(
        self,
        strategies: Optional[Sequence[str]] = None,
        exploration_floor: float = EXPLORATION_FLOOR,
        rng: Optional[random.Random] = None,
        state: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.strategies = list(strategies or DEFAULT_STRATEGIES)
        self.exploration_floor = _clamp(
            max(exploration_floor, CONSTITUTIONAL_LIMITS.min_exploration_floor),
            CONSTITUTIONAL_LIMITS.min_exploration_floor,
            1.0,
        )
        self.rng = rng or random.Random()
        self._stats: Dict[str, Dict[str, Any]] = {
            name: self._blank(name) for name in self.strategies
        }
        if state:
            self.from_dict(state)

    @staticmethod
    def _blank(name: str) -> Dict[str, Any]:
        return {
            "name": name,
            "pulls": 0,
            "wins": 0,
            "alpha": 1.0,
            "beta": 1.0,
            "speedup_sum": 0.0,
            "cost_sum": 0.0,
            "gain_sum": 0.0,
            "latencies": [],
        }

    def _ensure(self, strategy: str) -> Dict[str, Any]:
        if strategy not in self._stats:
            self.strategies.append(strategy)
            self._stats[strategy] = self._blank(strategy)
        return self._stats[strategy]

    def record(
        self,
        strategy: str,
        success: bool,
        speedup: float = 1.0,
        cost_seconds: float = 0.0,
        latency_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        stats = self._ensure(strategy)
        stats["pulls"] += 1
        if success:
            stats["wins"] += 1
            stats["alpha"] += 1.0
        else:
            stats["beta"] += 1.0
        gain = _safe_float(speedup, 1.0) or 1.0
        gain = gain if gain > 0 else 1.0
        stats["speedup_sum"] += gain
        stats["gain_sum"] += max(0.0, gain - 1.0)
        cost = _safe_float(cost_seconds, 0.0) or 0.0
        stats["cost_sum"] += max(0.0, cost)
        latency = _safe_float(latency_seconds, None)
        if latency is not None and latency >= 0:
            stats["latencies"] = (list(stats["latencies"]) + [round(latency, 6)])[-64:]
        return self.arm(strategy)

    def win_rate(self, strategy: str) -> float:
        stats = self._stats.get(strategy)
        if not stats:
            return 0.5
        return stats["alpha"] / (stats["alpha"] + stats["beta"])

    def avg_speedup(self, strategy: str) -> float:
        stats = self._stats.get(strategy)
        if not stats or not stats["pulls"]:
            return 1.0
        return max(0.01, stats["speedup_sum"] / stats["pulls"])

    def avg_cost(self, strategy: str) -> float:
        stats = self._stats.get(strategy)
        if not stats or not stats["pulls"]:
            return 0.05
        return max(0.001, stats["cost_sum"] / stats["pulls"])

    def yield_score(self, strategy: str) -> float:
        return (self.win_rate(strategy) * self.avg_speedup(strategy)) / self.avg_cost(strategy)

    def sample_score(self, strategy: str) -> float:
        stats = self._ensure(strategy)
        win = self.rng.betavariate(stats["alpha"], stats["beta"])
        mean = math.log(max(1e-6, self.avg_speedup(strategy)))
        sigma = 0.25 + 1.0 / (stats["pulls"] + 1.0)
        speedup = math.exp(self.rng.gauss(mean, sigma))
        return (win * speedup) / self.avg_cost(strategy)

    def allocate(self, slots: int) -> Dict[str, int]:
        slots = max(0, int(slots))
        allocation = {name: 0 for name in self.strategies}
        if slots == 0 or not self.strategies:
            return allocation

        explore_slots = 0
        if len(self.strategies) > 1:
            explore_slots = min(
                slots,
                max(1, int(math.ceil(slots * self.exploration_floor))),
            )
        least_pulled = sorted(
            self.strategies,
            key=lambda name: (self._stats[name]["pulls"], name),
        )
        for index in range(explore_slots):
            allocation[least_pulled[index % len(least_pulled)]] += 1

        for _ in range(slots - explore_slots):
            best = max(
                self.strategies,
                key=lambda name: (
                    self.sample_score(name) + self.rng.random() * 1e-6,
                    -self._stats[name]["pulls"],
                ),
            )
            allocation[best] += 1
        return allocation

    def p95_latency(self, strategy: str) -> Optional[float]:
        stats = self._stats.get(strategy)
        if not stats:
            return None
        return percentile(stats["latencies"], 0.95)

    def arm(self, strategy: str) -> Dict[str, Any]:
        stats = self._ensure(strategy)
        return {
            "name": strategy,
            "pulls": stats["pulls"],
            "wins": stats["wins"],
            "win_rate": round(self.win_rate(strategy), 4),
            "avg_speedup": round(self.avg_speedup(strategy), 4),
            "avg_cost_seconds": round(self.avg_cost(strategy), 4),
            "yield_score": round(self.yield_score(strategy), 4),
            "p95_latency_seconds": self.p95_latency(strategy),
        }

    def weights(self) -> Dict[str, Dict[str, Any]]:
        scores = {name: self.yield_score(name) for name in self.strategies}
        total = sum(scores.values()) or float(len(scores) or 1)
        return {
            name: {**self.arm(name), "weight": round(scores[name] / total, 4)}
            for name in sorted(self.strategies, key=lambda n: scores[n], reverse=True)
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "exploration_floor": self.exploration_floor,
            "arms": {name: dict(self._stats[name]) for name in self.strategies},
        }

    def from_dict(self, state: Mapping[str, Any]) -> None:
        floor = _safe_float(state.get("exploration_floor", self.exploration_floor), self.exploration_floor)
        if floor is not None:
            self.exploration_floor = _clamp(
                floor,
                CONSTITUTIONAL_LIMITS.min_exploration_floor,
                1.0,
            )
        arms = state.get("arms") if isinstance(state.get("arms"), dict) else state
        for name, payload in dict(arms or {}).items():
            if not isinstance(payload, dict):
                continue
            stats = self._ensure(str(name))
            for key in ("pulls", "wins"):
                if isinstance(payload.get(key), (int, float)):
                    stats[key] = max(0, int(payload[key]))
            for key in ("alpha", "beta", "speedup_sum", "cost_sum", "gain_sum"):
                value = _safe_float(payload.get(key), None)
                if value is not None:
                    stats[key] = value
            latencies = payload.get("latencies")
            if isinstance(latencies, list):
                stats["latencies"] = [
                    value
                    for value in (_safe_float(item, None) for item in latencies[-64:])
                    if value is not None
                ]


# ---------------------------------------------------------------------------
# Calibration from real telemetry
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    data = sorted(float(value) for value in values if _safe_float(value, None) is not None)
    if not data:
        return None
    if len(data) == 1:
        return round(data[0], 6)
    index = (len(data) - 1) * _clamp(q, 0.0, 1.0)
    lower = int(math.floor(index))
    upper = min(len(data) - 1, lower + 1)
    if lower == upper:
        return round(data[lower], 6)
    blended = data[lower] + (data[upper] - data[lower]) * (index - lower)
    return round(blended, 6)


def _slope(values: Sequence[float]) -> float:
    data = [float(value) for value in values]
    if len(data) < 2:
        return 0.0
    count = len(data)
    mean_x = (count - 1) / 2.0
    mean_y = sum(data) / count
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(data))
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    return numerator / denominator if denominator else 0.0


class Calibrator:
    """Derive batch, concurrency, and timeout guidance from recorded cycles."""

    def __init__(self, limits: Optional[ConstitutionalLimits] = None) -> None:
        self.limits = limits or CONSTITUTIONAL_LIMITS

    def calibrate(self, records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        latest = records[-1] if records else {}
        steps = latest.get("steps") if isinstance(latest, Mapping) else []
        steps = [step for step in (steps or []) if isinstance(step, Mapping)]
        durations = [
            max(0.0, (_safe_float(step.get("duration_ms"), 0.0) or 0.0) / 1000.0)
            for step in steps
        ]
        rss_values = [
            value
            for value in (_safe_float(step.get("rss_mb"), None) for step in steps)
            if value is not None
        ]
        deltas = [
            value
            for value in (_safe_float(step.get("rss_delta_mb"), None) for step in steps)
            if value is not None
        ]
        p50 = percentile(durations, 0.50)
        p95 = percentile(durations, 0.95)
        ram_growth = round(sum(max(0.0, delta) for delta in deltas), 3) if deltas else 0.0
        slope = round(_slope(rss_values), 4) if len(rss_values) >= 2 else 0.0

        if p95 is None:
            batch_size = 8
            timeout = 2.5
        else:
            if p95 >= 2.0:
                batch_size = 2
            elif p95 >= 1.0:
                batch_size = 4
            elif p95 <= 0.15:
                batch_size = 16
            else:
                batch_size = 8
            timeout = round(_clamp(max(0.5, p95 * 2.0), 0.5, 120.0), 3)

        concurrency = self.limits.max_local_workers
        if ram_growth > 512 or slope > 64:
            concurrency = max(1, concurrency // 2)
        if p95 is not None and p95 > 3.0:
            concurrency = max(1, concurrency - 1)

        return {
            "samples": len(durations),
            "p50_seconds": p50,
            "p95_seconds": p95,
            "ram_growth_mb": ram_growth,
            "ram_slope_mb_per_step": slope,
            "recommendations": {
                "batch_size": batch_size,
                "local_workers": min(concurrency, self.limits.max_local_workers),
                "solver_timeout_seconds": timeout,
            },
        }


# ---------------------------------------------------------------------------
# Cloud shard governor (bounded + exponential backoff)
# ---------------------------------------------------------------------------


class CloudShardGovernor:
    def __init__(
        self,
        limits: Optional[ConstitutionalLimits] = None,
        state: Optional[Mapping[str, Any]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or CONSTITUTIONAL_LIMITS
        payload = dict(state or {})
        self.max_shards = self.limits.max_cloud_shards
        self.active_shards = _clamp(
            _safe_int(payload.get("active_shards", min(2, self.max_shards)), 0),
            0,
            self.max_shards,
        )
        self.consecutive_throttles = max(0, _safe_int(payload.get("consecutive_throttles", 0), 0))
        self.cooldown_until = _safe_float(payload.get("cooldown_until", 0.0), 0.0) or 0.0
        self.clock = clock

    def request(self, desired: Optional[int] = None, rate_limited: bool = False) -> Dict[str, Any]:
        now = self.clock()
        if rate_limited:
            self.consecutive_throttles += 1
            backoff = min(900.0, 30.0 * (2 ** min(self.consecutive_throttles - 1, 5)))
            self.active_shards = max(0, self.active_shards // 2)
            if self.consecutive_throttles >= 2:
                self.active_shards = 0
            self.cooldown_until = now + backoff
            return self.snapshot("backoff")

        if now < self.cooldown_until:
            return self.snapshot("cooldown")

        self.consecutive_throttles = 0
        if desired is not None:
            self.active_shards = max(0, min(self.max_shards, _safe_int(desired, 0)))
        return self.snapshot("granted")

    def snapshot(self, status: str = "idle") -> Dict[str, Any]:
        now = self.clock()
        return {
            "status": status,
            "active_shards": self.active_shards,
            "max_shards": self.max_shards,
            "headroom_shards": max(0, self.max_shards - self.active_shards),
            "consecutive_throttles": self.consecutive_throttles,
            "cooldown_seconds": round(max(0.0, self.cooldown_until - now), 3),
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "active_shards": self.active_shards,
            "consecutive_throttles": self.consecutive_throttles,
            "cooldown_until": self.cooldown_until,
        }


# ---------------------------------------------------------------------------
# Policy store, snapshots, A/B benchmarking
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _lane_makespan(durations: Sequence[float], workers: int) -> float:
    lanes = [0.0] * max(1, workers)
    for duration in durations:
        index = min(range(len(lanes)), key=lambda position: lanes[position])
        lanes[index] += duration
    return max(lanes)


def replay_workload(workload: Sequence[float], policy: Mapping[str, Any]) -> Dict[str, Any]:
    """Estimate makespan/throughput for a policy on recorded real durations."""
    durations = [max(0.0, float(value)) for value in workload if _safe_float(value, None) is not None]
    durations = [value for value in durations if value > 0]
    batch_size = max(1, _safe_int(policy.get("batch_size", 8), 8))
    workers = max(1, _safe_int(policy.get("local_workers", 1), 1))
    overhead = max(0.0, _safe_float(policy.get("dispatch_overhead_seconds", 0.002), 0.002) or 0.0)
    if not durations:
        return {
            "work_seconds": 0.0,
            "makespan_seconds": 0.0,
            "throughput": 0.0,
            "tail_latency_ms": 0.0,
            "batches": 0,
            "batch_size": batch_size,
            "workers": workers,
        }

    chunks = [durations[index : index + batch_size] for index in range(0, len(durations), batch_size)]
    times = [_lane_makespan(chunk, min(workers, len(chunk))) for chunk in chunks]
    makespan = sum(times) + overhead * len(chunks)
    total = sum(durations)
    return {
        "work_seconds": round(total, 6),
        "makespan_seconds": round(makespan, 6),
        "throughput": round(total / makespan, 6) if makespan > 0 else 0.0,
        "tail_latency_ms": round(max(times, default=0.0) * 1000.0, 3),
        "batches": len(chunks),
        "batch_size": batch_size,
        "workers": workers,
    }


def ab_benchmark(
    baseline_policy: Mapping[str, Any],
    candidate_policy: Mapping[str, Any],
    workload: Sequence[float],
    limits: Optional[ConstitutionalLimits] = None,
) -> Dict[str, Any]:
    """Compare policies on a replay of real durations; promote only verified wins."""
    limits = limits or CONSTITUTIONAL_LIMITS
    baseline = replay_workload(workload, baseline_policy)
    candidate = replay_workload(workload, candidate_policy)
    report = validate_policy(candidate_policy, limits)
    speedup = (
        baseline["makespan_seconds"] / candidate["makespan_seconds"]
        if candidate["makespan_seconds"] > 0 and baseline["makespan_seconds"] > 0
        else 1.0
    )
    reasons: List[str] = []
    if not report["valid"]:
        reasons.extend(report["violations"])
    if speedup < PROMOTION_MIN_SPEEDUP:
        reasons.append(f"insufficient_speedup: {round(speedup, 4)} < {PROMOTION_MIN_SPEEDUP}")
    if candidate["tail_latency_ms"] > baseline["tail_latency_ms"] * TAIL_REGRESSION_TOLERANCE + 1e-9:
        reasons.append(
            "tail_latency_regression: "
            f"{candidate['tail_latency_ms']}ms > {baseline['tail_latency_ms']}ms"
        )
    decision = "promote" if not reasons else "reject"
    return {
        "decision": decision,
        "speedup": round(speedup, 6),
        "baseline": baseline,
        "candidate": candidate,
        "violations": report["violations"],
        "reasons": reasons,
        "workload_samples": len(workload),
    }


class PolicyStore:
    """Atomic policy persistence, versioned snapshots, promotion, and rollback."""

    def __init__(
        self,
        path: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        limits: Optional[ConstitutionalLimits] = None,
    ) -> None:
        self.path = Path(path) if path is not None else POLICY_PATH
        self.backup_dir = Path(backup_dir) if backup_dir is not None else self.path.parent / "scale_policy_backups"
        self.limits = limits or CONSTITUTIONAL_LIMITS
        self.active: Dict[str, Any] = default_policy(self.limits)
        self.issues: List[str] = []
        self.clamp_notes: List[str] = []

    def load(self) -> Dict[str, Any]:
        payload = _read_json(self.path, None)
        if not isinstance(payload, dict):
            self.active = default_policy(self.limits)
            self.issues = ["policy_missing"] if not self.path.exists() else ["policy_unreadable"]
            self.clamp_notes = []
            return dict(self.active)

        merged = default_policy(self.limits)
        for key in ("strategies", "cloud", "live"):
            if isinstance(payload.get(key), dict):
                merged[key] = dict(payload[key])
        if isinstance(payload.get("history"), list):
            merged["history"] = payload["history"][-HISTORY_LIMIT:]
        for key, value in payload.items():
            if key not in ("strategies", "cloud", "live", "history"):
                merged[key] = value

        report = validate_policy(merged, self.limits)
        clamped, notes = clamp_policy(merged, self.limits)
        for key in ("created_at", "version"):
            if key in payload:
                clamped[key] = payload[key]
        self.active = clamped
        self.issues = report["violations"]
        self.clamp_notes = notes
        return dict(clamped)

    def save(self, policy: Mapping[str, Any], strict: bool = False) -> Dict[str, Any]:
        report = validate_policy(policy, self.limits)
        if strict and not report["valid"]:
            raise ConstitutionalViolation(report["reason"])
        clamped, notes = clamp_policy(policy, self.limits)
        clamped.setdefault("created_at", _utc_now())
        clamped["updated_at"] = _utc_now()
        self.issues = report["violations"]
        self.clamp_notes = notes
        self.active = clamped
        _atomic_write(self.path, json.dumps(clamped, indent=2))
        return dict(clamped)

    def snapshot(self, policy: Optional[Mapping[str, Any]] = None, label: str = "auto") -> Optional[Path]:
        payload = dict(policy or self.active)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        version = _safe_int(payload.get("version", 0), 0)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_label = "".join(ch for ch in label if ch.isalnum() or ch in ("-", "_"))[:32] or "auto"
        path = self.backup_dir / f"policy-v{version:04d}-{stamp}-{safe_label}.json"
        try:
            _atomic_write(path, json.dumps(payload, indent=2))
        except OSError:
            return None
        self._prune()
        return path

    def _prune(self) -> None:
        try:
            backups = sorted(self.backup_dir.glob("policy-v*.json"))
        except OSError:
            return
        for stale in backups[:-MAX_BACKUPS]:
            try:
                stale.unlink()
            except OSError:
                continue

    def promote(
        self,
        candidate: Mapping[str, Any],
        reason: str = "ab_benchmark",
        metrics: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        report = validate_policy(candidate, self.limits)
        if not report["valid"]:
            raise ConstitutionalViolation(report["reason"])
        active = self.load()
        snapshot = self.snapshot(active, label="pre-promote")
        promoted, _notes = clamp_policy(candidate, self.limits)
        promoted["version"] = _safe_int(active.get("version", 0), 0) + 1
        promoted["created_at"] = active.get("created_at") or _utc_now()
        history = list(active.get("history") or [])
        entry: Dict[str, Any] = {
            "event": "promoted",
            "reason": reason,
            "version": promoted["version"],
            "previous_version": active.get("version"),
            "at": _utc_now(),
        }
        if metrics:
            entry["metrics"] = dict(metrics)
        history.append(entry)
        promoted["history"] = history[-HISTORY_LIMIT:]
        stored = self.save(promoted, strict=True)
        return {
            "promoted": True,
            "policy": stored,
            "snapshot": str(snapshot) if snapshot else None,
            "previous_version": active.get("version"),
        }

    def rollback(self, reason: str = "live_regression") -> Dict[str, Any]:
        active = self.load()
        backups = sorted(self.backup_dir.glob("policy-v*.json")) if self.backup_dir.is_dir() else []
        if not backups:
            return {"rolled_back": False, "reason": "no_snapshots", "policy": active}
        candidate = _read_json(backups[-1], None)
        if not isinstance(candidate, dict):
            return {"rolled_back": False, "reason": "snapshot_unreadable", "policy": active}
        report = validate_policy(candidate, self.limits)
        if not report["valid"]:
            return {
                "rolled_back": False,
                "reason": "snapshot_invalid",
                "violations": report["violations"],
                "policy": active,
            }
        restored, _notes = clamp_policy(candidate, self.limits)
        restored_version = _safe_int(restored.get("version", 0), 0)
        restored["version"] = _safe_int(active.get("version", 0), 0) + 1
        restored["created_at"] = active.get("created_at") or _utc_now()
        history = list(active.get("history") or [])
        history.append(
            {
                "event": "rolled_back",
                "reason": reason,
                "restored_version": restored_version,
                "version": restored["version"],
                "at": _utc_now(),
            }
        )
        restored["history"] = history[-HISTORY_LIMIT:]
        stored = self.save(restored, strict=True)
        return {
            "rolled_back": True,
            "reason": reason,
            "restored_version": restored_version,
            "policy": stored,
            "snapshot": str(backups[-1]),
        }

    def observe_live(
        self,
        tail_latency_ms: Optional[float],
        failure_rate: Optional[float] = None,
    ) -> Dict[str, Any]:
        policy = self.load()
        live = dict(policy.get("live") or {})
        samples = list(live.get("samples") or [])
        tail = _safe_float(tail_latency_ms, None)
        fail = _safe_float(failure_rate, None)
        if tail is not None:
            samples.append(
                {
                    "tail_latency_ms": round(max(0.0, tail), 3),
                    "failure_rate": round(_clamp(fail or 0.0, 0.0, 1.0), 6),
                    "at": _utc_now(),
                }
            )
        samples = samples[-32:]
        live["samples"] = samples
        tails = [sample["tail_latency_ms"] for sample in samples]
        fails = [sample["failure_rate"] for sample in samples]
        live["tail_latency_ms"] = percentile(tails, 0.95)
        live["failure_rate"] = round(sum(fails) / len(fails), 6) if fails else 0.0
        if live.get("baseline_tail_latency_ms") is None and len(samples) >= 3:
            live["baseline_tail_latency_ms"] = live["tail_latency_ms"]
            live["baseline_failure_rate"] = live["failure_rate"]

        baseline_tail = _safe_float(live.get("baseline_tail_latency_ms"), None)
        baseline_fail = _safe_float(live.get("baseline_failure_rate"), 0.0) or 0.0
        regressed = False
        reason = ""
        if baseline_tail and live["tail_latency_ms"] and live["tail_latency_ms"] > baseline_tail * LIVE_TAIL_REGRESSION_FACTOR:
            regressed = True
            reason = "tail_latency_regression"
        elif live["failure_rate"] > baseline_fail + LIVE_FAILURE_REGRESSION:
            regressed = True
            reason = "failure_rate_regression"

        policy["live"] = live
        self.save(policy)
        return {
            "regressed": regressed,
            "reason": reason,
            "live": {
                "tail_latency_ms": live["tail_latency_ms"],
                "failure_rate": live["failure_rate"],
                "baseline_tail_latency_ms": live.get("baseline_tail_latency_ms"),
                "baseline_failure_rate": live.get("baseline_failure_rate"),
                "samples": len(samples),
            },
        }

    def set_live_baseline(
        self,
        tail_latency_ms: float,
        failure_rate: float = 0.0,
    ) -> Dict[str, Any]:
        policy = self.load()
        live = dict(policy.get("live") or {})
        live["baseline_tail_latency_ms"] = round(max(0.0, float(tail_latency_ms)), 3)
        live["baseline_failure_rate"] = round(_clamp(float(failure_rate), 0.0, 1.0), 6)
        policy["live"] = live
        self.save(policy)
        return live


# ---------------------------------------------------------------------------
# Orchestrator facade
# ---------------------------------------------------------------------------


class ScaleOrchestrator:
    """Elastic execution scheduler + self-tuning policy controller."""

    def __init__(
        self,
        root: Optional[Path] = None,
        policy_path: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        limits: Optional[ConstitutionalLimits] = None,
        resources: Optional[HardwareProfile] = None,
        rng: Optional[random.Random] = None,
        clock: Callable[[], float] = time.monotonic,
        telemetry_enabled: bool = True,
    ) -> None:
        self.root = Path(root) if root is not None else ROOT
        self.limits = limits or CONSTITUTIONAL_LIMITS
        self._clock = clock
        self.telemetry_enabled = bool(telemetry_enabled)
        self.resources = resources or ResourceProbe(self.limits).probe()
        self.store = PolicyStore(
            path=Path(policy_path) if policy_path is not None else self.root / "scale_policy.json",
            backup_dir=Path(backup_dir) if backup_dir is not None else self.root / "scale_policy_backups",
            limits=self.limits,
        )
        self.policy = self.store.load()
        self.bandit = StrategyBandit(
            exploration_floor=_safe_float(self.policy.get("exploration_floor"), EXPLORATION_FLOOR) or EXPLORATION_FLOOR,
            rng=rng,
            state=self.policy.get("strategies") if isinstance(self.policy.get("strategies"), dict) else None,
        )
        self.calibrator = Calibrator(self.limits)
        self.cloud = CloudShardGovernor(
            self.limits,
            state=self.policy.get("cloud") if isinstance(self.policy.get("cloud"), dict) else None,
            clock=clock,
        )
        self.active_workers = self._safe_worker_count(self.policy)
        self.pool = WinnerPool(self.active_workers, clock=clock)
        self.allocation: Dict[str, int] = self.bandit.allocate(self.active_workers)
        self._events: List[Dict[str, Any]] = []
        self._cycle_id: Optional[int] = None
        self.stats: Dict[str, Any] = {
            "parallel_calls": 0,
            "races": 0,
            "cancelled_tasks": 0,
            "saved_cpu_seconds": 0.0,
            "ceiling_clamps": 0,
        }

    # -- runtime sizing --------------------------------------------------
    def _safe_worker_count(self, policy: Mapping[str, Any]) -> int:
        requested = _safe_int(policy.get("local_workers", 1), 1)
        requested = max(1, min(self.limits.max_local_workers, requested))
        safe = self.resources.safe_local_workers or requested
        return max(1, min(requested, safe))

    def _ram_headroom(self) -> Dict[str, Any]:
        total = self.resources.ram_total_mb
        available = self.resources.ram_available_mb
        budget = self.resources.ram_budget_mb
        if available is None or budget is None:
            return {"available_mb": None, "budget_mb": None, "headroom_mb": None}
        headroom = max(0.0, round(available - budget, 3))
        return {
            "available_mb": round(available, 3),
            "budget_mb": round(budget, 3),
            "headroom_mb": headroom,
            "total_mb": total,
        }

    # -- telemetry -------------------------------------------------------
    def _emit(self, kind: str, payload: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        event = {
            "kind": kind,
            "cycle_id": self._cycle_id,
            "at": _utc_now(),
            **dict(payload or {}),
        }
        self._events.append(event)
        self._events = self._events[-HISTORY_LIMIT:]
        if self.telemetry_enabled:
            try:
                import cycle_telemetry  # type: ignore[import]

                cycle_telemetry.record_scale_event(event, {"cycle_id": self._cycle_id})
            except Exception:
                pass
        return event

    def _persist(self) -> None:
        policy = dict(self.store.load())
        policy["strategies"] = self.bandit.as_dict()
        policy["cloud"] = self.cloud.as_dict()
        policy["exploration_floor"] = self.bandit.exploration_floor
        stored = self.store.save(policy)
        if self.store.clamp_notes:
            self.stats["ceiling_clamps"] += len(self.store.clamp_notes)
            self._emit("ceiling_clamped", {"notes": list(self.store.clamp_notes)[:6]})
        self.policy = stored

    # -- public API ------------------------------------------------------
    def tick(
        self,
        cycle_id: Optional[int] = None,
        telemetry_records: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        self._cycle_id = cycle_id
        self.policy = self.store.load()
        self.resources.ram_available_mb, self.resources.ram_total_mb = _ram_info()[1], self.resources.ram_total_mb
        if self.resources.ram_available_mb is not None:
            self.resources.ram_budget_mb = round(
                self.resources.ram_available_mb * self.limits.max_ram_fraction,
                3,
            )
            self.resources.safe_local_workers = max(
                1,
                min(
                    self.resources.max_local_workers,
                    int(self.resources.ram_budget_mb // DEFAULT_WORKER_RAM_MB) or 1,
                ),
            )

        previous_workers = self.active_workers
        self.active_workers = self._safe_worker_count(self.policy)
        if self.active_workers != previous_workers:
            self.pool.max_workers = self.active_workers
        self.allocation = self.bandit.allocate(self.active_workers)

        calibration = self.calibrator.calibrate(telemetry_records or [])
        if previous_workers > self.active_workers:
            self._emit(
                "scale_down",
                {
                    "from": previous_workers,
                    "to": self.active_workers,
                    "reason": "ram_budget_or_limit",
                },
            )
        self._persist()
        self._emit(
            "scale_tick",
            {
                "active_workers": self.active_workers,
                "max_local_workers": self.limits.max_local_workers,
                "ram_headroom": self._ram_headroom(),
                "strategy_slots": dict(self.allocation),
                "p95_seconds": calibration.get("p95_seconds"),
            },
        )
        return self.snapshot(calibration=calibration)

    def run_race(
        self,
        tasks: Iterable[Any],
        is_winner: Callable[[Any], bool],
        strategy: str = "mutation_race",
        max_seconds: Optional[float] = None,
        cost_hint: float = 0.0,
        cycle_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if cycle_id is not None:
            self._cycle_id = cycle_id
        started = self._clock()
        result = self.pool.run_race(tasks, is_winner, max_seconds=max_seconds, cost_hint=cost_hint)
        self.stats["races"] += 1
        self.stats["cancelled_tasks"] += result.early_stopped
        self.stats["saved_cpu_seconds"] = round(
            self.stats["saved_cpu_seconds"] + result.saved_compute_seconds, 6
        )
        success = result.found_winner
        speedup = 1.0
        if result.found_winner and result.elapsed_seconds > 0:
            speedup = max(1.0, min(64.0, (cost_hint * max(1, len(result.results) + len(result.failures))) / result.elapsed_seconds))
        self.bandit.record(
            strategy,
            success,
            speedup=speedup,
            cost_seconds=max(0.0, self._clock() - started),
            latency_seconds=result.elapsed_seconds,
        )
        if result.early_stopped:
            self._emit(
                "early_cancel",
                {
                    "strategy": strategy,
                    "winner": result.winner_name,
                    "cancelled": result.cancelled,
                    "early_stopped": result.early_stopped,
                    "saved_compute_seconds": result.saved_compute_seconds,
                    "elapsed_seconds": result.elapsed_seconds,
                },
            )
        self._persist()
        return result.as_dict()

    def run_parallel(
        self,
        tasks: Iterable[Any],
        strategy: str = "parallel",
        max_seconds: Optional[float] = None,
        cycle_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if cycle_id is not None:
            self._cycle_id = cycle_id
        started = self._clock()
        result = self.pool.run_parallel(tasks, max_seconds=max_seconds)
        elapsed = max(0.0, self._clock() - started)
        self.stats["parallel_calls"] += 1
        success = not result["errors"]
        self.bandit.record(
            strategy,
            success,
            speedup=1.0,
            cost_seconds=elapsed,
            latency_seconds=result["elapsed_seconds"],
        )
        self._persist()
        return result

    def record_strategy(
        self,
        strategy: str,
        success: bool,
        speedup: float = 1.0,
        cost_seconds: float = 0.0,
    ) -> Dict[str, Any]:
        arm = self.bandit.record(strategy, success, speedup=speedup, cost_seconds=cost_seconds)
        self._persist()
        return arm

    def allocate_slots(self, slots: Optional[int] = None) -> Dict[str, int]:
        return self.bandit.allocate(self.active_workers if slots is None else slots)

    def maybe_self_upgrade(
        self,
        workload: Sequence[float],
        candidate: Optional[Mapping[str, Any]] = None,
        telemetry_records: Optional[Sequence[Mapping[str, Any]]] = None,
        cycle_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if cycle_id is not None:
            self._cycle_id = cycle_id
        active = self.store.load()
        proposed = dict(active)
        if candidate is not None:
            proposed.update(dict(candidate))
        elif telemetry_records:
            calibration = self.calibrator.calibrate(telemetry_records)
            proposed.update(calibration.get("recommendations") or {})
        else:
            return {"decision": "skipped", "reason": "no_candidate", "workload_samples": len(workload)}

        result = ab_benchmark(active, proposed, workload, self.limits)
        if result["decision"] == "promote":
            promotion = self.store.promote(
                proposed,
                reason="ab_benchmark",
                metrics={
                    "speedup": result["speedup"],
                    "baseline_makespan_seconds": result["baseline"]["makespan_seconds"],
                    "candidate_makespan_seconds": result["candidate"]["makespan_seconds"],
                },
            )
            self.policy = promotion["policy"]
            self.bandit.exploration_floor = _clamp(
                max(
                    self.bandit.exploration_floor,
                    _safe_float(self.policy.get("exploration_floor"), EXPLORATION_FLOOR) or EXPLORATION_FLOOR,
                ),
                self.limits.min_exploration_floor,
                1.0,
            )
            self.active_workers = self._safe_worker_count(self.policy)
            self.pool.max_workers = self.active_workers
            self._emit(
                "policy_promoted",
                {
                    "version": self.policy.get("version"),
                    "speedup": result["speedup"],
                    "snapshot": promotion.get("snapshot"),
                },
            )
            return {
                "decision": "promote",
                "speedup": result["speedup"],
                "version": self.policy.get("version"),
                "snapshot": promotion.get("snapshot"),
                "baseline": result["baseline"],
                "candidate": result["candidate"],
            }

        self._emit(
            "policy_rejected",
            {
                "reasons": result["reasons"][:6],
                "speedup": result["speedup"],
            },
        )
        return {
            "decision": "reject",
            "reasons": result["reasons"],
            "speedup": result["speedup"],
            "baseline": result["baseline"],
            "candidate": result["candidate"],
        }

    def observe_live(
        self,
        tail_latency_ms: Optional[float],
        failure_rate: Optional[float] = None,
        cycle_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if cycle_id is not None:
            self._cycle_id = cycle_id
        observation = self.store.observe_live(tail_latency_ms, failure_rate)
        if observation["regressed"]:
            rollback = self.store.rollback(reason=observation["reason"])
            if rollback.get("rolled_back"):
                self.policy = rollback["policy"]
                self.active_workers = self._safe_worker_count(self.policy)
                self.pool.max_workers = self.active_workers
                self._emit(
                    "policy_rolled_back",
                    {
                        "reason": observation["reason"],
                        "restored_version": rollback.get("restored_version"),
                        "version": self.policy.get("version"),
                        "snapshot": rollback.get("snapshot"),
                    },
                )
            observation["rollback"] = rollback
        return observation

    def cloud_request(self, desired: Optional[int] = None, rate_limited: bool = False) -> Dict[str, Any]:
        status = self.cloud.request(desired, rate_limited)
        if rate_limited:
            self._emit("cloud_backoff", dict(status))
        self._persist()
        return status

    # -- snapshots -------------------------------------------------------
    def snapshot(
        self,
        calibration: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        policy = self.store.active or self.policy
        upgrades = [
            event
            for event in self._events
            if event["kind"] in ("policy_promoted", "policy_rejected", "policy_rolled_back")
        ][-8:]
        return {
            "available": True,
            "hardware": self.resources.as_dict(),
            "limits": self.limits.as_dict(),
            "policy": {
                "version": policy.get("version"),
                "batch_size": policy.get("batch_size"),
                "local_workers": policy.get("local_workers"),
                "solver_timeout_seconds": policy.get("solver_timeout_seconds"),
                "ram_fraction": policy.get("ram_fraction"),
                "cloud_shards": policy.get("cloud_shards"),
                "exploration_floor": policy.get("exploration_floor"),
                "verify_tiers": policy.get("verify_tiers"),
                "history": list(policy.get("history") or [])[-6:],
            },
            "active_workers": self.active_workers,
            "headroom": {
                "local_workers": max(0, self.limits.max_local_workers - self.active_workers),
                "ram": self._ram_headroom(),
                "cloud_shards": max(0, self.limits.max_cloud_shards - self.cloud.active_shards),
            },
            "strategy_slots": dict(self.allocation),
            "bandit": self.bandit.weights(),
            "cloud": self.cloud.snapshot(),
            "early_cancel": {
                "events": sum(1 for event in self._events if event["kind"] == "early_cancel"),
                "cancelled_tasks": self.stats["cancelled_tasks"],
                "saved_cpu_seconds": round(self.stats["saved_cpu_seconds"], 4),
            },
            "calibration": dict(calibration or {}),
            "upgrades": upgrades,
            "events": list(self._events[-12:]),
            "stats": dict(self.stats),
        }
