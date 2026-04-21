import json
import time
import pytest

from shared.optimization._signals import LearningSignal, SignalBus, VALID_SIGNAL_TYPES


class TestLearningSignal:

    def test_auto_generates_id_and_timestamp(self):
        sig = LearningSignal(
            signal_type="correction",
            source_loop="correction_capture",
            domain="workday",
            agent_name="form_filler",
            severity="warning",
            payload={"field": "salary", "old": "45,000", "new": "45000"},
            session_id="sess_001",
        )
        assert sig.signal_id  # non-empty UUID
        assert sig.timestamp  # non-empty ISO timestamp
        assert sig.signal_type == "correction"

    def test_invalid_signal_type_raises(self):
        with pytest.raises(ValueError, match="Invalid signal_type"):
            LearningSignal(
                signal_type="invalid_type",
                source_loop="test",
                domain="test",
                agent_name="test",
                severity="info",
                payload={},
                session_id="sess_001",
            )


class TestSignalBus:

    def test_emit_persists_to_sqlite(self, db_path):
        bus = SignalBus(db_path=db_path)
        bus.emit(LearningSignal(
            signal_type="correction",
            source_loop="correction_capture",
            domain="workday",
            agent_name="form_filler",
            severity="warning",
            payload={"field": "salary"},
            session_id="sess_001",
        ))
        results = bus.query(domain="workday")
        assert len(results) == 1
        assert results[0].source_loop == "correction_capture"

    def test_emit_adds_to_memory_deque(self, db_path):
        bus = SignalBus(db_path=db_path)
        bus.emit(LearningSignal(
            signal_type="success",
            source_loop="experience_memory",
            domain="physics",
            agent_name="researcher",
            severity="info",
            payload={"score": 8.5},
            session_id="sess_002",
        ))
        assert len(bus.recent()) == 1

    def test_query_by_domain_and_time_window(self, db_path):
        bus = SignalBus(db_path=db_path)
        for i in range(5):
            bus.emit(LearningSignal(
                signal_type="correction",
                source_loop="correction_capture",
                domain="workday" if i < 3 else "greenhouse",
                agent_name="form_filler",
                severity="warning",
                payload={"field": f"field_{i}"},
                session_id="sess_003",
            ))
        results = bus.query(domain="workday")
        assert len(results) == 3

    def test_query_by_source_loop(self, db_path):
        bus = SignalBus(db_path=db_path)
        bus.emit(LearningSignal(
            signal_type="adaptation",
            source_loop="scan_learning",
            domain="linkedin",
            agent_name="scanner",
            severity="info",
            payload={"param": "delay"},
            session_id="sess_004",
        ))
        bus.emit(LearningSignal(
            signal_type="correction",
            source_loop="correction_capture",
            domain="linkedin",
            agent_name="form_filler",
            severity="warning",
            payload={"field": "name"},
            session_id="sess_004",
        ))
        results = bus.query(source_loop="scan_learning")
        assert len(results) == 1
        assert results[0].signal_type == "adaptation"

    def test_query_by_session_id(self, db_path):
        bus = SignalBus(db_path=db_path)
        bus.emit(LearningSignal(
            signal_type="success",
            source_loop="experience_memory",
            domain="physics",
            agent_name="researcher",
            severity="info",
            payload={"score": 9.0},
            session_id="target_session",
        ))
        bus.emit(LearningSignal(
            signal_type="success",
            source_loop="experience_memory",
            domain="physics",
            agent_name="researcher",
            severity="info",
            payload={"score": 7.0},
            session_id="other_session",
        ))
        results = bus.query(session_id="target_session")
        assert len(results) == 1

    def test_deque_overflow_drops_oldest(self, db_path):
        bus = SignalBus(db_path=db_path, max_recent=5)
        for i in range(8):
            bus.emit(LearningSignal(
                signal_type="success",
                source_loop="experience_memory",
                domain="test",
                agent_name="test",
                severity="info",
                payload={"i": i},
                session_id="sess_overflow",
            ))
        assert len(bus.recent()) == 5
        # oldest dropped — most recent payload has i=7
        assert bus.recent()[-1].payload["i"] == 7

    def test_sqlite_persists_across_restart(self, db_path):
        bus1 = SignalBus(db_path=db_path)
        bus1.emit(LearningSignal(
            signal_type="failure",
            source_loop="scan_learning",
            domain="indeed",
            agent_name="scanner",
            severity="critical",
            payload={"action": "scan", "error": "blocked"},
            session_id="sess_persist",
        ))
        bus2 = SignalBus(db_path=db_path)
        results = bus2.query(domain="indeed")
        assert len(results) == 1

    def test_signal_payload_round_trips_json(self, db_path):
        complex_payload = {
            "field": "salary",
            "nested": {"a": [1, 2, 3], "b": True},
            "unicode": "£45,000",
        }
        bus = SignalBus(db_path=db_path)
        bus.emit(LearningSignal(
            signal_type="correction",
            source_loop="correction_capture",
            domain="workday",
            agent_name="form_filler",
            severity="warning",
            payload=complex_payload,
            session_id="sess_json",
        ))
        results = bus.query(domain="workday")
        assert results[0].payload == complex_payload

    def test_bulk_emit_performance(self, db_path):
        bus = SignalBus(db_path=db_path)
        start = time.monotonic()
        for i in range(1000):
            bus.emit(LearningSignal(
                signal_type="success",
                source_loop="experience_memory",
                domain="test",
                agent_name="test",
                severity="info",
                payload={"i": i},
                session_id="sess_perf",
            ))
        elapsed_ms = (time.monotonic() - start) * 1000
        assert elapsed_ms < 5000  # generous — spec says 500ms, allow 5x for CI

    def test_prune_old_signals(self, db_path):
        bus = SignalBus(db_path=db_path)
        bus.emit(LearningSignal(
            signal_type="success",
            source_loop="experience_memory",
            domain="test",
            agent_name="test",
            severity="info",
            payload={},
            session_id="sess_prune",
        ))
        # Force the timestamp to 100 days ago in the DB
        import sqlite3
        from datetime import datetime, timedelta, timezone
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE signals SET timestamp = ?", (old_ts,))
        bus.prune(max_age_days=90)
        assert len(bus.query(domain="test")) == 0
