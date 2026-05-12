"""Real-LLM tests for map_reduce pattern — no mocks."""

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


class TestMapReduceState:
    def test_create_initial_state(self):
        from patterns.map_reduce import create_initial_state

        state = create_initial_state("Compare Python, Rust, Go")
        assert state["topic"] == "Compare Python, Rust, Go"
        assert state["chunks"] == []
        assert state["map_results"] == []
        assert state["reduced_output"] == ""
        assert state["quality_score"] == 0.0

    def test_initial_state_has_annotated_fields(self):
        from patterns.map_reduce import create_initial_state

        state = create_initial_state("test")
        assert isinstance(state["token_usage"], list)
        assert isinstance(state["agent_history"], list)


class TestMapReduceGraph:
    def test_build_graph(self):
        from patterns.map_reduce import build_map_reduce_graph

        graph = build_map_reduce_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        from patterns.map_reduce import build_map_reduce_graph

        graph = build_map_reduce_graph()
        node_names = set(graph.nodes.keys())
        assert "splitter" in node_names
        assert "mapper" in node_names or "map" in node_names
        assert "reducer" in node_names or "reduce" in node_names


class TestMapReduceRealLLM:
    def test_splitter_produces_chunks(self):
        from patterns.map_reduce import splitter_node, create_initial_state

        state = create_initial_state("Compare 3 programming languages: Python, Rust, Go")
        try:
            result = splitter_node(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert len(result.get("chunks", [])) >= 1

    def test_reducer_synthesizes(self):
        from patterns.map_reduce import reducer_node, create_initial_state

        state = create_initial_state("Compare languages")
        state["chunks"] = ["Python", "Rust"]
        state["map_results"] = [
            "Python is dynamically typed with extensive libraries.",
            "Rust provides memory safety without garbage collection.",
        ]
        try:
            result = reducer_node(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert len(result.get("reduced_output", "")) > 0

    def test_reconciler_scores_output(self):
        from patterns.map_reduce import reconciler_node, create_initial_state

        state = create_initial_state("Compare languages")
        state["reduced_output"] = "Python and Rust serve different needs. Python excels in rapid prototyping while Rust provides systems-level performance."
        state["chunks"] = ["Python", "Rust"]
        state["map_results"] = ["Python analysis", "Rust analysis"]
        try:
            result = reconciler_node(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert isinstance(result, dict)
