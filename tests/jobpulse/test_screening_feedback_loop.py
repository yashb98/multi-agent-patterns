"""Tests for ScreeningFeedbackLoop — corrections teach the V2 pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.screening_feedback_loop import ScreeningFeedbackLoop


class TestLearnFromCorrection:
    def test_empty_question_returns_no_updates(self):
        loop = ScreeningFeedbackLoop(
            cache=MagicMock(), classifier=MagicMock(), aligner=MagicMock(), extractor=MagicMock(),
        )
        result = loop.learn_from_correction("", "a", "b")
        assert result == {
            "cache_updated": False,
            "intent_learned": False,
            "option_aligned": False,
            "pattern_recorded": False,
        }

    def test_learns_intent_from_correction(self):
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = (
            MagicMock(value="salary_expected"), 0.9
        )
        mock_intent = MagicMock()
        mock_intent.UNKNOWN = MagicMock(value="unknown")
        mock_classifier.add_intent_example = MagicMock()

        loop = ScreeningFeedbackLoop(
            cache=MagicMock(), classifier=mock_classifier, aligner=MagicMock(), extractor=MagicMock(),
        )
        loop._ScreeningIntent = mock_intent

        result = loop.learn_from_correction(
            question="What is your salary expectation?",
            agent_answer="40000",
            user_answer="45000",
        )
        assert result["intent_learned"] is True
        mock_classifier.add_intent_example.assert_called_once()

    def test_updates_cache_on_correction(self):
        mock_cache = MagicMock()
        loop = ScreeningFeedbackLoop(
            cache=mock_cache, classifier=MagicMock(), aligner=MagicMock(), extractor=MagicMock(),
        )

        result = loop.learn_from_correction(
            question="Work authorization?",
            agent_answer="No",
            user_answer="Yes",
        )
        assert result["cache_updated"] is True
        mock_cache.record_outcome.assert_called_once()
        mock_cache.cache.assert_called_once()

    def test_records_pattern_for_extractor(self):
        mock_extractor = MagicMock()
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = (
            MagicMock(value="work_auth"), 0.9
        )

        loop = ScreeningFeedbackLoop(
            cache=MagicMock(), classifier=mock_classifier, aligner=MagicMock(), extractor=mock_extractor,
        )

        result = loop.learn_from_correction(
            question="Right to work?",
            agent_answer="No",
            user_answer="Yes",
        )
        assert result["pattern_recorded"] is True
        assert mock_extractor.observe.call_count == 2  # failure + success

    def test_learns_option_mapping(self):
        mock_aligner = MagicMock()
        mock_aligner.align_answer.side_effect = [
            "Open to discussion",  # aligned wrong
            "Competitive",  # aligned right
        ]
        mock_aligner._normalise = lambda x: x.lower().strip()

        loop = ScreeningFeedbackLoop(
            cache=MagicMock(), classifier=MagicMock(), aligner=mock_aligner, extractor=MagicMock(),
        )

        result = loop.learn_from_correction(
            question="Salary expectations?",
            agent_answer="Open to discussion",
            user_answer="Competitive",
            field_options=["Open to discussion", "Competitive", "Fixed"],
        )
        assert result["option_aligned"] is True

    def test_option_skip_when_no_options(self):
        loop = ScreeningFeedbackLoop(
            cache=MagicMock(), classifier=MagicMock(), aligner=MagicMock(), extractor=MagicMock(),
        )
        result = loop.learn_from_correction(
            question="Tell us about yourself",
            agent_answer="bad",
            user_answer="good",
            field_options=None,
        )
        assert result["option_aligned"] is False

    def test_batch_learn(self):
        loop = ScreeningFeedbackLoop(
            cache=MagicMock(), classifier=MagicMock(), aligner=MagicMock(), extractor=MagicMock(),
        )
        with patch.object(loop, "learn_from_correction") as mock_learn:
            mock_learn.return_value = {"cache_updated": True}
            corrections = [
                {"question": "q1", "agent_answer": "a1", "user_answer": "u1"},
                {"question": "q2", "agent_answer": "a2", "user_answer": "u2"},
            ]
            results = loop.batch_learn(corrections)
            assert len(results) == 2
            assert mock_learn.call_count == 2


class TestLearnedOptionMappingPersistence:
    def test_lookup_returns_none_when_no_data(self):
        from jobpulse.screening_option_aligner import OptionAligner
        result = OptionAligner._lookup_learned_mapping("nonexistent", "")
        assert result is None
