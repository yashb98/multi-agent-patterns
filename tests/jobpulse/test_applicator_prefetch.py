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


def test_apply_job_threads_job_context_to_adapter(mock_adapter):
    """bug_004 regression: apply_job must pass job=job_context to
    fill_and_submit so the orchestrator's _job_for_bypass / pre-seed bypass
    cache / pre-submit gate company stub all receive the cron path's job
    context. Without this, they all run with job=None and silently degrade.
    """
    with patch("jobpulse.applicator.select_adapter", return_value=mock_adapter), \
         patch("jobpulse.form_prefetch.prefetch_form_hints") as mock_prefetch:
        from jobpulse.form_prefetch import FormHints
        mock_prefetch.return_value = FormHints(known_domain=False)

        from jobpulse.applicator import apply_job
        ctx = {"job_id": "abc123", "company": "Acme Corp", "title": "DE"}
        apply_job(
            url="https://boards.greenhouse.io/acme/jobs/9",
            ats_platform="greenhouse",
            cv_path=Path("/tmp/cv.pdf"),
            job_context=ctx,
            dry_run=True,
        )

        call_kwargs = mock_adapter.fill_and_submit.call_args.kwargs
        assert call_kwargs.get("job") == ctx, (
            "apply_job must thread job=job_context to fill_and_submit so the "
            "orchestrator can populate _job_for_bypass / pre-seed bypass cache / "
            "pre-submit gate company stub"
        )


def test_apply_job_threads_job_context_on_external_redirect():
    """bug_004 regression for the external-redirect retry path. When the
    primary adapter returns external_redirect, apply_job re-calls
    fill_and_submit on the resolved ATS URL — that retry must also carry
    job=job_context.
    """
    primary = MagicMock()
    primary.name = "linkedin"
    primary.fill_and_submit.return_value = {
        "success": False,
        "external_redirect": True,
        "external_url": "https://boards.greenhouse.io/acme/jobs/9",
    }
    external = MagicMock()
    external.name = "greenhouse"
    external.fill_and_submit.return_value = {"success": True, "pages_filled": 1}

    def fake_select(platform):
        return primary if (platform or "").lower() == "linkedin" else external

    with patch("jobpulse.applicator.select_adapter", side_effect=fake_select), \
         patch("jobpulse.form_prefetch.prefetch_form_hints") as mock_prefetch:
        from jobpulse.form_prefetch import FormHints
        mock_prefetch.return_value = FormHints(known_domain=False)

        from jobpulse.applicator import apply_job
        ctx = {"job_id": "x9", "company": "Acme Corp", "title": "DE"}
        apply_job(
            url="https://www.linkedin.com/jobs/view/9",
            ats_platform="linkedin",
            cv_path=Path("/tmp/cv.pdf"),
            job_context=ctx,
            dry_run=True,
        )

        primary_call = primary.fill_and_submit.call_args.kwargs
        external_call = external.fill_and_submit.call_args.kwargs
        assert primary_call.get("job") == ctx, "primary adapter call missing job=job_context"
        assert external_call.get("job") == ctx, "external-redirect retry missing job=job_context"


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
