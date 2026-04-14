import pytest


def test_fetch_interview_questions_returns_results(monkeypatch):
    from jobpulse.interview_prep import fetch_interview_questions
    from shared.searxng_client import SearchResult

    monkeypatch.setattr("shared.searxng_client.search_smart",
                        lambda q, **kw: [
                            SearchResult(title="Top 10 ML questions", url="https://x.com/1", content="Q1: Explain bias-variance", engine="google"),
                        ])

    results = fetch_interview_questions("Monzo", "Data Scientist")
    assert len(results) >= 1


def test_fetch_interview_questions_handles_failure(monkeypatch):
    from jobpulse.interview_prep import fetch_interview_questions

    monkeypatch.setattr("shared.searxng_client.search_smart", lambda q, **kw: [])

    results = fetch_interview_questions("Monzo", "Data Scientist")
    assert results == []
