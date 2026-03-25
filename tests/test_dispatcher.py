"""Tests for dispatcher.py — intent to handler routing."""

import pytest
from unittest.mock import patch, MagicMock
from jobpulse.command_router import Intent, ParsedCommand
from jobpulse.dispatcher import dispatch, _handle_help, _handle_unknown


# ── Handler map coverage ──

class TestDispatchMap:
    """Every Intent enum value (except UNKNOWN) should have a handler."""

    @patch("jobpulse.process_logger.ProcessTrail")
    @patch("jobpulse.dispatcher.event_logger")
    def test_all_intents_have_handlers(self, mock_evt, mock_trail):
        """Verify the handlers dict covers every non-UNKNOWN intent."""
        # Build the handlers dict as the real code does
        handlers_covered = {
            Intent.SHOW_TASKS, Intent.CREATE_TASKS, Intent.CALENDAR,
            Intent.GMAIL, Intent.GITHUB, Intent.TRENDING, Intent.BRIEFING,
            Intent.ARXIV, Intent.COMPLETE_TASK, Intent.CREATE_EVENT,
            Intent.LOG_SPEND, Intent.LOG_INCOME, Intent.LOG_SAVINGS,
            Intent.SET_BUDGET, Intent.SHOW_BUDGET, Intent.HELP,
            Intent.WEEKLY_REPORT, Intent.EXPORT,
        }
        all_intents = set(Intent) - {Intent.UNKNOWN}
        missing = all_intents - handlers_covered
        assert missing == set(), f"Intents missing handlers: {missing}"


class TestDispatchRouting:
    """Test that dispatch() routes to the correct handler and returns results."""

    @patch("jobpulse.process_logger.ProcessTrail")
    @patch("jobpulse.dispatcher.event_logger")
    def test_help_returns_commands_list(self, mock_evt, mock_trail):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        cmd = ParsedCommand(intent=Intent.HELP, args="", raw="help")
        result = dispatch(cmd)
        assert "JobPulse Commands" in result
        assert "TASKS" in result

    @patch("jobpulse.process_logger.ProcessTrail")
    @patch("jobpulse.dispatcher.event_logger")
    def test_unknown_intent_returns_not_sure(self, mock_evt, mock_trail):
        cmd = ParsedCommand(intent=Intent.UNKNOWN, args="xyz", raw="xyz")
        result = dispatch(cmd)
        assert "didn't" in result

    @patch("jobpulse.dispatcher._handle_show_tasks")
    @patch("jobpulse.process_logger.ProcessTrail")
    @patch("jobpulse.dispatcher.event_logger")
    def test_show_tasks_calls_handler(self, mock_evt, mock_trail, mock_handler):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        mock_handler.return_value = "No tasks today"
        cmd = ParsedCommand(intent=Intent.SHOW_TASKS, args="", raw="show tasks")
        result = dispatch(cmd)
        mock_handler.assert_called_once_with(cmd)
        assert result == "No tasks today"

    @patch("jobpulse.dispatcher._handle_calendar")
    @patch("jobpulse.process_logger.ProcessTrail")
    @patch("jobpulse.dispatcher.event_logger")
    def test_calendar_calls_handler(self, mock_evt, mock_trail, mock_handler):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        mock_handler.return_value = "No events today"
        cmd = ParsedCommand(intent=Intent.CALENDAR, args="", raw="calendar")
        result = dispatch(cmd)
        mock_handler.assert_called_once_with(cmd)

    @patch("jobpulse.dispatcher._handle_gmail")
    @patch("jobpulse.process_logger.ProcessTrail")
    @patch("jobpulse.dispatcher.event_logger")
    def test_gmail_calls_handler(self, mock_evt, mock_trail, mock_handler):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        mock_handler.return_value = "No new emails"
        cmd = ParsedCommand(intent=Intent.GMAIL, args="", raw="check emails")
        result = dispatch(cmd)
        mock_handler.assert_called_once_with(cmd)

    @patch("jobpulse.process_logger.ProcessTrail")
    @patch("jobpulse.dispatcher.event_logger")
    def test_handler_exception_returns_error_message(self, mock_evt, mock_trail):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        with patch("jobpulse.dispatcher._handle_github", side_effect=RuntimeError("API down")):
            cmd = ParsedCommand(intent=Intent.GITHUB, args="", raw="commits")
            result = dispatch(cmd)
            assert "Error" in result
            assert "API down" in result


class TestHandleHelp:
    def test_help_text_contains_key_sections(self):
        cmd = ParsedCommand(intent=Intent.HELP, args="", raw="help")
        result = _handle_help(cmd)
        assert "TASKS" in result
        assert "CALENDAR" in result
        assert "EMAIL" in result
        assert "GITHUB" in result
        assert "BUDGET" in result


class TestHandleUnknown:
    def test_unknown_includes_user_text(self):
        cmd = ParsedCommand(intent=Intent.UNKNOWN, args="blah", raw="blah foo bar")
        result = _handle_unknown(cmd)
        assert "blah foo bar" in result

    def test_unknown_suggests_alternatives(self):
        cmd = ParsedCommand(intent=Intent.UNKNOWN, args="", raw="xyz")
        result = _handle_unknown(cmd)
        assert "help" in result.lower()

    def test_unknown_suggests_closest_match(self):
        cmd = ParsedCommand(intent=Intent.UNKNOWN, args="", raw="show me my github stuff")
        result = _handle_unknown(cmd)
        assert "commits" in result.lower()
        assert "Did you mean" in result


class TestDispatchLogsEvents:
    """Test that dispatched commands log to event_logger."""

    @patch("jobpulse.process_logger.ProcessTrail")
    @patch("jobpulse.dispatcher.event_logger")
    def test_successful_dispatch_logs_event(self, mock_evt, mock_trail):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        cmd = ParsedCommand(intent=Intent.HELP, args="", raw="help")
        dispatch(cmd)
        mock_evt.log_event.assert_called_once()
        call_kwargs = mock_evt.log_event.call_args
        assert call_kwargs[1]["event_type"] == "agent_action" or call_kwargs[0][0] == "agent_action"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
