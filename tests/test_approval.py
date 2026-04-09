"""Tests for the approval flow module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from jobpulse import approval


@pytest.fixture(autouse=True)
def _reset_pending():
    """Reset module-level pending state between tests."""
    approval._pending = None
    yield
    approval._pending = None


def _mock_send():
    """Patch telegram_agent.send_message to avoid real HTTP calls."""
    return patch("jobpulse.telegram_agent.send_message", return_value=True)


class TestRequestApproval:
    def test_creates_pending_and_sends_telegram(self):
        with _mock_send() as mock_send:
            aid = approval.request_approval("Deploy to prod?")
            assert len(aid) == 8
            assert approval._pending is not None
            assert approval._pending["question"] == "Deploy to prod?"
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Deploy to prod?" in msg

    def test_custom_timeout(self):
        with _mock_send():
            approval.request_approval("ok?", timeout_seconds=60)
            assert approval._pending["timeout"] == 60

    def test_stores_callback(self):
        cb = MagicMock()
        with _mock_send():
            approval.request_approval("ok?", callback=cb)
            assert approval._pending["callback"] is cb


class TestGetPending:
    def test_returns_none_when_empty(self):
        assert approval.get_pending() is None

    def test_returns_pending_when_set(self):
        with _mock_send():
            approval.request_approval("test?")
        assert approval.get_pending() is not None

    def test_expires_after_timeout(self):
        with _mock_send():
            approval.request_approval("test?", timeout_seconds=1)
        # Force expiry
        approval._pending["created_at"] = time.time() - 2
        assert approval.get_pending() is None


class TestResolve:
    def test_approve(self):
        with _mock_send():
            approval.request_approval("ship it?")
        result = approval.resolve(approved=True)
        assert "Approved" in result
        assert approval._pending is None

    def test_reject(self):
        with _mock_send():
            approval.request_approval("ship it?")
        result = approval.resolve(approved=False)
        assert "Rejected" in result

    def test_no_pending(self):
        result = approval.resolve(approved=True)
        assert result == "No pending approval."

    def test_callback_invoked_on_approve(self):
        cb = MagicMock(return_value="deployed!")
        with _mock_send():
            approval.request_approval("deploy?", callback=cb)
        result = approval.resolve(approved=True)
        cb.assert_called_once_with(True)
        assert "deployed!" in result

    def test_callback_error_handled(self):
        cb = MagicMock(side_effect=RuntimeError("boom"))
        with _mock_send():
            approval.request_approval("deploy?", callback=cb)
        result = approval.resolve(approved=True)
        assert "callback failed" in result


class TestProcessReply:
    @pytest.fixture(autouse=True)
    def _setup_pending(self):
        with _mock_send():
            approval.request_approval("test?")

    @pytest.mark.parametrize("text", ["yes", "Yes", "y", "Y", "approve", "ok", "yep", "sure"])
    def test_positive_replies(self, text):
        result = approval.process_reply(text)
        assert result is not None
        assert "Approved" in result

    @pytest.mark.parametrize("text", ["no", "No", "n", "reject", "nope", "cancel"])
    def test_negative_replies(self, text):
        # Need fresh pending for each since previous test consumed it
        with _mock_send():
            approval.request_approval("test?")
        result = approval.process_reply(text)
        assert result is not None
        assert "Rejected" in result

    def test_non_approval_text_returns_none(self):
        result = approval.process_reply("what's the weather?")
        assert result is None

    def test_no_pending_returns_none(self):
        approval._pending = None
        result = approval.process_reply("yes")
        assert result is None
