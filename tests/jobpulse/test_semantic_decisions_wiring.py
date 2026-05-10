"""S3 wiring tests — three call sites emit decision rows.

Verifies that the post-S3 wiring on:
  - ``screening_pipeline._llm_answer`` (free-text + option branches)
  - ``OptionAligner.align_answer`` (every tier in the cascade)
  - ``ScreeningIntentClassifier.classify``
actually writes rows to ``shared.semantic_decisions``. Closes audit
dimension H1 for these sites; replaces the log-mining pattern the
audit previously relied on.
"""

from __future__ import annotations

import pytest

import shared.semantic_decisions as sd
from shared.semantic_decisions import query_decisions, set_decisions_db_path, set_test_mode


# ── Per-test isolation ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_decisions_db(tmp_path):
    """Each test gets its own decisions DB, writes are not short-
    circuited (we want the writer to actually run so we can query
    back)."""
    sd._schema_initialised = False
    set_test_mode(False)
    set_decisions_db_path(str(tmp_path / "sd.db"))
    yield
    set_test_mode(None)


# ── OptionAligner wiring ─────────────────────────────────────────────


class TestOptionAlignerWiring:
    def test_exact_match_emits_one_decision(self):
        from jobpulse.screening_option_aligner import OptionAligner
        OptionAligner().align_answer("Yes", ["Yes", "No"], field_type="select")
        rows = query_decisions(agent_name="OptionAligner")
        assert len(rows) == 1
        assert rows[0].tier_reached in {"exact_match", "normalised_match"}
        assert rows[0].decision_type == "option_align"
        assert rows[0].confidence == pytest.approx(1.0)

    def test_no_alignment_emits_no_alignment_tier(self):
        """Pre-S3 we discovered this case via log mining
        ('No option alignment for ...'). Post-S3 the audit replays
        from semantic_decisions.db."""
        from jobpulse.screening_option_aligner import OptionAligner
        OptionAligner().align_answer(
            "totally unrelated text",
            ["foo", "bar"],
            field_type="select",
        )
        rows = query_decisions(agent_name="OptionAligner")
        assert len(rows) == 1
        assert rows[0].tier_reached == "no_alignment"
        assert rows[0].confidence == pytest.approx(0.0)


# ── ScreeningIntentClassifier wiring ─────────────────────────────────


class TestIntentClassifierWiring:
    def test_classify_empty_question_emits_decision(self):
        from jobpulse.screening_intent import ScreeningIntentClassifier
        # Construct minimal classifier — empty question short-circuits
        # before embedder is touched.
        classifier = ScreeningIntentClassifier(db_path=":memory:")
        classifier.classify("")
        rows = query_decisions(agent_name="ScreeningIntentClassifier")
        assert len(rows) == 1
        assert rows[0].decision_type == "intent_classify"
        assert rows[0].mechanism == "embedding"
        assert rows[0].tier_reached == "empty_question"


# ── screening_pipeline._llm_answer wiring ────────────────────────────


