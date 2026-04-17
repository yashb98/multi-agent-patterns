"""Tests for ATS Unicode normalization — pure function, no I/O."""
import pytest


class TestNormalizeTextForAts:
    def test_replaces_em_dash(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Experience \u2014 3 years"
        result, counts = normalize_text_for_ats(text)
        assert result == "Experience - 3 years"
        assert counts["\u2014"] == 1

    def test_replaces_smart_quotes(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "\u201CHello\u201D and \u2018world\u2019"
        result, counts = normalize_text_for_ats(text)
        assert result == '"Hello" and \'world\''
        assert counts["\u201C"] == 1
        assert counts["\u201D"] == 1

    def test_removes_zero_width_chars(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Py\u200Bthon \u200CSkill\uFEFF"
        result, counts = normalize_text_for_ats(text)
        assert result == "Python Skill"

    def test_replaces_ellipsis(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Skills\u2026 more"
        result, counts = normalize_text_for_ats(text)
        assert result == "Skills... more"

    def test_replaces_nbsp(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "word\u00A0word"
        result, counts = normalize_text_for_ats(text)
        assert result == "word word"

    def test_idempotent(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Already clean text with no unicode"
        result1, counts1 = normalize_text_for_ats(text)
        result2, counts2 = normalize_text_for_ats(result1)
        assert result1 == result2
        assert all(v == 0 for v in counts2.values())

    def test_preserves_normal_text(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Built ML pipelines processing 10K+ records with 94% accuracy."
        result, counts = normalize_text_for_ats(text)
        assert result == text

    def test_en_dash(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "2024\u20132026"
        result, _ = normalize_text_for_ats(text)
        assert result == "2024-2026"
