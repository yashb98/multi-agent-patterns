"""A2A HTTP Endpoints — agent card discovery, task CRUD, SSE streaming."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.execution._a2a_card import AgentRegistry
from shared.execution._a2a_task import TaskManager
from shared.logging_config import get_logger

logger = get_logger(__name__)


class CreateTaskRequest(BaseModel):
    source_agent: str
    skill_id: str
    input: dict = {}
    timeout_s: int = 120
    parent_task_id: str | None = None


def create_a2a_router(task_manager: TaskManager, registry: AgentRegistry) -> APIRouter:
    router = APIRouter()

    @router.get("/a2a/{agent_name}/card")
    def get_card(agent_name: str):
        card = registry.get(agent_name)
        if not card:
            raise HTTPException(404, f"Agent not found: {agent_name}")
        return card.to_dict()

    @router.post("/a2a/{agent_name}/task", status_code=201)
    def create_task(agent_name: str, req: CreateTaskRequest):
        card = registry.get(agent_name)
        if not card:
            raise HTTPException(404, f"Agent not found: {agent_name}")
        task = task_manager.create_task(
            source_agent=req.source_agent,
            target_agent=agent_name,
            skill_id=req.skill_id,
            input=req.input,
            timeout_s=req.timeout_s,
            parent_task_id=req.parent_task_id,
        )
        return task

    @router.get("/a2a/{agent_name}/task/{task_id}")
    def get_task(agent_name: str, task_id: str):
        task = task_manager.get_task(task_id)
        if not task:
            raise HTTPException(404, f"Task not found: {task_id}")
        return task

    return router
