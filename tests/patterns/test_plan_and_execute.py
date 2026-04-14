import pytest


class TestPlanExecuteState:
    def test_step_has_required_fields(self):
        from patterns.plan_and_execute import Step
        step = Step(goal="research X", expected_output="summary of X", dependencies=[], delegate_to=None)
        assert step["goal"] == "research X"
        assert step["dependencies"] == []

    def test_step_result_has_required_fields(self):
        from patterns.plan_and_execute import StepResult
        sr = StepResult(step_index=0, output="done", success=True)
        assert sr["step_index"] == 0
        assert sr["success"] is True

    def test_initial_state_has_defaults(self):
        from patterns.plan_and_execute import create_initial_state
        state = create_initial_state("test topic")
        assert state["topic"] == "test topic"
        assert state["plan"] == []
        assert state["completed_steps"] == []
        assert state["current_step_index"] == 0
        assert state["replan_count"] == 0
        assert state["final_output"] == ""
        assert state["quality_score"] == 0.0
        assert state["accuracy_score"] == 0.0


class TestPlannerNode:
    def test_planner_produces_steps(self, monkeypatch):
        from patterns.plan_and_execute import planner_node, create_initial_state
        import json

        plan_json = json.dumps([
            {"goal": "Research topic", "expected_output": "Summary", "dependencies": [], "delegate_to": None},
            {"goal": "Analyze findings", "expected_output": "Analysis", "dependencies": [0], "delegate_to": None},
        ])
        monkeypatch.setattr("patterns.plan_and_execute.get_llm", lambda: None)
        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: plan_json)

        state = create_initial_state("test query")
        result = planner_node(state)
        assert len(result["plan"]) == 2
        assert result["plan"][0]["goal"] == "Research topic"
        assert "planner" in result["agent_history"][0]

    def test_planner_caps_at_max_steps(self, monkeypatch):
        from patterns.plan_and_execute import planner_node, create_initial_state, MAX_STEPS
        import json

        steps = [{"goal": f"Step {i}", "expected_output": "out", "dependencies": [], "delegate_to": None} for i in range(10)]
        monkeypatch.setattr("patterns.plan_and_execute.get_llm", lambda: None)
        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: json.dumps(steps))

        state = create_initial_state("big query")
        result = planner_node(state)
        assert len(result["plan"]) <= MAX_STEPS


class TestStepExecutorNode:
    def test_executor_runs_step_and_appends_result(self, monkeypatch):
        from patterns.plan_and_execute import step_executor_node, create_initial_state, Step

        monkeypatch.setattr("patterns.plan_and_execute.get_llm", lambda: None)
        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: "Step output here")

        state = create_initial_state("test")
        state["plan"] = [
            Step(goal="Do research", expected_output="findings", dependencies=[], delegate_to=None),
        ]
        state["current_step_index"] = 0
        result = step_executor_node(state)
        assert len(result["completed_steps"]) == 1
        assert result["completed_steps"][0]["output"] == "Step output here"
        assert result["completed_steps"][0]["success"] is True
