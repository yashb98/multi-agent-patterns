"""Tests for Screening V2 integration into screening_answers.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.screening_answers import try_screening_v2


class TestTryScreeningV2:
    def test_empty_question_returns_none(self):
        assert try_screening_v2("") is None
        assert try_screening_v2("   ") is None

    def test_test_mode_returns_none(self, monkeypatch):
        """V2 is skipped in test mode to preserve legacy test determinism."""
        monkeypatch.setenv("JOBPULSE_TEST_MODE", "1")
        assert try_screening_v2("Any question?") is None

    def test_pipeline_unavailable_returns_none(self, monkeypatch):
        monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
        with patch(
            "jobpulse.screening_answers._get_v2_pipeline", return_value=None
        ):
            assert try_screening_v2("Any question?") is None

    def test_low_confidence_returns_none(self, monkeypatch):
        monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
        mock_pipeline = MagicMock()
        mock_pipeline.answer.return_value = {
            "answer": "some answer",
            "confidence": 0.3,
            "source": "regex_fallback",
        }
        with patch(
            "jobpulse.screening_answers._get_v2_pipeline", return_value=mock_pipeline
        ):
            assert try_screening_v2("Any question?", min_confidence=0.55) is None

    def test_high_confidence_returns_answer(self, monkeypatch):
        monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
        mock_pipeline = MagicMock()
        mock_pipeline.answer.return_value = {
            "answer": "Yes, I have the right to work",
            "confidence": 0.85,
            "source": "intent_resolver",
        }
        with patch(
            "jobpulse.screening_answers._get_v2_pipeline", return_value=mock_pipeline
        ):
            result = try_screening_v2("Do you have the right to work in the UK?")
            assert result == "Yes, I have the right to work"
            mock_pipeline.answer.assert_called_once()

    def test_pipeline_exception_returns_none(self, monkeypatch):
        monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
        mock_pipeline = MagicMock()
        mock_pipeline.answer.side_effect = RuntimeError("Qdrant down")
        with patch(
            "jobpulse.screening_answers._get_v2_pipeline", return_value=mock_pipeline
        ):
            assert try_screening_v2("Any question?") is None

    def test_passes_field_to_pipeline(self, monkeypatch):
        monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
        mock_pipeline = MagicMock()
        mock_pipeline.answer.return_value = {
            "answer": "Yes",
            "confidence": 0.9,
            "source": "intent_resolver",
        }
        field = {"type": "radio", "options": ["Yes", "No"]}
        with patch(
            "jobpulse.screening_answers._get_v2_pipeline", return_value=mock_pipeline
        ):
            try_screening_v2("Work auth?", field=field)
            _call = mock_pipeline.answer.call_args
            assert _call.kwargs["field"] == field

    def test_passes_job_context_to_pipeline(self, monkeypatch):
        monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
        mock_pipeline = MagicMock()
        mock_pipeline.answer.return_value = {
            "answer": "London",
            "confidence": 0.8,
            "source": "intent_resolver",
        }
        ctx = {"company": "Acme", "job_title": "Engineer"}
        with patch(
            "jobpulse.screening_answers._get_v2_pipeline", return_value=mock_pipeline
        ):
            try_screening_v2("Location?", job_context=ctx)
            _call = mock_pipeline.answer.call_args
            assert _call.kwargs["job_context"] == ctx

    def test_empty_answer_returns_none(self, monkeypatch):
        monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
        mock_pipeline = MagicMock()
        mock_pipeline.answer.return_value = {
            "answer": "",
            "confidence": 0.9,
            "source": "intent_resolver",
        }
        with patch(
            "jobpulse.screening_answers._get_v2_pipeline", return_value=mock_pipeline
        ):
            assert try_screening_v2("Any question?") is None
