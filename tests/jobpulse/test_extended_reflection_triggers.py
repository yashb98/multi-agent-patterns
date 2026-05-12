"""reason_with_failure must fire for expected_outcome violations and vision disagreements,
not just ghost clicks."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from jobpulse.application_orchestrator_pkg._navigator import (
    FormNavigator, ActionVerification,
)


def _nav():
    n = FormNavigator.__new__(FormNavigator)
    n.driver = AsyncMock()
    return n


def _make_verification(ghost=False, outcome_met=None):
    return ActionVerification(
        pre_url="https://example.com/a",
        pre_hash="a",
        pre_dialog=False,
        post_url="https://example.com/a",
        post_hash="a",
        post_dialog=False,
        ghost_click=ghost,
        expected_outcome_met=outcome_met,
    )


class TestExtendedTriggers:
    def test_helper_exists(self):
        """The new helper must be importable."""
        from jobpulse.application_orchestrator_pkg._navigator import (
            _maybe_reflect_on_failure,
        )
        assert callable(_maybe_reflect_on_failure)

    def test_reflects_on_expected_outcome_violation(self, monkeypatch):
        from jobpulse.application_orchestrator_pkg._navigator import (
            _maybe_reflect_on_failure,
        )
        captured = {}
        fake_reasoner = MagicMock()
        fake_action = MagicMock(action="wait_human", confidence=0.4)
        fake_reasoner.reason_with_failure = MagicMock(return_value=fake_action)

        with patch("jobpulse.page_analysis.page_reasoner.get_page_reasoner",
                   return_value=fake_reasoner):
            verification = _make_verification(ghost=False, outcome_met=False)
            snapshot = {"url": "https://example.com/a"}
            result = _maybe_reflect_on_failure(
                verification=verification,
                snapshot=snapshot,
                trigger="expected_outcome_violation",
                context_extra={"expected": "url_changes"},
            )
        assert result is fake_action
        assert fake_reasoner.reason_with_failure.called
        ctx_arg = fake_reasoner.reason_with_failure.call_args.kwargs.get(
            "failure_context"
        ) or fake_reasoner.reason_with_failure.call_args.args[1]
        assert "expected_outcome" in str(ctx_arg).lower() or "url_changes" in str(ctx_arg).lower()

    def test_reflects_on_vision_disagreement(self, monkeypatch):
        from jobpulse.application_orchestrator_pkg._navigator import (
            _maybe_reflect_on_failure,
        )
        fake_reasoner = MagicMock()
        fake_reasoner.reason_with_failure = MagicMock(return_value=MagicMock())

        with patch("jobpulse.page_analysis.page_reasoner.get_page_reasoner",
                   return_value=fake_reasoner):
            _maybe_reflect_on_failure(
                verification=_make_verification(),
                snapshot={"url": "https://example.com/a"},
                trigger="vision_disagreement",
                context_extra={"reasoner_type": "login_form", "vision_type": "verification_wall"},
            )
        assert fake_reasoner.reason_with_failure.called

    def test_returns_none_when_reasoner_fails(self, monkeypatch):
        from jobpulse.application_orchestrator_pkg._navigator import (
            _maybe_reflect_on_failure,
        )
        fake_reasoner = MagicMock()
        fake_reasoner.reason_with_failure = MagicMock(side_effect=RuntimeError("boom"))

        with patch("jobpulse.page_analysis.page_reasoner.get_page_reasoner",
                   return_value=fake_reasoner):
            result = _maybe_reflect_on_failure(
                verification=_make_verification(),
                snapshot={"url": "https://example.com/a"},
                trigger="ghost_click",
                context_extra={},
            )
        assert result is None  # swallowed cleanly
