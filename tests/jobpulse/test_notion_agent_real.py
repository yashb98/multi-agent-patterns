"""Tests for jobpulse/notion_agent.py — pure logic, no Notion API needed."""

import pytest


class TestNormalize:
    def test_strips_and_lowercases(self):
        from jobpulse.notion_agent import _normalize

        assert _normalize("  Hello World  ") == "hello world"

    def test_removes_punctuation(self):
        from jobpulse.notion_agent import _normalize

        result = _normalize("Finish (the) report!")
        assert "(" not in result
        assert "!" not in result

    def test_normalizes_word_numbers(self):
        from jobpulse.notion_agent import _normalize

        assert "1" in _normalize("day one")
        assert "3" in _normalize("three tasks")


class TestFuzzyScore:
    def test_exact_match(self):
        from jobpulse.notion_agent import _fuzzy_score

        score = _fuzzy_score("buy groceries", "buy groceries")
        assert score >= 0.9

    def test_partial_match(self):
        from jobpulse.notion_agent import _fuzzy_score

        score = _fuzzy_score("finish report", "finish the final report today")
        assert 0.0 < score <= 1.0

    def test_no_match(self):
        from jobpulse.notion_agent import _fuzzy_score

        score = _fuzzy_score("zzzzz xyzzy", "buy groceries")
        assert score == 0.0

    def test_empty_query(self):
        from jobpulse.notion_agent import _fuzzy_score

        assert _fuzzy_score("", "buy groceries") == 0.0

    def test_filler_only_query(self):
        from jobpulse.notion_agent import _fuzzy_score

        assert _fuzzy_score("the a an", "buy groceries") == 0.0

    def test_number_normalization(self):
        from jobpulse.notion_agent import _fuzzy_score

        score = _fuzzy_score("day one task", "day 1 task")
        assert score >= 0.9


class TestFormatTasks:
    def test_formats_task_list(self):
        from jobpulse.notion_agent import format_tasks

        tasks = [
            {"title": "Task A", "status": "Not started"},
            {"title": "Task B", "status": "Done"},
        ]
        result = format_tasks(tasks)
        assert "Task A" in result
        assert "Task B" in result
        assert "□" in result
        assert "✅" in result

    def test_empty_list(self):
        from jobpulse.notion_agent import format_tasks

        result = format_tasks([])
        assert "No tasks" in result


class TestParseDueDate:
    def test_today(self):
        from jobpulse.notion_agent import parse_due_date
        from datetime import datetime

        text, date_str = parse_due_date("call dentist today")
        assert date_str == datetime.now().strftime("%Y-%m-%d")
        assert "today" not in text.lower()

    def test_tomorrow(self):
        from jobpulse.notion_agent import parse_due_date

        text, date_str = parse_due_date("submit report by tomorrow")
        assert date_str is not None
        assert "tomorrow" not in text.lower()

    def test_no_date(self):
        from jobpulse.notion_agent import parse_due_date

        text, date_str = parse_due_date("just a plain task")
        assert date_str is None
        assert "just a plain task" in text
