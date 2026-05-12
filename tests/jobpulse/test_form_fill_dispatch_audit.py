"""Regression tests for the form_fill_dispatch audit fixes (S1, 2026-05-06).

These are source-level guards against re-introducing the verification-claim
bugs and the radio exact-match bypass that the audit caught in
native_form_filler.py.
"""
from __future__ import annotations

import inspect
import re

import jobpulse.native_form_filler as nff


def _split_method_source(method_name: str) -> str:
    src = inspect.getsource(nff.NativeFormFiller)
    pattern = re.compile(rf"\n    async def {method_name}\b.*?(?=\n    async def |\n    def |\nclass )", re.S)
    m = pattern.search(src)
    assert m, f"could not isolate {method_name} in NativeFormFiller source"
    return m.group(0)


def test_text_fill_verified_default_is_false_not_true():
    """M-1.c: when input_value() fails to read back the filled value,
    _fill_resolved_widget must NOT claim value_verified=True. The
    dispatcher caller checks `result.get('value_verified', True)`, so a
    bare 'else True' here results in unverified fills counted as filled.
    """
    src = _split_method_source("_fill_resolved_widget")
    # The text/textarea/email/tel/url branch must use `else False` for the
    # "couldn't read back" fallback. If anyone reverts to `else True`,
    # this catches it.
    text_branch_marker = (
        'if input_type in ("text", "textarea", "number", "email", "tel", "url")'
    )
    assert text_branch_marker in src
    head, _, tail = src.partition(text_branch_marker)
    text_branch = tail.split("\n        if ", 1)[0]
    assert "(actual == value) if actual else True" not in text_branch, (
        "text branch must not default value_verified to True without readback"
    )
    # Both fill() and type() fallback must use input_value-based readback.
    assert text_branch.count("input_value()") >= 2, (
        "text branch must read back via input_value() in BOTH fill and type "
        "fallback paths"
    )


def test_range_fill_reads_back_both_inputs():
    """M-1.e: range/salary_range branch must read back BOTH input fields
    via input_value() before claiming verification. Without this,
    salary-range widgets silently report verified=True even when the
    inputs are still blank.
    """
    src = _split_method_source("_fill_resolved_widget")
    range_marker = 'if input_type in ("range", "split_numeric"'
    assert range_marker in src
    range_branch = src.partition(range_marker)[2].split("\n        if ", 1)[0]
    assert "input_value()" in range_branch, (
        "range branch must call input_value() to verify both fills"
    )
    # Hard guard: no literal True for value_verified in the range branch
    assert '"value_verified": True' not in range_branch, (
        "range branch must not hardcode value_verified=True without readback"
    )


def test_radio_branch_uses_semantic_matcher():
    """M-2: the named-radio-group branch in _fill_by_label must pick its
    target via _best_option_match (semantic matcher), not via exact
    lower-case equality. Exact equality breaks for "Asian / Indian" vs
    "Indian", "Yes — sponsored" vs "Yes", etc.
    """
    src = _split_method_source("_fill_by_label")
    # Find the input_type == "radio" branch
    radio_marker = 'elif input_type == "radio":'
    assert radio_marker in src
    radio_branch = src.partition(radio_marker)[2].split("\n        elif ", 1)[0]
    # Must use _best_option_match, not the old exact-equality pattern.
    assert "_best_option_match" in radio_branch, (
        "radio branch must use _best_option_match, not exact equality"
    )
    # The old exact-equality bug must not return.
    assert "lbl.strip().lower() == fill_value.strip().lower()" not in radio_branch, (
        "radio branch must not use case-insensitive exact equality"
    )
