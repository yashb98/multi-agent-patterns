import time
import pytest


class TestEventStore:
    def test_emit_and_get_stream(self, event_store):
        event_store.emit(
            stream_id="scan:2026-04-21T09:00",
            event_type="scan.platform_started",
            payload={"platform": "linkedin"},
        )
        events = event_store.get_stream("scan:2026-04-21T09:00")
        assert len(events) == 1
        assert events[0]["event_type"] == "scan.platform_started"
        assert events[0]["payload"]["platform"] == "linkedin"

    def test_emit_generates_ulid(self, event_store):
        event_store.emit(
            stream_id="test:1",
            event_type="test.event",
            payload={"x": 1},
        )
        events = event_store.get_stream("test:1")
        assert len(events[0]["event_id"]) == 26  # ULID length

    def test_get_stream_ordered_by_created_at(self, event_store):
        for i in range(5):
            event_store.emit(
                stream_id="test:order",
                event_type="test.step",
                payload={"index": i},
            )
        events = event_store.get_stream("test:order")
        indices = [e["payload"]["index"] for e in events]
        assert indices == [0, 1, 2, 3, 4]

    def test_query_by_event_type(self, event_store):
        event_store.emit("s:1", "scan.started", {"a": 1})
        event_store.emit("s:1", "scan.done", {"b": 2})
        event_store.emit("s:2", "scan.started", {"c": 3})
        results = event_store.query(event_types=["scan.started"])
        assert len(results) == 2

    def test_query_by_stream_prefix(self, event_store):
        event_store.emit("form:greenhouse:oak:1", "form.started", {})
        event_store.emit("form:greenhouse:oak:2", "form.started", {})
        event_store.emit("scan:2026", "scan.started", {})
        results = event_store.query(stream_prefix="form:greenhouse")
        assert len(results) == 2

    def test_query_since_filter(self, event_store):
        event_store.emit("s:1", "test.old", {})
        since = event_store.get_stream("s:1")[0]["created_at"]
        event_store.emit("s:1", "test.new", {})
        results = event_store.query(stream_prefix="s:1", since=since)
        assert any(e["event_type"] == "test.new" for e in results)

    def test_metadata_includes_timestamp(self, event_store):
        event_store.emit("s:1", "test.meta", {}, metadata={"agent": "scan"})
        events = event_store.get_stream("s:1")
        assert "timestamp" in events[0]["metadata"]
        assert events[0]["metadata"]["agent"] == "scan"

    def test_schema_v_defaults_to_1(self, event_store):
        event_store.emit("s:1", "test.v", {})
        events = event_store.get_stream("s:1")
        assert events[0]["schema_v"] == 1

    def test_schema_v_custom(self, event_store):
        event_store.emit("s:1", "test.v2", {}, schema_v=2)
        events = event_store.get_stream("s:1")
        assert events[0]["schema_v"] == 2

    def test_snapshot_save_and_load(self, event_store):
        event_store.emit("s:1", "test.a", {"x": 1})
        event_store.emit("s:1", "test.b", {"x": 2})
        events = event_store.get_stream("s:1")
        last_id = events[-1]["event_id"]
        event_store.save_snapshot("s:1", {"projected": "state"}, last_id)
        snap = event_store.load_snapshot("s:1")
        assert snap is not None
        assert snap["snapshot_state"] == {"projected": "state"}
        assert snap["last_event_id"] == last_id

    def test_snapshot_returns_none_when_missing(self, event_store):
        assert event_store.load_snapshot("nonexistent") is None

    def test_incomplete_streams(self, event_store):
        event_store.emit("scan:a", "scan.window_started", {})
        event_store.emit("scan:a", "scan.window_done", {})
        event_store.emit("scan:b", "scan.window_started", {})
        incomplete = event_store.find_incomplete_streams(
            prefix="scan:", start_event="scan.window_started", end_event="scan.window_done"
        )
        assert incomplete == ["scan:b"]
