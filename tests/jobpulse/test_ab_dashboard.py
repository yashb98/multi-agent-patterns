"""Tests for A/B engine dashboard."""
import sys
from pathlib import Path
_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from unittest.mock import patch, MagicMock
from jobpulse.ab_dashboard import engine_stats, engine_compare, engine_learning


def test_engine_stats_no_data():
    with patch("jobpulse.ab_dashboard.ABTracker") as MockTracker:
        instance = MockTracker.return_value
        instance.get_engine_stats.return_value = {
            "total_fields": 0, "fields_filled": 0, "fields_verified": 0,
            "applications": 0, "submit_success": 0,
        }
        result = engine_stats()
        assert "Extension" in result
        assert "Playwright" in result


def test_engine_stats_with_days():
    with patch("jobpulse.ab_dashboard.ABTracker") as MockTracker:
        instance = MockTracker.return_value
        instance.get_engine_stats.return_value = {
            "total_fields": 10, "fields_filled": 8, "fields_verified": 7,
            "applications": 2, "submit_success": 1,
        }
        result = engine_stats("14")
        assert "14 days" in result


def test_engine_learning_no_data():
    with patch("jobpulse.ab_dashboard.ABTracker") as MockTracker:
        instance = MockTracker.return_value
        instance.db_path = ":memory:"
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch("sqlite3.connect", return_value=mock_conn):
            result = engine_learning()
        assert result == "No data yet."
