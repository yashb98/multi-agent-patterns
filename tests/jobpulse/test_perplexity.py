"""Tests for Perplexity Sonar API client."""

import json
from unittest.mock import patch, MagicMock

import pytest

from jobpulse.perplexity import PerplexityClient, CompanyResearch, SalaryResearch


@pytest.fixture
def client():
    return PerplexityClient(api_key="test-key")


@pytest.fixture
def mock_httpx_response():
    def _make(content: str):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": content}}],
        }
        resp.raise_for_status = MagicMock()
        return resp
    return _make


def test_client_init_with_explicit_key():
    c = PerplexityClient(api_key="pplx-test")
    assert c.api_key == "pplx-test"


def test_client_init_from_env(monkeypatch):
    monkeypatch.setattr("jobpulse.config.PERPLEXITY_API_KEY", "pplx-env")
    c = PerplexityClient()
    assert c.api_key == "pplx-env"


def test_research_company_returns_cached(client, tmp_path):
    """Cached result returned without API call."""
    client._cache_path = tmp_path / "perplexity_cache.db"
    client._init_cache()
    cached = CompanyResearch(
        company="Acme Corp",
        description="AI startup",
        industry="Technology",
        size="startup",
        tech_stack=["Python", "AWS"],
    )
    client._store_cache("Acme Corp", "company", cached.model_dump_json())

    result = client.research_company("Acme Corp")
    assert result.company == "Acme Corp"
    assert result.description == "AI startup"


@patch("httpx.post")
def test_research_company_api_call(mock_post, client, mock_httpx_response):
    """API call parses response into CompanyResearch."""
    mock_post.return_value = mock_httpx_response(
        "**Acme Corp** is a Series B AI startup (200 employees) in fintech.\n"
        "Tech: Python, FastAPI, AWS, PostgreSQL.\n"
        "News: Raised $50M in 2026.\n"
        "Red flags: None.\n"
        "Culture: Remote-first, active engineering blog."
    )
    client._cache_path = None

    result = client.research_company("Acme Corp")
    assert result.company == "Acme Corp"
    assert result.description != ""
    mock_post.assert_called_once()
    call_json = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert call_json["model"] == "sonar"


@patch("httpx.post")
def test_research_company_deep_uses_sonar_pro(mock_post, client, mock_httpx_response):
    """deep=True uses sonar-pro model."""
    mock_post.return_value = mock_httpx_response("Deep research result.")
    client._cache_path = None

    client.research_company("Dream Co", deep=True)
    call_json = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert call_json["model"] == "sonar-pro"


@patch("httpx.post")
def test_research_salary(mock_post, client, mock_httpx_response):
    """Salary research returns parsed ranges."""
    mock_post.return_value = mock_httpx_response(
        "ML Engineer at Acme Corp in London: £35,000 - £45,000 (median £40,000). "
        "Source: Glassdoor."
    )
    client._cache_path = None

    result = client.research_salary("ML Engineer", "Acme Corp", "London")
    assert result.role == "ML Engineer"
    assert result.company == "Acme Corp"
    assert result.location == "London"


@patch("httpx.post")
def test_api_error_returns_empty_research(mock_post, client):
    """API failure returns empty CompanyResearch, not exception."""
    mock_post.side_effect = Exception("Network error")
    client._cache_path = None

    result = client.research_company("Broken Corp")
    assert result.company == "Broken Corp"
    assert result.description == ""


def test_company_research_model():
    cr = CompanyResearch(company="Test", description="A company", tech_stack=["Python"])
    assert cr.size == ""
    assert cr.red_flags == []
    assert cr.glassdoor_rating is None


def test_salary_research_model():
    sr = SalaryResearch(role="SWE", company="Test", location="London", min_gbp=30000, median_gbp=35000, max_gbp=40000)
    assert sr.source == ""
