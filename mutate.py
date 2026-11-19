"""Mutate Module: Surgical Abstract Syntax Tree (AST) Modifier.

Provides surgical parsing, programmatic node injection, function logic mutation,
AST validation, source code compilation, and disk persistence while preserving
clean Python structure and syntax guarantees.

Integrates with drives.py for discipline-aware mutation pressure scaling.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union


@dataclass
class TransformationResult:
    """Represents the outcome of a code or text transformation."""
    original: str
    transformed: str
    is_modified: bool
    diff: str = ""
    applied_rules: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    target_path: Optional[str] = None
    backup_path: Optional[str] = None


class AstDocstringNormalizer(ast.NodeTransformer):
    """AST transformer that normalizes or attaches module docstrings."""

    def __init__(self, default_docstring: str) -> None:
        super().__init__()
        self.default_docstring = default_docstring
        self.docstring_added = False

    def visit_Module(self, node: ast.Module) -> ast.Module:
        has_doc = (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        )
        if not has_doc:
            doc_node = ast.Expr(value=ast.Constant(value=self.default_docstring))
            node.body.insert(0, doc_node)
            self.docstring_added = True
        return self.generic_visit(node)  # type: ignore[return-value]


class CodeInspector:
    """Inspects Python code structures using standard AST."""

    @staticmethod
    def inspect(source_code: str) -> Dict[str, Any]:
        """Extract high-level structural metrics from Python code."""
        tree = ast.parse(source_code)
        imports: List[str] = []
        functions: List[str] = []
        classes: List[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}" if module else alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)

        return {
            "imports": sorted(set(imports)),
            "functions": functions,
            "classes": classes,
            "line_count": len(source_code.splitlines()),
        }


class SurgicalAstModifier:
    """Surgical Abstract Syntax Tree (AST) parser, transformer, and code generator."""

    @staticmethod
    def parse_source(source_code: str) -> ast.Module:
        """Parse source code string into an AST Module node with strict syntax checks."""
        try:
            return ast.parse(source_code)
        except SyntaxError as exc:
            raise ValueError(f"Failed to parse source code into AST: {exc}") from exc

    @classmethod
    def parse_file(cls, file_path: Union[str, Path]) -> ast.Module:
        """Read a Python file from disk and parse its contents into an AST Module node."""
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Target Python file does not exist: {path}")
        content = path.read_text(encoding="utf-8")
        return cls.parse_source(content)

    @staticmethod
    def compile_tree(tree: ast.AST) -> str:
        """Compile an AST tree back into clean, formatted Python source code."""
        ast.fix_missing_locations(tree)
        unparsed = ast.unparse(tree)
        # Ensure trailing newline for standard Python file structure
        if not unparsed.endswith("\n"):
            unparsed += "\n"
        return unparsed

    @staticmethod
    def find_function(
        tree: ast.Module,
        func_name: str,
    ) -> Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]]:
        """Find a top-level or method function node matching func_name."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                return node
        return None

    @staticmethod
    def find_class(tree: ast.Module, class_name: str) -> Optional[ast.ClassDef]:
        """Find a class definition node matching class_name."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
        return None

    @classmethod
    def inject_function(
        cls,
        tree: ast.Module,
        function_def: Union[str, ast.FunctionDef, ast.AsyncFunctionDef],
        position: Optional[int] = None,
    ) -> Union[ast.FunctionDef, ast.AsyncFunctionDef]:
        """Surgically inject a new function node into an AST Module.

        Args:
            tree: Target AST Module.
            function_def: Python source code of function or an AST function node.
            position: Insertion index in module body (None appends to end).
        """
        if isinstance(function_def, str):
            parsed_func = ast.parse(function_def.strip())
            if not parsed_func.body or not isinstance(
                parsed_func.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                raise ValueError("Provided string does not define a valid function.")
            func_node = parsed_func.body[0]
        else:
            func_node = function_def

        if position is not None:
            tree.body.insert(position, func_node)
        else:
            tree.body.append(func_node)

        ast.fix_missing_locations(tree)
        return func_node

    @classmethod
    def inject_statement_into_function(
        cls,
        tree: ast.Module,
        func_name: str,
        statement_code: str,
        index: int = 0,
    ) -> bool:
        """Surgically inject one or more statements into a function's body block.

        Args:
            tree: Target AST Module.
            func_name: Name of the function to modify.
            statement_code: Python code string representing statements to insert.
            index: Position in the function body to insert statements (default: beginning).
        """
        func_node = cls.find_function(tree, func_name)
        if not func_node:
            return False

        parsed_stmt = ast.parse(statement_code.strip())
        if not parsed_stmt.body:
            return False

        # If index is 0 and the first statement is a docstring, insert after docstring
        target_index = index
        if target_index == 0 and func_node.body:
            first_stmt = func_node.body[0]
            if (
                isinstance(first_stmt, ast.Expr)
                and isinstance(first_stmt.value, ast.Constant)
                and isinstance(first_stmt.value.value, str)
            ):
                target_index = 1

        for i, stmt in enumerate(parsed_stmt.body):
            func_node.body.insert(target_index + i, stmt)

        ast.fix_missing_locations(tree)
        return True

    @classmethod
    def modify_function_body(
        cls,
        tree: ast.Module,
        func_name: str,
        body_transformer: Callable[[List[ast.stmt]], List[ast.stmt]],
    ) -> bool:
        """Apply a functional transformation callback to the statements of a function body."""
        func_node = cls.find_function(tree, func_name)
        if not func_node:
            return False

        new_body = body_transformer(func_node.body)
        if not new_body:
            raise ValueError(f"Function body for '{func_name}' cannot be empty.")

        func_node.body = new_body
        ast.fix_missing_locations(tree)
        return True

    @classmethod
    def replace_function(
        cls,
        tree: ast.Module,
        func_name: str,
        replacement_code: str,
    ) -> bool:
        """Replace an existing function definition with new source code."""
        parsed_replacement = ast.parse(replacement_code.strip())
        if not parsed_replacement.body or not isinstance(
            parsed_replacement.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            raise ValueError("Replacement code must define a function.")

        new_node = parsed_replacement.body[0]

        for i, node in enumerate(tree.body):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                tree.body[i] = new_node
                ast.fix_missing_locations(tree)
                return True

        return False

    @classmethod
    def mutate_file(
        cls,
        file_path: Union[str, Path],
        mutator_fn: Callable[[ast.Module], Any],
        create_backup: bool = True,
    ) -> TransformationResult:
        """Surgically modify a Python file on disk using an AST mutator callback.

        Steps:
        1. Parse existing file into AST.
        2. Apply mutator callback.
        3. Compile modified AST to source code and verify syntax.
        4. Optionally create a timestamped or .bak backup file.
        5. Write back to disk atomically.
        """
        path = Path(file_path).resolve()
        original_source = path.read_text(encoding="utf-8")
        tree = cls.parse_source(original_source)

        # Apply the mutation
        mutator_fn(tree)

        # Compile and validate syntax
        modified_source = cls.compile_tree(tree)
        if not CodeMutator.ensure_valid_syntax(modified_source):
            raise ValueError("AST mutation produced invalid Python syntax. Write aborted.")

        is_modified = original_source != modified_source
        backup_path: Optional[str] = None

        if is_modified and create_backup:
            bak = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, bak)
            backup_path = str(bak)

        if is_modified:
            # Atomic file write
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(modified_source, encoding="utf-8")
            os.replace(temp_path, path)

        diff = CodeMutator.generate_diff(original_source, modified_source, filename=path.name)

        return TransformationResult(
            original=original_source,
            transformed=modified_source,
            is_modified=is_modified,
            diff=diff,
            target_path=str(path),
            backup_path=backup_path,
        )


class ContentAddressedCache:
    """Thread-safe memo keyed by a content hash of the exact inputs.

    The digest includes the interpreter's major/minor version so a cached
    verdict from a different Python grammar can never leak across runtimes.
    """

    def __init__(self, capacity: int = 4096, namespace: str = "syntax") -> None:
        self.capacity = max(16, int(capacity))
        self.namespace = str(namespace)
        self._entries: Dict[str, bool] = {}
        self._order: List[str] = []
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def digest(self, payload: str) -> str:
        runtime = f"{sys.version_info[0]}.{sys.version_info[1]}"
        material = "\x1f".join((self.namespace, runtime, payload))
        return hashlib.blake2b(
            material.encode("utf-8", "replace"), digest_size=20
        ).hexdigest()

    def get(self, payload: str) -> Optional[bool]:
        key = self.digest(payload)
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self._misses += 1
            else:
                self._hits += 1
            return value

    def set(self, payload: str, value: bool) -> None:
        key = self.digest(payload)
        with self._lock:
            if key not in self._entries:
                self._order.append(key)
            self._entries[key] = value
            while len(self._order) > self.capacity:
                evicted = self._order.pop(0)
                self._entries.pop(evicted, None)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "entries": len(self._entries),
                "capacity": self.capacity,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._order.clear()


class CodeMutator:
    """Coordinates AST parsing and structured source code transformations."""

    _syntax_cache = ContentAddressedCache(namespace="ast-parse")

    @classmethod
    def ensure_valid_syntax(cls, source_code: str) -> bool:
        """Validate whether the given string is syntactically valid Python."""
        cached = cls._syntax_cache.get(source_code)
        if cached is not None:
            return cached
        try:
            ast.parse(source_code)
            valid = True
        except (SyntaxError, ValueError):
            valid = False
        cls._syntax_cache.set(source_code, valid)
        return valid

    @classmethod
    def syntax_cache_stats(cls) -> Dict[str, int]:
        return cls._syntax_cache.stats()

    @classmethod
    def apply_docstring(
        cls,
        source_code: str,
        docstring: str,
    ) -> TransformationResult:
        """Ensure the module has a valid top-level docstring."""
        if not cls.ensure_valid_syntax(source_code):
            raise SyntaxError("Cannot transform code with syntax errors.")

        tree = ast.parse(source_code)
        transformer = AstDocstringNormalizer(default_docstring=docstring)
        new_tree = transformer.visit(tree)
        ast.fix_missing_locations(new_tree)

        new_source = ast.unparse(new_tree)
        modified = transformer.docstring_added or (new_source != source_code)
        diff = cls.generate_diff(source_code, new_source)

        return TransformationResult(
            original=source_code,
            transformed=new_source,
            is_modified=modified,
            diff=diff,
            applied_rules=["ensure_module_docstring"] if transformer.docstring_added else [],
        )

    @classmethod
    def replace_symbols(
        cls,
        source_code: str,
        symbol_map: Dict[str, str],
    ) -> TransformationResult:
        """Rename identifiers in source code using token/regex boundary matching."""
        if not cls.ensure_valid_syntax(source_code):
            raise SyntaxError("Cannot transform code with syntax errors.")

        applied_rules: List[str] = []
        transformed = source_code

        for old_name, new_name in symbol_map.items():
            pattern = rf"\b{re.escape(old_name)}\b"
            if re.search(pattern, transformed):
                transformed = re.sub(pattern, new_name, transformed)
                applied_rules.append(f"rename_{old_name}_to_{new_name}")

        modified = transformed != source_code
        if modified and not cls.ensure_valid_syntax(transformed):
            raise ValueError("Mutation resulted in invalid Python syntax; reverted.")

        diff = cls.generate_diff(source_code, transformed)
        return TransformationResult(
            original=source_code,
            transformed=transformed,
            is_modified=modified,
            diff=diff,
            applied_rules=applied_rules,
        )

    @staticmethod
    def generate_diff(original: str, transformed: str, filename: str = "source.py") -> str:
        """Compute a unified diff representation between original and transformed code."""
        orig_lines = original.splitlines(keepends=True)
        trans_lines = transformed.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                orig_lines,
                trans_lines,
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
            )
        )
        return "".join(diff_lines)


class DisciplineAwareMutator:
    """Applies discipline-scaled mutation pressure from the Unified Discipline Engine.

    Wraps SurgicalAstModifier and scales injection/transformation aggressiveness
    based on the current mutation pressure reported by drives.py.
    """

    @staticmethod
    def _get_mutation_pressure(discipline_override: Optional[float] = None) -> float:
        """Retrieve current mutation pressure from drives module (graceful fallback)."""
        if discipline_override is not None:
            return max(0.0, min(1.0, discipline_override))
        try:
            from drives import get_mutation_pressure  # type: ignore[import]
            return get_mutation_pressure()
        except Exception:
            return 0.5  # Neutral pressure if drives unavailable

    @classmethod
    def discipline_aware_mutate(
        cls,
        source: str,
        discipline_override: Optional[float] = None,
        inject_marker: bool = True,
    ) -> TransformationResult:
        """
        Perform a discipline-scaled AST mutation on a source code string.

        Mutation aggressiveness is determined by get_mutation_pressure():
          - pressure >= 0.75: aggressive — inject both a telemetry marker and a
            normalised docstring.
          - pressure >= 0.40: moderate — inject a docstring only.
          - pressure <  0.40: conservative — validate syntax only, no structural
            changes.

        Args:
            source: Python source code string to mutate.
            discipline_override: Optional float [0.0–1.0] to bypass drives lookup.
            inject_marker: Whether to inject a telemetry marker comment (default True).

        Returns:
            TransformationResult capturing original/transformed source and diff.
        """
        pressure = cls._get_mutation_pressure(discipline_override)

        if not CodeMutator.ensure_valid_syntax(source):
            raise SyntaxError("discipline_aware_mutate: source code has syntax errors.")

        if pressure >= 0.75:
            # Aggressive: inject docstring + telemetry marker
            result = CodeMutator.apply_docstring(
                source,
                docstring=f"# [SASK-MUTATE] Discipline pressure={pressure:.2f} — aggressive transformation applied.",
            )
            transformed = result.transformed
            if inject_marker:
                marker = f"\n# [SASK-DRIVES] mutation_pressure={pressure:.4f}\n"
                if marker.strip() not in transformed:
                    transformed = transformed.rstrip() + marker
            applied = ["aggressive_docstring_injection", "telemetry_marker"]
        elif pressure >= 0.40:
            # Moderate: docstring normalisation only
            result = CodeMutator.apply_docstring(
                source,
                docstring="# [SASK-MUTATE] Discipline-normalised module.",
            )
            transformed = result.transformed
            applied = ["moderate_docstring_normalisation"]
        else:
            # Conservative: syntax check only, no structural changes
            transformed = source
            applied = ["conservative_syntax_check_only"]

        is_modified = transformed != source
        diff = CodeMutator.generate_diff(source, transformed)

        return TransformationResult(
            original=source,
            transformed=transformed,
            is_modified=is_modified,
            diff=diff,
            applied_rules=applied,
            metadata={"mutation_pressure": pressure},
        )


class TextMutator:
    """Performs structured template substitutions and text block manipulation."""

    @staticmethod
    def render_template(template: str, context: Dict[str, Any]) -> str:
        """Substitute {{ variable }} placeholders using context dictionary."""
        def repl(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            return str(context.get(key, match.group(0)))

        return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, template)

    @staticmethod
    def inject_header(text: str, header: str, comment_prefix: str = "# ") -> str:
        """Inject a structured header banner at the top of a document."""
        formatted_header = "\n".join(
            f"{comment_prefix}{line}".rstrip() for line in header.strip().splitlines()
        )
        return f"{formatted_header}\n\n{text.lstrip()}"
