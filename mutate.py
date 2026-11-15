"""Mutate Module: Script parsing and structured code/text transformations.

Provides AST-level analysis and safe transformations for Python source files,
as well as structured templating and textual transformation utilities.
"""

from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class TransformationResult:
    """Represents the outcome of a code or text transformation."""
    original: str
    transformed: str
    is_modified: bool
    diff: str = ""
    applied_rules: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


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


class CodeMutator:
    """Coordinates AST parsing and structured source code transformations."""

    @staticmethod
    def ensure_valid_syntax(source_code: str) -> bool:
        """Validate whether the given string is syntactically valid Python."""
        try:
            ast.parse(source_code)
            return True
        except SyntaxError:
            return False

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
