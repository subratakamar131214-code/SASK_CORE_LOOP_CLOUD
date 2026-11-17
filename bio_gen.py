"""Bio-Gen Module: Transparent Lightweight Bio-Gen Replicator.

Dynamically spawns specialized worker harnesses tailored to any arbitrary goal,
executing in isolated subprocesses and merging results back into the main tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union


class HarnessVector(str, Enum):
    """Execution vector taxonomy for spawned harnesses."""
    FORMAL_PROOF      = "FORMAL_PROOF"       # Lean 4 theorem proving & logic formalization
    ALGORITHMIC_CODE  = "ALGORITHMIC_CODE"   # Code synthesis, mutation, benchmarking
    FRONTIER_EXPLORE  = "FRONTIER_EXPLORE"   # Data mining, simulation, generative problem-solving


@dataclass
class HarnessResult:
    """Merged output from a completed harness subprocess."""
    harness_id: str
    vector: HarnessVector
    goal_id: str
    success: bool
    output: str
    error: str
    exit_code: int
    duration_seconds: float
    artifacts: Dict[str, Any] = field(default_factory=dict)
    merged_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dict for telemetry and manifest handoff."""
        return {
            "harness_id": self.harness_id,
            "vector": self.vector.value,
            "goal_id": self.goal_id,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "artifacts": self.artifacts,
            "merged_at": self.merged_at,
        }


class BioGenReplicator:
    """Spawns and manages isolated lightweight worker harnesses for arbitrary goal execution."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        default_timeout: float = 60.0,
    ) -> None:
        self.workspace_root = workspace_root or Path.cwd()
        self.default_timeout = default_timeout
        self._harness_registry: Dict[str, HarnessResult] = {}

    def spawn_specialized_harness(
        self,
        goal_id: str,
        vector: Union[HarnessVector, str],
        payload: str,
        timeout: Optional[float] = None,
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Spawn an isolated harness subprocess tailored to the goal's execution vector.

        Args:
            goal_id: Unique goal identifier from the ingestion pipeline.
            vector: Execution vector (formal proof / code synthesis / frontier explore).
                    Accepts either a HarnessVector or its string value.
            payload: Python code or shell command to execute inside the harness.
            timeout: Subprocess timeout in seconds.
            env_overrides: Optional environment variable overrides.

        Returns:
            JSON-safe dict describing the merged harness result.
        """
        if isinstance(vector, str):
            try:
                vector = HarnessVector(vector.upper())
            except ValueError:
                vector = HarnessVector.FRONTIER_EXPLORE

        harness_id = f"harness_{goal_id}_{vector.value}_{int(time.monotonic() * 1000)}"
        effective_timeout = timeout or self.default_timeout
        start_time = time.monotonic()

        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        env["SASK_HARNESS_ID"] = harness_id
        env["SASK_GOAL_ID"] = goal_id
        env["SASK_VECTOR"] = vector.value

        # Write payload to a temporary script file for clean subprocess isolation
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix=f"sask_{vector.value.lower()}_",
            dir=tempfile.gettempdir(),
            delete=False,
            encoding="utf-8",
        ) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(payload)

        try:
            proc = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=str(self.workspace_root),
                env=env,
                check=False,
            )
            duration = round(time.monotonic() - start_time, 4)
            result = HarnessResult(
                harness_id=harness_id,
                vector=vector,
                goal_id=goal_id,
                success=(proc.returncode == 0),
                output=proc.stdout,
                error=proc.stderr,
                exit_code=proc.returncode,
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired as exc:
            duration = round(time.monotonic() - start_time, 4)
            result = HarnessResult(
                harness_id=harness_id,
                vector=vector,
                goal_id=goal_id,
                success=False,
                output="",
                error=f"[BIO-GEN] Harness timed out after {effective_timeout}s.",
                exit_code=-1,
                duration_seconds=duration,
            )
        except Exception as exc:
            duration = round(time.monotonic() - start_time, 4)
            result = HarnessResult(
                harness_id=harness_id,
                vector=vector,
                goal_id=goal_id,
                success=False,
                output="",
                error=f"[BIO-GEN] Spawn failed: {exc}",
                exit_code=-1,
                duration_seconds=duration,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return self._merge_result(result).to_dict()

    def _merge_result(self, result: HarnessResult) -> HarnessResult:
        """Merge harness result back into the main registry."""
        result.merged_at = datetime.now(timezone.utc).isoformat()
        self._harness_registry[result.harness_id] = result
        return result

    def get_all_results(self) -> List[HarnessResult]:
        """Return all merged harness results."""
        return list(self._harness_registry.values())

    def get_results_by_goal(self, goal_id: str) -> List[HarnessResult]:
        """Return all harness results belonging to a given goal."""
        return [r for r in self._harness_registry.values() if r.goal_id == goal_id]

    def get_summary(self) -> Dict[str, Any]:
        """Return aggregate metrics across all spawned harnesses."""
        total = len(self._harness_registry)
        succeeded = sum(1 for r in self._harness_registry.values() if r.success)
        return {
            "total_harnesses": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "success_rate": round(succeeded / total, 4) if total > 0 else 1.0,
        }
