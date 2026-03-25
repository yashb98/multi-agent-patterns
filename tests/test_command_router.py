"""Tests for command_router.py — intent classification from Telegram messages."""

import pytest
from unittest.mock import patch, MagicMock
from jobpulse.command_router import (
    Intent, ParsedCommand, classify_rule_based, classify, is_task_list,
)


# ── Rule-based classification tests ──

class TestClassifyRuleBased:
    """Test that each intent pattern matches correctly."""

    def test_help_matches(self):
        assert classify_rule_based("help").intent == Intent.HELP
        assert classify_rule_based("commands").intent == Intent.HELP
        assert classify_rule_based("/help").intent == Intent.HELP
        assert classify_rule_based("what can you do").intent == Intent.HELP

    def test_show_tasks_matches(self):
        assert classify_rule_based("show tasks").intent == Intent.SHOW_TASKS
        assert classify_rule_based("list my tasks").intent == Intent.SHOW_TASKS
        assert classify_rule_based("view todo").intent == Intent.SHOW_TASKS
        assert classify_rule_based("get my checklist").intent == Intent.SHOW_TASKS
        assert classify_rule_based("tasks").intent == Intent.SHOW_TASKS
        assert classify_rule_based("what are my tasks").intent == Intent.SHOW_TASKS

    def test_complete_task_matches(self):
        result = classify_rule_based("mark fix bug done")
        assert result.intent == Intent.COMPLETE_TASK

        result = classify_rule_based("done: apply to jobs")
        assert result.intent == Intent.COMPLETE_TASK

    def test_create_tasks_matches(self):
        result = classify_rule_based("add task fix the login bug")
        assert result.intent == Intent.CREATE_TASKS

        result = classify_rule_based("new todo write tests")
        assert result.intent == Intent.CREATE_TASKS

    def test_calendar_matches(self):
        assert classify_rule_based("calendar").intent == Intent.CALENDAR
        assert classify_rule_based("schedule").intent == Intent.CALENDAR
        assert classify_rule_based("what's on today").intent == Intent.CALENDAR
        assert classify_rule_based("my day").intent == Intent.CALENDAR
        assert classify_rule_based("today's calendar").intent == Intent.CALENDAR

    def test_gmail_matches(self):
        assert classify_rule_based("check emails").intent == Intent.GMAIL
        assert classify_rule_based("check my mail").intent == Intent.GMAIL
        assert classify_rule_based("any recruiter emails").intent == Intent.GMAIL
        assert classify_rule_based("inbox").intent == Intent.GMAIL

    def test_github_matches(self):
        assert classify_rule_based("commits").intent == Intent.GITHUB
        assert classify_rule_based("what did i push").intent == Intent.GITHUB
        assert classify_rule_based("my commits").intent == Intent.GITHUB

    def test_trending_matches(self):
        assert classify_rule_based("trending").intent == Intent.TRENDING
        assert classify_rule_based("hot repos").intent == Intent.TRENDING
        # "github trending" matches GITHUB first due to pattern ordering
        assert classify_rule_based("top repos").intent == Intent.TRENDING

    def test_briefing_matches(self):
        assert classify_rule_based("briefing").intent == Intent.BRIEFING
        assert classify_rule_based("morning update").intent == Intent.BRIEFING
        assert classify_rule_based("daily update").intent == Intent.BRIEFING
        assert classify_rule_based("full report").intent == Intent.BRIEFING

    def test_arxiv_matches(self):
        assert classify_rule_based("arxiv").intent == Intent.ARXIV
        assert classify_rule_based("ai paper").intent == Intent.ARXIV
        assert classify_rule_based("latest paper").intent == Intent.ARXIV

    def test_log_spend_matches(self):
        assert classify_rule_based("spent 15 on lunch").intent == Intent.LOG_SPEND
        assert classify_rule_based("£8.50 coffee").intent == Intent.LOG_SPEND
        assert classify_rule_based("paid 20 for dinner").intent == Intent.LOG_SPEND
        assert classify_rule_based("15 on groceries").intent == Intent.LOG_SPEND

    def test_log_income_matches(self):
        assert classify_rule_based("earned 500 freelance").intent == Intent.LOG_INCOME
        assert classify_rule_based("got paid 2000").intent == Intent.LOG_INCOME
        assert classify_rule_based("income 500 salary").intent == Intent.LOG_INCOME

    def test_log_savings_matches(self):
        assert classify_rule_based("saved 100").intent == Intent.LOG_SAVINGS
        assert classify_rule_based("invest 200").intent == Intent.LOG_SAVINGS

    def test_set_budget_matches(self):
        assert classify_rule_based("set budget groceries 50").intent == Intent.SET_BUDGET
        assert classify_rule_based("budget groceries 50").intent == Intent.SET_BUDGET

    def test_show_budget_matches(self):
        assert classify_rule_based("budget").intent == Intent.SHOW_BUDGET
        assert classify_rule_based("how much have I spent").intent == Intent.SHOW_BUDGET
        assert classify_rule_based("weekly budget").intent == Intent.SHOW_BUDGET
        assert classify_rule_based("show spending").intent == Intent.SHOW_BUDGET

    def test_create_event_matches(self):
        result = classify_rule_based("remind me to call mom at 3pm")
        assert result.intent == Intent.CREATE_EVENT

    def test_unrecognized_returns_none(self):
        assert classify_rule_based("hello there") is None
        assert classify_rule_based("what is the meaning of life") is None
        assert classify_rule_based("random gibberish xyz") is None

    def test_case_insensitive(self):
        assert classify_rule_based("SHOW TASKS").intent == Intent.SHOW_TASKS
        assert classify_rule_based("Calendar").intent == Intent.CALENDAR
        assert classify_rule_based("BRIEFING").intent == Intent.BRIEFING

    def test_parsed_command_contains_raw_text(self):
        result = classify_rule_based("spent 15 on lunch")
        assert result.raw == "spent 15 on lunch"


