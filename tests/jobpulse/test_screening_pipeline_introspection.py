"""Tests for S26-follow-up-O-1.3: introspection_confirmation routing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jobpulse.screening_intent import (
    ScreeningIntent,
    classify_intent,
    get_intent_classifier,
)
from jobpulse.screening_pipeline import (
    ScreeningPipeline,
    _extract_referenced_fields,
)
from jobpulse.screening_session_state import SessionFillState


@pytest.fixture
def _profile():
    return {
        "visa_status": "Graduate Visa",
        "current_salary": "20000",
        "expected_salary": "35000",
    }


def test_introspection_intent_classified_correctly():
    """The new INTROSPECTION_CONFIRMATION intent classifies expected
    paraphrases correctly via the embedding tier."""
    intent_str = classify_intent("Have you added your full legal name and surname?")
    assert intent_str == ScreeningIntent.INTROSPECTION_CONFIRMATION.value


def test_referenced_field_extraction_legal_name():
    refs = _extract_referenced_fields(
        "Have you added your full legal name and surname?",
    )
    assert "First Name" in refs
    assert "Last Name" in refs


def test_referenced_field_extraction_returns_empty_for_unrelated():
    refs = _extract_referenced_fields("What is your favourite colour?")
    assert refs == []


def test_introspection_yes_when_referenced_fields_filled(_profile):
    state = SessionFillState()
    state.record_fill("First Name*", "Yash", field_type="text", verified=True)
    state.record_fill("Last Name*", "Bishnoi", field_type="text", verified=True)
    pipeline = ScreeningPipeline(profile=_profile)
    result = pipeline.answer(
        question="Have you added your full legal name and surname?",
        field={"type": "boolean", "options": ["Yes", "No"]},
        session_state=state,
    )
    assert result["source"] == "introspection_session_state"
    assert result["answer"].lower() in {"yes", "true"}
    assert result["intent"] == "introspection_confirmation"


def test_introspection_no_when_referenced_fields_unfilled(_profile):
    state = SessionFillState()  # nothing filled
    pipeline = ScreeningPipeline(profile=_profile)
    result = pipeline.answer(
        question="Have you added your full legal name and surname?",
        field={"type": "boolean", "options": ["Yes", "No"]},
        session_state=state,
    )
    assert result["source"] == "introspection_session_state"
    assert result["answer"].lower() in {"no", "false"}
