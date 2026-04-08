"""Tests for ABTracker — engine A/B tracking database."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from jobpulse.tracked_driver import ABTracker


def test_log_field_and_query(tmp_path):
    db_path = str(tmp_path / "ab_test.db")
    tracker = ABTracker(db_path=db_path)
    tracker.log_field(
        application_id="app_1", engine="playwright", platform="greenhouse",
        action="fill", selector="#email", success=True, value_verified=True,
        duration_ms=120, error=None, retry_count=0,
    )
    stats = tracker.get_engine_stats("playwright", days=7)
    assert stats["total_fields"] == 1
    assert stats["fields_verified"] == 1


def test_log_outcome(tmp_path):
    db_path = str(tmp_path / "ab_test.db")
    tracker = ABTracker(db_path=db_path)
    tracker.log_outcome(
        app_id="app_1", engine="extension", platform="lever",
        domain="jobs.lever.co", total_fields=10, fields_filled=9,
        fields_verified=8, validation_errors=0, outcome="submitted",
        total_duration_s=180.5, pages_navigated=3,
        fixes_applied=1, fixes_learned=0,
    )
    stats = tracker.get_engine_stats("extension", days=7)
    assert stats["applications"] == 1
    assert stats["submit_success"] == 1


def test_stats_filters_by_engine(tmp_path):
    """Stats for one engine don't include the other."""
    db_path = str(tmp_path / "ab_test.db")
    tracker = ABTracker(db_path=db_path)
    tracker.log_field(application_id="a1", engine="playwright", action="fill", success=True)
    tracker.log_field(application_id="a2", engine="extension", action="fill", success=True)
    pw = tracker.get_engine_stats("playwright")
    ext = tracker.get_engine_stats("extension")
    assert pw["total_fields"] == 1
    assert ext["total_fields"] == 1


def test_log_field_with_error(tmp_path):
    db_path = str(tmp_path / "ab_test.db")
    tracker = ABTracker(db_path=db_path)
    tracker.log_field(
        application_id="app_1", engine="playwright", action="fill",
        selector="#name", success=False, error="Element not found",
    )
    stats = tracker.get_engine_stats("playwright")
    assert stats["total_fields"] == 1
    assert stats["fields_filled"] == 0


def test_multiple_fields_aggregation(tmp_path):
    db_path = str(tmp_path / "ab_test.db")
    tracker = ABTracker(db_path=db_path)
    for i in range(5):
        tracker.log_field(
            application_id="app_1", engine="playwright", action="fill",
            selector=f"#field_{i}", success=True, value_verified=(i < 3),
        )
    stats = tracker.get_engine_stats("playwright")
    assert stats["total_fields"] == 5
    assert stats["fields_filled"] == 5
    assert stats["fields_verified"] == 3


def test_outcome_replace_on_duplicate_app_id(tmp_path):
    """INSERT OR REPLACE updates outcome for same app_id."""
    db_path = str(tmp_path / "ab_test.db")
    tracker = ABTracker(db_path=db_path)
    base = dict(engine="pw", platform="gh", domain="d", total_fields=5,
                fields_filled=5, fields_verified=5, validation_errors=0,
                total_duration_s=100, pages_navigated=2, fixes_applied=0, fixes_learned=0)
    tracker.log_outcome(app_id="a1", outcome="error", **base)
    tracker.log_outcome(app_id="a1", outcome="submitted", **base)
    stats = tracker.get_engine_stats("pw")
    assert stats["applications"] == 1
    assert stats["submit_success"] == 1
