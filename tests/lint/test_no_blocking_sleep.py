"""Lint-style guard: forbid blocking `time.sleep` in async functions."""

from __future__ import annotations

import ast
from pathlib import Path


def _workspace_root() -> Path:
    # tests/lint/test_no_blocking_sleep.py -> ../../..
    return Path(__file__).resolve().parents[2]


def _violations(root: Path) -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    for path in root.rglob("*.py"):
        if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception:
            continue

        class _Visitor(ast.NodeVisitor):
            def __init__(self):
                self.async_stack: list[str] = []

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self.async_stack.append(node.name)
                self.generic_visit(node)
                self.async_stack.pop()

            def visit_Call(self, node: ast.Call):
                if self.async_stack:
                    fn = node.func
                    if (
                        isinstance(fn, ast.Attribute)
                        and fn.attr == "sleep"
                        and isinstance(fn.value, ast.Name)
                        and fn.value.id == "time"
                    ):
                        rel = str(path.relative_to(root))
                        violations.append((rel, node.lineno, self.async_stack[-1]))
                self.generic_visit(node)

        _Visitor().visit(tree)
    return violations


def test_no_blocking_sleep_inside_async_functions():
    violations = _violations(_workspace_root())
    assert not violations, "Blocking time.sleep in async context:\n" + "\n".join(
        f"- {path}:{lineno} in async {func}()"
        for path, lineno, func in violations
    )

