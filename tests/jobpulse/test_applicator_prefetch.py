# tests/jobpulse/test_applicator_prefetch.py
"""Tests that apply_job injects form hints before calling the adapter."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.name = "mock"
    adapter.fill_and_submit.return_value = {
        "success": True,
        "pages_filled": 2,
        "field_types": ["text"],
        "screening_questions": [],
        "time_seconds": 10.0,
    }
    return adapter


def test_prefetch_hints_injected_on_dry_run(mock_adapter):
    with patch("jobpulse.applicator.select_adapter", return_value=mock_adapter), \
         patch("jobpulse.form_prefetch.prefetch_form_hints") as mock_prefetch:
        from jobpulse.form_prefetch import FormHints
        mock_prefetch.return_value = FormHints(
            known_domain=True, platform="greenhouse", expected_pages=3,
            field_types=["text", "select"], screening_questions=["Sponsorship?"],
            apply_count=1, avg_time_seconds=40.0,
        )

        from jobpulse.applicator import apply_job
        result = apply_job(
            url="https://boards.greenhouse.io/company/jobs/123",
            ats_platform="greenhouse",
            cv_path=Path("/tmp/cv.pdf"),
            dry_run=True,
        )

        mock_prefetch.assert_called_once()
        call_kwargs = mock_adapter.fill_and_submit.call_args
        answers = call_kwargs.kwargs.get("custom_answers") or call_kwargs[1].get("custom_answers", {})
        assert "_form_hints" in answers
        assert answers["_form_hints"]["known_domain"] is True
        assert answers["_form_hints"]["expected_pages"] == 3


def test_unknown_domain_no_hints_injected(mock_adapter):
    with patch("jobpulse.applicator.select_adapter", return_value=mock_adapter), \
         patch("jobpulse.form_prefetch.prefetch_form_hints") as mock_prefetch:
        from jobpulse.form_prefetch import FormHints
        mock_prefetch.return_value = FormHints()  # unknown domain

        from jobpulse.applicator import apply_job
        apply_job(
            url="https://never-seen-before.com/apply",
            ats_platform=None,
            cv_path=Path("/tmp/cv.pdf"),
            dry_run=True,
        )

        call_kwargs = mock_adapter.fill_and_submit.call_args
        answers = call_kwargs.kwargs.get("custom_answers") or call_kwargs[1].get("custom_answers", {})
        assert "_form_hints" not in answers


def test_screening_questions_pre_resolved_from_hints(mock_adapter):
    with patch("jobpulse.applicator.select_adapter", return_value=mock_adapter), \
         patch("jobpulse.form_prefetch.prefetch_form_hints") as mock_prefetch, \
         patch("jobpulse.screening_answers.get_answer", return_value="Yes") as mock_answer:
        from jobpulse.form_prefetch import FormHints
        mock_prefetch.return_value = FormHints(
            known_domain=True,
            screening_questions=["Do you require sponsorship?", "Willing to relocate?"],
        )

        from jobpulse.applicator import apply_job
        apply_job(
            url="https://boards.greenhouse.io/company/jobs/456",
            ats_platform="greenhouse",
            cv_path=Path("/tmp/cv.pdf"),
            dry_run=True,
        )

        # Verify screening questions from hints were pre-resolved
        screening_calls = [
            c for c in mock_answer.call_args_list
            if c[0][0] in ("Do you require sponsorship?", "Willing to relocate?")
        ]
        assert len(screening_calls) >= 2
