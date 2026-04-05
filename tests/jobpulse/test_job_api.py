"""Tests for FastAPI job API routes."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from mindgraph_app.main import app
    return TestClient(app)


def test_health_endpoint(client):
    resp = client.get("/api/job/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_evaluate_returns_gate_results(client, tmp_path):
    """POST /api/job/evaluate with JD text returns gate evaluation."""
    mock_listing = MagicMock()
    mock_listing.required_skills = ["python", "sql"]

    mock_screen = MagicMock()
    mock_screen.gate1_passed = True
    mock_screen.gate2_passed = True
    mock_screen.gate3_score = 85
    mock_screen.tier = "strong"
    mock_screen.matched_skills = ["python"]
    mock_screen.missing_skills = ["sql"]
    mock_screen.breakdown = "M1: 1/5, M2: 1/2, M3: 85%"

    mock_jd_quality = MagicMock()
    mock_jd_quality.passed = True

    with patch("jobpulse.job_api.analyze_jd", return_value=mock_listing), \
         patch("jobpulse.job_api.gate0_title_relevance", return_value=True), \
         patch("jobpulse.job_api.check_jd_quality", return_value=mock_jd_quality), \
         patch("jobpulse.job_api._get_store") as mock_get_store:
        mock_get_store.return_value.pre_screen_jd.return_value = mock_screen
        resp = client.post("/api/job/evaluate", json={
            "url": "https://example.com/job/123",
            "title": "Data Scientist",
            "company": "Acme Corp",
            "platform": "linkedin",
            "jd_text": "We need a data scientist with Python and SQL...",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is True
    assert data["tier"] == "strong"


def test_evaluate_gate0_fail(client):
    """Gate 0 title filter rejects irrelevant jobs."""
    with patch("jobpulse.job_api.gate0_title_relevance", return_value=False):
        resp = client.post("/api/job/evaluate", json={
            "url": "https://example.com/job/456",
            "title": "Senior iOS Developer",
            "company": "Acme",
            "platform": "linkedin",
            "jd_text": "iOS developer needed...",
        })
    assert resp.status_code == 200
    assert resp.json()["passed"] is False
    assert resp.json()["gate_failed"] == "gate0"


def test_evaluate_gate1_fail(client):
    """Gate 1 kill signal rejects job."""
    mock_listing = MagicMock()
    mock_listing.required_skills = ["python"]

    mock_screen = MagicMock()
    mock_screen.gate1_passed = False

    mock_jd_quality = MagicMock()
    mock_jd_quality.passed = True

    with patch("jobpulse.job_api.analyze_jd", return_value=mock_listing), \
         patch("jobpulse.job_api.gate0_title_relevance", return_value=True), \
         patch("jobpulse.job_api.check_jd_quality", return_value=mock_jd_quality), \
         patch("jobpulse.job_api._get_store") as mock_get_store:
        mock_get_store.return_value.pre_screen_jd.return_value = mock_screen
        resp = client.post("/api/job/evaluate", json={
            "url": "https://example.com/job/789",
            "title": "Data Scientist",
            "company": "Corp",
            "platform": "linkedin",
            "jd_text": "Need 10+ years senior lead...",
        })
    assert resp.status_code == 200
    assert resp.json()["passed"] is False
    assert resp.json()["gate_failed"] == "gate1"


def test_scan_reed(client):
    """POST /api/job/scan-reed returns job list."""
    mock_jobs = [{"title": "Data Scientist", "company": "Corp", "url": "https://reed.co.uk/1"}]
    with patch("jobpulse.job_api.scan_reed", return_value=mock_jobs):
        resp = client.post("/api/job/scan-reed", json={
            "titles": ["data scientist"],
            "location": "United Kingdom",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["jobs"]) == 1
    assert data["count"] == 1


def test_scan_linkedin(client):
    """POST /api/job/scan-linkedin returns job list."""
    mock_jobs = [{"title": "ML Engineer", "company": "AI Co", "url": "https://linkedin.com/1"}]
    with patch("jobpulse.job_api.scan_linkedin", return_value=mock_jobs):
        resp = client.post("/api/job/scan-linkedin", json={
            "titles": ["machine learning engineer"],
        })
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_evaluate_batch(client):
    """POST /api/job/evaluate-batch evaluates multiple jobs."""
    with patch("jobpulse.job_api.gate0_title_relevance", return_value=False):
        resp = client.post("/api/job/evaluate-batch", json={
            "jobs": [
                {
                    "url": "https://example.com/1",
                    "title": "iOS Dev",
                    "company": "A",
                    "platform": "linkedin",
                    "jd_text": "iOS...",
                },
                {
                    "url": "https://example.com/2",
                    "title": "Android Dev",
                    "company": "B",
                    "platform": "linkedin",
                    "jd_text": "Android...",
                },
            ]
        })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2
    assert all(r["passed"] is False for r in data["results"])