# ── is_task_list tests ──

class TestIsTaskList:
    def test_multi_line_short_items_detected(self):
        text = "Fix bug\nApply to jobs\nTailor resume"
        assert is_task_list(text) is True

    def test_single_line_not_task_list(self):
        assert is_task_list("just one line") is False

    def test_long_lines_not_task_list(self):
        text = ("A" * 100) + "\n" + ("B" * 100) + "\n" + ("C" * 100)
        assert is_task_list(text) is False

    def test_empty_string(self):
        assert is_task_list("") is False

    def test_blank_lines_ignored(self):
        text = "Task one\n\nTask two\n\nTask three"
        assert is_task_list(text) is True

    def test_two_lines_enough(self):
        text = "Buy milk\nSend invoice"
        assert is_task_list(text) is True


# ── Main classify() function tests ──

class TestClassify:
    def test_rule_based_takes_priority(self):
        result = classify("show tasks")
        assert result.intent == Intent.SHOW_TASKS

    def test_empty_string_returns_unknown(self):
        result = classify("")
        assert result.intent == Intent.UNKNOWN

    def test_whitespace_only_returns_unknown(self):
        result = classify("   ")
        assert result.intent == Intent.UNKNOWN

    def test_bot_mention_stripped(self):
        result = classify("@jobpulsebot show tasks")
        assert result.intent == Intent.SHOW_TASKS

    def test_multi_line_becomes_create_tasks(self):
        text = "Buy groceries\nFix login bug\nCall dentist"
        result = classify(text)
        assert result.intent == Intent.CREATE_TASKS

    @patch("jobpulse.command_router.classify_llm")
    def test_falls_back_to_llm(self, mock_llm):
        mock_llm.return_value = ParsedCommand(
            intent=Intent.UNKNOWN, args="hello world", raw="hello world"
        )
        result = classify("hello world")
        mock_llm.assert_called_once_with("hello world")
        assert result.intent == Intent.UNKNOWN

    def test_set_budget_before_show_budget(self):
        """set budget X should match SET_BUDGET, not SHOW_BUDGET."""
        result = classify("set budget groceries 50")
        assert result.intent == Intent.SET_BUDGET


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
