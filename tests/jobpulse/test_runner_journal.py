import sys
from unittest.mock import patch, AsyncMock


def test_runner_journal_daily(monkeypatch):
    fake_pipeline = type("P", (), {"daily_journal": AsyncMock(return_value={"core_count": 9, "tangent_count": 1})})()
    with patch("research_journal.pipeline.JournalPipeline", return_value=fake_pipeline), \
         patch.object(sys, "argv", ["runner", "journal-daily"]):
        from jobpulse import runner
        runner.main()
    fake_pipeline.daily_journal.assert_awaited_once()
