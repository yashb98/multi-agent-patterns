"""Tests for EventStoreCheckpointer — LangGraph checkpoint saver backed by event store."""

import pytest


class TestEventStoreCheckpointer:
    def test_put_emits_checkpoint_event(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer

        cp = EventStoreCheckpointer(event_store)
        config = {"configurable": {"thread_id": "run_abc"}}
        checkpoint = {"channel_values": {"draft": "hello", "iteration": 1}}
        metadata = {"step": 2, "source": "loop"}
        cp.put(config, checkpoint, metadata)
        events = event_store.get_stream("pattern:run_abc", event_type="pattern.checkpoint")
        assert len(events) == 1
        assert events[0]["payload"]["checkpoint"] == checkpoint

    def test_get_tuple_returns_none_when_empty(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer

        cp = EventStoreCheckpointer(event_store)
        config = {"configurable": {"thread_id": "run_xyz"}}
        result = cp.get_tuple(config)
        assert result is None

    def test_get_tuple_returns_latest_checkpoint(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer

        cp = EventStoreCheckpointer(event_store)
        config = {"configurable": {"thread_id": "run_abc"}}
        cp.put(config, {"v": 1}, {"step": 1})
        cp.put(config, {"v": 2}, {"step": 2})
        result = cp.get_tuple(config)
        assert result is not None
        assert result.checkpoint == {"v": 2}

    def test_list_returns_all_checkpoints(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer

        cp = EventStoreCheckpointer(event_store)
        config = {"configurable": {"thread_id": "run_list"}}
        cp.put(config, {"v": 1}, {"step": 1})
        cp.put(config, {"v": 2}, {"step": 2})
        results = list(cp.list(config))
        assert len(results) == 2
