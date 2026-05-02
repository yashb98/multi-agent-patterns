"""dry_run must survive the route_and_apply seam."""
from unittest.mock import patch, MagicMock
import pytest
from jobpulse.scan_pipeline import route_and_apply


def _make_listing():
    listing = MagicMock()
    listing.url = "https://example.com/job/1"
    listing.job_id = "job-1234"
    listing.title = "Software Engineer"
    listing.company = "Example Co"
    listing.ats_platform = "greenhouse"
    listing.easy_apply = True
    return listing


def _make_bundle():
    from pathlib import Path
    bundle = MagicMock()
    bundle.ats_score = 96.0
    bundle.cv_path = Path("/tmp/cv.pdf")
    bundle.cover_letter_path = Path("/tmp/cl.pdf")
    bundle.notion_page_id = "page-1"
    bundle.matched_project_names = []
    return bundle


class TestDryRunPropagation:
    def test_route_and_apply_passes_dry_run_to_apply_job(self):
        with patch("jobpulse.scan_pipeline.apply_job") as mock_apply:
            mock_apply.return_value = {"success": True, "submitted": False}
            route_and_apply(
                listing=_make_listing(),
                bundle=_make_bundle(),
                db=MagicMock(),
                review_batch=[],
                remaining_cap=10,
                auto_applied=0,
                dry_run=True,
            )
        assert mock_apply.called
        call_kwargs = mock_apply.call_args.kwargs
        assert call_kwargs.get("dry_run") is True, (
            f"apply_job was called without dry_run=True. kwargs were: {call_kwargs}"
        )

    def test_route_and_apply_dry_run_default_is_true(self):
        """Safer-by-default: callers that forget to pass dry_run should NOT submit."""
        with patch("jobpulse.scan_pipeline.apply_job") as mock_apply:
            mock_apply.return_value = {"success": True, "submitted": False}
            route_and_apply(
                listing=_make_listing(),
                bundle=_make_bundle(),
                db=MagicMock(),
                review_batch=[],
                remaining_cap=10,
                auto_applied=0,
            )
        call_kwargs = mock_apply.call_args.kwargs
        assert call_kwargs.get("dry_run") is True

    def test_route_and_apply_explicit_false_is_passed_through(self):
        """The cron auto-submit path explicitly opts out."""
        with patch("jobpulse.scan_pipeline.apply_job") as mock_apply:
            mock_apply.return_value = {"success": True, "submitted": True}
            route_and_apply(
                listing=_make_listing(),
                bundle=_make_bundle(),
                db=MagicMock(),
                review_batch=[],
                remaining_cap=10,
                auto_applied=0,
                dry_run=False,
            )
        call_kwargs = mock_apply.call_args.kwargs
        assert call_kwargs.get("dry_run") is False
