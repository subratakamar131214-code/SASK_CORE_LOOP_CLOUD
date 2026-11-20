"""Real cycle telemetry ledger for the SASK kernel.

This module records *measured* per-cycle and per-step data only:

* wall-clock durations from ``time.monotonic``
* process CPU time from ``time.process_time``
* resident memory from the OS (Win32 ``GetProcessMemoryInfo`` or
  ``/proc/self/status``)

It never fabricates numbers. If a metric cannot be measured it is omitted
(``None``) rather than guessed. The rolling JSON ledger powers the web deck's
reliability panel, bottleneck ranking, and the agent fix queue.

All files written here are local, git-ignored telemetry artifacts.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
LEDGER_PATH = ROOT / "cycle_telemetry.jsonl"
LATEST_PATH = ROOT / "cycle_telemetry.json"
FIX_QUEUE_PATH = ROOT / "sask_agent_fix_queue.json"
TUNING_PATH = ROOT / "sask_tuning.json"

MAX_RECORDS = 120
MAX_FIXES = 200

VERDICT_RECORD_TYPE = "verification_verdict"

_LEDGER_LOCK = threading.RLock()
_FIX_LOCK = threading.RLock()

DISCIPLINE_FLOOR = 0.90
WINDOW_MINUTES = 340.0

MODULE_FILES: Dict[str, List[str]] = {
    "core": ["core.py"],
    "drives": ["drives.py"],
    "telos": ["telos.py", "telos_v2.py"],
    "egraph_engine": ["egraph_engine.py"],
    "meta_prover": ["meta_prover.py"],
    "manifold_bridge": ["manifold_bridge.py"],
    "devil_advocate": ["devil_advocate.py"],
    "mutate": ["mutate.py"],
    "verify": ["verify.py"],
    "prover": ["prover.py"],
    "science_mcp": ["science_mcp.py"],
    "bio_gen": ["bio_gen.py"],
}

DEFAULT_TUNING: Dict[str, Any] = {
    "cycle_delay_seconds": 0.0,
    "stream_max_goals_per_cycle": 32,
    "syntax_cache_enabled": True,
    "ledger_max_records": MAX_RECORDS,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rss_mb() -> Optional[float]:
    """Current resident set size in MB, or None when unmeasurable."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            handle = kernel32.GetCurrentProcess()
            try:
                fn = kernel32.K32GetProcessMemoryInfo
            except AttributeError:
                fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            fn.restype = wintypes.BOOL
            if fn(handle, ctypes.byref(counters), counters.cb):
                return round(counters.WorkingSetSize / (1024 * 1024), 3)
            return None
        status = Path("/proc/self/status")
        if status.is_file():
            for line in status.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    kb = float(line.split()[1])
                    return round(kb / 1024.0, 3)
    except Exception:
        return None
    return None


