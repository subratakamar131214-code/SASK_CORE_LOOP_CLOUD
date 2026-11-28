"""SASK Local Command Deck Server.

A strictly local, read-only telemetry server for the SASK kernel.

Design rules
------------
* Kernel modules and their state files are only ever *read* here.
* In-memory e-graph / manifold / prover computations are ephemeral.
* The only files this module writes are local UX/reliability artifacts:
  ``ui_telemetry.json``, ``ui_evolver_log.json``, ``ui_backups/``,
  ``ui/evolved.css``, ``sask_agent_fix_queue.json`` and ``sask_tuning.json``
  (all git-ignored). Kernel sources are never modified.
* Sandbox games are client-side only. This server has no endpoint that
  accepts game state, so a game can never reach kernel state.
* The GitHub bridge reads workflow runs with an optional local token. The
  token is never returned, logged, or rendered; any URL containing
  credentials is stripped before it reaches a response.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # Real cycle ledger + fix queue (must never block the deck)
    import cycle_telemetry
except Exception:  # pragma: no cover - defensive
    cycle_telemetry = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
UX_TELEMETRY_PATH = ROOT / "ui_telemetry.json"
UX_LOG_PATH = ROOT / "ui_evolver_log.json"
BACKUP_DIR = ROOT / "ui_backups"

HOST = "127.0.0.1"
DEFAULT_PORT = 8420

logger = logging.getLogger("sask.command_center")

try:  # FastAPI is optional so the CLI can still print snapshots without it.
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    FASTAPI_AVAILABLE = True
except Exception:  # pragma: no cover - depends on local install
    FastAPI = WebSocket = WebSocketDisconnect = None  # type: ignore[assignment]
    FileResponse = JSONResponse = StaticFiles = None  # type: ignore[assignment]
    FASTAPI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Read-only kernel fingerprint (isolation proof)
# ---------------------------------------------------------------------------

_KERNEL_FILES: Tuple[str, ...] = (
    "core.py",
    "drives.py",
    "telos_v2.py",
    "egraph_engine.py",
    "meta_prover.py",
    "manifold_bridge.py",
    "devil_advocate.py",
    "prover.py",
    "mutate.py",
    "science_mcp.py",
    "verify.py",
)

_STATE_FILES: Tuple[str, ...] = (
    ".sask_egraph.json",
    ".sask_meta_prover.json",
    ".sask_manifold.json",
    ".sask_devil_advocate.json",
    ".sask_curiosity.json",
    ".sask_goal_ledger.json",
    ".sask_goal_stream.json",
    ".sask_ego_ratchet.json",
    "execution_state.json",
)


def kernel_fingerprint() -> str:
    """Hash kernel sources + persisted kernel state (read-only)."""
    digest = hashlib.blake2b(digest_size=12)
    for name in _KERNEL_FILES + _STATE_FILES:
        path = ROOT / name
        if not path.is_file():
            continue
        digest.update(name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


_SERVER_START_FINGERPRINT = kernel_fingerprint()
_SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _safe_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Engine singletons (constructed read-only; never asked to save)
# ---------------------------------------------------------------------------

_DRIVE_LOCK = threading.Lock()
_DRIVE_ENGINE: Any = None
_MANIFOLD_LOCK = threading.Lock()
_MANIFOLD_BRIDGE: Any = None
_PROVER_CACHE: Any = None
_PROVER_CACHE_LOCK = threading.Lock()

_VISUAL_CACHE_LOCK = threading.Lock()
_VISUAL_CACHE: Dict[str, Tuple[float, Any]] = {}
_VISUAL_KEY_LOCKS_GUARD = threading.Lock()
_VISUAL_KEY_LOCKS: Dict[str, threading.Lock] = {}


def _key_lock(key: str) -> threading.Lock:
    with _VISUAL_KEY_LOCKS_GUARD:
        lock = _VISUAL_KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _VISUAL_KEY_LOCKS[key] = lock
        return lock


def _cached(key: str, ttl: float, factory) -> Any:
    """Single-flight TTL cache: concurrent callers share one computation."""
    now = time.monotonic()
    with _VISUAL_CACHE_LOCK:
        hit = _VISUAL_CACHE.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    with _key_lock(key):
        now = time.monotonic()
        with _VISUAL_CACHE_LOCK:
            hit = _VISUAL_CACHE.get(key)
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
        value = factory()
        with _VISUAL_CACHE_LOCK:
            _VISUAL_CACHE[key] = (now, value)
        return value


def _invalidate_cache(key: str) -> None:
    with _VISUAL_CACHE_LOCK:
        _VISUAL_CACHE.pop(key, None)


def _drives_engine():
    global _DRIVE_ENGINE
    if _DRIVE_ENGINE is None:
        with _DRIVE_LOCK:
            if _DRIVE_ENGINE is None:
                from drives import UnifiedDisciplineEngine

                _DRIVE_ENGINE = UnifiedDisciplineEngine()
    return _DRIVE_ENGINE


def _manifold_bridge():
    global _MANIFOLD_BRIDGE
    if _MANIFOLD_BRIDGE is None:
        with _MANIFOLD_LOCK:
            if _MANIFOLD_BRIDGE is None:
                from manifold_bridge import AnalogicalManifoldBridge

                _MANIFOLD_BRIDGE = AnalogicalManifoldBridge(
                    state_path=ROOT / ".sask_manifold.json"
                )
    return _MANIFOLD_BRIDGE


# ---------------------------------------------------------------------------
# Snapshot builders
# ---------------------------------------------------------------------------


def _drives_snapshot(execution: Dict[str, Any]) -> Dict[str, Any]:
    engine = _drives_engine()
    metrics = engine.get_telemetry()
    saved = execution.get("drives_telemetry") or {}
    unified = float(saved.get("unified_discipline", metrics.unified_discipline))
    mutation = float(
        saved.get("mutation_pressure", engine.get_mutation_pressure())
    )
    depth = int(saved.get("overthinking_depth", engine.get_overthinking_depth()))
    return {
        "discipline": round(metrics.discipline, 4),
        "obsession": round(metrics.obsession, 4),
        "anger": round(metrics.anger, 4),
        "madness": round(metrics.madness, 4),
        "motivation": round(metrics.motivation, 4),
        "overthinking": round(metrics.overthinking, 4),
        "unified_discipline": round(unified, 4),
        "floor": round(metrics.floor, 4),
        "mutation_pressure": round(mutation, 4),
        "overthinking_depth": depth,
    }


def _build_egraph_visual(mastery: float, discipline: float) -> Dict[str, Any]:
    from egraph_engine import CostModel, EGraph, default_rules, parse_expression, render

    a = round(1.0 + float(mastery), 4)
    b = round(1.5 + float(discipline), 4)
    c = 3.0
    texts = [
        f"{a} + 0",
        f"0 + {a}",
        f"{a} * 1",
        f"1 * {a}",
        f"({a} + {b}) + {c}",
        f"{a} + ({b} + {c})",
        f"{a} - {a}",
        f"{a} ** 2",
        f"{a} * ({b} + {c})",
        f"{a} * {b} + {a} * {c}",
    ]
    graph = EGraph()
    roots = {text: graph.add_expression(parse_expression(text)) for text in texts}
    stats = graph.saturate(
        default_rules(),
        max_iterations=8,
        node_budget=1800,
        match_budget=1800,
        time_budget=1.0,
    )
    graph.rebuild()
    model = CostModel()
    classes = graph.class_index()

    by_class: Dict[int, List[str]] = {}
    for text, root in roots.items():
        by_class.setdefault(graph.find(root), []).append(text)

    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    for cid in sorted(classes):
        enodes = classes[cid]
        best = None
        try:
            best = graph.extract_best(cid, model)[0]
        except Exception:  # pragma: no cover - defensive
            best = None
        label = render(best) if best is not None else (enodes[0].op if enodes else "?")
        nodes.append(
            {
                "id": f"c{cid}",
                "label": label[:30],
                "size": len(enodes),
                "members": by_class.get(cid, []),
                "ops": sorted({node.op for node in enodes})[:6],
            }
        )
        for node in enodes:
            for child in node.children:
                links.append(
                    {
                        "source": f"c{cid}",
                        "target": f"c{graph.find(child)}",
                        "op": node.op,
                    }
                )

    merged = [
        {"class": f"c{cid}", "members": members}
        for cid, members in by_class.items()
        if len(members) > 1
    ]
    goal_root = roots.get(f"{a} * 1")
    goal_expr = None
    if goal_root is not None:
        try:
            goal_expr = render(graph.extract_best(goal_root, model)[0])
        except Exception:  # pragma: no cover - defensive
            goal_expr = None

    return {
        "available": True,
        "classes": len(classes),
        "nodes": len(nodes),
        "merged_classes": len(merged),
        "merged": merged[:4],
        "goal_expr": goal_expr,
        "saturation": {
            "iterations": stats.get("iterations", 0),
            "rewrites": stats.get("rewrites", 0),
            "matches": stats.get("matches", 0),
            "elapsed_seconds": stats.get("elapsed_seconds", 0.0),
            "stopped": stats.get("stopped", "fixed_point"),
        },
        "graph": {"nodes": nodes, "links": links},
    }


_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE
)


def _pipeline_graph() -> Tuple[List[str], List[Tuple[str, str]]]:
    modules = sorted(path.stem for path in ROOT.glob("*.py"))
    module_set = set(modules)
    edges: set = set()
    for path in ROOT.glob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _IMPORT_PATTERN.finditer(text):
            target = match.group(1)
            if target in module_set and target != path.stem:
                edges.add((path.stem, target))
    return modules, sorted(edges)


def _prover_snapshot() -> Dict[str, Any]:
    from prover import BidirectionalSearchEngine, SearchBudget, SearchExpansionCache

    global _PROVER_CACHE
    with _PROVER_CACHE_LOCK:
        if _PROVER_CACHE is None:
            _PROVER_CACHE = SearchExpansionCache(capacity=1024)

    modules, edges = _pipeline_graph()
    module_set = set(modules)
    forward: Dict[str, List[str]] = {}
    backward: Dict[str, List[str]] = {}
    for source, target in edges:
        forward.setdefault(source, []).append(target)
        backward.setdefault(target, []).append(source)

    roots = [name for name in ("core", "verify") if name in module_set] or modules[:1]
    targets = [
        name
        for name in ("verify", "prover", "science_mcp")
        if name in module_set and name not in roots
    ]
    if not roots or not targets:
        return {"available": True, "solved": False, "reason": "graph_incomplete", "modules": len(modules)}

    engine = BidirectionalSearchEngine(
        forward_successors=lambda state: forward.get(state, []),
        backward_predecessors=lambda state: backward.get(state, []),
        precheck=lambda state: state in module_set,
        verify=lambda state: state in module_set,
        ranker=lambda state: 0.0 if state in targets else 1.0,
        budget=SearchBudget(
            max_nodes_per_side=64,
            max_depth=8,
            max_seconds=0.8,
            beam_width=48,
            parallel=False,
        ),
        cache=_PROVER_CACHE,
        tag="command-center-pipeline",
    )
    payload = engine.search(roots, targets).to_dict()
    payload.update({"available": True, "modules": len(modules), "edges": len(edges)})
    return payload


def _manifold_snapshot() -> Dict[str, Any]:
    from manifold_bridge import BOUNDARY_DIMENSIONS

    bridge = _manifold_bridge()
    telemetry = bridge.get_telemetry()
    matches: List[Dict[str, Any]] = []
    try:
        found = bridge.find_isomorphisms(cross_domain_only=True)
        found.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)
        for match in found[:5]:
            matches.append(
                {
                    key: match.get(key)
                    for key in (
                        "source",
                        "target",
                        "source_domain",
                        "target_domain",
                        "structural_isomorphism",
                        "similarity",
                        "confidence",
                    )
                }
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("isomorphism snapshot failed: %s", exc)

    probes: List[Dict[str, Any]] = []
    try:
        probes = [probe.to_dict() for probe in bridge.boundary_probes(limit=4)]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("boundary probe snapshot failed: %s", exc)

    return {
        "available": True,
        "structures": telemetry.get("structure_count", 0),
        "domains": telemetry.get("domains", []),
        "recent_structures": telemetry.get("structures", []),
        "comparisons": telemetry.get("comparisons", 0),
        "isomorphisms": telemetry.get("isomorphisms_found", 0),
        "transfers": telemetry.get("transfers", 0),
        "matches": matches,
        "probes": probes,
        "dimensions": list(BOUNDARY_DIMENSIONS),
    }


def _meta_snapshot(meta_state: Dict[str, Any]) -> Dict[str, Any]:
    best = None
    best_by_goal = meta_state.get("best_by_goal") or {}
    if isinstance(best_by_goal, dict) and best_by_goal:
        try:
            latest = max(
                best_by_goal.values(),
                key=lambda item: str(item.get("timestamp", "")),
            )
            best = {
                key: latest.get(key)
                for key in (
                    "goal",
                    "best_framework_name",
                    "best_score",
                    "best_success",
                    "best_steps",
                    "best_compactness",
                )
            }
        except Exception:  # pragma: no cover - defensive
            best = None
    return {
        "available": True,
        "evaluations": meta_state.get("evaluations", 0),
        "searches": meta_state.get("searches", 0),
        "counterfactual_runs": meta_state.get("counterfactual_runs", 0),
        "promotions": len(meta_state.get("promotions") or []),
        "best": best,
    }


def _devil_snapshot(devil_state: Dict[str, Any]) -> Dict[str, Any]:
    history = devil_state.get("history") or {}
    spark = []
    if isinstance(history, dict):
        series = history.get("system_mastery") or history.get("success_rate") or []
        if isinstance(series, list):
            spark = [float(value) for value in series[-24:] if isinstance(value, (int, float))]
    return {
        "available": True,
        "cycles_observed": devil_state.get("cycles_observed", 0),
        "challenges": devil_state.get("challenges", 0),
        "stagnation_events": devil_state.get("stagnation_events", 0),
        "local_convergence_events": devil_state.get("local_convergence_events", 0),
        "last_intensity": devil_state.get("last_intensity", 0.0),
        "system_mastery_series": spark,
    }


def _curiosity_snapshot(curiosity_state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": True,
        "observations": curiosity_state.get("observations", 0),
        "generated": curiosity_state.get("generated", 0),
        "proposed": curiosity_state.get("proposed", 0),
        "ticks": curiosity_state.get("ticks", 0),
        "last_reward": curiosity_state.get("last_reward", 0.0),
        "mean_surprise": round(
            float(curiosity_state.get("total_surprise", 0.0))
            / max(1, int(curiosity_state.get("observations", 0))),
            6,
        ),
    }


def _telos_snapshot(ledger: Dict[str, Any], stream: Dict[str, Any]) -> Dict[str, Any]:
    stats = stream.get("stats") or {}
    return {
        "available": True,
        "ledger_entries": int(ledger.get("count", 0) or 0),
        "objectives": len(stream.get("objectives") or {}),
        "pending": len(stream.get("pending") or []),
        "in_flight": len(stream.get("in_flight") or []),
        "produced": int(stats.get("produced", 0) or 0),
        "completed": int(stats.get("completed", 0) or 0),
        "failed": int(stats.get("failed", 0) or 0),
        "duplicates_suppressed": int(stats.get("duplicates_suppressed", 0) or 0),
    }


def _science_snapshot() -> Dict[str, Any]:
    try:
        from science_mcp import get_science_telemetry

        telemetry = get_science_telemetry()
    except Exception as exc:  # pragma: no cover - optional dependency
        return {"available": False, "error": f"{type(exc).__name__}: {exc}", "routers": []}
    return {
        "available": bool(telemetry.get("available", True)),
        "fastmcp_available": bool(telemetry.get("fastmcp_available", False)),
        "router_count": telemetry.get("router_count", 0),
        "routers": telemetry.get("routers", []),
        "boundary_dimensions": telemetry.get("boundary_dimensions", []),
        "adapters": telemetry.get("adapters", []),
    }


def _mutation_snapshot() -> Dict[str, Any]:
    try:
        from mutate import CodeMutator

        stats = CodeMutator.syntax_cache_stats()
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "cache_hits": stats.get("hits", 0),
        "cache_misses": stats.get("misses", 0),
        "cache_entries": stats.get("entries", 0),
        "cache_capacity": stats.get("capacity", 0),
    }


def _ux_summary() -> Dict[str, Any]:
    log = _safe_json(UX_LOG_PATH, {})
    entries = log.get("entries", []) if isinstance(log, dict) else []
    latest = entries[-1] if entries else None
    return {
        "evolutions": len(entries),
        "active_version": log.get("active_version") if isinstance(log, dict) else None,
        "last": (
            {
                key: latest.get(key)
                for key in ("version", "timestamp", "type", "summary", "promoted")
            }
            if isinstance(latest, dict)
            else None
        ),
    }


def _execution_summary(execution: Dict[str, Any]) -> Dict[str, Any]:
    cycles = execution.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    window = execution.get("wall_clock") or {}
    return {
        "state": execution.get("state", "UNKNOWN"),
        "cycles": execution.get("total_cycles_executed", len(cycles)),
        "system_mastery": execution.get("system_mastery", 0.0),
        "remaining_minutes": window.get("remaining_minutes", 0.0),
        "window_minutes": window.get("window_minutes", 0.0),
        "handoff_ready": bool(execution.get("container_handoff", False)),
        "last_cycle": {
            key: last_cycle.get(key)
            for key in ("cycle", "system_mastery", "error_count", "state")
            if key in last_cycle
        },
        "last_details": (last_cycle.get("details") or {}) if isinstance(last_cycle, dict) else {},
    }


_SERIES: deque = deque(maxlen=90)
_SERIES_LOCK = threading.Lock()
_LAST_COUNTERS: Dict[str, float] = {}
_LAST_TICK = time.monotonic()


def _record_series(snapshot: Dict[str, Any]) -> None:
    global _LAST_TICK
    now = time.monotonic()
    dt = max(0.25, now - _LAST_TICK)
    _LAST_TICK = now
    egraph = snapshot.get("egraph") or {}
    prover = snapshot.get("prover") or {}
    mutation = snapshot.get("mutate") or {}
    verification = snapshot.get("verification") or {}
    meta = snapshot.get("meta") or {}
    manifold = snapshot.get("manifold") or {}

    counters = {
        "rewrites": float((egraph.get("saturation") or {}).get("rewrites", 0)),
        "searches": float(meta.get("searches", 0)),
        "transfers": float(manifold.get("transfers", 0)),
    }
    rates = {}
    for key, value in counters.items():
        previous = _LAST_COUNTERS.get(key, value)
        rates[key] = max(0.0, (value - previous) / dt)
        _LAST_COUNTERS[key] = value

    hits = float(mutation.get("cache_hits", 0))
    misses = float(mutation.get("cache_misses", 0))
    cache_ratio = hits / (hits + misses) if (hits + misses) else 0.0
    passed = float(verification.get("high_watermark_passed", 0))
    total = float(verification.get("high_watermark_total", 0))
    verify_ratio = passed / total if total else 0.0
    velocity = 0.0
    if prover.get("solved"):
        elapsed = max(float(prover.get("elapsed_seconds", 0.0)), 1e-3)
        steps = max(1, int(prover.get("depth", 1)))
        velocity = round(steps / elapsed, 3)

    with _SERIES_LOCK:
        _SERIES.append(
            {
                "t": round(time.time(), 3),
                "rewrite_rate": round(rates["rewrites"], 3),
                "search_rate": round(rates["searches"], 3),
                "transfer_rate": round(rates["transfers"], 3),
                "proof_velocity": velocity,
                "cache_ratio": round(cache_ratio, 4),
                "verify_ratio": round(verify_ratio, 4),
            }
        )


def _series_payload() -> Dict[str, List[Any]]:
    with _SERIES_LOCK:
        rows = list(_SERIES)
    return {
        "timestamps": [row["t"] for row in rows],
        "rewrite_rate": [row["rewrite_rate"] for row in rows],
        "search_rate": [row["search_rate"] for row in rows],
        "transfer_rate": [row["transfer_rate"] for row in rows],
        "proof_velocity": [row["proof_velocity"] for row in rows],
        "cache_ratio": [row["cache_ratio"] for row in rows],
        "verify_ratio": [row["verify_ratio"] for row in rows],
    }


# ---------------------------------------------------------------------------
# GitHub Actions bridge (real cloud runs, credential-safe)
# ---------------------------------------------------------------------------

_GITHUB_API = "https://api.github.com"
_GITHUB_TTL = 45.0
_WINDOW_MINUTES = 340.0
_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "sask-local-command-deck",
    "X-GitHub-Api-Version": "2022-11-28",
}
_CREDENTIAL_PATTERN = re.compile(r"//[^/@\s]+@")
_SLUG_PATTERN = re.compile(
    r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)
_NO_DATA = "NO DATA YET (WAITING FOR CYCLE)"


def _strip_credentials(text: str) -> str:
    """Remove any ``user:token@`` userinfo from a URL before display."""
    return _CREDENTIAL_PATTERN.sub("//", text or "")


def _git_origin_url() -> Optional[str]:
    """Read ``remote.origin.url`` directly from .git/config (no subprocess)."""
    try:
        text = (ROOT / ".git" / "config").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(
        r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text, re.DOTALL
    )
    return match.group(1) if match else None


def _repo_slug(remote: Optional[str]) -> Optional[str]:
    if not remote:
        return None
    match = _SLUG_PATTERN.search(remote)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


def _github_token(remote: Optional[str]) -> Optional[str]:
    """Local token from env, .env, or embedded in the remote; never exposed."""
    for key in ("GITHUB_TOKEN", "RELAY_PAT", "GH_TOKEN"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip().removeprefix("export ").strip() in (
                "GITHUB_TOKEN",
                "RELAY_PAT",
                "GH_TOKEN",
            ):
                cleaned = value.strip().strip("'\"")
                if cleaned:
                    return cleaned
    except OSError:
        pass
    match = re.search(r"//(?:[^:@/\s]+:)?([^@/\s]+)@", remote or "")
    return match.group(1) if match else None


def _github_get(path: str, token: Optional[str]) -> Dict[str, Any]:
    """GET a GitHub REST path; returns ``{ok, data|error}`` and never raises."""
    request = urllib.request.Request(_GITHUB_API + path, headers=dict(_GITHUB_HEADERS))
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            return {"ok": True, "data": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_between(start: Any, end: Any) -> Optional[float]:
    began = _parse_iso(start)
    finished = _parse_iso(end)
    if began is None or finished is None:
        return None
    return round(max(0.0, (finished - began).total_seconds()), 1)


def _build_workflow_snapshot() -> Dict[str, Any]:
    """Query the relay workflow and its recent runs (TTL-cached upstream)."""
    remote = _git_origin_url()
    slug = _repo_slug(remote)
    token = _github_token(remote)
    now = datetime.now(timezone.utc)
    snapshot: Dict[str, Any] = {
        "available": False,
        "provenance": _NO_DATA,
        "repo": slug,
        "window_minutes": _WINDOW_MINUTES,
        "token_present": bool(token),
        "workflow_path": None,
        "current": None,
        "recent": [],
        "jobs": [],
        "handoff_gap_seconds": None,
        "generated_at": _utc_now(),
    }
    if not slug:
        snapshot["reason"] = "no GitHub remote configured"
        return snapshot

    owner, repo = slug.split("/", 1)
    workflow_path: Optional[str] = None
    workflows = _github_get(f"/repos/{owner}/{repo}/actions/workflows?per_page=30", token)
    if workflows.get("ok"):
        for workflow in workflows["data"].get("workflows") or []:
            path = str(workflow.get("path") or "")
            name = str(workflow.get("name") or "")
            if "immortal_relay" in path.lower() or "relay" in name.lower():
                workflow_path = path
                break

    runs_response: Dict[str, Any] = {"ok": False, "error": "unavailable"}
    if workflow_path:
        quoted = urllib.parse.quote(workflow_path, safe="")
        runs_response = _github_get(
            f"/repos/{owner}/{repo}/actions/workflows/{quoted}/runs?per_page=6", token
        )
        snapshot["workflow_path"] = workflow_path
    if not runs_response.get("ok"):
        runs_response = _github_get(
            f"/repos/{owner}/{repo}/actions/runs?per_page=6", token
        )
    if not runs_response.get("ok"):
        snapshot["reason"] = runs_response.get("error", "unavailable")
        return snapshot

    runs = runs_response["data"].get("workflow_runs") or []
    if not runs:
        snapshot["available"] = True
        snapshot["provenance"] = "REAL CLOUD RUN"
        snapshot["reason"] = "workflow has no runs yet"
        return snapshot

    def run_view(run: Dict[str, Any]) -> Dict[str, Any]:
        status = str(run.get("status") or "")
        created = _parse_iso(run.get("created_at"))
        updated = _parse_iso(run.get("updated_at"))
        if status in ("in_progress", "queued") and created:
            elapsed = round(max(0.0, (now - created).total_seconds()), 1)
        else:
            elapsed = _duration_between(run.get("created_at"), run.get("updated_at"))
        return {
            "id": run.get("id"),
            "run_number": run.get("run_number"),
            "name": run.get("name"),
            "status": status,
            "conclusion": run.get("conclusion"),
            "event": run.get("event"),
            "head_branch": run.get("head_branch"),
            "head_sha": str(run.get("head_sha") or "")[:8],
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "html_url": _strip_credentials(str(run.get("html_url") or "")),
            "elapsed_seconds": elapsed,
            "window_used_ratio": (
                round(elapsed / (_WINDOW_MINUTES * 60.0), 4)
                if elapsed is not None
                else None
            ),
        }

    recent = [run_view(run) for run in runs[:6]]
    current = recent[0]
    snapshot["available"] = True
    snapshot["provenance"] = "REAL CLOUD RUN"
    snapshot["current"] = current
    snapshot["recent"] = recent

    if len(recent) > 1:
        newest = _parse_iso(recent[0].get("created_at"))
        previous = _parse_iso(recent[1].get("created_at"))
        if newest and previous:
            snapshot["handoff_gap_seconds"] = round(
                abs((newest - previous).total_seconds()), 1
            )

    run_id = current.get("id")
    if run_id:
        jobs_response = _github_get(
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=4", token
        )
        if jobs_response.get("ok"):
            jobs: List[Dict[str, Any]] = []
            for job in (jobs_response["data"].get("jobs") or [])[:3]:
                steps = []
                for step in job.get("steps") or []:
                    steps.append(
                        {
                            "name": step.get("name"),
                            "status": step.get("status"),
                            "conclusion": step.get("conclusion"),
                            "started_at": step.get("started_at"),
                            "completed_at": step.get("completed_at"),
                            "duration_seconds": _duration_between(
                                step.get("started_at"), step.get("completed_at")
                            ),
                        }
                    )
                steps.sort(
                    key=lambda row: row.get("duration_seconds") or 0.0, reverse=True
                )
                jobs.append(
                    {
                        "name": job.get("name"),
                        "status": job.get("status"),
                        "conclusion": job.get("conclusion"),
                        "started_at": job.get("started_at"),
                        "completed_at": job.get("completed_at"),
                        "duration_seconds": _duration_between(
                            job.get("started_at"), job.get("completed_at")
                        ),
                        "steps": steps,
                    }
                )
            snapshot["jobs"] = jobs
    return snapshot


def _workflow_snapshot() -> Dict[str, Any]:
    return _cached("github-workflow", _GITHUB_TTL, _build_workflow_snapshot)


def _current_bottlenecks() -> List[Dict[str, Any]]:
    if cycle_telemetry is None:
        return []
    return cycle_telemetry.derive_bottlenecks(
        cycle_telemetry.read_records(60), _workflow_snapshot()
    )


def _fix_queue_summary() -> Dict[str, Any]:
    if cycle_telemetry is None:
        return {"available": False, "items": [], "counts": {}, "path": None}
    queue = cycle_telemetry.load_fix_queue()
    items = []
    counts: Dict[str, int] = {}
    for entry in queue.get("items") or []:
        status = str(entry.get("status") or "queued")
        counts[status] = counts.get(status, 0) + 1
        items.append(
            {
                key: entry.get(key)
                for key in (
                    "id",
                    "label",
                    "module",
                    "kind",
                    "severity",
                    "status",
                    "provenance",
                    "updated_at",
                    "target_files",
                )
            }
        )
    return {
        "available": True,
        "path": cycle_telemetry.FIX_QUEUE_PATH.name,
        "generated_at": queue.get("generated_at"),
        "counts": counts,
        "items": items,
    }


def _cycle_telemetry_snapshot(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Real per-cycle ledger summary + derived bottlenecks for the deck."""
    base: Dict[str, Any] = {
        "available": False,
        "provenance": _NO_DATA,
        "records": 0,
        "current": None,
        "latest_steps": [],
        "trends": [],
        "bottlenecks": [],
        "negative_factors": [],
        "limiters": {},
        "fixes": _fix_queue_summary(),
        "window_minutes": _WINDOW_MINUTES,
    }
    if cycle_telemetry is None:
        base["reason"] = "cycle_telemetry module unavailable"
        return base

    records = cycle_telemetry.read_records(60)
    if not records:
        return base

    latest = records[-1]
    provenance = (
        "REAL CLOUD RUN" if latest.get("source") == "cloud-runner" else "REAL LOCAL RUN"
    )
    bottlenecks = cycle_telemetry.derive_bottlenecks(records, workflow)
    outcomes = latest.get("outcomes") or {}
    trends = []
    for record in records[-40:]:
        record_outcomes = record.get("outcomes") or {}
        trends.append(
            {
                "t": record.get("completed_at"),
                "cycle_id": record.get("cycle_id"),
                "source": record.get("source"),
                "duration_seconds": record.get("duration_seconds"),
                "cpu_percent_avg": record.get("cpu_percent_avg"),
                "rss_peak_mb": record.get("rss_peak_mb"),
                "pass_rate": record_outcomes.get("pass_rate"),
                "rejected_mutations": record_outcomes.get("rejected_mutations"),
                "discipline": record_outcomes.get("discipline"),
                "success": record.get("success"),
            }
        )
    limiters: Dict[str, int] = {}
    for item in bottlenecks:
        kind = str(item.get("kind") or "other")
        limiters[kind] = limiters.get(kind, 0) + 1

    base.update(
        {
            "available": True,
            "provenance": provenance,
            "records": len(records),
            "source": latest.get("source"),
            "current": latest,
            "latest_steps": latest.get("steps") or [],
            "outcomes": outcomes,
            "wall_clock": latest.get("wall_clock") or {},
            "trends": trends,
            "bottlenecks": bottlenecks,
            "negative_factors": [
                item for item in bottlenecks if item.get("kind") != "slow-step"
            ],
            "limiters": limiters,
        }
    )
    return base


