"""Autonomous crash-to-patch repair inside the mutable ring.

The engine classifies a captured traceback, synthesizes a minimal patch from a
small audited template set, and proves the patch against a caller-supplied
regression predicate before touching the working tree. Patches are only ever
written to mutable-ring paths (``sask_v2/ring``, ``sask_v2/templates``,
``sask_v2/adapters``); the boundary rejects everything else. Repeated failures
of the same signature are blacklisted in the encrypted knowledge store.

Rollback is a separate, explicitly gated operation: the supervisor (or an
operator) must pass ``allow=True``, the tree must be clean, and a recovery
branch is always created before the hard reset.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import boundary
from .knowledge import KnowledgeStore

PATCH_IMPORT = "import_error"
PATCH_SCHEMA = "schema_error"
ACTION_ROTATE = "rotate"
ACTION_REVERT = "revert"
ACTION_NONE = "none"
ACTION_PATCH = "patch"

_IMPORT_MODULE = re.compile(r"No module named '([^']+)'")


def classify_traceback(text: str) -> str:
    lowered = (text or "").lower()
    if "modulenotfounderror" in lowered or "no module named" in lowered:
        return PATCH_IMPORT
    if "keyerror" in lowered:
        return PATCH_SCHEMA
    if "429" in lowered or "rate limit" in lowered or "ratelimit" in lowered:
        return "rate_limit"
    if "410" in lowered or "deprecat" in lowered or "no longer supported" in lowered:
        return "deprecated_endpoint"
    if any(token in lowered for token in ("connectionreset", "connectionerror", "timeout", "timed out", "temporary failure")):
        return "connection"
    if "syntaxerror" in lowered:
        return "syntax"
    return "unknown"


def failure_signature(kind: str, text: str) -> str:
    tail = [line.strip() for line in (text or "").splitlines() if line.strip()]
    excerpt = tail[-1][:200] if tail else kind
    return hashlib.sha256(f"{kind}|{excerpt}".encode("utf-8")).hexdigest()[:24]


class _OptionalImports(ast.NodeTransformer):
    """Wrap top-level imports so a missing optional dependency degrades to None."""

    @staticmethod
    def _assign(name: str, line: int, col: int) -> ast.Assign:
        return ast.Assign(
            targets=[ast.Name(id=name, ctx=ast.Store())],
            value=ast.Constant(value=None),
            lineno=line,
            col_offset=col,
        )

    @staticmethod
    def _handler(names: List[str], line: int, col: int) -> ast.ExceptHandler:
        body = [_OptionalImports._assign(name, line, col) for name in names]
        return ast.ExceptHandler(
            type=ast.Name(id="ImportError", ctx=ast.Load()),
            name=None,
            body=body,
            lineno=line,
            col_offset=col,
        )

    def visit_Import(self, node: ast.Import) -> ast.AST:
        self.generic_visit(node)
        if len(node.names) != 1:
            return node
        alias = node.names[0]
        target = alias.asname or alias.name.split(".")[0]
        try_node = ast.Try(
            body=[node],
            handlers=[self._handler([target], node.lineno, node.col_offset)],
            orelse=[],
            finalbody=[],
        )
        return ast.copy_location(try_node, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        self.generic_visit(node)
        names = [alias.asname or alias.name for alias in node.names if alias.name != "*"]
        if not names:
            return node
        try_node = ast.Try(
            body=[node],
            handlers=[self._handler(names, node.lineno, node.col_offset)],
            orelse=[],
            finalbody=[],
        )
        return ast.copy_location(try_node, node)


_HELPER_SOURCE = '''\
class _SaskMissing:
    """Indexable, falsy stand-in for an absent upstream JSON field."""

    def __getitem__(self, key):
        return self

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __str__(self):
        return ""

    def __repr__(self):
        return "<missing>"

    def get(self, key, default=None):
        return default


def _sask_get(obj, key, default=None):
    try:
        return obj[key]
    except (KeyError, IndexError, TypeError):
        return _SaskMissing()
'''


class _SafeSubscripts(ast.NodeTransformer):
    """Route string subscript reads through a defensive helper."""

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            call = ast.Call(
                func=ast.Name(id="_sask_get", ctx=ast.Load()),
                args=[node.value, node.slice],
                keywords=[],
            )
            return ast.copy_location(call, node)
        return node


@dataclass
class PatchPlan:
    kind: str
    signature: str
    action: str
    description: str
    patched_source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "signature": self.signature,
            "action": self.action,
            "description": self.description,
            "patched_bytes": len(self.patched_source),
        }


class RepairEngine:
    """Classify, synthesize, verify, and apply minimal adapter repairs."""

    def __init__(self, store: KnowledgeStore, max_attempts: int = 3) -> None:
        self.store = store
        self.max_attempts = max(1, int(max_attempts))

    # -- planning ----------------------------------------------------------

    def plan(self, traceback_text: str, source: str, file_name: str = "<mutable>") -> PatchPlan:
        kind = classify_traceback(traceback_text)
        signature = failure_signature(kind, traceback_text)

        if kind == PATCH_IMPORT:
            patched = self._patch_imports(source, file_name)
            action = ACTION_PATCH if patched != source else ACTION_NONE
            return PatchPlan(kind, signature, action, "guard optional import", patched)
        if kind == PATCH_SCHEMA:
            patched = self._patch_schema(source, file_name)
            action = ACTION_PATCH if patched != source else ACTION_NONE
            return PatchPlan(kind, signature, action, "heal upstream JSON schema reads", patched)
        if kind in {"rate_limit", "deprecated_endpoint", "connection"}:
            return PatchPlan(kind, signature, ACTION_ROTATE, "delegate to provider mesh", "")
        if kind == "syntax":
            return PatchPlan(kind, signature, ACTION_REVERT, "revert generated template to last known good", "")
        return PatchPlan(kind, signature, ACTION_NONE, "no safe autonomous patch", "")

    @staticmethod
    def _patch_imports(source: str, file_name: str) -> str:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source
        patched = ast.fix_missing_locations(_OptionalImports().visit(tree))
        return ast.unparse(patched)

    @staticmethod
    def _patch_schema(source: str, file_name: str) -> str:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source
        patched = ast.fix_missing_locations(_SafeSubscripts().visit(tree))
        helper = ast.parse(_HELPER_SOURCE).body
        body = list(getattr(patched, "body", []))
        insert_at = 1 if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str) else 0
        patched.body = body[:insert_at] + helper + body[insert_at:]
        ast.fix_missing_locations(patched)
        return ast.unparse(patched)

    # -- application -------------------------------------------------------

    def attempt(
        self,
        traceback_text: str,
        source: str,
        mutable_path: Path | str,
        regression: Callable[[str], bool],
        file_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        plan = self.plan(traceback_text, source, file_name or Path(mutable_path).name)
        if self.store.is_blacklisted(plan.signature):
            return {"status": "blacklisted", "plan": plan.to_dict()}
        if plan.action == ACTION_ROTATE:
            return {"status": "delegated", "plan": plan.to_dict()}
        if plan.action != ACTION_PATCH:
            record = self.store.remember_failure(plan.signature, plan.description, self.max_attempts)
            return {
                "status": "unpatched",
                "plan": plan.to_dict(),
                "attempts": record.get("attempts"),
                "blacklisted": record.get("blacklisted"),
            }
        try:
            target = boundary.assert_mutable(mutable_path)
        except boundary.BoundaryViolation as exc:
            record = self.store.remember_failure(plan.signature, str(exc), self.max_attempts)
            return {"status": "boundary_denied", "detail": str(exc), "plan": plan.to_dict()}

        try:
            compile(plan.patched_source, str(target), "exec")
        except SyntaxError as exc:
            record = self.store.remember_failure(plan.signature, f"syntax:{exc.msg}", self.max_attempts)
            return {"status": "patch_invalid", "detail": exc.msg, "blacklisted": record.get("blacklisted")}

        with tempfile.TemporaryDirectory(prefix="sask-repair-") as raw:
            probe = Path(raw) / target.name
            probe.write_text(plan.patched_source, encoding="utf-8")
            try:
                verified = bool(regression(plan.patched_source))
            except Exception:
                verified = False
        if not verified:
            record = self.store.remember_failure(plan.signature, "regression_failed", self.max_attempts)
            return {
                "status": "reverted",
                "detail": "patched source failed the regression probe",
                "blacklisted": record.get("blacklisted"),
                "plan": plan.to_dict(),
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text(plan.patched_source, encoding="utf-8")
        os.replace(tmp, target)
        self.store.upsert(
            {
                "kind": "repair_patch",
                "signature": plan.signature,
                "kind_detail": plan.kind,
                "file": target.name,
            },
            signature_from=f"repair|{plan.signature}",
            utility_delta=1.0,
        )
        return {"status": "applied", "target": str(target), "plan": plan.to_dict()}

    # -- rollback ----------------------------------------------------------

    @staticmethod
    def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def rollback_to_tag(self, repo_root: Path | str, tag: str, allow: bool = False) -> Dict[str, Any]:
        root = Path(repo_root).resolve()
        if not allow:
            return {"performed": False, "reason": "rollback_disabled"}
        if not (root / ".git").exists():
            return {"performed": False, "reason": "not_a_repo"}
        verify = self._git(root, "rev-parse", "--verify", f"{tag}^{{commit}}")
        if verify.returncode != 0:
            return {"performed": False, "reason": "unknown_tag", "tag": tag}
        status = self._git(root, "status", "--porcelain", "--untracked-files=no")
        if status.stdout.strip():
            return {"performed": False, "reason": "dirty_worktree"}
        backup = f"sask-pre-rollback-{int(time.time())}"
        self._git(root, "branch", backup, "HEAD")
        reset = self._git(root, "reset", "--hard", tag)
        return {
            "performed": reset.returncode == 0,
            "tag": tag,
            "backup_branch": backup,
            "detail": (reset.stderr or reset.stdout).strip()[:300],
        }
