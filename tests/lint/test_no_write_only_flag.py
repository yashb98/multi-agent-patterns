"""Lint guard: forbid write-only `_-prefixed` dict-key flags in
`jobpulse/ats_adapters/` + `jobpulse/form_engine/`.

Pattern (`pipeline-bugs.md` S12 W-12.1, theme #4):

    custom_answers["_cv_pre_uploaded"] = True   # producer
    # …no consumer reads it — risk: double-CV-upload because the
    # `file_uploader` never checks the flag.

The audit caught `_cv_pre_uploaded` in `native_form_filler.py:3322`,
written by `smartrecruiters.py:43` (`pre_fill`) but never read by
`form_engine/file_uploader.py`. The shape recurs whenever an adapter or
form-engine module stashes a marker on the `custom_answers` dict
intending to influence a downstream filler — and the downstream filler
forgets to read it.

This guard enforces zero write-only flags in the two scoped subdirs:
`jobpulse/ats_adapters/` and `jobpulse/form_engine/`. The audit's actual
S12 fix in `native_form_filler.py` is tracked separately
(`pipeline-bugs.md` S12 W-12.1 → S11 readback session). The repo-wide
inventory found 5 known write-only `_-prefixed` keys outside these two
dirs (`_cv_pre_uploaded`, `_donor`, `_gotchas`, `_stream`, `_total`,
`_transfer`) — those are out of scope for this guard until later
sessions wire or delete them.

For each `dict_var["_key"] = ...` write inside the scope, the test
requires that `"_key"` (literal string) is referenced ANYWHERE in
`jobpulse/` or `shared/` outside that single write line. The check is
intentionally lax — any `.get("_key")`, `["_key"]` read, `"_key" in
something`, or even a docstring mention satisfies it — because the goal
is to flag *new orphans*, not enforce a specific access pattern.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


# Subdirs scoped to this guard. ats_adapters + form_engine are the
# subsystems where the W-12.1 pattern surfaced; today both are clean.
SCOPED_ROOTS = (
    "jobpulse/ats_adapters",
    "jobpulse/form_engine",
)


def _underscore_key_writes(scope_root: Path) -> list[tuple[Path, int, str]]:
    """Collect every `dict_var["_key"] = ...` write inside `scope_root`."""
    writes: list[tuple[Path, int, str]] = []
    for path in scope_root.rglob("*.py"):
        if any(p in path.parts for p in ("__pycache__", ".claude", ".venv")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, ValueError):
            continue

        class _Visitor(ast.NodeVisitor):
            def visit_Assign(self, node: ast.Assign) -> None:
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.slice, ast.Constant)
                        and isinstance(tgt.slice.value, str)
                        and tgt.slice.value.startswith("_")
                    ):
                        writes.append((path, node.lineno, tgt.slice.value))
                self.generic_visit(node)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                if (
                    isinstance(node.target, ast.Subscript)
                    and isinstance(node.target.slice, ast.Constant)
                    and isinstance(node.target.slice.value, str)
                    and node.target.slice.value.startswith("_")
                ):
                    writes.append((path, node.lineno, node.target.slice.value))
                self.generic_visit(node)

        _Visitor().visit(tree)
    return writes


def _key_referenced_elsewhere(
    key: str, write_path: Path, write_lineno: int, root: Path
) -> bool:
    """Return True iff `"_key"` appears in any .py under `jobpulse/` or
    `shared/` outside of the single (file, line) where it's written."""
    pattern = re.compile(
        rf"""(?:["']){re.escape(key)}(?:["'])"""
    )
    write_pattern_left = re.compile(
        rf"""\[\s*(?:["']){re.escape(key)}(?:["'])\s*\]\s*="""
    )
    for search_root in ("jobpulse", "shared"):
        for path in (root / search_root).rglob("*.py"):
            if any(p in path.parts for p in ("__pycache__", ".claude", ".venv")):
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(source.splitlines(), start=1):
                if not pattern.search(line):
                    continue
                if path == write_path and i == write_lineno:
                    continue
                # Skip other write-style lines (`dict["_key"] = X`) so we
                # only credit *reads*.
                if write_pattern_left.search(line):
                    continue
                return True
    return False


@pytest.mark.parametrize("scope", SCOPED_ROOTS)
def test_no_write_only_underscore_flag(scope: str) -> None:
    root = _root()
    scope_root = root / scope
    writes = _underscore_key_writes(scope_root)

    orphans: list[str] = []
    for write_path, lineno, key in writes:
        if not _key_referenced_elsewhere(key, write_path, lineno, root):
            rel = write_path.relative_to(root)
            orphans.append(f"{rel}:{lineno}  key={key!r}")

    if not orphans:
        return

    raise AssertionError(
        f"\nWrite-only `_-prefixed` dict-key flags in {scope}/:\n  "
        + "\n  ".join(orphans)
        + "\n\nThe S12 W-12.1 incident: SmartRecruiters' `pre_fill` set "
        f"`custom_answers['_cv_pre_uploaded'] = True` to skip the "
        f"second CV upload, but `form_engine/file_uploader.py` never read "
        f"the flag — leading to double CV uploads.\n"
        f"\nFix: either wire a reader in the relevant filler / orchestrator "
        f"that consumes the flag, OR delete the write if it has no "
        f"intended consumer. See `pipeline-bugs.md` S12 W-12.1 + theme #4 "
        f"(Wired-but-unconsumed infrastructure).\n"
    )