def _cpu_seconds() -> float:
    try:
        return float(time.process_time())
    except Exception:
        return 0.0


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class CycleRecorder:
    """Measure a running cycle boundary by boundary.

    ``boundary(phase, module)`` closes the segment that started at the previous
    boundary and attributes it to the module/phase that just finished. This
    keeps the instrumentation to a single call per stage, so the kernel's
    control flow and exception handling stay untouched.
    """

    def __init__(self, cycle_id: int, source: Optional[str] = None) -> None:
        env_source = (source or os.environ.get("SASK_RUN_SOURCE") or "").strip().lower()
        if not env_source:
            env_source = (
                "cloud-runner"
                if str(os.environ.get("GITHUB_ACTIONS", "")).lower() == "true"
                else "local"
            )
        self.cycle_id = int(cycle_id)
        self.source = env_source
        self.started_at = _utc_now()
        self._t0 = time.monotonic()
        self._cpu0 = _cpu_seconds()
        self._rss0 = _rss_mb()
        self._last_t = self._t0
        self._last_cpu = self._cpu0
        self._last_rss = self._rss0
        self.steps: List[Dict[str, Any]] = []
        self.peak_rss_mb = self._rss0 or 0.0
        self._closed = False
        self._tuning = load_tuning()

    def boundary(self, phase: str, module: str) -> None:
        if self._closed:
            return
        try:
            now = time.monotonic()
            cpu = _cpu_seconds()
            rss = _rss_mb()
            wall = max(0.0, now - self._last_t)
            cpu_delta = max(0.0, cpu - self._last_cpu)
            step = {
                "phase": phase,
                "module": module,
                "duration_ms": round(wall * 1000, 2),
                "cpu_percent": round(min(999.0, 100.0 * cpu_delta / wall), 2) if wall > 0 else 0.0,
                "rss_mb": rss,
                "rss_delta_mb": (
                    round(rss - self._last_rss, 3)
                    if (rss is not None and self._last_rss is not None)
                    else None
                ),
            }
            if wall >= 0.001:
                self.steps.append(step)
            if rss is not None:
                self.peak_rss_mb = max(self.peak_rss_mb, rss)
            self._last_t = now
            self._last_cpu = cpu
            self._last_rss = rss if rss is not None else self._last_rss
        except Exception:
            return

    def finish(
        self,
        success: bool,
        exit_status: str,
        details: Optional[Dict[str, Any]] = None,
        timer: Any = None,
    ) -> Optional[Dict[str, Any]]:
        if self._closed:
            return None
        self._closed = True
        self.boundary("cycle_end", "core")
        try:
            duration = round(time.monotonic() - self._t0, 4)
            cpu_total = max(0.0, _cpu_seconds() - self._cpu0)
            rss_end = _rss_mb()
            outcomes = _extract_outcomes(details or {})
            elapsed_minutes = None
            remaining_minutes = None
            handoff_ready = False
            if timer is not None:
                try:
                    elapsed_minutes = round(timer.elapsed_minutes, 3)
                    remaining_minutes = round(timer.remaining_minutes, 3)
                    handoff_ready = bool(timer.is_threshold_reached())
                except Exception:
                    pass
            steps = sorted(self.steps, key=lambda item: item.get("duration_ms", 0), reverse=True)
            record: Dict[str, Any] = {
                "cycle_id": self.cycle_id,
                "source": self.source,
                "started_at": self.started_at,
                "completed_at": _utc_now(),
                "duration_seconds": duration,
                "success": bool(success),
                "exit_status": exit_status,
                "handoff_ready": handoff_ready,
                "wall_clock": {
                    "elapsed_minutes": elapsed_minutes,
                    "remaining_minutes": remaining_minutes,
                    "window_minutes": WINDOW_MINUTES,
                },
                "cpu_percent_avg": round(min(999.0, 100.0 * cpu_total / duration), 2) if duration > 0 else 0.0,
                "rss_start_mb": self._rss0,
                "rss_end_mb": rss_end,
                "rss_delta_mb": (
                    round(rss_end - self._rss0, 3)
                    if (rss_end is not None and self._rss0 is not None)
                    else None
                ),
                "rss_peak_mb": round(self.peak_rss_mb, 3) if self.peak_rss_mb else None,
                "steps": steps,
                "outcomes": outcomes,
                "tuning": self._tuning,
            }
            append_record(record)
            return record
        except Exception:
            return None


def _extract_outcomes(details: Dict[str, Any]) -> Dict[str, Any]:
    test = details.get("test_report") or {}
    meta = details.get("meta_prover") or {}
    counterfactual = meta.get("counterfactual") or {}
    devil = details.get("devil_advocate") or {}
    egraph = details.get("egraph") or {}
    bidirectional = details.get("bidirectional") or {}
    passed = int(test.get("passed", 0) or 0)
    failed = int(test.get("failed", 0) or 0)
    total = int(test.get("total", passed + failed) or 0)
    discipline = _safe_float(details.get("drives_discipline"))
    cache_hits = cache_misses = None
    recorded_cache = details.get("syntax_cache")
    if isinstance(recorded_cache, dict) and (
        "hits" in recorded_cache or "misses" in recorded_cache
    ):
        cache_hits = int(recorded_cache.get("hits", 0) or 0)
        cache_misses = int(recorded_cache.get("misses", 0) or 0)
    else:
        try:
            from mutate import CodeMutator

            stats = CodeMutator.syntax_cache_stats()
            cache_hits = int(stats.get("hits", 0) or 0)
            cache_misses = int(stats.get("misses", 0) or 0)
        except Exception:
            pass
    probes = devil.get("probes") or []
    stopped = str(bidirectional.get("stopped") or "").lower()
    return {
        "checks_total": total,
        "checks_passed": passed,
        "checks_failed": failed,
        "pass_rate": round(passed / total, 4) if total else None,
        "verification_failures": failed,
        "rejected_mutations": int(counterfactual.get("rejected", 0) or 0),
        "counterexamples": len(probes),
        "timeouts": 1 if stopped in ("timeout", "max_seconds", "budget", "budget_exhausted") else 0,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cache_hit_ratio": (
            round(cache_hits / (cache_hits + cache_misses), 4)
            if (cache_hits is not None and (cache_hits + cache_misses) > 0)
            else None
        ),
        "egraph_rewrites": egraph.get("rewrites"),
        "egraph_merged_classes": egraph.get("merged_classes"),
        "egraph_goal_matched": egraph.get("goal_matched"),
        "discipline": discipline,
        "discipline_floor": DISCIPLINE_FLOOR,
        "discipline_ok": (discipline is None or discipline >= DISCIPLINE_FLOOR),
        "counterfactual_promotions": int(counterfactual.get("promoted", 0) or 0),
    }


