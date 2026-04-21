import pytest
from unittest.mock import patch, MagicMock


class TestPatternCheckpointing:
    def test_enhanced_swarm_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.enhanced_swarm import build_enhanced_swarm_graph
        graph = build_enhanced_swarm_graph(checkpointer=cp)
        assert graph is not None

    def test_dynamic_swarm_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.dynamic_swarm import build_swarm_graph
        graph = build_swarm_graph(checkpointer=cp)
        assert graph is not None

    def test_plan_execute_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.plan_and_execute import build_plan_execute_graph
        graph = build_plan_execute_graph(checkpointer=cp)
        assert graph is not None

    def test_peer_debate_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.peer_debate import build_debate_graph
        graph = build_debate_graph(checkpointer=cp)
        assert graph is not None

    def test_hierarchical_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.hierarchical import build_hierarchical_graph
        graph = build_hierarchical_graph(checkpointer=cp)
        assert graph is not None

    def test_map_reduce_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.map_reduce import build_map_reduce_graph
        graph = build_map_reduce_graph(checkpointer=cp)
        assert graph is not None

    def test_default_no_checkpointer(self):
        """All patterns still work with no checkpointer (backwards compat)."""
        from patterns.enhanced_swarm import build_enhanced_swarm_graph
        graph = build_enhanced_swarm_graph()
        assert graph is not None