def _verdicts_snapshot(limit: int = 20) -> Dict[str, Any]:
    """Recent zero-trust verifier verdicts for the diagnostic deck."""
    if cycle_telemetry is None:
        return {"available": False, "reason": "cycle_telemetry module unavailable"}
    try:
        events = cycle_telemetry.read_verification_verdicts(limit)
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not events:
        return {
            "available": True,
            "provenance": _NO_DATA,
            "events": [],
            "counts": {"total": 0, "passed": 0, "rejected": 0, "by_tier": {}},
        }
    passed = sum(1 for event in events if event.get("passed"))
    by_tier: Dict[str, int] = {}
    for event in events:
        if not event.get("passed"):
            tier = str(event.get("failed_tier") or "unknown")
            by_tier[tier] = by_tier.get(tier, 0) + 1
    return {
        "available": True,
        "provenance": "REAL VERIFIER VERDICTS",
        "events": events,
        "latest": events[-1],
        "counts": {
            "total": len(events),
            "passed": passed,
            "rejected": len(events) - passed,
            "by_tier": by_tier,
        },
    }


def _scale_snapshot() -> Dict[str, Any]:
    """Live elastic scaling and meta-scheduler state for the reliability deck."""
    base: Dict[str, Any] = {
        "available": False,
        "provenance": _NO_DATA,
        "active_workers": None,
        "max_workers": None,
        "strategy_slots": {},
        "bandit": {},
        "early_cancel": {},
        "policy": {},
        "limits": {},
        "hardware": {},
        "cloud": {},
        "headroom": {},
        "upgrades": [],
        "events": [],
    }
    state = _safe_json(ROOT / "execution_state.json", {})
    live = state.get("scale") if isinstance(state, dict) else None
    policy = _safe_json(ROOT / "scale_policy.json", {})
    events: List[Dict[str, Any]] = []
    if cycle_telemetry is not None:
        try:
            events = cycle_telemetry.read_scale_events(60)
        except Exception:
            events = []
    live = live if isinstance(live, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    if not live and not policy and not events:
        base["reason"] = "elastic scaling has not reported yet"
        return base

    latest_tick = next(
        (event for event in reversed(events) if event.get("kind") == "scale_tick"), {}
    )
    limits = live.get("limits") if isinstance(live.get("limits"), dict) else {}
    if not limits:
        try:
            import scale_orchestrator as scale  # type: ignore[import]

            limits = scale.CONSTITUTIONAL_LIMITS.as_dict()
        except Exception:
            limits = {}
    active = live.get("active_workers", latest_tick.get("active_workers"))
    max_workers = latest_tick.get("max_local_workers")
    if max_workers is None:
        max_workers = limits.get("max_local_workers")
    slots = live.get("strategy_slots") or latest_tick.get("strategy_slots") or {}
    early = live.get("early_cancel") if isinstance(live.get("early_cancel"), dict) else {}
    headroom = live.get("headroom") if isinstance(live.get("headroom"), dict) else {}
    if not headroom:
        ram_headroom = latest_tick.get("ram_headroom")
        if isinstance(ram_headroom, dict):
            headroom = {
                "ram": ram_headroom,
                "local_workers": (
                    max(0, int(max_workers) - int(active))
                    if max_workers is not None and active is not None
                    else None
                ),
            }
    upgrades = [
        event
        for event in (live.get("upgrades") or events)
        if event.get("kind") in ("policy_promoted", "policy_rejected", "policy_rolled_back")
    ]
    stored_policy = live.get("policy") if isinstance(live.get("policy"), dict) else {}
    if not stored_policy:
        stored_policy = {
            "version": policy.get("version"),
            "batch_size": policy.get("batch_size"),
            "local_workers": policy.get("local_workers"),
            "cloud_shards": policy.get("cloud_shards"),
            "exploration_floor": policy.get("exploration_floor"),
            "verify_tiers": policy.get("verify_tiers"),
        }
    base.update(
        {
            "available": True,
            "provenance": "REAL SCALE TELEMETRY",
            "active_workers": active,
            "max_workers": max_workers,
            "ram_fraction_ceiling": limits.get("max_ram_fraction"),
            "cycle_ceiling_minutes": (
                round(float(limits.get("max_cycle_seconds")) / 60.0, 1)
                if limits.get("max_cycle_seconds")
                else None
            ),
            "strategy_slots": dict(slots) if isinstance(slots, dict) else {},
            "bandit": live.get("bandit") if isinstance(live.get("bandit"), dict) else {},
            "early_cancel": {
                "events": int(early.get("events", 0) or 0),
                "cancelled_tasks": int(
                    early.get("cancelled_tasks", latest_tick.get("cancelled_tasks", 0)) or 0
                ),
                "saved_cpu_seconds": round(
                    float(early.get("saved_cpu_seconds", 0.0) or 0.0), 3
                ),
            },
            "policy": stored_policy,
            "limits": limits,
            "hardware": live.get("hardware") if isinstance(live.get("hardware"), dict) else {},
            "cloud": live.get("cloud") if isinstance(live.get("cloud"), dict) else {},
            "headroom": headroom,
            "upgrades": upgrades[-8:],
            "events": events[-12:],
            "p95_seconds": latest_tick.get("p95_seconds"),
        }
    )
    return base


def _world_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only per-script station beacons for the open-world command deck."""
    kernel = snapshot.get("kernel") or {}
    drives = snapshot.get("drives") or {}
    egraph = snapshot.get("egraph") or {}
    prover = snapshot.get("prover") or {}
    manifold = snapshot.get("manifold") or {}
    meta = snapshot.get("meta") or {}
    devil = snapshot.get("devil") or {}
    telos = snapshot.get("telos") or {}
    mutate = snapshot.get("mutate") or {}
    science = snapshot.get("science") or {}
    verification = snapshot.get("verification") or {}
    ux = snapshot.get("ux") or {}
    saturation = egraph.get("saturation") or {}

    def unit(value: Any) -> float:
        try:
            return round(max(0.0, min(1.0, float(value))), 3)
        except (TypeError, ValueError):
            return 0.0

    def num(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    routers = science.get("routers") or []
    verify_passed = num(verification.get("high_watermark_passed"))
    verify_total = num(verification.get("high_watermark_total"))
    verify_ratio = verify_passed / verify_total if verify_total else 0.0
    pending = int(telos.get("pending", 0) or 0)
    in_flight = int(telos.get("in_flight", 0) or 0)

    stations = [
        {
            "id": "core",
            "live": f"{kernel.get('state', 'UNKNOWN')} · {kernel.get('cycles', 0)} cycles · mastery {num(kernel.get('system_mastery')):.2f}",
            "pulse": unit(0.35 + min(20.0, num(kernel.get("cycles"))) / 40.0),
            "metrics": {
                "state": kernel.get("state"),
                "cycles": kernel.get("cycles"),
                "mastery": kernel.get("system_mastery"),
            },
        },
        {
            "id": "drives",
            "live": f"discipline {num(drives.get('discipline')):.2f} · floor {num(drives.get('floor', 0.9)):.2f}",
            "pulse": unit(drives.get("discipline")),
            "metrics": {
                key: drives.get(key)
                for key in (
                    "discipline",
                    "obsession",
                    "motivation",
                    "overthinking",
                    "mutation_pressure",
                    "floor",
                )
            },
        },
        {
            "id": "telos",
            "live": f"{pending} pending · {in_flight} in flight · {int(telos.get('completed', 0) or 0)} completed",
            "pulse": unit(0.3 + verify_ratio * 0.5),
            "metrics": {
                key: telos.get(key)
                for key in ("objectives", "pending", "in_flight", "produced", "completed", "failed")
            },
        },
        {
            "id": "egraph",
            "live": (
                f"{egraph.get('classes', 0)} classes · "
                f"{saturation.get('rewrites', 0)} rewrites · "
                f"{egraph.get('merged_classes', 0)} merged"
            ),
            "pulse": unit(0.3 + num(saturation.get("rewrites")) / 400.0),
            "metrics": {
                "classes": egraph.get("classes"),
                "merged": egraph.get("merged_classes"),
                "rewrites": saturation.get("rewrites"),
                "iterations": saturation.get("iterations"),
            },
        },
        {
            "id": "prover",
            "live": (
                f"{'solved' if prover.get('solved') else prover.get('stopped') or 'searching'} · "
                f"depth {prover.get('depth', 0)} · cache {prover.get('cache_hits', 0)} hits"
            ),
            "pulse": unit(1.0 if prover.get("solved") else 0.35),
            "metrics": {
                key: prover.get(key)
                for key in ("solved", "depth", "forward_expanded", "backward_expanded", "cache_hits", "modules")
            },
        },
        {
            "id": "devil",
            "live": (
                f"{devil.get('challenges', 0)} challenges · "
                f"{devil.get('stagnation_events', 0)} stagnation events · "
                f"intensity {num(devil.get('last_intensity')):.2f}"
            ),
            "pulse": unit(0.3 + num(devil.get("last_intensity"))),
            "metrics": {
                key: devil.get(key)
                for key in ("challenges", "stagnation_events", "local_convergence_events", "last_intensity")
            },
        },
        {
            "id": "manifold",
            "live": (
                f"{manifold.get('structures', 0)} structures · "
                f"{manifold.get('transfers', 0)} transfers · "
                f"{manifold.get('isomorphisms', 0)} isomorphisms"
            ),
            "pulse": unit(0.3 + num(manifold.get("transfers")) / 12.0),
            "metrics": {
                key: manifold.get(key)
                for key in ("structures", "transfers", "isomorphisms", "comparisons")
            },
        },
        {
            "id": "mutate",
            "live": (
                f"{mutate.get('cache_hits', 0)} cache hits · "
                f"{verify_passed:.0f}/{verify_total:.0f} gate checks passed"
            ),
            "pulse": unit(0.25 + verify_ratio * 0.6),
            "metrics": {
                "cache_hits": mutate.get("cache_hits"),
                "cache_entries": mutate.get("cache_entries"),
                "gate_passed": verification.get("high_watermark_passed"),
                "gate_total": verification.get("high_watermark_total"),
            },
        },
        {
            "id": "science",
            "live": (
                f"{science.get('router_count', len(routers))} routers · "
                f"fastmcp {'on' if science.get('fastmcp_available') else 'off'}"
            ),
            "pulse": unit(0.3 + len(routers) / 20.0),
            "metrics": {
                "routers": science.get("router_count") or len(routers),
                "fastmcp": science.get("fastmcp_available"),
                "adapters": len(science.get("adapters") or []),
            },
        },
        {
            "id": "evolver",
            "live": (
                f"{ux.get('evolutions', 0)} evolutions · "
                f"active v{ux.get('active_version') if ux.get('active_version') is not None else 0}"
            ),
            "pulse": unit(0.25 + num(ux.get("evolutions")) / 10.0),
            "metrics": {
                "evolutions": ux.get("evolutions"),
                "active_version": ux.get("active_version"),
                "searches": meta.get("searches"),
            },
        },
    ]

    module_station = {
        "core": "core",
        "drives": "drives",
        "telos": "telos",
        "egraph_engine": "egraph",
        "prover": "prover",
        "verify": "prover",
        "devil_advocate": "devil",
        "manifold_bridge": "manifold",
        "mutate": "mutate",
        "science_mcp": "science",
        "meta_prover": "evolver",
        "bio_gen": "science",
    }
    warnings_by_station: Dict[str, List[Dict[str, Any]]] = {}
    for item in snapshot.get("bottlenecks") or []:
        station_id = module_station.get(str(item.get("module") or ""))
        if station_id:
            warnings_by_station.setdefault(station_id, []).append(item)
    for station in stations:
        station_warnings = warnings_by_station.get(station["id"]) or []
        station["warnings"] = [
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "kind": item.get("kind"),
                "severity": item.get("severity"),
            }
            for item in station_warnings[:3]
        ]
        station["warning"] = (
            station_warnings[0].get("label") if station_warnings else None
        )
        station["warning_severity"] = (
            station_warnings[0].get("severity") if station_warnings else 0.0
        )
    return {"layout": "ring-hub-v1", "stations": stations}


def build_snapshot(record_series: bool = False) -> Dict[str, Any]:
    """Assemble a complete read-only telemetry snapshot for HUD and web deck."""
    execution = _safe_json(ROOT / "execution_state.json", {})
    meta_state = _safe_json(ROOT / ".sask_meta_prover.json", {})
    devil_state = _safe_json(ROOT / ".sask_devil_advocate.json", {})
    curiosity_state = _safe_json(ROOT / ".sask_curiosity.json", {})
    egraph_state = _safe_json(ROOT / ".sask_egraph.json", {})
    ratchet = _safe_json(ROOT / ".sask_ego_ratchet.json", {})
    ledger = _safe_json(ROOT / ".sask_goal_ledger.json", {})
    stream = _safe_json(ROOT / ".sask_goal_stream.json", {})

    drives = _drives_snapshot(execution if isinstance(execution, dict) else {})
    mastery = float((execution or {}).get("system_mastery", 0.0))

    egraph_visual = _cached(
        "egraph-visual",
        20.0,
        lambda: _build_egraph_visual(mastery, drives["discipline"]),
    )
    egraph = dict(egraph_visual)
    egraph.update(
        {
            "superpositions": int(egraph_state.get("superpositions", 0) or 0),
            "rewrites": int(egraph_state.get("rewrites", 0) or 0),
            "promotions": int(egraph_state.get("promotions", 0) or 0),
            "rejections": int(egraph_state.get("rejections", 0) or 0),
            "last_state": egraph_state.get("last", {}),
        }
    )

    prover = _cached("prover", 30.0, _prover_snapshot)
    manifold = _cached("manifold", 30.0, _manifold_snapshot)
    science = _cached("science", 20.0, _science_snapshot)
    workflow = _workflow_snapshot()
    cycle_snapshot = _cached(
        "cycle-telemetry", 1.0, lambda: _cycle_telemetry_snapshot(workflow)
    )

    snapshot: Dict[str, Any] = {
        "generated_at": _utc_now(),
        "source": "local-readonly",
        "kernel": _execution_summary(execution if isinstance(execution, dict) else {}),
        "isolation": {
            "kernel_fingerprint": _SERVER_START_FINGERPRINT,
            "state_unchanged": kernel_fingerprint() == _SERVER_START_FINGERPRINT,
            "server_started_at": _SERVER_STARTED_AT,
            "sandbox": "client-side ephemeral; server accepts no game state",
            "server_writes": [
                "ui_telemetry.json",
                "ui_evolver_log.json",
                "ui_backups/",
                "ui/evolved.css",
                "sask_agent_fix_queue.json",
                "sask_tuning.json",
            ],
        },
        "drives": drives,
        "egraph": egraph,
        "manifold": manifold,
        "prover": prover,
        "meta": _meta_snapshot(meta_state if isinstance(meta_state, dict) else {}),
        "devil": _devil_snapshot(devil_state if isinstance(devil_state, dict) else {}),
        "curiosity": _curiosity_snapshot(
            curiosity_state if isinstance(curiosity_state, dict) else {}
        ),
        "telos": _telos_snapshot(
            ledger if isinstance(ledger, dict) else {},
            stream if isinstance(stream, dict) else {},
        ),
        "mutate": _mutation_snapshot(),
        "science": science,
        "scale": _cached("scale", 1.0, _scale_snapshot),
        "verification": {
            "high_watermark_passed": ratchet.get("high_watermark_passed", 0),
            "high_watermark_total": ratchet.get("high_watermark_total", 0),
            "gate_runs": ratchet.get("gate_runs", 0),
            "updated_at": ratchet.get("updated_at"),
            "zero_trust": _verdicts_snapshot(),
        },
        "ux": _ux_summary(),
        "workflows": workflow,
        "cycle_telemetry": cycle_snapshot,
        "bottlenecks": cycle_snapshot.get("bottlenecks") or [],
        "negative_factors": cycle_snapshot.get("negative_factors") or [],
        "fixes": cycle_snapshot.get("fixes") or {},
    }
    snapshot["world"] = _world_snapshot(snapshot)
    if record_series:
        _record_series(snapshot)
    snapshot["series"] = _series_payload()
    return snapshot


# ---------------------------------------------------------------------------
# UX telemetry ingest (whitelisted, bounded, game-state-free)
# ---------------------------------------------------------------------------

_UX_LOCK = threading.Lock()
_UX_ALLOWED_KEYS = {
    "type",
    "panel",
    "game",
    "action",
    "dwell_ms",
    "fps",
    "viewport",
    "mode",
    "detail",
}


def record_ux_events(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Append bounded UX events to the local telemetry file.

    Only whitelisted interaction metadata is accepted. Any sandbox/game state
    fields are dropped before persistence, so games cannot leak into files.
    """
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return {"recorded": 0, "rejected": "events must be a list", "total": 0}

    cleaned: List[Dict[str, Any]] = []
    for event in events[:60]:
        if not isinstance(event, dict):
            continue
        item: Dict[str, Any] = {}
        for key in _UX_ALLOWED_KEYS:
            if key not in event:
                continue
            value = event[key]
            if isinstance(value, str):
                item[key] = value[:120]
            elif isinstance(value, (int, float, bool)) and not isinstance(value, bool):
                item[key] = value
            elif isinstance(value, bool):
                item[key] = value
        if item.get("type"):
            cleaned.append(item)

    with _UX_LOCK:
        existing = _safe_json(UX_TELEMETRY_PATH, [])
        if isinstance(existing, dict):
            existing = existing.get("events", [])
        if not isinstance(existing, list):
            existing = []
        stamped = [
            {**event, "recorded_at": _utc_now()}
            for event in cleaned
        ]
        existing.extend(stamped)
        existing = existing[-2000:]
        _atomic_write(
            UX_TELEMETRY_PATH,
            json.dumps(existing, indent=2),
        )
    return {"recorded": len(stamped), "total": len(existing), "accepted_keys": sorted(_UX_ALLOWED_KEYS)}


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


def create_app() -> "FastAPI":
    """Build the local FastAPI app (serves UI + read-only telemetry)."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. Run: pip install -e . "
            "(or pip install fastapi uvicorn websockets)."
        )

    app = FastAPI(
        title="SASK Local Command Deck",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.get("/api/health")
    async def health() -> Dict[str, Any]:
        current = kernel_fingerprint()
        return {
            "ok": True,
            "service": "sask-command-deck",
            "started_at": _SERVER_STARTED_AT,
            "kernel_fingerprint": current,
            "state_unchanged": current == _SERVER_START_FINGERPRINT,
            "sandbox_isolation": "client-side only",
            "fastapi_available": True,
        }

    @app.get("/api/state")
    async def state() -> Dict[str, Any]:
        return build_snapshot(record_series=False)

    @app.post("/api/ux/telemetry")
    async def ux_telemetry(payload: Dict[str, Any]) -> Dict[str, Any]:
        return record_ux_events(payload)

    @app.post("/api/ux/evolve")
    async def ux_evolve() -> Dict[str, Any]:
        try:
            from ui_evolver import UIEvolver

            return UIEvolver(root=ROOT).evolve()
        except Exception as exc:
            return {"promoted": False, "error": f"{type(exc).__name__}: {exc}"}

    @app.get("/api/ux/versions")
    async def ux_versions() -> Dict[str, Any]:
        try:
            from ui_evolver import UIEvolver

            return UIEvolver(root=ROOT).status()
        except Exception as exc:
            return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    @app.post("/api/ux/revert")
    async def ux_revert(payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from ui_evolver import UIEvolver

            version = payload.get("version") if isinstance(payload, dict) else None
            return UIEvolver(root=ROOT).revert(
                int(version) if version is not None else None
            )
        except Exception as exc:
            return {"reverted": False, "error": f"{type(exc).__name__}: {exc}"}

    @app.get("/api/fixes")
    async def fixes() -> Dict[str, Any]:
        queue = (
            cycle_telemetry.load_fix_queue()
            if cycle_telemetry is not None
            else {"items": [], "generated_at": None}
        )
        workflow = _workflow_snapshot()
        return {
            "generated_at": _utc_now(),
            "bottlenecks": _current_bottlenecks(),
            "queue": queue,
            "workflow": {
                "available": workflow.get("available"),
                "provenance": workflow.get("provenance"),
                "current": workflow.get("current"),
            },
        }

    @app.post("/api/fixes/queue")
    async def queue_bottleneck_fixes(payload: Dict[str, Any]) -> Dict[str, Any]:
        if cycle_telemetry is None:
            return {"queued": 0, "error": "cycle_telemetry unavailable"}
        items = _current_bottlenecks()
        ids = payload.get("ids") if isinstance(payload, dict) else None
        if isinstance(ids, list) and ids:
            wanted = {str(item) for item in ids}
            items = [item for item in items if str(item.get("id")) in wanted]
        result = cycle_telemetry.queue_fixes(items, source="ui")
        _invalidate_cache("cycle-telemetry")
        result["items"] = result.get("items") or []
        return result

    @app.post("/api/fixes/apply")
    async def apply_bottleneck_fixes(payload: Dict[str, Any]) -> Dict[str, Any]:
        if cycle_telemetry is None:
            return {"applied": False, "error": "cycle_telemetry unavailable"}
        items = _current_bottlenecks()
        item_id = payload.get("id") if isinstance(payload, dict) else None
        if item_id:
            selected = [
                item for item in items if str(item.get("id")) == str(item_id)
            ]
            if not selected and cycle_telemetry is not None:
                queue = cycle_telemetry.load_fix_queue()
                selected = [
                    entry
                    for entry in queue.get("items") or []
                    if str(entry.get("id")) == str(item_id)
                ]
            if not selected:
                return {"applied": False, "error": f"unknown fix id {item_id}"}
            cycle_telemetry.queue_fixes(selected, source="ui")
            result = cycle_telemetry.apply_safe_remediation(selected[0])
            _invalidate_cache("cycle-telemetry")
            return result
        cycle_telemetry.queue_fixes(items, source="ui")
        results = [cycle_telemetry.apply_safe_remediation(item) for item in items]
        _invalidate_cache("cycle-telemetry")
        return {
            "applied": sum(1 for row in results if row.get("applied")),
            "awaiting_agent": sum(1 for row in results if not row.get("applied")),
            "results": results,
        }

    @app.websocket("/ws/telemetry")
    async def telemetry_socket(websocket: "WebSocket") -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(build_snapshot(record_series=True))
                await asyncio.sleep(1.5)
        except WebSocketDisconnect:
            return
        except Exception:
            return

    @app.get("/")
    async def index():
        return FileResponse(UI_DIR / "index.html")

    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
    return app


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def wait_until_ready(port: int = DEFAULT_PORT, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://{HOST}:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def deck_url(port: int = DEFAULT_PORT, anchor: str = "") -> str:
    base = f"http://{HOST}:{port}/"
    return f"{base}#{anchor}" if anchor else base


def open_deck(port: int = DEFAULT_PORT, anchor: str = "") -> None:
    if wait_until_ready(port, timeout=10.0):
        webbrowser.open(f"http://{HOST}:{port}/" + (f"#{anchor}" if anchor else ""))
    else:
        webbrowser.open(f"http://{HOST}:{port}/" + (f"#{anchor}" if anchor else ""))


def start_server_thread(port: int = DEFAULT_PORT, log_level: str = "warning"):
    """Start uvicorn on a daemon thread; returns ``(thread, server)``."""
    import uvicorn

    config = uvicorn.Config(
        create_app(),
        host=HOST,
        port=port,
        log_level=log_level,
        access_log=log_level == "debug",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="sask-ui-server", daemon=True)
    thread.start()
    return thread, server


def run_server(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    import uvicorn

    if open_browser:
        threading.Thread(
            target=lambda: webbrowser.open(f"http://{HOST}:{port}/"),
            name="sask-open-browser",
            daemon=True,
        ).start()
    uvicorn.run(
        create_app(),
        host=HOST,
        port=port,
        log_level="warning",
        access_log=False,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="SASK local command deck server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--state",
        action="store_true",
        help="print one read-only snapshot as JSON and exit",
    )
    args = parser.parse_args()
    if args.state:
        print(json.dumps(build_snapshot(), indent=2, default=str))
        return 0
    run_server(port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
