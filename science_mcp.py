"""SASK 10-Router Hyper-Specialized Science Engine (FastMCP server).

Exposes ten universal scientific and computational routers as MCP tools. Each
router normalizes a batch payload, selects solver/container/accelerator
parameters via :class:`SpecializationEngine`, mutates its task graph via
:class:`RecursiveSelfOptimizer`, and reports latency, residual, and success
telemetry into :class:`drives.UnifiedDisciplineEngine`.

Live infrastructure (quantum hardware, HPC schedulers, beamlines, wet labs,
satellite archives, volunteer grids, multi-physics solvers, digital-twin
engines, and biomolecular toolchains) is not bundled with this repository.
When no live adapter is registered, every router executes a deterministic
in-process simulation and reports ``execution_mode="simulated_fallback"``.
Register a real backend with :func:`register_adapter` without changing the
tool surface, schemas, or telemetry contract.

The server is transport-agnostic: run it over stdio with
``python science_mcp.py`` or set ``SASK_MCP_TRANSPORT=streamable-http``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import threading
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("SASK.ScienceMCP")


# ---------------------------------------------------------------------------
# FastMCP discovery with a non-blocking in-process fallback
# ---------------------------------------------------------------------------

class _FastMCPShim:
    """Minimal FastMCP-compatible registry used when no MCP SDK is installed.

    The shim preserves the decorator surface and tool registry so the SASK
    kernel remains importable and testable offline. It does not speak the MCP
    wire protocol; install ``fastmcp`` or ``mcp[cli]`` to serve tools.
    """

    def __init__(self, name: str = "SASK") -> None:
        self.name = name
        self.tools: Dict[str, Callable[..., Any]] = {}
        self._lock = threading.RLock()

    def tool(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a function as a tool, mirroring ``FastMCP.tool()``."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or func.__name__
            with self._lock:
                self.tools[tool_name] = func
            return func

        return decorator

    def get_tools(self) -> Dict[str, Callable[..., Any]]:
        """Return a snapshot of registered tool callables."""
        with self._lock:
            return dict(self.tools)

    def run(self, transport: str = "stdio", **kwargs: Any) -> None:
        """Explain why no server is listening instead of blocking forever."""
        logger.warning(
            "FastMCP SDK unavailable: %s registered %d tools in-process; "
            "no %s transport started. Install fastmcp to serve MCP.",
            self.name,
            len(self.tools),
            transport,
        )


try:  # Preferred standalone server
    from fastmcp import FastMCP  # type: ignore[import-not-found]

    FASTMCP_IMPLEMENTATION = "fastmcp"
    FASTMCP_AVAILABLE = True
except Exception:  # pragma: no cover - depends on environment
    try:  # Official MCP SDK fallback
        from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found,no-redef]

        FASTMCP_IMPLEMENTATION = "mcp.server.fastmcp"
        FASTMCP_AVAILABLE = True
    except Exception:  # Offline fallback
        FastMCP = _FastMCPShim  # type: ignore[assignment,misc]
        FASTMCP_IMPLEMENTATION = "in_process_shim"
        FASTMCP_AVAILABLE = False

mcp = FastMCP("SASK 10-Router Hyper-Specialized Science Engine")


def _tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register a router with FastMCP but keep the raw callable for dispatch.

    fastmcp>=2 replaces decorated functions with a non-callable ``FunctionTool``
    wrapper, so the registry must retain the undecorated function.
    """
    try:
        mcp.tool()(fn)
    except Exception:  # pragma: no cover - registration must not block dispatch
        logger.warning(
            "FastMCP tool registration failed for %s; dispatch remains in-process.",
            getattr(fn, "__name__", fn),
        )
    return fn


# ---------------------------------------------------------------------------
# Router identity
# ---------------------------------------------------------------------------

ROUTER_NAMES: Tuple[str, ...] = (
    "quantum_router",
    "atomistic_router",
    "hpc_router",
    "wetlab_router",
    "sensing_router",
    "beamline_router",
    "free_grid_router",
    "multiphysics_router",
    "digital_twin_router",
    "biomolecular_router",
)

