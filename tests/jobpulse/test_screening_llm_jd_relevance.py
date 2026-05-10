"""JD-relevance guard for screening-pipeline LLM fallback — S13.

Pre-S13 the free-text branch of ``ScreeningPipeline._llm_answer`` accepted
whatever ``cognitive_llm_call`` returned, including cross-domain
orchestration leaks like ``"Enhanced swarm convergence: GRPO group
sampling..."`` (the procedural-memory-recall root cause is fixed in
``_stores.py``; this is the defense-in-depth backstop).

The guard mirrors the existing option-field guard at lines 511-523
(``OptionAligner.align_answer`` rejects answers that don't fit any
option) — for free-text fields we use BGE-M3 cosine similarity between
the question and the answer. Threshold derived from measured Q/A pairs
(see ``_LLM_ANSWER_RELEVANCE_THRESHOLD``).

A rejected answer must:
  1. Cause ``_llm_answer`` to return ``None`` (caller falls through).
  2. NOT be cached (no ``record_outcome`` write that would poison the
     screening_semantic_cache for the next run, repeating the S1 cache
     poisoning we already cleaned up).
"""

import pytest

from jobpulse.screening_intent import ScreeningIntentClassifier
from jobpulse.screening_pattern_extractor import PatternExtractor
from jobpulse.screening_pipeline import ScreeningPipeline
from jobpulse.screening_semantic_cache import ScreeningSemanticCache


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def profile():
    return {
        "right_to_work": True,
        "work_auth_type": "Graduate Visa",
        "visa_type": "Graduate Visa",
        "visa_sponsorship_required": False,
        "notice_period": "1 month",
        "salary_expectation": "38000",
        "willing_to_relocate": True,
        "years_of_experience": "2",
        "location": "Dundee, UK",
    }


@pytest.fixture()
def pipeline(tmp_path, profile):
    semantic_cache = ScreeningSemanticCache(
        sqlite_path=str(tmp_path / "cache.db"),
        qdrant_location=None,
    )
    intent = ScreeningIntentClassifier(db_path=str(tmp_path / "intent.db"))
    patterns = PatternExtractor(qdrant_url=None)
    patterns._db_path = str(tmp_path / "patterns.db")
    patterns._ensure_db()
    return ScreeningPipeline(
        profile=profile,
        semantic_cache=semantic_cache,
        intent_classifier=intent,
        pattern_extractor=patterns,
    )


# ── Off-topic / leaked answers must be rejected ──────────────────────────


class TestLLMAnswerRejectsOffTopic:
    """When the LLM returns text whose embedding similarity to the
    question is below the JD-relevance threshold, ``_llm_answer`` MUST
    return ``None`` so the caller falls through to the next strategy
    (semantic miss) instead of caching garbage."""

    def test_rejects_enhanced_swarm_orchestration_leak(
        self, pipeline, monkeypatch,
    ):
        """The literal leak text observed live — must be rejected."""
        leak = (
            "Enhanced swarm convergence: GRPO group sampling. "
            "Score 8.5/10 at iteration 1. Round 1/3 — still needs: "
            "accuracy 0.0/9.5 (not checked)"
        )
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: leak,
        )
        result = pipeline._llm_answer(
            question="Will you now or in the future require employment "
                     "visa sponsorship?",
            field={"type": "text", "options": []},
            job_context={"company": "Anthropic"},
        )
        assert result is None, (
            f"Off-topic leak passed the guard: {result!r}"
        )

    def test_rejects_optimization_success_streak_leak(
        self, pipeline, monkeypatch,
    ):
        """A second observed leak shape (success-streak template)."""
        leak = "7 successes on map_reduce across 7 sessions"
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: leak,
        )
        result = pipeline._llm_answer(
            question="What is your salary expectation?",
            field={"type": "text", "options": []},
            job_context={"company": "Anthropic"},
        )
        assert result is None, (
            f"Optimization-streak leak passed the guard: {result!r}"
        )

    def test_rejected_answer_does_not_poison_cache(
        self, pipeline, monkeypatch, tmp_path,
    ):
        """A rejected free-text LLM answer must NOT be cached. S1 already
        burned us once: cache writes survive the run and poison every
        subsequent matching apply."""
        import sqlite3
        leak = (
            "Enhanced swarm convergence: GRPO group sampling. "
            "Score 8.5/10 at iteration 1."
        )
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: leak,
        )
        question = "Why do you want to work at this company?"
        # Drive through the public answer() path so cache writes go
        # through the same code as production.
        out = pipeline.answer(question, job_context={"company": "Acme"})
        # Resulting answer should not be the leak text.
        assert "Enhanced swarm" not in (out.get("answer") or ""), (
            f"answer() leaked orchestration text: {out!r}"
        )
        # The cache table itself must not contain the leak text. The
        # cache is the long-lived poisoning surface — even if the
        # current call fell through, a write-then-fall-through would
        # serve the leak on the next matching apply at score=1.00.
        cache_db = pipeline._semantic_cache._sqlite_path
        with sqlite3.connect(cache_db) as conn:
            rows = conn.execute(
                "SELECT answer FROM screening_semantic_cache "
                "WHERE answer LIKE '%Enhanced swarm%' OR answer LIKE '%GRPO%'"
            ).fetchall()
        assert rows == [], (
            f"Cache poisoning: leak text written to "
            f"screening_semantic_cache despite JD-relevance rejection: "
            f"{rows!r}"
        )


