"""Tests for scan pipeline event emission."""

import pytest
from unittest.mock import patch, MagicMock


class TestScanPipelineEvents:
    @patch("jobpulse.job_autopilot.JOB_AUTOPILOT_ENABLED", True)
    @patch("jobpulse.job_autopilot.is_paused", return_value=False)
    @patch("jobpulse.job_autopilot._applied_today", return_value=0)
    @patch("jobpulse.job_autopilot.JOB_AUTOPILOT_MAX_DAILY", 50)
    @patch("jobpulse.job_autopilot.JOB_AUTOPILOT_AUTO_SUBMIT", False)
    @patch("jobpulse.scan_pipeline.fetch_and_filter_jobs")
    @patch("jobpulse.scan_pipeline.analyze_and_deduplicate")
    @patch("jobpulse.scan_pipeline.prescreen_listings")
    @patch("jobpulse.scan_pipeline.generate_materials")
    @patch("jobpulse.job_autopilot.load_search_config")
    @patch("jobpulse.job_autopilot.send_jobs")
    @patch("jobpulse.job_autopilot.JobDB")
    def test_scan_emits_events(
        self, mock_db, mock_send, mock_config,
        mock_gen, mock_prescreen, mock_analyze, mock_fetch,
        mock_paused, mock_applied, event_store,
    ):
        mock_config.return_value = {}
        mock_fetch.return_value = ([], 0, 0)
        mock_analyze.return_value = []
        mock_prescreen.return_value = ([], 0, 0, 0)

        with patch("jobpulse.job_autopilot._get_event_store", return_value=event_store):
            from jobpulse.job_autopilot import _run_scan_window_inner
            _run_scan_window_inner(platforms=["linkedin"])

        events = event_store.query(stream_prefix="scan:")
        types = [e["event_type"] for e in events]
        assert "scan.window_started" in types
        assert "scan.window_done" in types
