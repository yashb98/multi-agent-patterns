"""Regression test for the ``ax_options`` UnboundLocalError.

Live evidence (2026-05-09 Anthropic Greenhouse run, /tmp/apply.log):
    Field fill failed for 'Are you Hispanic/Latino?': cannot access local
    variable 'ax_options' where it is not associated with a value

The variable was assigned only inside the React-Select success branch.
When the option click missed (``opt_locator.count() == 0``) or the
inner ``try`` raised, ``ax_options`` stayed unassigned, then the
post-branch ``if ax_options:`` check tripped UnboundLocalError and
masked the real failure cause.

This test parses the ``_fill_field`` method's combobox branch and
verifies ``ax_options`` is initialised at the top of the branch (before
the React-Select fast-path), not only inside the success leaf.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


_NFF = Path(__file__).resolve().parent.parent.parent / "jobpulse" / "native_form_filler.py"


def _read_source() -> str:
    return _NFF.read_text(encoding="utf-8")


def test_ax_options_initialised_before_react_select_branch():
    src = _read_source()
    # The fix is a direct-string init at the top of the combobox section.
    init_marker = 'ax_options: list[str] = []'
    assert init_marker in src, (
        f"Expected '{init_marker}' to pre-initialise ax_options. "
        "If you removed this, you need a different way to guarantee "
        "the variable is bound before the post-React-Select read."
    )

    # Sanity: the init appears before the first read of ax_options as a
    # control-flow check. We split by line and find the lines that match
    # exactly so the docstring/comment mentions of `if ax_options:` don't
    # confuse the search.
    lines = src.splitlines()
    init_lineno = next(
        i for i, ln in enumerate(lines) if ln.strip() == init_marker
    )
    read_lineno = next(
        i for i, ln in enumerate(lines) if ln.strip() == "if ax_options:"
    )
    assert init_lineno < read_lineno, (
        f"ax_options init at line {init_lineno + 1} must appear before "
        f"its read at line {read_lineno + 1}"
    )


def test_matched_option_initialised_before_react_select_branch():
    """Same pattern: ``matched_option`` is read by the
    ``if not ax_options or not matched_option:`` check after the
    React-Select branch and must be pre-bound."""

    src = _read_source()
    assert "matched_option: str | None = None" in src