def append_record(record: Dict[str, Any]) -> None:
    with _LEDGER_LOCK:
        try:
            LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
        latest = _read_json(LATEST_PATH, {})
        records = latest.get("records") if isinstance(latest, dict) else None
        if not isinstance(records, list):
            records = []
        records.append(record)
        records = records[-MAX_RECORDS:]
        payload = {
            "generated_at": _utc_now(),
            "source": record.get("source", "local"),
            "record_count": len(records),
            "records": records,
        }
        try:
            _atomic_write(LATEST_PATH, json.dumps(payload, indent=2))
        except OSError:
            pass


def read_records(limit: int = 40) -> List[Dict[str, Any]]:
    payload = _read_json(LATEST_PATH, {})
    records = payload.get("records") if isinstance(payload, dict) else None
    if isinstance(records, list) and records:
        return [
            row
            for row in records[-limit:]
            if isinstance(row, dict) and row.get("record_type") != VERDICT_RECORD_TYPE
        ]
    rows: List[Dict[str, Any]] = []
    try:
        with LEDGER_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("record_type") != VERDICT_RECORD_TYPE:
                    rows.append(item)
    except OSError:
        return []
    return rows[-limit:]


# ---------------------------------------------------------------------------
# Verification verdict events (zero-trust pipeline)
# ---------------------------------------------------------------------------