class TestLLMAnswerWiring:
    """The _llm_answer wiring uses monkeypatched cognitive_llm_call so
    we don't depend on Kimi/Ollama for this unit test. Both branches
    (option / free_text) and both outcomes (ok / rejection) are
    exercised so each tier_reached value lands in the DB at least
    once."""

    @pytest.fixture
    def pipeline(self, tmp_path):
        from jobpulse.screening_intent import ScreeningIntentClassifier
        from jobpulse.screening_pattern_extractor import PatternExtractor
        from jobpulse.screening_pipeline import ScreeningPipeline
        from jobpulse.screening_semantic_cache import ScreeningSemanticCache

        semantic_cache = ScreeningSemanticCache(
            sqlite_path=str(tmp_path / "cache.db"),
            qdrant_location=None,
        )
        intent = ScreeningIntentClassifier(db_path=str(tmp_path / "intent.db"))
        patterns = PatternExtractor(qdrant_url=None)
        patterns._db_path = str(tmp_path / "patterns.db")
        patterns._ensure_db()
        return ScreeningPipeline(
            profile={"work_auth_type": "Graduate Visa"},
            semantic_cache=semantic_cache,
            intent_classifier=intent,
            pattern_extractor=patterns,
        )

    def test_llm_returns_none_emits_decision(self, pipeline, monkeypatch):
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: None,
        )
        pipeline._llm_answer(
            question="Will you require visa sponsorship?",
            field={"type": "text", "options": []},
            job_context={"company": "Acme"},
        )
        rows = query_decisions(
            agent_name="screening_pipeline",
            call_site="_llm_answer:free_text",
        )
        assert len(rows) == 1
        assert rows[0].tier_reached == "llm_returned_none"

    def test_llm_ai_leak_emits_rejected_tier(self, pipeline, monkeypatch):
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: "As an AI, I don't have a visa status.",
        )
        pipeline._llm_answer(
            question="Will you require visa sponsorship?",
            field={"type": "text", "options": []},
            job_context={"company": "Acme"},
        )
        rows = query_decisions(
            agent_name="screening_pipeline",
            call_site="_llm_answer:free_text",
        )
        assert len(rows) == 1
        assert rows[0].tier_reached == "rejected_ai_leak"

    def test_llm_ok_free_text_emits_ok_tier(self, pipeline, monkeypatch):
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: "No, I have Graduate Visa work authorization.",
        )
        pipeline._llm_answer(
            question="Will you require visa sponsorship?",
            field={"type": "text", "options": []},
            job_context={"company": "Acme"},
        )
        rows = query_decisions(
            agent_name="screening_pipeline",
            call_site="_llm_answer:free_text",
        )
        assert len(rows) == 1
        assert rows[0].tier_reached == "ok_free_text"
        assert rows[0].confidence == pytest.approx(0.85)

    def test_llm_option_mismatch_emits_rejected_tier(self, pipeline, monkeypatch):
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: "completely off-topic answer",
        )
        pipeline._llm_answer(
            question="What is your gender?",
            field={"type": "select", "options": ["Man", "Woman", "Other"]},
            job_context={"company": "Acme"},
        )
        rows = query_decisions(
            agent_name="screening_pipeline",
            call_site="_llm_answer:option",
        )
        # rejected_option_mismatch on the LLM-fallback row; the
        # OptionAligner.align_answer call inside also wrote a decision
        # under agent_name='OptionAligner'.
        assert len(rows) == 1
        assert rows[0].tier_reached == "rejected_option_mismatch"

    def test_llm_option_ok_emits_ok_tier(self, pipeline, monkeypatch):
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: "Man",
        )
        pipeline._llm_answer(
            question="What is your gender?",
            field={"type": "select", "options": ["Man", "Woman", "Other"]},
            job_context={"company": "Acme"},
        )
        rows = query_decisions(
            agent_name="screening_pipeline",
            call_site="_llm_answer:option",
        )
        assert len(rows) == 1
        assert rows[0].tier_reached == "ok_option_aligned"


# ── End-to-end: pipeline.answer hits multiple sites ──────────────────


class TestEndToEndDecisionTrail:
    """One pipeline.answer call should leave a trail of decisions
    spanning OptionAligner + IntentClassifier (cache hit paths) or
    + LLM (cache miss). The audit replay queries this trail."""

    def test_end_to_end_emits_decisions(self, tmp_path, monkeypatch):
        from jobpulse.screening_intent import ScreeningIntentClassifier
        from jobpulse.screening_pattern_extractor import PatternExtractor
        from jobpulse.screening_pipeline import ScreeningPipeline
        from jobpulse.screening_semantic_cache import ScreeningSemanticCache

        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: "No, I have Graduate Visa.",
        )
        # Use a SQLite-only cache (no Qdrant client) to keep this test
        # hermetic — the production Qdrant collection from prior runs
        # contains a near-match for "Will you require visa sponsorship?"
        # via cross-question similarity, which would force a cache-hit
        # path that bypasses the intent classifier we want to test.
        cache = ScreeningSemanticCache(
            sqlite_path=str(tmp_path / "cache.db"),
            qdrant_location=None,
        )
        cache._qdrant = None  # disable Qdrant lookups for this test
        pipeline = ScreeningPipeline(
            profile={"work_auth_type": "Graduate Visa"},
            semantic_cache=cache,
            intent_classifier=ScreeningIntentClassifier(
                db_path=str(tmp_path / "intent.db"),
            ),
            pattern_extractor=PatternExtractor(qdrant_url=None),
        )
        pipeline.answer(
            "Will you require visa sponsorship?",
            job_context={"company": "Acme"},
        )
        # Should have at least one intent_classify decision (cache miss
        # path → intent classifier fires)
        intent_rows = query_decisions(
            decision_type="intent_classify",
        )
        assert len(intent_rows) >= 1, (
            f"Expected at least one intent_classify decision, got {len(intent_rows)}"
        )
