"""Tests for tone framework — banned phrase filtering + proof point injection."""
import pytest
from unittest.mock import MagicMock


class TestBannedPhraseDetection:
    def test_detects_passionate_about(self):
        from jobpulse.tone_framework import contains_banned_phrase

        assert contains_banned_phrase("I am passionate about data science") is True

    def test_detects_proven_track_record(self):
        from jobpulse.tone_framework import contains_banned_phrase

        assert contains_banned_phrase("I have a proven track record in ML") is True

    def test_clean_text_passes(self):
        from jobpulse.tone_framework import contains_banned_phrase

        assert contains_banned_phrase("Built 3 production ML systems processing 10K+ records") is False


class TestApplyTone:
    def test_removes_banned_phrases(self):
        from jobpulse.tone_framework import apply_tone

        answer = "I am passionate about this role and have a proven track record."
        listing = MagicMock()
        listing.company = "TestCo"
        listing.title = "Data Analyst"
        listing.archetype = None
        result = apply_tone(answer, "why this role", listing)
        assert "passionate about" not in result.lower()
        assert "proven track record" not in result.lower()

    def test_preserves_clean_answers(self):
        from jobpulse.tone_framework import apply_tone

        answer = "Built 3 production ML systems. Reduced pipeline latency by 40%."
        listing = MagicMock()
        listing.company = "TestCo"
        listing.title = "ML Engineer"
        listing.archetype = None
        result = apply_tone(answer, "experience", listing)
        assert "production ML systems" in result

    def test_passthrough_on_empty(self):
        from jobpulse.tone_framework import apply_tone

        listing = MagicMock()
        listing.archetype = None
        assert apply_tone("", "question", listing) == ""


class TestClassifyQuestionType:
    def test_why_this_role(self):
        from jobpulse.tone_framework import classify_question_type

        assert classify_question_type("Why are you interested in this role?") == "why_this_role"

    def test_experience(self):
        from jobpulse.tone_framework import classify_question_type

        assert classify_question_type("Describe your relevant experience") == "relevant_experience"

    def test_unknown(self):
        from jobpulse.tone_framework import classify_question_type

        assert classify_question_type("asdfghjkl") == "other"
