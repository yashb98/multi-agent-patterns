"""Tests for API-specific circuit breakers."""

from shared.circuit_breaker import get_breaker


def test_llm_breaker_exists():
    breaker = get_breaker("openai")
    assert breaker is not None
    assert breaker.name == "openai"


def test_notion_breaker_exists():
    breaker = get_breaker("notion")
    assert breaker is not None


def test_linkedin_breaker_exists():
    breaker = get_breaker("linkedin")
    assert breaker is not None


def test_breaker_opens_after_failures():
    breaker = get_breaker("openai")
    breaker.reset()
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    assert breaker.state == "OPEN"


def test_breaker_rejects_when_open():
    breaker = get_breaker("openai")
    breaker.reset()
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    assert not breaker.allow_request()
