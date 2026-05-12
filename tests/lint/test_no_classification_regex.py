"""Lint guard: forbid `re.match` / `re.search` / `re.compile` / `re.fullmatch`
in modules audited clean of classification-regex.

`pipeline-bugs.md` Cross-Subsystem Theme #1: regex-for-classification is
the most-recurring Principle-8 violation across the 12 subsystem audits.
The migration plan (`docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md`)
covers the multi-session cleanup; sessions S12 (screening), S13 (form
scanner), S14 (navigator) drain it.

This guard enforces on the SUBSET of modules that have already been
cleaned, so regressions surface immediately. Modules still containing
classification regex are intentionally NOT in `CLEAN_FILES`; they're
expected to be added once S12-S14 land their fixes.

Per `.claude/rules/jobpulse.md` and `.claude/rules/shared.md`, regex
remains acceptable for:
  - text normalization (`re.sub` for whitespace/punctuation)
  - security sanitization (stripping injection tags)
  - structural format validation (email/phone/date/URL patterns)
  - number extraction from known-format strings

This guard targets the four `re.<call>` callables that are most
diagnostic of classification work — `match`, `search`, `compile`, and
`fullmatch`. `re.sub`, `re.split`, `re.findall` are NOT forbidden because
they appear in legitimate normalization helpers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


# Modules audited clean of classification-regex. Re-running the inventory
# (`ast.walk` for `re.match|search|compile|fullmatch`) on these files must
# return zero. Future regex-purge sessions add files here as they finish.
CLEAN_FILES: list[str] = [
    # post-S5 audit
    "jobpulse/post_apply_hook.py",
    "jobpulse/correction_capture.py",
    "jobpulse/agent_rules.py",
    "jobpulse/trajectory_store.py",
    "jobpulse/strategy_reflector.py",
    # post-S12 audit (ats_adapters unification)
    "jobpulse/ats_adapters/__init__.py",
    "jobpulse/ats_adapters/strategy.py",
    "jobpulse/ats_adapters/base.py",
    # post-S4 audit (screening pipeline + qdrant cache + intent classifier
    # were rewritten to use embeddings; only screening_validator and
    # screening_option_aligner still hold regex hold-outs scheduled for S12)
    "jobpulse/screening_pipeline.py",
    "jobpulse/screening_intent.py",
    "jobpulse/screening_semantic_cache.py",
    # post-S11 audit (memory_layer)
    "shared/memory_layer/_manager.py",
    # post-S6 audit (cognitive engine — _classifier still has 1 known
    # holdout at line 196, scheduled for S14)
    "shared/cognitive/_engine.py",
    # post-S10 audit (optimization)
    "shared/optimization/_engine.py",
    "shared/optimization/_aggregator.py",
]


_FORBIDDEN_RE_CALLS = {"match", "search", "compile", "fullmatch"}


def _classification_regex_calls(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "re":
            continue
        if func.attr in _FORBIDDEN_RE_CALLS:
            hits.append((func.attr, node.lineno))
    return hits


@pytest.mark.parametrize("rel", CLEAN_FILES)
def test_clean_file_has_no_classification_regex(rel: str) -> None:
    source = (_root() / rel).read_text(encoding="utf-8")
    hits = _classification_regex_calls(source)
    if not hits:
        return
    formatted = ", ".join(f"re.{attr} (line {line})" for attr, line in hits)
    raise AssertionError(
        f"\n{rel} now contains classification-regex calls: {formatted}.\n"
        f"\nThis file was previously audited clean of `re.match` / `re.search` "
        f"/ `re.compile` / `re.fullmatch`. Reintroducing them violates the "
        f"`.claude/rules/jobpulse.md` Dynamic-Over-Hardcoded rule.\n"
        f"\nReplace with one of: LLM classification (with caching), "
        f"embedding similarity, semantic_matcher.py, a11y-tree inspection, "
        f"or database-stored learned patterns. `re.sub` / `re.split` / "
        f"`re.findall` are still allowed for text normalization and "
        f"format extraction — only the four classification-style callables "
        f"are forbidden.\n"
        f"\nIf the regex is genuinely a sanitization helper (not "
        f"classification), justify it in the PR and remove this file from "
        f"`CLEAN_FILES`.\n"
    )