_ROUTER_ALIASES: Dict[str, str] = {
    "quantum": "quantum_router",
    "atomistic": "atomistic_router",
    "atoms": "atomistic_router",
    "hpc": "hpc_router",
    "wetlab": "wetlab_router",
    "wet_lab": "wetlab_router",
    "sensing": "sensing_router",
    "sensor": "sensing_router",
    "beamline": "beamline_router",
    "free_grid": "free_grid_router",
    "freegrid": "free_grid_router",
    "grid": "free_grid_router",
    "multiphysics": "multiphysics_router",
    "digital_twin": "digital_twin_router",
    "twin": "digital_twin_router",
    "biomolecular": "biomolecular_router",
    "bio": "biomolecular_router",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WorkloadProfile:
    """Normalized description of a router workload."""

    router: str
    items: int = 0
    payload_bytes: int = 0
    precision_target: float = 1e-6
    gpu_preferred: bool = False
    live_required: bool = False


@dataclass
class TaskGraph:
    """Mutable execution graph optimized between router invocations."""

    router: str
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    revision: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the graph to a JSON-safe structure."""
        return {
            "router": self.router,
            "nodes": self.nodes,
            "edges": [list(edge) for edge in self.edges],
            "revision": self.revision,
        }


@dataclass
class RouterResult:
    """Structured telemetry returned by every science router."""

    router: str
    operation: str
    success: bool
    latency_seconds: float
    numerical_residual: float
    execution_mode: str
    artifacts: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dict for MCP and SASK telemetry."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_size(payload: Any) -> int:
    """Return the serialized payload size in bytes, tolerating odd objects."""
    try:
        return len(json.dumps(payload, default=str, sort_keys=True).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _count_items(payload: Any) -> int:
    """Estimate the number of work items contained in a router payload."""
    if isinstance(payload, (list, tuple, set)):
        return len(payload)
    if isinstance(payload, dict):
        nested = sum(
            len(value)
            for value in payload.values()
            if isinstance(value, (list, tuple, set))
        )
        return nested or len(payload)
    return 0 if payload is None else 1


def _hash_fraction(payload: Any) -> float:
    """Derive a deterministic fraction in [0, 1) from a payload."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.blake2b(blob, digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(2 ** 64)


def _build_task_graph(router: str, payload: Any) -> TaskGraph:
    """Create the baseline prepare/execute/verify graph for a router."""
    return TaskGraph(
        router=router,
        nodes=[
            {"id": "prepare", "op": "normalize_inputs", "parallel": 1},
            {"id": "execute", "op": router, "parallel": 1},
            {"id": "verify", "op": "residual_check_and_persist", "parallel": 1},
        ],
        edges=[("prepare", "execute"), ("execute", "verify")],
    )


# ---------------------------------------------------------------------------
# Specialization and self-optimization
# ---------------------------------------------------------------------------

class SpecializationEngine:
    """Selects solver parameters, container flags, and accelerator drivers.

    Presets are deliberately explicit: each router advertises the solver family
    and container capabilities expected by its live adapter. The engine scales
    precision and parallelism from the workload profile and the optional
    ``SASK_ACCELERATOR`` override (``cuda``, ``rocm``, ``opencl``, or ``cpu``).
    """

    _PRESETS: Dict[str, Dict[str, Any]] = {
        "quantum_router": {
            "solver": "statevector_tensor_network",
            "container_flags": ["--gpus all", "--shm-size=8g"],
            "precision": "double",
        },
        "atomistic_router": {
            "solver": "mlip_dft_hybrid",
            "container_flags": ["--cap-add=SYS_PTRACE", "--shm-size=4g"],
            "precision": "double",
        },
        "hpc_router": {
            "solver": "mpi_python_hybrid",
            "container_flags": ["--ipc=host", "--ulimit=memlock=-1"],
            "precision": "double",
        },
        "wetlab_router": {
            "solver": "rdf_protocol_scheduler",
            "container_flags": ["--read-only", "--tmpfs=/scratch"],
            "precision": "single",
        },
        "sensing_router": {
            "solver": "stac_ingest_mosaic",
            "container_flags": ["--memory=8g", "--cpus=4"],
            "precision": "single",
        },
        "beamline_router": {
            "solver": "flyscan_trajectory_solver",
            "container_flags": ["--network=host", "--privileged"],
            "precision": "double",
        },
        "free_grid_router": {
            "solver": "boinc_container_swarm",
            "container_flags": ["--cpus=2", "--memory=4g"],
            "precision": "single",
        },
        "multiphysics_router": {
            "solver": "fenics_coupled_newton",
            "container_flags": ["--shm-size=8g", "--ipc=host"],
            "precision": "double",
        },
        "digital_twin_router": {
            "solver": "physx_usd_kinematics",
            "container_flags": ["--gpus all", "--shm-size=4g"],
            "precision": "single",
        },
        "biomolecular_router": {
            "solver": "openmm_alphafold_bridge",
            "container_flags": ["--gpus all", "--shm-size=16g"],
            "precision": "double",
        },
        "_default": {
            "solver": "generic_deterministic_solver",
            "container_flags": ["--cpus=2", "--memory=2g"],
            "precision": "double",
        },
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._selections = 0

    @staticmethod
    def _select_driver(workload: WorkloadProfile) -> str:
        """Choose an accelerator driver from the environment or workload."""
        requested = os.environ.get("SASK_ACCELERATOR", "auto").strip().lower()
        if requested in {"cuda", "rocm", "opencl", "cpu"}:
            return requested
        return "cuda" if workload.gpu_preferred else "cpu"

    def select(self, router: str, workload: WorkloadProfile) -> Dict[str, Any]:
        """Select concrete execution parameters for a router workload.

        Args:
            router: Canonical router name.
            workload: Normalized workload profile.

        Returns:
            A dict containing solver, container flags, driver, precision, and
            maximum parallelism.
        """
        preset = self._PRESETS.get(router, self._PRESETS["_default"])
        precision = preset["precision"]
        if workload.precision_target <= 1e-9:
            precision = "quad"
        elif workload.precision_target >= 1e-3:
            precision = "single"
        max_parallelism = max(1, min(256, 2 + workload.items))
        with self._lock:
            self._selections += 1
        return {
            "router": router,
            "solver": preset["solver"],
            "container_flags": list(preset["container_flags"]),
            "driver": self._select_driver(workload),
            "precision": precision,
            "max_parallelism": max_parallelism,
            "live_required": workload.live_required,
        }

    def describe(self) -> Dict[str, Any]:
        """Return a summary of specialization presets and usage."""
        return {
            "presets": sorted(self._PRESETS),
            "selections_issued": self._selections,
            "accelerator_env": os.environ.get("SASK_ACCELERATOR", "auto"),
        }


class RecursiveSelfOptimizer:
    """Evaluates router telemetry and mutates graphs and prompt structures.

    The optimizer tracks a bounded history per router and derives one of five
    strategies: ``cold_start``, ``stabilize``, ``refine``, ``shard``, or
    ``converge``. Each strategy injects a corresponding control node into the
    task graph and a matching directive set into the prompt structure.

    Args:
        history_limit: Maximum retained telemetry records per router.
    """

    def __init__(self, history_limit: int = 128) -> None:
        self.history_limit = max(1, int(history_limit))
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def evaluate(
        self,
        router: str,
        latency_seconds: float,
        residual: float,
        success: bool,
    ) -> Dict[str, Any]:
        """Record one execution and return updated router telemetry.

        Args:
            router: Canonical router name.
            latency_seconds: Measured execution latency.
            residual: Numerical residual in [0, 1].
            success: Whether the pipeline completed successfully.

        Returns:
            Aggregated telemetry for the router.
        """
        record = {
            "latency_seconds": round(max(0.0, float(latency_seconds)), 6),
            "numerical_residual": round(max(0.0, min(1.0, float(residual))), 8),
            "success": bool(success),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            bucket = self._history.setdefault(router, [])
            bucket.append(record)
            if len(bucket) > self.history_limit:
                del bucket[: len(bucket) - self.history_limit]
        return self.telemetry(router)

    def telemetry(self, router: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate telemetry for one router or all routers."""
        with self._lock:
            if router is None:
                records = [
                    record
                    for bucket in self._history.values()
                    for record in bucket
                ]
                label = "all"
            else:
                records = list(self._history.get(router, []))
                label = router

        runs = len(records)
        if runs == 0:
            return {
                "router": label,
                "runs": 0,
                "success_rate": 1.0,
                "mean_latency_seconds": 0.0,
                "mean_residual": 0.0,
                "strategy": "cold_start",
            }

        success_rate = sum(1 for record in records if record["success"]) / runs
        mean_latency = sum(record["latency_seconds"] for record in records) / runs
        mean_residual = (
            sum(record["numerical_residual"] for record in records) / runs
        )

        if success_rate < 0.8:
            strategy = "stabilize"
        elif mean_residual > 0.25:
            strategy = "refine"
        elif mean_latency > 2.0:
            strategy = "shard"
        else:
            strategy = "converge"

        return {
            "router": label,
            "runs": runs,
            "success_rate": round(success_rate, 4),
            "mean_latency_seconds": round(mean_latency, 6),
            "mean_residual": round(mean_residual, 8),
            "strategy": strategy,
        }

    def mutate_task_graph(
        self,
        graph: TaskGraph,
        router: str,
    ) -> Tuple[TaskGraph, str]:
        """Inject a control node into the task graph for the active strategy.

        Args:
            graph: Baseline task graph.
            router: Canonical router name.

        Returns:
            The mutated graph and the selected strategy name.
        """
        strategy = self.telemetry(router)["strategy"]
        node_ids = {node["id"] for node in graph.nodes}
        addition: Optional[Dict[str, Any]] = None

        if strategy == "cold_start" and "warmup_probe" not in node_ids:
            addition = {
                "id": "warmup_probe",
                "op": "validate_environment_and_units",
                "parallel": 1,
            }
        elif strategy == "stabilize" and "fallback_validate" not in node_ids:
            addition = {
                "id": "fallback_validate",
                "op": "redundant_cross_check",
                "parallel": 2,
            }
        elif strategy == "refine" and "residual_refinement" not in node_ids:
            addition = {
                "id": "residual_refinement",
                "op": "iterative_residual_reduction",
                "parallel": 1,
            }
        elif strategy == "shard" and "sharded_dispatch" not in node_ids:
            addition = {
                "id": "sharded_dispatch",
                "op": "split_and_parallelize",
                "parallel": max(2, len(graph.nodes)),
            }

        if addition is None:
            return graph, strategy

        graph.nodes.insert(1, addition)
        graph.edges = (
            [("prepare", addition["id"])]
            + [(addition["id"], "execute")]
            + [edge for edge in graph.edges if edge != ("prepare", "execute")]
        )
        graph.revision += 1
        return graph, strategy

    @staticmethod
    def mutate_prompt_structure(objective: str, strategy: str) -> Dict[str, Any]:
        """Derive an execution directive set from the active strategy."""
        directives = {
            "cold_start": [
                "establish unit and schema invariants",
                "run a minimal calibration probe before the full batch",
            ],
            "stabilize": [
                "add redundant validation",
                "reduce step size",
                "retain the last known-good checkpoint",
            ],
            "refine": [
                "tighten numerical tolerance",
                "iterate residual correction to convergence",
            ],
            "shard": [
                "partition the workload across independent containers",
                "overlap I/O with compute",
            ],
            "converge": [
                "freeze solver configuration",
                "capture a reproducibility manifest",
            ],
        }.get(strategy, ["execute deterministically"])
        return {
            "objective": objective,
            "strategy": strategy,
            "directives": directives,
            "verification": [
                "schema validation",
                "numerical residual bound",
                "artifact persistence",
            ],
        }


class MasterIdentityContext:
    """Manages dynamic, scoped credential delegation for science routers.

    Secrets are held in memory only and are never serialized into router
    results, telemetry, or logs. Environment discovery recognizes Globus bearer
    tokens, CILogon X.509 certificate paths, and SSH agent sockets.

    Args:
        default_ttl_seconds: Default lifetime for delegated credentials.
    """

    _ENV_SOURCES: Dict[str, Tuple[str, str]] = {
        "globus": ("GLOBUS_TOKEN", "bearer"),
        "cilogon": ("CILOGON_CERT", "x509"),
        "ssh": ("SSH_AUTH_SOCK", "ssh-agent"),
    }

    def __init__(self, default_ttl_seconds: float = 3600.0) -> None:
        self.default_ttl_seconds = max(1.0, float(default_ttl_seconds))
        self._delegations: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def delegate(
        self,
        scope: str,
        token_type: str,
        token: str,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Delegate a credential to a scope for a bounded lifetime.

        Args:
            scope: Logical consumer scope, for example ``quantum_router``.
            token_type: Credential type such as ``bearer`` or ``x509``.
            token: Secret value or credential handle.
            ttl_seconds: Lifetime; defaults to the context default.

        Returns:
            Redacted delegation metadata.
        """
        if not scope or not token:
            raise ValueError("scope and token are required for delegation.")
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        record = {
            "scope": scope,
            "token_type": token_type,
            "token": token,
            "expires_at": time.time() + max(1.0, float(ttl)),
        }
        with self._lock:
            self._delegations[scope] = record
        return self.redacted(scope)

    def delegate_from_environment(self, scope: str, source: str) -> Optional[Dict[str, Any]]:
        """Delegate from a recognized environment credential source.

        Args:
            scope: Logical consumer scope.
            source: One of ``globus``, ``cilogon``, or ``ssh``.

        Returns:
            Redacted delegation metadata, or None when the source is absent.
        """
        source_key = source.strip().lower()
        if source_key not in self._ENV_SOURCES:
            return None
        env_name, token_type = self._ENV_SOURCES[source_key]
        token = os.environ.get(env_name)
        if not token:
            return None
        return self.delegate(scope, token_type, token)

    def resolve(self, scope: str) -> Optional[str]:
        """Return a live credential for a scope, purging expired entries."""
        now = time.time()
        with self._lock:
            record = self._delegations.get(scope)
            if record is None:
                return None
            if record["expires_at"] <= now:
                del self._delegations[scope]
                return None
            return str(record["token"])

    def has_credentials(self, scope: str) -> bool:
        """Check whether a live credential exists for a scope."""
        return self.resolve(scope) is not None

    def redacted(self, scope: Optional[str] = None) -> Dict[str, Any]:
        """Return scope metadata without exposing any secret values."""
        now = time.time()
        with self._lock:
            if scope is not None:
                record = self._delegations.get(scope)
                if record is None or record["expires_at"] <= now:
                    return {"scope": scope, "present": False}
                return {
                    "scope": scope,
                    "present": True,
                    "token_type": record["token_type"],
                    "expires_in_seconds": round(record["expires_at"] - now, 1),
                }
            return {
                key: {
                    "present": True,
                    "token_type": record["token_type"],
                    "expires_in_seconds": round(record["expires_at"] - now, 1),
                }
                for key, record in self._delegations.items()
                if record["expires_at"] > now
            }

    def purge_expired(self) -> int:
        """Remove expired delegations and return the count removed."""
        now = time.time()
        with self._lock:
            expired = [
                scope
                for scope, record in self._delegations.items()
                if record["expires_at"] <= now
            ]
            for scope in expired:
                del self._delegations[scope]
        return len(expired)


# ---------------------------------------------------------------------------
# Engine state, adapters, and discipline feedback
# ---------------------------------------------------------------------------

specialization_engine = SpecializationEngine()
self_optimizer = RecursiveSelfOptimizer()
identity_context = MasterIdentityContext()

_ADAPTERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
_ADAPTER_LOCK = threading.RLock()
_DRIVE_ENGINE_CTX: ContextVar[Any] = ContextVar(
    "sask_science_drive_engine", default=None
)

# Meta-cognitive extensions registered by the SASK kernel (meta-prover,
# curiosity engine, manifold bridge, adversarial injector).
_EXTENSIONS: Dict[str, Any] = {}
_EXTENSION_LOCK = threading.RLock()


def register_extension(name: str, engine: Any) -> Dict[str, Any]:
    """Register a kernel extension so router results can surface its hints."""
    key = str(name or "").strip()
    if not key:
        raise ValueError("extension name must be non-empty")
    with _EXTENSION_LOCK:
        _EXTENSIONS[key] = engine
    return {"registered": True, "name": key, "extensions": sorted(_EXTENSIONS)}


def unregister_extension(name: str) -> bool:
    """Remove a registered extension, returning True when one was removed."""
    with _EXTENSION_LOCK:
        return _EXTENSIONS.pop(str(name or "").strip(), None) is not None


def get_extension_snapshot() -> Dict[str, Any]:
    """Return redacted telemetry for every registered extension."""
    with _EXTENSION_LOCK:
        extensions = dict(_EXTENSIONS)
    snapshot: Dict[str, Any] = {}
    for name, engine in extensions.items():
        telemetry: Dict[str, Any] = {"registered": True}
        try:
            if hasattr(engine, "get_telemetry"):
                telemetry.update(engine.get_telemetry())
        except Exception as exc:  # Telemetry must never break a router
            telemetry["error"] = f"{type(exc).__name__}: {exc}"
        snapshot[name] = telemetry
    return snapshot


def _extension_hints(router: str) -> Dict[str, Any]:
    """Lightweight per-result hints drawn from registered extensions."""
    with _EXTENSION_LOCK:
        extensions = dict(_EXTENSIONS)
    hints: Dict[str, Any] = {}
    meta_prover = extensions.get("meta_prover")
    if meta_prover is not None and hasattr(meta_prover, "preferred_framework"):
        try:
            framework = meta_prover.preferred_framework()
            if framework:
                hints["preferred_framework"] = framework
        except Exception:
            pass
    curiosity = extensions.get("curiosity") or extensions.get("telos_v2")
    if curiosity is not None:
        try:
            telemetry = curiosity.get_telemetry()
            hints["curiosity_free_energy"] = telemetry.get("free_energy")
            hints["curiosity_intrinsic_reward"] = telemetry.get("intrinsic_reward")
        except Exception:
            pass
    manifold = extensions.get("manifold_bridge") or extensions.get("manifold")
    if manifold is not None:
        try:
            telemetry = manifold.get_telemetry()
            hints["manifold_isomorphisms"] = telemetry.get("isomorphisms_found")
            hints["manifold_embedding_dimensions"] = telemetry.get(
                "embedding_dimensions"
            )
        except Exception:
            pass
    devil = extensions.get("devil_advocate") or extensions.get("devil")
    if devil is not None:
        try:
            telemetry = devil.get_telemetry()
            hints["adversarial_challenges"] = telemetry.get("challenges_issued")
            hints["adversarial_intensity"] = telemetry.get("last_intensity")
        except Exception:
            pass
    if hints:
        hints["router"] = router
    return hints


def _canonical_router(router_name: str) -> Optional[str]:
    """Resolve aliases and short names to a canonical router identifier."""
    if not isinstance(router_name, str):
        return None
    key = router_name.strip().lower().replace("-", "_").replace(" ", "_")
    if key in ROUTER_NAMES:
        return key
    if key in _ROUTER_ALIASES:
        return _ROUTER_ALIASES[key]
    candidate = f"{key}_router"
    if candidate in ROUTER_NAMES:
        return candidate
    return None


def _report_discipline(
    router: str,
    latency_seconds: float,
    residual: float,
    success: bool,
) -> Dict[str, Any]:
    """Feed router friction back into the active discipline engine."""
    engine = _DRIVE_ENGINE_CTX.get()
    try:
        if engine is not None and hasattr(engine, "record_execution_telemetry"):
            return engine.record_execution_telemetry(
                router=router,
                latency_seconds=latency_seconds,
                residual=residual,
                success=success,
            )
        from drives import record_execution_telemetry  # type: ignore[import-not-found]

        return record_execution_telemetry(
            router=router,
            latency_seconds=latency_seconds,
            residual=residual,
            success=success,
        )
    except Exception as exc:  # Never let telemetry break a router
        logger.warning("Discipline telemetry unavailable: %s", exc)
        return {"router": router, "error": str(exc)}


def _execute_router(
    router: str,
    operation: str,
    payload: Dict[str, Any],
    precision_target: float = 1e-6,
    gpu_preferred: bool = False,
    live_required: bool = False,
) -> Dict[str, Any]:
    """Execute a router through an adapter or the deterministic simulator."""
    started = time.monotonic()
    workload = WorkloadProfile(
        router=router,
        items=_count_items(payload),
        payload_bytes=_json_size(payload),
        precision_target=float(precision_target),
        gpu_preferred=bool(gpu_preferred),
        live_required=bool(live_required),
    )
    specialization = specialization_engine.select(router, workload)
    graph, strategy = self_optimizer.mutate_task_graph(
        _build_task_graph(router, payload), router
    )
    prompt_structure = self_optimizer.mutate_prompt_structure(operation, strategy)

    adapter_error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    with _ADAPTER_LOCK:
        adapter = _ADAPTERS.get(router)

    if adapter is not None:
        try:
            candidate = adapter(
                {
                    "router": router,
                    "operation": operation,
                    "payload": payload,
                    "workload": asdict(workload),
                    "specialization": specialization,
                    "credentials": identity_context.redacted(),
                }
            )
            if isinstance(candidate, dict):
                raw = candidate
            else:
                adapter_error = "adapter returned a non-dict result"
        except Exception as exc:  # Fall back instead of failing the tool call
            adapter_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Adapter for %s failed; using simulated fallback: %s",
                router,
                adapter_error,
            )

    if raw is None:
        fraction = _hash_fraction(payload)
        residual = round(
            min(
                1.0,
                0.0005
                + 0.02 * fraction
                + min(0.05, workload.payload_bytes / 5_000_000.0),
            ),
            8,
        )
        simulated_latency = (
            0.001
            + min(0.5, workload.payload_bytes / 2_000_000.0)
            + fraction * 0.002
        )
        success = True
        artifacts: Dict[str, Any] = {
            "items_processed": workload.items,
            "simulated_units": max(1, workload.items),
            "backend": specialization["solver"],
        }
        execution_mode = "simulated_fallback"
        error = adapter_error
    else:
        success = bool(raw.get("success", True))
        residual = round(
            max(
                0.0,
                min(
                    1.0,
                    float(raw.get("numerical_residual", raw.get("residual", 0.0))),
                ),
            ),
            8,
        )
        simulated_latency = float(raw.get("latency_seconds", 0.0) or 0.0)
        candidate_artifacts = raw.get("artifacts", {})
        artifacts = candidate_artifacts if isinstance(candidate_artifacts, dict) else {}
        execution_mode = "live_adapter"
        raw_error = raw.get("error", adapter_error)
        error = str(raw_error) if raw_error else adapter_error

    latency = round(
        max(time.monotonic() - started, max(0.0, simulated_latency)), 6
    )
    optimizer_telemetry = self_optimizer.evaluate(router, latency, residual, success)
    discipline_telemetry = _report_discipline(router, latency, residual, success)

    result = RouterResult(
        router=router,
        operation=operation,
        success=success,
        latency_seconds=latency,
        numerical_residual=residual,
        execution_mode=execution_mode,
        artifacts=artifacts,
        error=error,
    ).to_dict()
    result.update(
        {
            "specialization": specialization,
            "task_graph": graph.to_dict(),
            "prompt_structure": prompt_structure,
            "optimizer": optimizer_telemetry,
            "discipline": discipline_telemetry,
            "credentials": identity_context.redacted(),
        }
    )
    extension_hints = _extension_hints(router)
    if extension_hints:
        result["extensions"] = extension_hints
    return result


def register_adapter(
    router_name: str,
    adapter: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    """Register a live adapter for a router.

    Args:
        router_name: Canonical router name or alias.
        adapter: Callable receiving the router context dict and returning a dict
            with optional ``success``, ``numerical_residual``, ``latency_seconds``,
            ``artifacts``, and ``error`` keys.

    Raises:
        ValueError: If the router name is unknown.
        TypeError: If the adapter is not callable.
    """
    canonical = _canonical_router(router_name)
    if canonical is None:
        raise ValueError(f"Unknown router '{router_name}'.")
    if not callable(adapter):
        raise TypeError("adapter must be callable.")
    with _ADAPTER_LOCK:
        _ADAPTERS[canonical] = adapter


def unregister_adapter(router_name: str) -> bool:
    """Remove a live adapter, returning True when one was removed."""
    canonical = _canonical_router(router_name)
    if canonical is None:
        return False
    with _ADAPTER_LOCK:
        return _ADAPTERS.pop(canonical, None) is not None


# ---------------------------------------------------------------------------
# Ten universal science routers (FastMCP tools)
# ---------------------------------------------------------------------------

@_tool
def run_hyper_specialized_quantum_swarm(
    qasm_batch: Optional[List[str]] = None,
    target_backends: Optional[List[str]] = None,
    precision_target: float = 1e-6,
) -> Dict[str, Any]:
    """Run a hyper-specialized quantum circuit swarm across target backends.

    Args:
        qasm_batch: OpenQASM programs to execute.
        target_backends: Backend identifiers; defaults to a local statevector simulator.
        precision_target: Target amplitude precision for convergence checks.

    Returns:
        Router telemetry including latency, numerical residual, specialization,
        task graph mutation, and discipline feedback.
    """
    payload = {
        "qasm_batch": list(qasm_batch or []),
        "target_backends": list(target_backends or ["statevector_sim"]),
        "precision_target": float(precision_target),
    }
    return _execute_router(
        "quantum_router",
        "run_hyper_specialized_quantum_swarm",
        payload,
        precision_target=float(precision_target),
        gpu_preferred=True,
    )


@_tool
def run_live_calibrated_atomistic_sim(
    atoms_batch: Optional[List[Dict[str, Any]]] = None,
    calculator_chain: Optional[List[str]] = None,
    live_sensor_feed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a live-calibrated atomistic simulation across a calculator chain.

    Args:
        atoms_batch: Atomic structures or structure references.
        calculator_chain: Calculators to compose, e.g. ``["mlip", "dft"]``.
        live_sensor_feed: Optional live calibration stream or endpoint metadata.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "atoms_batch": list(atoms_batch or []),
        "calculator_chain": list(calculator_chain or ["mlip", "dft"]),
        "live_sensor_feed": live_sensor_feed,
    }
    return _execute_router(
        "atomistic_router",
        "run_live_calibrated_atomistic_sim",
        payload,
        live_required=bool(live_sensor_feed),
        gpu_preferred=True,
    )


@_tool
def dispatch_segmented_hpc_pipeline(
    execution_segments: Optional[List[Dict[str, Any]]] = None,
    target_hpc_matrix: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Dispatch a segmented HPC pipeline across a matrix of clusters.

    Args:
        execution_segments: Pipeline segments with commands and resources.
        target_hpc_matrix: Scheduler/cluster identifiers.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "execution_segments": list(execution_segments or []),
        "target_hpc_matrix": list(target_hpc_matrix or ["local"]),
    }
    return _execute_router(
        "hpc_router",
        "dispatch_segmented_hpc_pipeline",
        payload,
        gpu_preferred=True,
    )


@_tool
def execute_precision_wetlab_protocol(
    protocol_rdf_graphs: Optional[List[Dict[str, Any]]] = None,
    facility_matrix: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a precision wet-lab protocol represented as RDF graphs.

    Args:
        protocol_rdf_graphs: RDF/JSON-LD protocol graphs.
        facility_matrix: Facility or instrument identifiers.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "protocol_rdf_graphs": list(protocol_rdf_graphs or []),
        "facility_matrix": list(facility_matrix or ["simulated_facility"]),
    }
    return _execute_router(
        "wetlab_router",
        "execute_precision_wetlab_protocol",
        payload,
    )


@_tool
def stream_ultra_fresh_sensor_telemetry(
    bbox: Optional[Any] = None,
    datetime_range: Optional[Dict[str, str]] = None,
    collections: Optional[List[str]] = None,
    auto_inject_simulators: bool = True,
) -> Dict[str, Any]:
    """Stream ultra-fresh sensor telemetry for a bounding box and time range.

    Args:
        bbox: Bounding box as ``[west, south, east, north]`` or a dict.
        datetime_range: ISO-8601 ``{"start": ..., "end": ...}`` range.
        collections: STAC collection identifiers.
        auto_inject_simulators: Inject synthetic feeds when live archives are
            unavailable.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "bbox": bbox,
        "datetime_range": dict(datetime_range or {}),
        "collections": list(collections or []),
        "auto_inject_simulators": bool(auto_inject_simulators),
    }
    return _execute_router(
        "sensing_router",
        "stream_ultra_fresh_sensor_telemetry",
        payload,
        live_required=not bool(auto_inject_simulators),
    )


@_tool
def orchestrate_multi_beamline_flyscans(
    scan_plans: Optional[List[Dict[str, Any]]] = None,
    beamline_nodes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Orchestrate synchronized flyscans across multiple beamline nodes.

    Args:
        scan_plans: Trajectory and detector plans per scan.
        beamline_nodes: Beamline node identifiers.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "scan_plans": list(scan_plans or []),
        "beamline_nodes": list(beamline_nodes or ["simulated_beamline"]),
    }
    return _execute_router(
        "beamline_router",
        "orchestrate_multi_beamline_flyscans",
        payload,
    )


@_tool
def execute_hyper_parallel_grid_swarm(
    executable_cmds: Optional[List[str]] = None,
    input_matrix: Optional[List[List[Any]]] = None,
    target_pools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a hyper-parallel grid swarm across volunteer compute pools.

    Args:
        executable_cmds: Sandboxed executable command templates.
        input_matrix: Cartesian inputs broadcast to the command set.
        target_pools: Grid pool identifiers.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "executable_cmds": list(executable_cmds or []),
        "input_matrix": list(input_matrix or []),
        "target_pools": list(target_pools or ["local_pool"]),
    }
    return _execute_router(
        "free_grid_router",
        "execute_hyper_parallel_grid_swarm",
        payload,
    )


@_tool
def solve_composite_multiphysics_pde(
    mesh_files: Optional[List[Any]] = None,
    physics_domains: Optional[List[str]] = None,
    coupled_solvers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Solve a coupled multiphysics PDE system on composite meshes.

    Args:
        mesh_files: Mesh paths or inline mesh descriptors.
        physics_domains: Coupled domains, e.g. ``["thermal", "structural"]``.
        coupled_solvers: Solver libraries participating in the coupling loop.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "mesh_files": list(mesh_files or []),
        "physics_domains": list(physics_domains or ["thermal", "structural"]),
        "coupled_solvers": list(coupled_solvers or ["fenics"]),
    }
    return _execute_router(
        "multiphysics_router",
        "solve_composite_multiphysics_pde",
        payload,
    )


@_tool
def simulate_kinematic_digital_twin_matrix(
    usd_scenes: Optional[List[Any]] = None,
    physics_engines: Optional[List[str]] = None,
    dt: float = 0.01,
) -> Dict[str, Any]:
    """Simulate a matrix of kinematic digital twins from USD scenes.

    Args:
        usd_scenes: USD scene paths or inline scene descriptors.
        physics_engines: Physics engine identifiers.
        dt: Integration timestep in seconds.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "usd_scenes": list(usd_scenes or []),
        "physics_engines": list(physics_engines or ["physx"]),
        "dt": float(dt),
    }
    return _execute_router(
        "digital_twin_router",
        "simulate_kinematic_digital_twin_matrix",
        payload,
        gpu_preferred=True,
    )


@_tool
def run_multiscale_biological_pipeline(
    pdb_ids: Optional[List[str]] = None,
    pipeline_steps: Optional[List[str]] = None,
    forcefields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run a multiscale biomolecular pipeline from structures to observables.

    Args:
        pdb_ids: Protein Data Bank identifiers.
        pipeline_steps: Ordered pipeline stage identifiers.
        forcefields: Forcefield families used by each stage.

    Returns:
        Router telemetry including residual, latency, and discipline feedback.
    """
    payload = {
        "pdb_ids": list(pdb_ids or []),
        "pipeline_steps": list(pipeline_steps or ["prepare", "simulate", "analyze"]),
        "forcefields": list(forcefields or ["amber"]),
    }
    return _execute_router(
        "biomolecular_router",
        "run_multiscale_biological_pipeline",
        payload,
        gpu_preferred=True,
    )


_ROUTER_FUNCTIONS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "quantum_router": run_hyper_specialized_quantum_swarm,
    "atomistic_router": run_live_calibrated_atomistic_sim,
    "hpc_router": dispatch_segmented_hpc_pipeline,
    "wetlab_router": execute_precision_wetlab_protocol,
    "sensing_router": stream_ultra_fresh_sensor_telemetry,
    "beamline_router": orchestrate_multi_beamline_flyscans,
    "free_grid_router": execute_hyper_parallel_grid_swarm,
    "multiphysics_router": solve_composite_multiphysics_pde,
    "digital_twin_router": simulate_kinematic_digital_twin_matrix,
    "biomolecular_router": run_multiscale_biological_pipeline,
}


# ---------------------------------------------------------------------------
# Dispatcher and telemetry exports
# ---------------------------------------------------------------------------

def dispatch_router(
    router_name: str,
    payload: Optional[Dict[str, Any]] = None,
    drive_engine: Optional[Any] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Dispatch a payload to one router without raising on bad input.

    Args:
        router_name: Canonical router name or alias.
        payload: Keyword payload matching the router tool signature.
        drive_engine: Optional discipline engine to receive telemetry.
        **kwargs: Additional payload keys merged over ``payload``.

    Returns:
        The router result dict, or a structured failure for unknown routers.
    """
    canonical = _canonical_router(router_name)
    if canonical is None:
        return {
            "router": router_name,
            "operation": "dispatch_router",
            "success": False,
            "execution_mode": "rejected",
            "error": f"Unknown router '{router_name}'.",
            "available_routers": list(ROUTER_NAMES),
        }

    function = _ROUTER_FUNCTIONS[canonical]
    arguments: Dict[str, Any] = dict(payload or {})
    arguments.update(kwargs)

    signature = inspect.signature(function)
    accepted = {
        key: value
        for key, value in arguments.items()
        if key in signature.parameters
    }
    ignored = sorted(set(arguments) - set(accepted))

    token = _DRIVE_ENGINE_CTX.set(drive_engine)
    try:
        result = function(**accepted)
    except Exception as exc:
        return {
            "router": canonical,
            "operation": canonical,
            "success": False,
            "execution_mode": "dispatcher_error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        _DRIVE_ENGINE_CTX.reset(token)

    if ignored:
        result["ignored_payload_keys"] = ignored
    return result


def describe_routers() -> List[Dict[str, Any]]:
    """Describe all registered routers and their live-adapter status."""
    with _ADAPTER_LOCK:
        adapted = set(_ADAPTERS)
    return [
        {
            "router": name,
            "operation": _ROUTER_FUNCTIONS[name].__name__,
            "doc": (_ROUTER_FUNCTIONS[name].__doc__ or "").strip().splitlines()[0],
            "live_adapter_registered": name in adapted,
        }
        for name in ROUTER_NAMES
    ]


def get_science_telemetry() -> Dict[str, Any]:
    """Return engine-wide telemetry for SASK state serialization."""
    with _ADAPTER_LOCK:
        adapters = sorted(_ADAPTERS)
    return {
        "available": True,
        "fastmcp_implementation": FASTMCP_IMPLEMENTATION,
        "fastmcp_available": FASTMCP_AVAILABLE,
        "router_count": len(ROUTER_NAMES),
        "routers": list(ROUTER_NAMES),
        "adapters": adapters,
        "optimizer": self_optimizer.telemetry(),
        "specialization": specialization_engine.describe(),
        "credentials": identity_context.redacted(),
        "extensions": get_extension_snapshot(),
    }


if __name__ == "__main__":
    _transport = os.environ.get("SASK_MCP_TRANSPORT", "stdio")
    logger.info(
        "Starting SASK 10-router science server via %s (%s).",
        FASTMCP_IMPLEMENTATION,
        _transport,
    )
    try:
        mcp.run(transport=_transport)
    except TypeError:
        mcp.run()
