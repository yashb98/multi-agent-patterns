"""Unit tests for SessionFillState (S26-follow-up-O-1.2)."""

from __future__ import annotations

from jobpulse.screening_session_state import SessionFillState


def test_records_fill_and_lookup():
    state = SessionFillState()
    state.record_fill("First Name*", "Yash", field_type="text", verified=True)
    state.record_fill("Last Name*", "Bishnoi", field_type="text", verified=True)
    assert state.has_filled("First Name") is True
    assert state.has_filled("Last name") is True  # case + marker insensitive
    assert state.has_filled("Phone") is False


def test_get_filled_labels_returns_normalized():
    state = SessionFillState()
    state.record_fill(
        "Email Address*", "y@b.com", field_type="email", verified=True,
    )
    assert "email address" in state.get_filled_labels_normalized()


def test_record_fill_with_verified_false_still_logs():
    state = SessionFillState()
    state.record_fill("First Name", "Yash", field_type="text", verified=False)
    assert state.has_filled("First Name") is True
    assert state.was_verified("First Name") is False


def test_references_present_returns_true_when_subset_filled():
    state = SessionFillState()
    state.record_fill("First Name", "Yash", field_type="text", verified=True)
    state.record_fill("Last Name", "Bishnoi", field_type="text", verified=True)
    assert state.references_present(["First Name", "Last Name"]) is True
    assert state.references_present(["First Name", "Phone"]) is False


def test_references_present_empty_input_returns_false():
    state = SessionFillState()
    state.record_fill("First Name", "Yash", field_type="text", verified=True)
    assert state.references_present([]) is False


def test_clear_resets():
    state = SessionFillState()
    state.record_fill("X", "v", field_type="text", verified=True)
    state.clear()
    assert state.has_filled("X") is False
