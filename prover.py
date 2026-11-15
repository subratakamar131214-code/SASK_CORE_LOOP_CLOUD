"""Prover Module: Subprocess execution interface for external tools and compilers.

Handles command execution, external compiler/linter invocations, timeout safety,
and structured output verification across platform environments.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class ExecutionResult:
    """Structured result from an external subprocess invocation."""
    command: List[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Returns True if the process completed with an exit code of 0."""
        return self.exit_code == 0 and not self.timed_out


class ProverInterface:
    """Subprocess execution coordinator with timeout management and isolation."""

    def __init__(self, default_timeout: float = 60.0) -> None:
        self.default_timeout = default_timeout

    @staticmethod
    def resolve_executable(name: str) -> Optional[str]:
        """Locate an executable binary within system PATH or environment."""
        resolved = shutil.which(name)
        if resolved:
            return resolved

        # Fallback for Python binaries when running in virtual or UV environments
        if name in ("python", "python3"):
            return sys.executable

        return None

    def execute(
        self,
        args: List[str],
        cwd: Optional[Union[str, Path]] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> ExecutionResult:
        """Execute a process safely with structured output capture and timeout safeguards."""
        effective_timeout = timeout if timeout is not None else self.default_timeout
        start_time = time.monotonic()

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        effective_cwd = str(cwd) if cwd else None

        # Resolve primary executable if necessary
        if args:
            binary = self.resolve_executable(args[0])
            if binary:
                args = [binary] + args[1:]

        try:
            process = subprocess.run(
                args,
                cwd=effective_cwd,
                env=merged_env,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
            duration = time.monotonic() - start_time
            return ExecutionResult(
                command=args,
                exit_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                duration_seconds=round(duration, 4),
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start_time
            stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout.decode() if exc.stdout else "")
            stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr.decode() if exc.stderr else "")
            return ExecutionResult(
                command=args,
                exit_code=-1,
                stdout=stdout,
                stderr=stderr + f"\n[PROVER ERROR] Process timed out after {effective_timeout}s.",
                duration_seconds=round(duration, 4),
                timed_out=True,
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return ExecutionResult(
                command=args,
                exit_code=-1,
                stdout="",
                stderr=f"[PROVER ERROR] Invocation failed: {exc}",
                duration_seconds=round(duration, 4),
                timed_out=False,
            )

    def verify_python_syntax(self, file_path: Union[str, Path]) -> ExecutionResult:
        """Validate Python syntax using py_compile via subprocess."""
        path_str = str(Path(file_path).resolve())
        args = [sys.executable, "-m", "py_compile", path_str]
        return self.execute(args)

    def run_unit_tests(
        self,
        test_target: str = "discover",
        start_dir: str = ".",
        pattern: str = "test*.py",
    ) -> ExecutionResult:
        """Execute automated tests using Python's standard unittest runner."""
        if test_target == "discover":
            args = [sys.executable, "-m", "unittest", "discover", "-s", start_dir, "-p", pattern]
        else:
            args = [sys.executable, "-m", "unittest", test_target]
        return self.execute(args)
