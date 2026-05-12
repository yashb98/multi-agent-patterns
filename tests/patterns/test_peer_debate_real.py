"""Tests for patterns/peer_debate.py — real LLM via Ollama."""

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


class TestPeerDebateGraph:
    def test_build_debate_graph(self):
        from patterns.peer_debate import build_debate_graph

        graph = build_debate_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        from patterns.peer_debate import build_debate_graph

        graph = build_debate_graph()
        node_names = set(graph.nodes.keys())
        assert "debate_researcher" in node_names
        assert "debate_writer" in node_names
        assert "convergence" in node_names
        assert "synthesis" in node_names


class TestPeerDebateRouting:
    def test_route_after_convergence_continue(self):
        from patterns.peer_debate import route_after_convergence
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["current_agent"] = "continue"
        assert route_after_convergence(state) == "debate_researcher"

    def test_route_after_convergence_finish(self):
        from patterns.peer_debate import route_after_convergence
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["current_agent"] = "finish"
        assert route_after_convergence(state) == "synthesis"

    def test_route_default_is_synthesis(self):
        from patterns.peer_debate import route_after_convergence
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        assert route_after_convergence(state) == "synthesis"


class TestConvergenceCheck:
    def test_returns_valid_decision(self):
        from patterns.peer_debate import convergence_check
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["review_score"] = 9.0
        state["accuracy_score"] = 9.8
        state["iteration"] = 2
        state["draft"] = "A good draft."
        result = convergence_check(state)
        assert result.get("current_agent") in ("continue", "finish")

    def test_low_scores_returns_valid_decision(self):
        from patterns.peer_debate import convergence_check
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["review_score"] = 3.0
        state["accuracy_score"] = 4.0
        state["iteration"] = 0
        state["draft"] = "A weak draft."
        result = convergence_check(state)
        assert result.get("current_agent") in ("continue", "finish")


class TestPeerDebateRealLLM:
    def test_researcher_produces_notes(self):
        from patterns.peer_debate import debate_researcher_node
        from shared.agents import create_initial_state

        state = create_initial_state("Is Python better than Java for data science?")
        state["iteration"] = 0
        try:
            result = debate_researcher_node(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert "research_notes" in result
        assert len(result["research_notes"]) >= 1

    def test_synthesis_packages_output(self):
        from patterns.peer_debate import synthesis_node
        from shared.agents import create_initial_state

        state = create_initial_state("Python vs Java")
        state["draft"] = "Python dominates data science due to libraries."
        state["review_score"] = 8.5
        state["accuracy_score"] = 9.5
        state["research_notes"] = ["Python has pandas, numpy, sklearn"]
        try:
            result = synthesis_node(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert isinstance(result, dict)
