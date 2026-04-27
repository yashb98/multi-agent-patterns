"""Tests for ScreeningOutcomeRecorder — single writer for screening feedback."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def recorder(tmp_path):
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache

    cache = ScreeningSemanticCache(
        sqlite_path=str(tmp_path / "cache.db"), qdrant_location=""
    )
    from jobpulse.screening_outcome_recorder import ScreeningOutcomeRecorder

    return ScreeningOutcomeRecorder(cache=cache)


@pytest.fixture
def cache_db(tmp_path):
    return str(tmp_path / "cache.db")


def test_record_fill_increments_usage(recorder, cache_db):
    recorder.record_fill(
        question="Do you have the right to work in the UK?",
        answer="Yes",
        field_options=None,
        field_type="radio",
        intent="work_auth_yes_no",
    )

    with sqlite3.connect(cache_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT times_used, answer FROM screening_semantic_cache"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["times_used"] == 1
        assert rows[0]["answer"] == "Yes"


def test_record_fill_skips_empty_question(recorder, cache_db):
    recorder.record_fill(
        question="",
        answer="Yes",
        field_options=None,
        field_type="radio",
        intent="work_auth",
    )

    with sqlite3.connect(cache_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM screening_semantic_cache"
        ).fetchone()[0]
        assert count == 0


def test_record_fill_skips_empty_answer(recorder, cache_db):
    recorder.record_fill(
        question="Right to work?",
        answer="",
        field_options=None,
        field_type="radio",
        intent="work_auth",
    )

    with sqlite3.connect(cache_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM screening_semantic_cache"
        ).fetchone()[0]
        assert count == 0


def test_record_fill_no_cache_returns_silently():
    from jobpulse.screening_outcome_recorder import ScreeningOutcomeRecorder

    rec = ScreeningOutcomeRecorder.__new__(ScreeningOutcomeRecorder)
    rec._cache = None
    # Should not raise
    rec.record_fill(
        question="Q?", answer="A", field_options=None, field_type="text", intent="x"
    )


def test_record_fill_increments_existing(recorder, cache_db):
    """Second fill for the same question increments times_used to 2."""
    recorder.record_fill(
        question="Notice period?",
        answer="1 month",
        field_options=None,
        field_type="text",
        intent="notice",
    )
    recorder.record_fill(
        question="Notice period?",
        answer="1 month",
        field_options=None,
        field_type="text",
        intent="notice",
    )

    with sqlite3.connect(cache_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT times_used FROM screening_semantic_cache LIMIT 1"
        ).fetchone()
        assert row["times_used"] == 2


def test_record_confirmation_success_and_correction(recorder, cache_db):
    recorder.record_fill(
        question="Right to work?",
        answer="Yes",
        field_options=None,
        field_type="radio",
        intent="work_auth",
    )
    recorder.record_fill(
        question="Salary?",
        answer="35000",
        field_options=["30k", "35k", "40k"],
        field_type="select",
        intent="salary",
    )

    screening_results = [
        {
            "question": "Right to work?",
            "answer": "Yes",
            "field_options": None,
            "field_type": "radio",
            "intent": "work_auth",
        },
        {
            "question": "Salary?",
            "answer": "35000",
            "field_options": ["30k", "35k", "40k"],
            "field_type": "select",
            "intent": "salary",
        },
    ]
    corrections = {
        "corrections": [{"field": "Salary?", "agent": "35000", "user": "40000"}],
    }

    result = recorder.record_confirmation(screening_results, corrections)
    assert result == {"confirmed": 1, "corrected": 1}

    with sqlite3.connect(cache_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            r["question_text"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM screening_semantic_cache"
            ).fetchall()
        }

    assert rows["Right to work?"]["success_count"] == 1
    assert rows["Salary?"]["correction_count"] == 1


def test_record_confirmation_all_success(recorder, cache_db):
    recorder.record_fill(
        question="Notice period?",
        answer="1 month",
        field_options=None,
        field_type="text",
        intent="notice",
    )

    screening_results = [
        {
            "question": "Notice period?",
            "answer": "1 month",
            "field_options": None,
            "field_type": "text",
            "intent": "notice",
        },
    ]

    result = recorder.record_confirmation(screening_results, corrections=None)
    assert result == {"confirmed": 1, "corrected": 0}

    with sqlite3.connect(cache_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT success_count FROM screening_semantic_cache LIMIT 1"
        ).fetchone()
        assert row["success_count"] == 1


def test_record_confirmation_empty_corrections(recorder, cache_db):
    """Empty corrections dict means all confirmed."""
    recorder.record_fill(
        question="Start date?",
        answer="ASAP",
        field_options=None,
        field_type="text",
        intent="start_date",
    )

    screening_results = [
        {
            "question": "Start date?",
            "answer": "ASAP",
            "field_options": None,
            "field_type": "text",
            "intent": "start_date",
        },
    ]

    result = recorder.record_confirmation(screening_results, corrections={"corrections": []})
    assert result == {"confirmed": 1, "corrected": 0}


def test_record_confirmation_case_insensitive_match(recorder, cache_db):
    """Correction field matching is case-insensitive."""
    recorder.record_fill(
        question="Do you require sponsorship?",
        answer="No",
        field_options=None,
        field_type="radio",
        intent="visa",
    )

    screening_results = [
        {
            "question": "Do you require sponsorship?",
            "answer": "No",
            "field_options": None,
            "field_type": "radio",
            "intent": "visa",
        },
    ]
    corrections = {
        "corrections": [
            {"field": "do you require sponsorship?", "agent": "No", "user": "Yes"}
        ],
    }

    result = recorder.record_confirmation(screening_results, corrections)
    assert result == {"confirmed": 0, "corrected": 1}

    with sqlite3.connect(cache_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT correction_count FROM screening_semantic_cache LIMIT 1"
        ).fetchone()
        assert row["correction_count"] == 1


def test_record_confirmation_no_screening_results(recorder):
    """Empty screening results returns zeros."""
    result = recorder.record_confirmation([], corrections=None)
    assert result == {"confirmed": 0, "corrected": 0}


def test_singleton_factory():
    from jobpulse.screening_outcome_recorder import get_screening_outcome_recorder

    r1 = get_screening_outcome_recorder()
    r2 = get_screening_outcome_recorder()
    assert r1 is r2
