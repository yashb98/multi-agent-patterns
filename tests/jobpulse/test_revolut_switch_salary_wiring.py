"""Wiring tests: Revolut welovealfa.com toggle switches + salary lookup.

Live regression on Revolut welovealfa.com 2026-05-05:
  • 5 screening questions rendered as <button role="switch"
    aria-checked="false">. Field scanner queried only inputs/selects/
    radios → 0 fields found, all 5 Qs skipped.
  • Min salary (GBP) and Max salary (GBP) inputs had empty <label>
    elements. Agent's LLM saw the JD's listed range £85,500-£118,000
    on the page and wrote those into both inputs as the user's salary
    expectation.

Two structural fixes:
  1. field_scanner._scan_dom_query now picks up [role="switch"] toggles
     with question label from previous-sibling/ancestor text. Returns
     type='switch' with aria-checked state.
  2. field_scanner._scan_dom_query now picks up number inputs whose
     ancestor text contains "salary"/"compensation"/"GBP"/"USD" and
     tags them with type='salary_number' + salary_role hint
     (min_salary / max_salary / salary).
  3. screening_answers.lookup_user_salary uses token-Jaccard fallback
     when no substring match (so "Software Engineer (Data)" matches
     "software engineer", "Data Analytics" maps to data scientist tier).
  4. NativeFormFiller dispatches type='switch' to a click-to-toggle
     handler, type='salary_number' to lookup_user_salary().
"""
from __future__ import annotations
import inspect


def test_field_scanner_detects_role_switch():
    from jobpulse.form_engine import field_scanner
    src = inspect.getsource(field_scanner)
    assert 'button[role="switch"]' in src or "role=\"switch\"" in src
    assert "type: 'switch'" in src
    # Must capture aria-checked state
    assert "aria-checked" in src


def test_field_scanner_detects_salary_number_context():
    from jobpulse.form_engine import field_scanner
    src = inspect.getsource(field_scanner)
    # Salary regex hits common money phrasings
    for marker in ("salary", "compensation", "gbp", "usd"):
        assert marker.lower() in src.lower()
    assert "type: 'salary_number'" in src
    # Min/max distinction
    assert "min_salary" in src
    assert "max_salary" in src


def test_lookup_user_salary_substring_match():
    from jobpulse.screening_answers import lookup_user_salary
    # Substring match — title contains role key verbatim
    assert lookup_user_salary("Senior Data Analyst Bournemouth") == 30000


def test_lookup_user_salary_token_fallback_for_data_engineering_roles():
    """Title 'Software Engineer (Data)' → fallback should match
    'software engineer' via tokens (overlap data+engineer)."""
    from jobpulse.screening_answers import lookup_user_salary
    salary = lookup_user_salary("Software Engineer (Data)")
    # Should match `software engineer` (35000) — not the default 30000
    assert salary == 35000


def test_lookup_user_salary_default_for_unknown_titles():
    from jobpulse.screening_answers import lookup_user_salary
    assert lookup_user_salary("Office Cleaner") == 30000
    assert lookup_user_salary("") == 30000


def test_native_form_filler_routes_switch_to_click():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller)
    # The dispatch branch must exist
    assert 'input_type == "switch"' in src
    # Reads aria-checked / aria-pressed before clicking
    assert "aria-checked" in src
    # Truthiness mapping for "Yes"/"true"/"on"
    assert '"yes"' in src.lower() or '"true"' in src.lower()


def test_native_form_filler_routes_salary_to_lookup():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller)
    assert 'input_type == "salary_number"' in src
    assert "lookup_user_salary" in src
    # Min/max distinction in the handler
    assert "max_salary" in src
    assert "min_salary" in src


def test_lookup_user_salary_helper_is_exported():
    """lookup_user_salary must be importable from screening_answers
    so the filler doesn't reach into private helpers."""
    from jobpulse.screening_answers import lookup_user_salary
    assert callable(lookup_user_salary)
