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
