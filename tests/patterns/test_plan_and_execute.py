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


class TestEvaluatorNode:
    def test_evaluator_returns_continue_when_steps_remain(self):
        from patterns.plan_and_execute import evaluator_node, create_initial_state, Step, StepResult
        import time as _time

        state = create_initial_state("test")
        state["plan"] = [
            Step(goal="A", expected_output="out", dependencies=[], delegate_to=None),
            Step(goal="B", expected_output="out", dependencies=[], delegate_to=None),
        ]
        state["completed_steps"] = [StepResult(step_index=0, output="result A", success=True)]
        state["current_step_index"] = 1
        state["start_time"] = _time.time()

        result = evaluator_node(state)
        assert result.get("eval_decision") == "continue"

    def test_evaluator_returns_synthesize_when_all_done(self):
        from patterns.plan_and_execute import evaluator_node, create_initial_state, Step, StepResult
        import time as _time

        state = create_initial_state("test")
        state["plan"] = [Step(goal="A", expected_output="out", dependencies=[], delegate_to=None)]
        state["completed_steps"] = [StepResult(step_index=0, output="result", success=True)]
        state["current_step_index"] = 1
        state["start_time"] = _time.time()

        result = evaluator_node(state)
        assert result.get("eval_decision") == "synthesize"

    def test_evaluator_returns_synthesize_on_timeout(self):
        from patterns.plan_and_execute import evaluator_node, create_initial_state, Step, StepResult, TOTAL_TIMEOUT_S
        import time as _time

        state = create_initial_state("test")
        state["plan"] = [
            Step(goal="A", expected_output="out", dependencies=[], delegate_to=None),
            Step(goal="B", expected_output="out", dependencies=[], delegate_to=None),
        ]
        state["completed_steps"] = [StepResult(step_index=0, output="result", success=True)]
        state["current_step_index"] = 1
        state["start_time"] = _time.time() - TOTAL_TIMEOUT_S - 10

        result = evaluator_node(state)
        assert result.get("eval_decision") == "synthesize"


class TestReplannerNode:
    def test_replanner_increments_count(self, monkeypatch):
        from patterns.plan_and_execute import replanner_node, create_initial_state, Step, StepResult
        import json

        new_steps = [{"goal": "New step", "expected_output": "out", "dependencies": [], "delegate_to": None}]
        monkeypatch.setattr("patterns.plan_and_execute.get_llm", lambda **kw: None)
        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: json.dumps(new_steps))

        state = create_initial_state("test")
        state["plan"] = [
            Step(goal="A", expected_output="out", dependencies=[], delegate_to=None),
            Step(goal="B", expected_output="out", dependencies=[], delegate_to=None),
        ]
        state["completed_steps"] = [StepResult(step_index=0, output="result", success=True)]
        state["current_step_index"] = 1
        state["replan_count"] = 0

        result = replanner_node(state)
        assert result["replan_count"] == 1
        assert len(result["plan"]) >= 1


class TestSynthesizerNode:
    def test_synthesizer_produces_final_output(self, monkeypatch):
        from patterns.plan_and_execute import synthesizer_node, create_initial_state, Step, StepResult

        monkeypatch.setattr("patterns.plan_and_execute.get_llm", lambda **kw: None)
        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: "Final synthesis output")

        state = create_initial_state("test topic")
        state["plan"] = [Step(goal="A", expected_output="out", dependencies=[], delegate_to=None)]
        state["completed_steps"] = [StepResult(step_index=0, output="Step A result", success=True)]

        result = synthesizer_node(state)
        assert result["final_output"] == "Final synthesis output"
        assert "synthesizer" in result["agent_history"][0]


class TestPlanExecuteGraph:
    def test_graph_builds_without_error(self):
        from patterns.plan_and_execute import build_plan_execute_graph
        graph = build_plan_execute_graph()
        assert graph is not None

    def test_run_plan_execute_end_to_end(self, monkeypatch):
        from patterns.plan_and_execute import run_plan_execute
        import json

        call_count = {"n": 0}
        plan = [{"goal": "Research", "expected_output": "findings", "dependencies": [], "delegate_to": None}]

        def mock_llm_call(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return json.dumps(plan)
            return "Mock output"

        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", mock_llm_call)
        monkeypatch.setattr("patterns.plan_and_execute.get_llm", lambda **kw: None)

        result = run_plan_execute("Test topic")
        assert isinstance(result, dict)
        assert result.get("final_output")
