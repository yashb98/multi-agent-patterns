"""Tests for patterns/dynamic_swarm.py — real LLM via Ollama."""

import httpx
import pytest


def _ollama_available():
    try:
        return httpx.get("http://localhost:11434/api/tags", timeout=2).status_code == 200
    except Exception:
        return False


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _ollama_available(), reason="Ollama not running"),
]


class TestDynamicSwarmGraph:
    def test_build_swarm_graph(self):
        from patterns.dynamic_swarm import build_swarm_graph

        graph = build_swarm_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        from patterns.dynamic_swarm import build_swarm_graph

        graph = build_swarm_graph()
        node_names = set(graph.nodes.keys())
        assert "analyzer" in node_names
        assert "executor" in node_names
        assert "finish" in node_names


class TestDynamicSwarmRouting:
    def test_should_continue_swarm_finishes_on_convergence(self):
        from patterns.dynamic_swarm import should_continue_swarm
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["iteration"] = 3
        state["review_score"] = 9.0
        state["accuracy_score"] = 9.8
        state["current_agent"] = "FINISH"
        result = should_continue_swarm(state)
        assert result == "finish"

    def test_should_continue_after_analysis_with_tasks(self):
        from patterns.dynamic_swarm import should_continue_after_analysis
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["pending_tasks"] = [{"agent": "researcher", "description": "research"}]
        assert should_continue_after_analysis(state) == "executor"

    def test_should_continue_after_analysis_empty(self):
        from patterns.dynamic_swarm import should_continue_after_analysis
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["pending_tasks"] = []
        assert should_continue_after_analysis(state) == "finish"


class TestFallbackDecomposition:
    def test_no_research_suggests_researcher(self):
        from patterns.dynamic_swarm import _fallback_task_decomposition
        from shared.agents import create_initial_state

        state = create_initial_state("test topic")
        state["research_notes"] = []
        tasks = _fallback_task_decomposition(state)
        assert len(tasks) >= 1
        assert tasks[0]["agent"] == "researcher"

    def test_has_research_no_draft_suggests_writer(self):
        from patterns.dynamic_swarm import _fallback_task_decomposition
        from shared.agents import create_initial_state

        state = create_initial_state("test topic")
        state["research_notes"] = ["some research"]
        state["draft"] = ""
        tasks = _fallback_task_decomposition(state)
        assert len(tasks) >= 1
        assert tasks[0]["agent"] == "writer"

    def test_has_draft_no_review_suggests_reviewer(self):
        from patterns.dynamic_swarm import _fallback_task_decomposition
        from shared.agents import create_initial_state

        state = create_initial_state("test topic")
        state["research_notes"] = ["some research"]
        state["draft"] = "some draft"
        state["review_feedback"] = ""
        tasks = _fallback_task_decomposition(state)
        assert len(tasks) >= 1
        assert tasks[0]["agent"] == "reviewer"


class TestDynamicSwarmRealLLM:
    def test_task_analyzer_produces_tasks(self):
        from patterns.dynamic_swarm import task_analyzer_node
        from shared.agents import create_initial_state

        state = create_initial_state("What are the benefits of test-driven development?")
        state["iteration"] = 0
        try:
            result = task_analyzer_node(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert any(k in result for k in ["research_notes", "draft", "pending_tasks", "agent_history"])

    def test_swarm_finish_packages_output(self):
        from patterns.dynamic_swarm import swarm_finish_node
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["draft"] = "A completed analysis of TDD benefits."
        state["review_score"] = 8.5
        state["research_notes"] = ["TDD reduces bugs"]
        result = swarm_finish_node(state)
        assert "draft" in result or "final_output" in result or "agent_history" in result