# ── On-topic answers must still pass ─────────────────────────────────────


class TestLLMAnswerPassesOnTopic:
    """Happy path: a real-looking screening answer threads the guard."""

    def test_passes_visa_sponsorship_answer(self, pipeline, monkeypatch):
        good = ("No, I have Graduate Visa work authorization until "
                "May 2028 and do not require sponsorship at this time.")
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: good,
        )
        result = pipeline._llm_answer(
            question="Will you now or in the future require employment "
                     "visa sponsorship?",
            field={"type": "text", "options": []},
            job_context={"company": "Anthropic"},
        )
        assert result is not None
        assert "Graduate Visa" in result

    def test_passes_motivation_answer(self, pipeline, monkeypatch):
        good = ("I am drawn to your team's focus on AI safety research "
                "and the opportunity to work on production systems at scale.")
        monkeypatch.setattr(
            "shared.agents.cognitive_llm_call",
            lambda **kw: good,
        )
        result = pipeline._llm_answer(
            question="Why do you want to work at this company?",
            field={"type": "text", "options": []},
            job_context={"company": "Anthropic"},
        )
        assert result is not None
        assert "AI safety" in result


# ── Direct cognitive_llm_call sanity (root-cause closure) ────────────────


class TestCognitiveLLMCallNoLeakForScreeningDomain:
    """Once the ProceduralMemory.recall fallback is removed,
    ``cognitive_llm_call(domain="screening_answers")`` invoked in a
    process where no screening-domain procedures have been written must
    NOT return orchestration content from a different domain.

    This verifies the root cause is closed at the memory-layer level,
    independent of the screening_pipeline guard above. It uses the
    real shared MemoryManager + cognitive engine, with the production
    procedural store seeded with cross-domain entries (the same shape
    as the live state we reproduced)."""

    def test_screening_domain_does_not_return_writing_strategy(
        self, tmp_path, monkeypatch,
    ):
        # Isolate persistent state to tmp_path so the test doesn't
        # touch production data/agent_memory/.
        from shared.memory_layer._manager import MemoryManager
        from shared.memory_layer._sqlite_store import SQLiteStore
        from shared.cognitive._engine import CognitiveEngine

        sqlite_store = SQLiteStore(str(tmp_path / "memories.db"))
        memory = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=None, neo4j=None, embedder=None,
        )
        # Seed the cross-domain leak source.
        memory.learn_procedure(
            domain="writing",
            strategy=(
                "Enhanced swarm convergence: GRPO group sampling. "
                "Score 8.5/10 at iteration 1."
            ),
            context="",
            score=8.5,
            source="enhanced_swarm",
        )

        engine = CognitiveEngine(
            memory_manager=memory, agent_name="screening_answers",
        )

        # Run a screening-style task at L0_MEMORY explicitly. Pre-S13
        # this returned the writing-domain strategy as the "answer".
        from shared.cognitive._budget import ThinkLevel
        result = engine.think_sync(
            task="Will you require visa sponsorship?",
            domain="screening_answers",
            stakes="high",
            force_level=ThinkLevel.L0_MEMORY,
        )
        # L0 with no in-domain templates should return empty answer
        # (which would auto-escalate in production); never the leaked
        # strategy verbatim.
        assert "Enhanced swarm" not in (result.answer or "")
        assert "GRPO" not in (result.answer or "")
