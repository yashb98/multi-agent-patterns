"""A2A Task Lifecycle — create, transition, and track agent tasks.

All mutations emit events. Task state is reconstructed from events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict

from ulid import ULID

from shared.execution._event_store import EventStore
from shared.logging_config import get_logger

logger = get_logger(__name__)

TASK_TIMEOUTS = {
    "form_fill": 600,
    "scan_window": 900,
    "pattern_run": 420,
    "materials": 120,
    "budget": 30,
}

VALID_TRANSITIONS = {
    "pending": {"running", "failed"},
    "running": {"verifying", "completed", "failed", "escalated", "timed_out"},
    "verifying": {"completed", "failed"},
    "escalated": {"running", "completed", "failed"},
    "timed_out": {"escalated", "failed"},
    "completed": set(),
    "failed": {"pending"},
}


class InvalidTransition(Exception):
    pass


class A2ATask(TypedDict):
    task_id: str
    parent_task_id: str | None
    source_agent: str
    target_agent: str
    skill_id: str
    input: dict
    status: str
    output: dict | None
    artifacts: list[dict]
    history: list[dict]
    timeout_s: int
    created_at: str
    updated_at: str


class TaskManager:
    """Manages A2A task lifecycle. All mutations go through the event store."""

    def __init__(self, event_store: EventStore):
        self._store = event_store
        self._tasks: dict[str, A2ATask] = {}

    def create_task(
        self,
        source_agent: str,
        target_agent: str,
        skill_id: str,
        input: dict,
        timeout_s: int = 120,
        parent_task_id: str | None = None,
    ) -> A2ATask:
        task_id = str(ULID())
        now = datetime.now(timezone.utc).isoformat()
        task = A2ATask(
            task_id=task_id,
            parent_task_id=parent_task_id,
            source_agent=source_agent,
            target_agent=target_agent,
            skill_id=skill_id,
            input=input,
            status="pending",
            output=None,
            artifacts=[],
            history=[{"status": "pending", "timestamp": now}],
            timeout_s=timeout_s,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task_id] = task
        self._store.emit(
            stream_id=f"task:{task_id}",
            event_type="task.created",
            payload={
                "source_agent": source_agent,
                "target_agent": target_agent,
                "skill_id": skill_id,
                "parent_task_id": parent_task_id,
            },
        )
        logger.info("Task created: %s (%s → %s:%s)", task_id[:8], source_agent, target_agent, skill_id)
        return task

    def transition(
        self,
        task_id: str,
        new_status: str,
        output: dict | None = None,
        artifacts: list[dict] | None = None,
    ) -> A2ATask:
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(f"Task {task_id} not found")
        current = task["status"]
        if new_status not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransition(f"Cannot transition from {current} to {new_status}")
        now = datetime.now(timezone.utc).isoformat()
        task["status"] = new_status
        task["updated_at"] = now
        task["history"].append({"status": new_status, "timestamp": now})
        if output is not None:
            task["output"] = output
        if artifacts:
            task["artifacts"].extend(artifacts)
        self._store.emit(
            stream_id=f"task:{task_id}",
            event_type=f"task.{new_status}",
            payload={"from_status": current, "output": output},
        )
        logger.info("Task %s: %s → %s", task_id[:8], current, new_status)
        return task

    def get_task(self, task_id: str) -> A2ATask | None:
        return self._tasks.get(task_id)
