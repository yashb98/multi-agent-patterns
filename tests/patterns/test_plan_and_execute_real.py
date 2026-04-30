"""Real-LLM tests for plan_and_execute pattern — no mocks."""

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


class TestPlanExecuteState:
    def test_create_initial_state(self):
        from patterns.plan_and_execute import create_initial_state

        state = create_initial_state("Build a REST API")
        assert state["topic"] == "Build a REST API"
        assert state["plan"] == []
        assert state["completed_steps"] == []
        assert state["current_step_index"] == 0
        assert state["replan_count"] == 0
        assert state["quality_score"] == 0.0

    def test_initial_state_has_start_time(self):
        import time
        from patterns.plan_and_execute import create_initial_state

        before = time.time()
        state = create_initial_state("test")
        assert state["start_time"] >= before


class TestPlanExecuteGraph:
    def test_build_graph(self):
        from patterns.plan_and_execute import build_plan_execute_graph

        graph = build_plan_execute_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        from patterns.plan_and_execute import build_plan_execute_graph

        graph = build_plan_execute_graph()
        node_names = set(graph.nodes.keys())
        assert "planner" in node_names
        assert "step_executor" in node_names


class TestPlanExecuteRouting:
    def test_route_after_eval_complete(self):
        from patterns.plan_and_execute import _route_after_eval, create_initial_state

        state = create_initial_state("test")
        state["eval_decision"] = "complete"
        assert _route_after_eval(state) == "synthesizer"

    def test_route_after_eval_continue(self):
        from patterns.plan_and_execute import _route_after_eval, create_initial_state

        state = create_initial_state("test")
        state["eval_decision"] = "continue"
        assert _route_after_eval(state) == "step_executor"

    def test_route_after_eval_replan(self):
        from patterns.plan_and_execute import _route_after_eval, create_initial_state

        state = create_initial_state("test")
        state["eval_decision"] = "replan"
        assert _route_after_eval(state) == "replanner"


class TestPlanExecuteRealLLM:
    def test_planner_produces_steps(self):
        from patterns.plan_and_execute import planner_node, create_initial_state

        state = create_initial_state("Compare REST vs GraphQL for mobile apps")
        try:
            result = planner_node(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert "plan" in result
        assert len(result["plan"]) >= 1

    def test_synthesizer_packages_output(self):
        from patterns.plan_and_execute import synthesizer_node, create_initial_state

        state = create_initial_state("Compare REST vs GraphQL")
        state["plan"] = [
            {"goal": "Research REST", "expected_output": "REST overview", "dependencies": [], "delegate_to": None},
            {"goal": "Research GraphQL", "expected_output": "GraphQL overview", "dependencies": [], "delegate_to": None},
        ]
        state["completed_steps"] = [
            {"step_index": 0, "output": "REST uses HTTP methods for CRUD.", "success": True},
            {"step_index": 1, "output": "GraphQL uses a single endpoint with queries.", "success": True},
        ]
        state["research_notes"] = ["REST is stateless", "GraphQL reduces overfetching"]
        try:
            result = synthesizer_node(state)
        except Exception as e:
            if "not found" in str(e).lower() or "api_key" in str(e).lower():
                pytest.skip(f"LLM not available: {e}")
            raise
        assert "final_output" in result
        assert len(result["final_output"]) > 0
