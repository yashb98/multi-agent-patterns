import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def authed_app(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-secret-token")
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    from shared.governance._api_auth import require_auth
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/data")
    def data():
        return {"secret": "value"}

    require_auth(app)
    return app


@pytest.fixture
def client(authed_app):
    return TestClient(authed_app)


class TestBearerAuth:
    def test_public_path_no_auth_needed(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_protected_path_rejects_without_token(self, client):
        resp = client.get("/api/data")
        assert resp.status_code == 401

    def test_protected_path_accepts_valid_token(self, client):
        resp = client.get("/api/data", headers={"Authorization": "Bearer test-secret-token"})
        assert resp.status_code == 200

    def test_rejects_wrong_token(self, client):
        resp = client.get("/api/data", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_rejects_malformed_header(self, client):
        resp = client.get("/api/data", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 401

    def test_disabled_when_env_false(self, monkeypatch):
        monkeypatch.setenv("API_AUTH_REQUIRED", "false")
        monkeypatch.setenv("API_AUTH_TOKEN", "some-token")
        from shared.governance._api_auth import require_auth
        app = FastAPI()

        @app.get("/api/data")
        def data():
            return {"ok": True}

        require_auth(app)
        client = TestClient(app)
        resp = client.get("/api/data")
        assert resp.status_code == 200
