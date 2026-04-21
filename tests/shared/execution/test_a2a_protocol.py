import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def a2a_app(event_store, tmp_path):
    from shared.execution._a2a_protocol import create_a2a_router
    from shared.execution._a2a_task import TaskManager
    from shared.execution._a2a_card import FileAgentRegistry, AgentCard
    from fastapi import FastAPI
    mgr = TaskManager(event_store)
    registry = FileAgentRegistry(path=str(tmp_path / "test_agents.json"))
    registry.register(AgentCard(name="test-agent", description="t", url="http://localhost", skills=[]))
    app = FastAPI()
    app.include_router(create_a2a_router(mgr, registry))
    return app


@pytest.fixture
def a2a_client(a2a_app):
    return TestClient(a2a_app)


class TestA2AEndpoints:
    def test_get_agent_card(self, a2a_client):
        resp = a2a_client.get("/a2a/test-agent/card")
        assert resp.status_code == 200
        assert resp.json()["name"] == "test-agent"

    def test_get_unknown_agent_card(self, a2a_client):
        resp = a2a_client.get("/a2a/nonexistent/card")
        assert resp.status_code == 404

    def test_create_task(self, a2a_client):
        resp = a2a_client.post("/a2a/test-agent/task", json={
            "source_agent": "caller",
            "skill_id": "test-skill",
            "input": {"x": 1},
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"

    def test_get_task(self, a2a_client):
        create_resp = a2a_client.post("/a2a/test-agent/task", json={
            "source_agent": "caller", "skill_id": "s", "input": {},
        })
        task_id = create_resp.json()["task_id"]
        resp = a2a_client.get(f"/a2a/test-agent/task/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == task_id
