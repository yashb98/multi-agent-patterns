"""Tests for patterns/hierarchical.py — real LLM via Ollama."""

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


class TestHierarchicalGraph:
    def test_build_graph_rule_based(self):
        from patterns.hierarchical import build_hierarchical_graph

        graph = build_hierarchical_graph(use_llm_supervisor=False)
        assert graph is not None

    def test_build_graph_llm_based(self):
        from patterns.hierarchical import build_hierarchical_graph

        graph = build_hierarchical_graph(use_llm_supervisor=True)
        assert graph is not None

    def test_graph_has_supervisor_node(self):
        from patterns.hierarchical import build_hierarchical_graph

        graph = build_hierarchical_graph()
        node_names = set(graph.nodes.keys())
        assert "supervisor" in node_names
        assert "finish" in node_names


class TestHierarchicalRouting:
    def test_route_from_supervisor_finish(self):
        from patterns.hierarchical import route_from_supervisor
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["current_agent"] = "FINISH"
        assert route_from_supervisor(state) == "finish"

    def test_route_from_supervisor_researcher(self):
        from patterns.hierarchical import route_from_supervisor
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["current_agent"] = "researcher"
        assert route_from_supervisor(state) == "researcher"

    def test_route_default_is_finish(self):
        from patterns.hierarchical import route_from_supervisor

        state = {}
        assert route_from_supervisor(state) == "finish"


class TestSupervisorRuleBased:
    def test_no_research_routes_to_researcher(self):
        from patterns.hierarchical import supervisor_node_rule_based
        from shared.agents import create_initial_state

        state = create_initial_state("Explain quantum computing")
        state["research_notes"] = []
        state["iteration"] = 0
        result = supervisor_node_rule_based(state)
        assert result.get("current_agent") in ("researcher", "FINISH")

    def test_has_research_no_draft_routes_to_writer(self):
        from patterns.hierarchical import supervisor_node_rule_based
        from shared.agents import create_initial_state

        state = create_initial_state("Explain quantum computing")
        state["research_notes"] = ["Quantum computing uses qubits"]
        state["draft"] = ""
        state["iteration"] = 0
        result = supervisor_node_rule_based(state)
        assert result.get("current_agent") in ("writer", "FINISH")


class TestHierarchicalHelpers:
    def test_extract_strengths(self):
        from patterns.hierarchical import _extract_strengths

        state = {"agent_history": [
            "Reviewer: Strengths: clear explanation, good examples",
        ]}
        strengths = _extract_strengths(state)
        assert isinstance(strengths, list)

    def test_extract_weaknesses(self):
        from patterns.hierarchical import _extract_weaknesses

        state = {"agent_history": [
            "Reviewer: Weaknesses: lacks depth",
        ]}
        weaknesses = _extract_weaknesses(state)
        assert isinstance(weaknesses, list)


class TestHierarchicalFinish:
    def test_finish_node_packages_output(self):
        from patterns.hierarchical import finish_node
        from shared.agents import create_initial_state

        state = create_initial_state("test")
        state["draft"] = "Final analysis of quantum computing."
        state["review_score"] = 8.5
        state["research_notes"] = ["Qubits enable superposition"]
        result = finish_node(state)
        assert isinstance(result, dict)