def record_verification_verdict(
    verdict: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a structured verifier verdict as a ledger event.

    Verdict events share ``cycle_telemetry.jsonl`` with cycle records but are
    never written to the rolling latest file, so cycle analytics stay clean.
    """
    context = context or {}
    event = {
        "record_type": VERDICT_RECORD_TYPE,
        "ts": _utc_now(),
        "passed": bool(verdict.get("passed")),
        "failed_tier": verdict.get("failed_tier"),
        "reason_code": verdict.get("reason_code"),
        "reason": str(verdict.get("reason") or "")[:280],
        "duration_ms": _safe_float(verdict.get("duration_ms")),
        "cached": bool(verdict.get("cached")),
        "speedup_ratio": _safe_float(verdict.get("speedup_ratio")),
        "counterexample": verdict.get("counterexample"),
        "label": context.get("label"),
        "entrypoint": context.get("entrypoint"),
        "cache_key": context.get("cache_key"),
        "files": context.get("files"),
    }
    with _LEDGER_LOCK:
        try:
            LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass


def read_verification_verdicts(limit: int = 40) -> List[Dict[str, Any]]:
    """Return the most recent verifier verdict events from the ledger."""
    events: List[Dict[str, Any]] = []
    try:
        with LEDGER_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("record_type") == VERDICT_RECORD_TYPE:
                    events.append(item)
    except OSError:
        return []
    return events[-limit:]


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------


def load_tuning() -> Dict[str, Any]:
    payload = _read_json(TUNING_PATH, {})
    tuning = dict(DEFAULT_TUNING)
    if isinstance(payload, dict):
        for key in DEFAULT_TUNING:
            if key in payload:
                tuning[key] = payload[key]
    return tuning


def save_tuning(updates: Dict[str, Any]) -> Dict[str, Any]:
    current = load_tuning()
    for key, value in updates.items():
        if key in DEFAULT_TUNING:
            current[key] = value
    _atomic_write(TUNING_PATH, json.dumps(current, indent=2))
    return current


# ---------------------------------------------------------------------------
# Bottleneck derivation
# ---------------------------------------------------------------------------


def _target_files(module: str) -> List[str]:
    files = MODULE_FILES.get(module, [])
    return [name for name in files if (ROOT / name).is_file()]


def _hypothesis(kind: str, module: str, metric_label: str) -> str:
    if kind == "slow-step":
        table = {
            "verify": "The ancestral gate re-runs the full verification suite at overthinking depth every cycle; unchanged checks are recomputed.",
            "prover": "Syntax proving launches one subprocess per Python file each cycle even when the file's mtime and size are unchanged.",
            "mutate": "Workspace-wide AST inspection re-reads and re-parses every Python file on every cycle.",
            "telos": "Goal dispatch waits on the stream thread pool and manifest persistence inside the cycle.",
            "egraph_engine": "Equivalence saturation expands expressions without reusing the previous cycle's fixed point.",
            "meta_prover": "Meta-prover search evaluates fresh candidate frameworks on every run without warm starting.",
            "devil_advocate": "Adversarial probes run unconditionally instead of only when stagnation is detected.",
            "manifold_bridge": "Cross-domain matching recomputes the full structural graph rather than reusing stable edges.",
            "science_mcp": "Router dispatch fans out to every science router each cycle.",
        }
        return table.get(module, f"{metric_label} dominates cycle wall time; add caching or reduce redundant work.")
    if kind == "cpu":
        return "Process CPU time is near saturation; parallel goal workers and subprocess proving compete for the same cores."
    if kind == "memory":
        return "Resident memory grows during the cycle; large in-memory graphs, cached sources, or result lists are retained longer than needed."
    if kind == "failure":
        return "Verification or mutation outcomes failed; a regression or an over-tight guard is blocking the cycle from completing cleanly."
    if kind == "discipline":
        return "Unified discipline fell below the 0.90 floor, which weakens mutation acceptance and proof prioritisation."
    if kind == "egraph-bloat":
        return "E-graph classes or nodes keep growing without pruning, which slows saturation and increases memory."
    if kind == "cache":
        return "Cache hit ratio is low, so repeated syntax or AST work is recomputed instead of reused."
    if kind == "handoff":
        return "The relay workflow left a large gap between runs, so the 340-minute execution window is not continuously covered."
    if kind == "workflow":
        return "The GitHub Actions relay run failed, timed out, or was cancelled before handoff completed."
    if kind == "ledger":
        return "The telemetry ledger is large, so appending and reading it adds avoidable I/O to the loop."
    return "Observed metric is limiting throughput; inspect the target module and add a bounded fix."


def _make_item(
    slug: str,
    kind: str,
    module: str,
    label: str,
    metric_label: str,
    value: Any,
    unit: str,
    severity: float,
    provenance: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    targets = _target_files(module)
    item: Dict[str, Any] = {
        "id": slug,
        "kind": kind,
        "module": module,
        "label": label,
        "metric": {"label": metric_label, "value": value, "unit": unit},
        "severity": round(max(0.05, min(1.0, severity)), 3),
        "provenance": provenance,
        "hypothesis": _hypothesis(kind, module, metric_label),
        "target_files": targets,
        "verification": ["SASK --doctor", "SASK --selftest"],
    }
    if extra:
        item.update(extra)
    item["prompt"] = build_fix_prompt(item)
    return item


def derive_bottlenecks(
    records: List[Dict[str, Any]],
    workflow: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    latest = records[-1] if records else None
    if latest:
        provenance = "REAL LOCAL RUN"
        steps = latest.get("steps") or []
        total_ms = max(1.0, float(latest.get("duration_seconds") or 0.0) * 1000.0)
        for step in steps[:4]:
            duration_ms = float(step.get("duration_ms") or 0.0)
            share = duration_ms / total_ms
            if duration_ms < 200 or share < 0.12:
                continue
            severity = 0.45 + min(0.45, share * 0.9)
            module = str(step.get("module") or "core")
            slug = f"local-{module}-{str(step.get('phase', 'step')).replace(' ', '-')}"
            items.append(
                _make_item(
                    slug,
                    "slow-step",
                    module,
                    f"{step.get('phase')} dominates the cycle",
                    f"{duration_ms / 1000:.2f}s across {share * 100:.0f}% of cycle",
                    round(duration_ms, 1),
                    "ms",
                    severity,
                    provenance,
                    {
                        "share": round(share, 4),
                        "phase": step.get("phase"),
                        "cpu_percent": step.get("cpu_percent"),
                        "rss_delta_mb": step.get("rss_delta_mb"),
                    },
                )
            )
        cpu_avg = float(latest.get("cpu_percent_avg") or 0.0)
        if cpu_avg >= 75.0:
            items.append(
                _make_item(
                    "local-cpu-saturation",
                    "cpu",
                    "core",
                    "Process CPU is approaching saturation",
                    f"{cpu_avg:.0f}% average process CPU",
                    round(cpu_avg, 1),
                    "%",
                    min(0.95, 0.5 + (cpu_avg - 75.0) / 50.0),
                    provenance,
                )
            )
        peak = float(latest.get("rss_peak_mb") or 0.0)
        delta = float(latest.get("rss_delta_mb") or 0.0)
        if peak >= 600 or delta >= 180:
            items.append(
                _make_item(
                    "local-memory-growth",
                    "memory",
                    "core",
                    "Resident memory grows during the cycle",
                    f"peak {peak:.0f} MB · delta {delta:+.0f} MB",
                    round(peak, 1),
                    "MB",
                    min(0.95, 0.45 + max(0.0, peak - 600) / 1200 + max(0.0, delta - 180) / 600),
                    provenance,
                )
            )
        outcomes = latest.get("outcomes") or {}
        failed = int(outcomes.get("verification_failures") or 0)
        if failed > 0 or not latest.get("success", True):
            items.append(
                _make_item(
                    "local-verification-failure",
                    "failure",
                    "verify",
                    "Cycle verification is failing",
                    f"{failed} failed checks · exit {latest.get('exit_status')}",
                    failed,
                    "checks",
                    min(0.98, 0.6 + failed / 20.0),
                    provenance,
                )
            )
        rejected = int(outcomes.get("rejected_mutations") or 0)
        if rejected >= 5:
            items.append(
                _make_item(
                    "local-mutation-rejections",
                    "failure",
                    "mutate",
                    "Counterfactual mutations are being rejected",
                    f"{rejected} rejected candidates",
                    rejected,
                    "candidates",
                    min(0.85, 0.4 + rejected / 60.0),
                    provenance,
                )
            )
        discipline = _safe_float(outcomes.get("discipline"))
        if discipline is not None and discipline < DISCIPLINE_FLOOR:
            items.append(
                _make_item(
                    "local-discipline-floor",
                    "discipline",
                    "drives",
                    "Discipline dropped below the 0.90 floor",
                    f"D = {discipline:.3f} (< 0.90)",
                    round(discipline, 3),
                    "D",
                    min(0.98, 0.7 + (DISCIPLINE_FLOOR - discipline)),
                    provenance,
                )
            )
        if int(outcomes.get("timeouts") or 0):
            items.append(
                _make_item(
                    "local-search-timeout",
                    "failure",
                    "prover",
                    "A bounded search hit its timeout",
                    "bidirectional search exhausted its budget",
                    1,
                    "timeout",
                    0.7,
                    provenance,
                )
            )
        ratio = _safe_float(outcomes.get("cache_hit_ratio"))
        misses = int(outcomes.get("cache_misses") or 0)
        # Only a cold cache on later cycles (same process) is actionable; cycle 1
        # legitimately must fill the cache first.
        if (
            int(latest.get("cycle_id") or 1) > 1
            and ratio is not None
            and misses > 0
            and ratio < 0.25
        ):
            items.append(
                _make_item(
                    "local-cache-cold",
                    "cache",
                    "mutate",
                    "Syntax/AST cache hit ratio is low",
                    f"{ratio * 100:.0f}% hit ratio over {misses} misses",
                    round(ratio, 4),
                    "ratio",
                    0.55,
                    provenance,
                )
            )
        rewrites = int(outcomes.get("egraph_rewrites") or 0)
        if rewrites >= 2000:
            items.append(
                _make_item(
                    "local-egraph-bloat",
                    "egraph-bloat",
                    "egraph_engine",
                    "E-graph saturation is expanding heavily",
                    f"{rewrites} rewrites in one cycle",
                    rewrites,
                    "rewrites",
                    min(0.9, 0.45 + rewrites / 6000.0),
                    provenance,
                )
            )

    if workflow and workflow.get("available"):
        provenance = "REAL CLOUD RUN"
        current = workflow.get("current") or {}
        status = str(current.get("status") or "")
        conclusion = str(current.get("conclusion") or "")
        if conclusion in ("failure", "timed_out", "cancelled") or status in ("failure", "timed_out", "cancelled"):
            items.append(
                _make_item(
                    "cloud-workflow-failure",
                    "workflow",
                    "core",
                    "GitHub Actions relay run is not healthy",
                    f"{status or 'completed'} · {conclusion or 'unknown'}",
                    conclusion or status,
                    "status",
                    0.97,
                    provenance,
                )
            )
        gap = _safe_float(workflow.get("handoff_gap_seconds"))
        if gap is not None and gap > 600:
            items.append(
                _make_item(
                    "cloud-handoff-gap",
                    "handoff",
                    "core",
                    "Handoff gap between cloud runs is large",
                    f"{gap / 60.0:.1f} min between runs",
                    round(gap, 1),
                    "s",
                    min(0.9, 0.45 + gap / 7200.0),
                    provenance,
                )
            )
        elapsed = _safe_float(current.get("elapsed_seconds"))
        if elapsed is not None and elapsed > WINDOW_MINUTES * 60 * 0.9:
            items.append(
                _make_item(
                    "cloud-window-pressure",
                    "handoff",
                    "core",
                    "Cloud run is close to the 340-minute window",
                    f"{elapsed / 60:.1f} of {WINDOW_MINUTES:.0f} minutes used",
                    round(elapsed, 1),
                    "s",
                    min(0.95, 0.6 + elapsed / (WINDOW_MINUTES * 60 * 2)),
                    provenance,
                )
            )

    deduped: Dict[str, Dict[str, Any]] = {}
    for item in items:
        deduped[item["id"]] = item
    return sorted(deduped.values(), key=lambda item: item["severity"], reverse=True)


def build_fix_prompt(item: Dict[str, Any]) -> str:
    metric = item.get("metric") or {}
    targets = item.get("target_files") or []
    target_text = "\n".join(f"- {name}" for name in targets) or "- (locate the owning module from the metric below)"
    verification = "\n".join(f"- {cmd}" for cmd in item.get("verification") or ["SASK --selftest"])
    return (
        "SASK AGENT FIX DIRECTIVE\n"
        f"ID: {item.get('id')}\n"
        f"Provenance: {item.get('provenance')} | severity {item.get('severity')}\n"
        f"Module: {item.get('module')}\n"
        f"Observed: {metric.get('label')} = {metric.get('value')} {metric.get('unit')}\n"
        f"Kind: {item.get('kind')}\n"
        f"Root-cause hypothesis: {item.get('hypothesis')}\n"
        "Target files:\n"
        f"{target_text}\n"
        "Required changes:\n"
        "1. Read the target module and confirm the hypothesis against the real code path.\n"
        "2. Implement the smallest bounded fix (cache unchanged work, tighten a guard, bound a loop, or correct handoff/config).\n"
        "3. Do not modify git remotes or workflow files; do not stage, commit, or push telemetry/UI artifacts.\n"
        "4. Record the change and the measured effect in the fix queue entry.\n"
        "Verification (run locally, in order):\n"
        f"{verification}\n"
        "- Restart the deck and confirm /api/health reports state_unchanged: true.\n"
        "Acceptance: the observed metric improves or the failure is cleared, with verification green.\n"
    )


# ---------------------------------------------------------------------------
# Fix queue + safe remediation
# ---------------------------------------------------------------------------


def load_fix_queue() -> Dict[str, Any]:
    payload = _read_json(FIX_QUEUE_PATH, {})
    if not isinstance(payload, dict):
        payload = {}
    items = payload.get("items")
    if not isinstance(items, list):
        items = []
    return {"generated_at": payload.get("generated_at"), "items": items}


def queue_fixes(items: List[Dict[str, Any]], source: str = "ui") -> Dict[str, Any]:
    with _FIX_LOCK:
        payload = load_fix_queue()
        existing = {str(entry.get("id")): entry for entry in payload["items"] if isinstance(entry, dict)}
        queued: List[Dict[str, Any]] = []
        now = _utc_now()
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            slug = str(item["id"])
            current = existing.get(slug, {})
            entry = dict(item)
            entry["status"] = current.get("status") if current.get("status") in ("applied", "resolved") else "queued"
            entry["queued_at"] = current.get("queued_at") or now
            entry["updated_at"] = now
            entry["source"] = source
            entry.setdefault("prompt", build_fix_prompt(entry))
            existing[slug] = entry
            queued.append(entry)
        merged = sorted(existing.values(), key=lambda row: str(row.get("updated_at", "")), reverse=True)
        payload = {"generated_at": now, "items": merged[:MAX_FIXES]}
        _atomic_write(FIX_QUEUE_PATH, json.dumps(payload, indent=2))
        return {"queued": len(queued), "items": queued, "path": str(FIX_QUEUE_PATH.name)}


def mark_fix(item_id: str, status: str, note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with _FIX_LOCK:
        payload = load_fix_queue()
        for entry in payload["items"]:
            if str(entry.get("id")) != str(item_id):
                continue
            entry["status"] = status
            entry["updated_at"] = _utc_now()
            if note:
                entry["resolution_note"] = note
            _atomic_write(FIX_QUEUE_PATH, json.dumps(payload, indent=2))
            return entry
    return None


def apply_safe_remediation(item: Dict[str, Any], records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Apply a deterministic, local-only remediation when one exists.

    Only git-ignored tuning or ledger compaction is touched. Code-level fixes
    return ``applied=False`` with the directive the workspace agent must run.
    """
    kind = str(item.get("kind") or "")
    module = str(item.get("module") or "")
    changes: Dict[str, Any] = {}

    if kind in ("slow-step", "cpu", "cache") and module in ("prover", "mutate", "core"):
        changes["syntax_cache_enabled"] = True
        note = "Enabled the mtime/size syntax cache so unchanged files are not re-proved every cycle."
    elif kind in ("slow-step", "cpu") and module == "telos":
        current = int(load_tuning().get("stream_max_goals_per_cycle", 32) or 32)
        changes["stream_max_goals_per_cycle"] = max(8, current // 2)
        note = f"Reduced the per-cycle goal budget to {changes['stream_max_goals_per_cycle']} to shorten dispatch waits."
    elif kind in ("memory", "egraph-bloat"):
        current = int(load_tuning().get("stream_max_goals_per_cycle", 32) or 32)
        changes["stream_max_goals_per_cycle"] = max(4, current // 2)
        changes["cycle_delay_seconds"] = max(0.1, float(load_tuning().get("cycle_delay_seconds", 0.0) or 0.0))
        note = "Reduced the goal budget and added a small inter-cycle delay to relieve memory pressure."
    elif kind == "ledger":
        compact_ledger()
        note = "Compacted the telemetry ledger to the retained window."
    else:
        return {
            "id": item.get("id"),
            "module": module,
            "kind": kind,
            "applied": False,
            "status": "awaiting_agent",
            "reason": "This issue needs a code or workflow change, which the local deck does not apply blindly.",
            "prompt": item.get("prompt") or build_fix_prompt(item),
            "verification": item.get("verification") or ["SASK --doctor", "SASK --selftest"],
        }

    if kind == "ledger":
        tuning = load_tuning()
    else:
        tuning = load_tuning()
        effective = {key: value for key, value in changes.items() if tuning.get(key) != value}
        if not effective:
            entry = mark_fix(str(item.get("id")), "resolved", "Safe tuning was already in effect.")
            return {
                "id": item.get("id"),
                "module": module,
                "kind": kind,
                "applied": False,
                "status": "already_satisfied",
                "reason": "The safe tuning for this issue is already in effect.",
                "changes": changes,
                "tuning": tuning,
                "item": entry,
                "verification": item.get("verification") or ["SASK --doctor", "SASK --selftest"],
            }
        changes = effective
        tuning = save_tuning(changes)
    entry = mark_fix(str(item.get("id")), "applied", note)
    return {
        "id": item.get("id"),
        "module": module,
        "kind": kind,
        "applied": True,
        "status": "applied",
        "changes": changes,
        "tuning": tuning,
        "note": note,
        "item": entry,
        "verification": item.get("verification") or ["SASK --doctor", "SASK --selftest"],
    }


def compact_ledger() -> int:
    with _LEDGER_LOCK:
        if not LEDGER_PATH.is_file():
            return 0
        try:
            lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        keep = lines[-MAX_RECORDS:]
        try:
            _atomic_write(LEDGER_PATH, "\n".join(keep) + ("\n" if keep else ""))
        except OSError:
            return 0
        return max(0, len(lines) - len(keep))
