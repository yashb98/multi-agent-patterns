import pytest


class TestA2ATask:
    def test_create_task(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task(
            source_agent="scan-agent",
            target_agent="materials-agent",
            skill_id="generate-cv",
            input={"company": "OakNorth"},
            timeout_s=120,
        )
        assert task["status"] == "pending"
        assert task["source_agent"] == "scan-agent"
        assert task["target_agent"] == "materials-agent"
        assert len(task["task_id"]) == 26

    def test_create_emits_event(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        events = event_store.get_stream(f"task:{task['task_id']}")
        assert len(events) == 1
        assert events[0]["event_type"] == "task.created"

    def test_transition_pending_to_running(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        updated = mgr.transition(task["task_id"], "running")
        assert updated["status"] == "running"

    def test_transition_running_to_completed(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        mgr.transition(task["task_id"], "running")
        updated = mgr.transition(task["task_id"], "completed", output={"result": "ok"})
        assert updated["status"] == "completed"
        assert updated["output"] == {"result": "ok"}

    def test_invalid_transition_raises(self, event_store):
        from shared.execution._a2a_task import TaskManager, InvalidTransition
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        with pytest.raises(InvalidTransition):
            mgr.transition(task["task_id"], "completed")

    def test_get_task(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {"x": 1})
        found = mgr.get_task(task["task_id"])
        assert found is not None
        assert found["input"] == {"x": 1}

    def test_delegation_chain(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        parent = mgr.create_task("scan", "apply", "apply-job", {})
        child = mgr.create_task("apply", "materials", "gen-cv", {}, parent_task_id=parent["task_id"])
        assert child["parent_task_id"] == parent["task_id"]

    def test_history_tracks_transitions(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        mgr.transition(task["task_id"], "running")
        mgr.transition(task["task_id"], "completed")
        final = mgr.get_task(task["task_id"])
        assert len(final["history"]) == 3

    def test_task_timeout_constants(self):
        from shared.execution._a2a_task import TASK_TIMEOUTS
        assert TASK_TIMEOUTS["form_fill"] == 600
        assert TASK_TIMEOUTS["scan_window"] == 900
        assert TASK_TIMEOUTS["pattern_run"] == 420
