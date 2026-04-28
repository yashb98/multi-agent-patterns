"""Test that aggregator detects patterns from SQLite, not just in-memory deque."""
import pytest
from shared.optimization._signals import SignalBus, LearningSignal
from shared.optimization._aggregator import SignalAggregator
from shared.optimization._tracker import PerformanceTracker


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "opt.db")


@pytest.fixture
def signal_bus(db_path):
    return SignalBus(db_path=db_path)


@pytest.fixture
def aggregator(db_path, signal_bus):
    tracker = PerformanceTracker(db_path=db_path)
    return SignalAggregator(signal_bus=signal_bus, tracker=tracker)


def test_realtime_detects_from_sqlite_after_deque_cleared(signal_bus, aggregator):
    """Aggregator must find patterns from SQLite even if deque was cleared."""
    for i in range(4):
        signal_bus.emit(LearningSignal(
            signal_type="correction",
            source_loop="correction_capture",
            domain="greenhouse.io",
            agent_name="form_filler",
            severity="info",
            payload={"field": "salary", "old_value": "40000", "new_value": "45000"},
            session_id=f"session_{i % 3}",
        ))

    signal_bus._recent.clear()
    assert len(signal_bus.recent()) == 0

    insights = aggregator.check_realtime()
    systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
    assert len(systemic) >= 1
    assert systemic[0].domain == "greenhouse.io"


def test_realtime_success_patterns_from_sqlite(signal_bus, aggregator):
    """Success streak detection works from SQLite after restart."""
    for i in range(4):
        signal_bus.emit(LearningSignal(
            signal_type="success",
            source_loop="form_experience",
            domain="linkedin.com",
            agent_name="form_filler",
            severity="info",
            payload={"action": "record_experience"},
            session_id=f"sess_{i % 3}",
        ))

    signal_bus._recent.clear()

    insights = aggregator.check_realtime()
    success = [i for i in insights if i.pattern_type == "success_streak"]
    assert len(success) >= 1
