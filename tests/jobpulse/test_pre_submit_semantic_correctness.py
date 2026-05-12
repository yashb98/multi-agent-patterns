"""Tests for PreSubmitGate.check_semantic_correctness.

Real production data used where possible (real screening answer patterns,
real profile shape). LLM judge is mocked because tests must not hit a
live API; the deterministic-checks branch runs against real-style data.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jobpulse.pre_submit_gate import (
    PreSubmitGate,
    _deterministic_consistency_checks,
    _yes_no,
)


# Real production profile shape (from APPLICANT_PROFILE)
REAL_PROFILE = {
    "first_name": "Yash",
    "last_name": "Bishnoi",
    "email": "yash@example.com",
    "phone": "+44 7000 000000",
    "location": "Dundee, UK",
    "visa_type": "Graduate Visa",
    "salary_expected": "£35,000-£42,000",
    "notice_period": "1 month",
}


class TestYesNoParser:
    def test_yes_variants(self):
        assert _yes_no("Yes") is True
        assert _yes_no("yes") is True
        assert _yes_no("TRUE") is True
        assert _yes_no("y") is True
        assert _yes_no("✓") is True

    def test_no_variants(self):
        assert _yes_no("No") is False
        assert _yes_no("FALSE") is False
        assert _yes_no("0") is False

    def test_ambiguous_returns_none(self):
        assert _yes_no("maybe") is None
        assert _yes_no("") is None
        assert _yes_no(None) is None


class TestDeterministicChecks:
    def test_visa_sponsorship_contradiction_detected(self):
        # Real production screening answer pattern from screening_answers.db
        filled = {
            "Do you have the right to work in the UK?": "Yes",
            "Do you require visa sponsorship?": "Yes",  # ← contradiction
        }
        issues = _deterministic_consistency_checks(filled)
        assert len(issues) >= 1
        assert any("contradiction" in i.lower() for i in issues)
        assert any("sponsorship" in i.lower() for i in issues)

    def test_consistent_visa_no_contradiction(self):
        # Yash's real profile pattern
        filled = {
            "Do you have the right to work in the UK?": "Yes",
            "Do you require visa sponsorship?": "No",
        }
        issues = _deterministic_consistency_checks(filled)
        assert not any("contradiction" in i.lower() for i in issues)

    def test_profile_name_mismatch_caught(self):
        filled = {"First Name": "Wrong"}
        issues = _deterministic_consistency_checks(filled, profile=REAL_PROFILE)
        assert any("first name" in i.lower() and "wrong" in i.lower() for i in issues)

    def test_profile_email_mismatch_caught(self):
        filled = {"Email Address": "wrong@nowhere.com"}
        issues = _deterministic_consistency_checks(filled, profile=REAL_PROFILE)
        assert any("email" in i.lower() and "wrong" in i.lower() for i in issues)

    def test_correct_profile_data_passes(self):
        filled = {
            "First Name": REAL_PROFILE["first_name"],
            "Email": REAL_PROFILE["email"],
        }
        issues = _deterministic_consistency_checks(filled, profile=REAL_PROFILE)
        # No mismatch issues
        mismatch_issues = [i for i in issues if "mismatch" in i.lower()]
        assert mismatch_issues == []

    def test_placeholder_value_caught(self):
        filled = {"Notice Period": "TBD"}
        issues = _deterministic_consistency_checks(filled)
        assert any("placeholder" in i.lower() or "tbd" in i.lower() for i in issues)

    def test_empty_value_caught(self):
        filled = {"Phone": ""}
        issues = _deterministic_consistency_checks(filled)
        assert any("empty" in i.lower() or "phone" in i.lower() for i in issues)

    def test_internal_keys_skipped(self):
        # Internal keys (prefixed with _) should not produce issues even if empty
        filled = {"_job_context": "", "_cl_generator": None}
        issues = _deterministic_consistency_checks(filled)
        assert not any("_job_context" in i or "_cl_generator" in i for i in issues)


class TestCheckSemanticCorrectness:
    def test_clean_answers_pass(self):
        gate = PreSubmitGate()
        filled = {
            "First Name": REAL_PROFILE["first_name"],
            "Email": REAL_PROFILE["email"],
            "Right to work in UK": "Yes",
            "Require sponsorship": "No",
        }
        # Mock LLM judge to return no issues
        with patch.object(gate, "_llm_field_judge", return_value=[]):
            result = gate.check_semantic_correctness(
                filled, jd_keywords=["python"], profile=REAL_PROFILE,
            )
        assert result.passed
        assert result.score >= PreSubmitGate.PASS_THRESHOLD

    def test_visa_contradiction_blocks(self):
        gate = PreSubmitGate()
        filled = {
            "Right to work in UK": "Yes",
            "Require sponsorship": "Yes",  # contradiction
        }
        with patch.object(gate, "_llm_field_judge", return_value=[]):
            result = gate.check_semantic_correctness(filled, profile=REAL_PROFILE)
        # 1 deterministic issue × 2 points = score 8 (still passes threshold of 7)
        # But weakness must be reported
        assert any("contradiction" in w.lower() for w in result.weaknesses)

    def test_multiple_issues_drop_score_below_threshold(self):
        gate = PreSubmitGate()
        filled = {
            "First Name": "WRONG",  # profile mismatch
            "Email": "wrong@nowhere.com",  # profile mismatch
            "Notice": "TBD",  # placeholder
        }
        with patch.object(gate, "_llm_field_judge", return_value=[]):
            result = gate.check_semantic_correctness(filled, profile=REAL_PROFILE)
        # 3 issues × 2 = 6 points lost → score 4 → blocks
        assert not result.passed
        assert result.score < PreSubmitGate.PASS_THRESHOLD

    def test_llm_judge_can_be_disabled(self):
        gate = PreSubmitGate()
        filled = {"First Name": REAL_PROFILE["first_name"]}
        # When run_llm_judge=False, _llm_field_judge MUST NOT be called
        with patch.object(gate, "_llm_field_judge") as mock_judge:
            gate.check_semantic_correctness(
                filled, profile=REAL_PROFILE, run_llm_judge=False,
            )
        mock_judge.assert_not_called()

    def test_llm_judge_issues_blend_with_deterministic(self):
        gate = PreSubmitGate()
        filled = {
            "Right to work in UK": "Yes",
            "Require sponsorship": "Yes",  # 1 deterministic issue
        }
        with patch.object(
            gate, "_llm_field_judge",
            return_value=["JD requires Python; agent filled C++ for primary language"],
        ):
            result = gate.check_semantic_correctness(filled, profile=REAL_PROFILE)
        # 1 deterministic + 1 LLM = 2 issues × 2 points = score 6 → blocks
        assert len(result.weaknesses) == 2
        assert any("Python" in w or "C++" in w for w in result.weaknesses)
        assert not result.passed
