"""Tests for patterns/enhanced_swarm.py — real LLM via Ollama."""

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


class TestEnhancedSwarmGraph:
    def test_build_graph(self):
        from patterns.enhanced_swarm import build_enhanced_swarm_graph

        graph = build_enhanced_swarm_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        from patterns.enhanced_swarm import build_enhanced_swarm_graph

        graph = build_enhanced_swarm_graph()
        node_names = set(graph.nodes.keys())
        assert "task_analysis" in node_names
        assert "enhanced_researcher" in node_names
        assert "enhanced_writer" in node_names
        assert "enhanced_reviewer" in node_names


class TestEnhancedSwarmRouting:
    def test_route_after_convergence_finish(self):
        from patterns.enhanced_swarm import route_after_convergence
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["current_agent"] = "finish"
        assert route_after_convergence(state) == "finish"

    def test_route_after_convergence_continue(self):
        from patterns.enhanced_swarm import route_after_convergence
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["current_agent"] = "continue"
        assert route_after_convergence(state) == "enhanced_researcher"

    def test_route_default_is_finish(self):
        from patterns.enhanced_swarm import route_after_convergence
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        assert route_after_convergence(state) == "finish"


class TestEnhancedSwarmRealLLM:
    def test_task_analysis_produces_output(self):
        from patterns.enhanced_swarm import enhanced_task_analysis
        from shared.agents import create_initial_state

        state = create_initial_state("Compare Python and Go for web services")
        try:
            result = enhanced_task_analysis(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert isinstance(result, dict)
        assert "agent_history" in result

    def test_finish_packages_output(self):
        from patterns.enhanced_swarm import enhanced_finish
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["draft"] = "Python excels in rapid development."
        state["review_score"] = 8.0
        state["accuracy_score"] = 9.5
        state["research_notes"] = ["Python is versatile"]
        result = enhanced_finish(state)
        assert isinstance(result, dict)
