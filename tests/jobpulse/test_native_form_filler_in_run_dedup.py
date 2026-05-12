"""Tests for S26-follow-up-O-3: in-run filled-set dedup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from jobpulse.native_form_filler import NativeFormFiller
from jobpulse.screening_session_state import SessionFillState


def _filler_with_session() -> NativeFormFiller:
    filler = NativeFormFiller.__new__(NativeFormFiller)
    filler._page = MagicMock()
    filler._page.url = "https://job-boards.greenhouse.io/x"
    filler._fields_by_label = {}
    filler._session_state = SessionFillState()
    return filler


def test_second_fill_of_verified_field_is_skipped_in_run():
    filler = _filler_with_session()
    filler._session_state.record_fill(
        "Email", "y@b.com", field_type="email", verified=True,
    )
    result = asyncio.run(filler._try_in_run_skip("Email", "y@b.com"))
    assert result is not None
    assert result["skipped"] == "already_verified_in_run"
    assert result["success"] is True


def test_unfilled_field_passes_through():
    filler = _filler_with_session()
    result = asyncio.run(filler._try_in_run_skip("Email", "y@b.com"))
    assert result is None


def test_filled_but_unverified_does_not_skip():
    """A fill that wasn't verified shouldn't short-circuit the next attempt."""
    filler = _filler_with_session()
    filler._session_state.record_fill(
        "Email", "y@b.com", field_type="email", verified=False,
    )
    result = asyncio.run(filler._try_in_run_skip("Email", "y@b.com"))
    assert result is None


def test_different_value_does_not_skip():
    """Cached value differs from current claim → don't trust the skip."""
    filler = _filler_with_session()
    filler._session_state.record_fill(
        "Email", "old@b.com", field_type="email", verified=True,
    )
    result = asyncio.run(filler._try_in_run_skip("Email", "new@b.com"))
    assert result is None
