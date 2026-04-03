"""Tests verifying FormIntelligence wiring into the state machine and adapter."""

from __future__ import annotations

import pytest

from jobpulse.ext_models import FieldInfo, PageSnapshot
from jobpulse.form_intelligence import FormIntelligence
from jobpulse.state_machines import ApplicationState, get_state_machine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _screening_snapshot(label: str, input_type: str = "radio") -> PageSnapshot:
    """Return a minimal snapshot with a single screening question field."""
    return PageSnapshot(
        url="https://boards.greenhouse.io/acme/jobs/123",
        title="Apply - Engineer at Acme",
        fields=[
            FieldInfo(
                selector="#q_rtw",
                input_type=input_type,
                label=label,
                required=True,
            )
        ],
        buttons=[],
        verification_wall=None,
        page_text_preview="",
        has_file_inputs=False,
        iframe_count=0,
        timestamp=1000,
    )


_PROFILE: dict[str, str] = {
    "first_name": "Yash",
    "last_name": "Bishnoi",
    "email": "yash@example.com",
}


# ---------------------------------------------------------------------------
# Test 1 — FormIntelligence is used when provided
# ---------------------------------------------------------------------------


def test_actions_screening_uses_form_intelligence() -> None:
    """When form_intelligence is passed, _actions_screening resolves via the 5-tier router."""
    machine = get_state_machine("greenhouse")
    fi = FormIntelligence()

    snapshot = _screening_snapshot(
        "Do you have the right to work in the UK?", input_type="radio"
    )
    actions = machine.get_actions(
        ApplicationState.SCREENING_QUESTIONS,
        snapshot,
        _PROFILE,
        {},
        "/cv.pdf",
        None,
        form_intelligence=fi,
    )

    assert len(actions) >= 1, "Expected at least one action from FormIntelligence resolution"
    # The right-to-work question should resolve to "Yes" via Tier-1 pattern
    assert actions[0].value == "Yes"


# ---------------------------------------------------------------------------
# Test 2 — Falls back to get_answer() when form_intelligence is not provided
# ---------------------------------------------------------------------------


def test_actions_screening_falls_back_without_intelligence() -> None:
    """Without form_intelligence, _actions_screening uses the legacy get_answer() path."""
    machine = get_state_machine("greenhouse")

    snapshot = _screening_snapshot(
        "Do you have the right to work in the UK?", input_type="radio"
    )
    # No form_intelligence kwarg — uses old path
    actions = machine.get_actions(
        ApplicationState.SCREENING_QUESTIONS,
        snapshot,
        _PROFILE,
        {},
        "/cv.pdf",
        None,
    )

    assert len(actions) >= 1, "Expected at least one action from legacy get_answer() path"
    assert actions[0].value == "Yes"


# ---------------------------------------------------------------------------
# Test 3 — FieldAnswer tier is tracked correctly
# ---------------------------------------------------------------------------


def test_field_answer_tier_tracked() -> None:
    """resolve() for a sponsorship question returns tier=1 (pattern match) with tier_name 'pattern'."""
    fi = FormIntelligence()
    result = fi.resolve(
        "Do you require visa sponsorship?",
        input_type="radio",
        platform="greenhouse",
    )

    assert result.tier == 1, f"Expected tier 1 (pattern), got {result.tier}"
    assert result.tier_name == "pattern", f"Expected tier_name 'pattern', got {result.tier_name!r}"
