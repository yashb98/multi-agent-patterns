"""Domain isolation for ProceduralMemory.recall — pipeline-bugs S13.

Pre-fix bug:
    ``ProceduralMemory.recall(domain)`` returned **all** procedures across
    every domain when the requested domain had zero matching entries::

        if not relevant:
            relevant = self.procedures   # cross-domain bleed

    With ``patterns/enhanced_swarm.py`` writing
    ``learn_procedure(domain="writing", strategy="Enhanced swarm
    convergence: GRPO group sampling. Score 8.5/10 ...")`` to procedural
    memory, every cognitive call for a domain *without* its own templates
    (``screening_answers``, ``cv_tailoring``, ``form_recovery``, …)
    received that orchestration text as a "best procedure", which the L0
    memory-recall path returned **verbatim** as the LLM answer.

    Live reproduction (pre-fix):

        cognitive_llm_call(
            task='SYSTEM: ... USER: Will you require visa sponsorship?',
            domain='screening_answers', stakes='high',
        )
        → "Enhanced swarm convergence: GRPO group sampling. Score 8.5/10
           at iteration 1. Round 1/3 — still needs: accuracy 0.0/9.5
           (not checked)"

These tests fail pre-fix and pass once ``recall`` returns ``[]`` for an
unknown domain instead of falling back to ``self.procedures``.
"""

from shared.memory_layer._entries import ProceduralEntry
from shared.memory_layer._stores import ProceduralMemory


def _make_entry(domain: str, strategy: str, score: float = 8.5,
                success: float = 1.0, source: str = "test") -> ProceduralEntry:
    return ProceduralEntry(
        procedure_id=f"proc_{domain}_{hash(strategy) & 0xffff:04x}",
        domain=domain,
        strategy=strategy,
        context="",
        success_rate=success,
        times_used=3,
        avg_score_when_used=score,
        source=source,
        created_at="2026-05-10T00:00:00",
    )


class TestRecallDomainIsolation:
    """``recall(domain)`` MUST NOT bleed entries from other domains."""

    def test_recall_returns_empty_for_unknown_domain(self, tmp_path):
        store = ProceduralMemory(storage_path=str(tmp_path / "p.json"))
        store.store(_make_entry(
            domain="writing",
            strategy=(
                "Enhanced swarm convergence: GRPO group sampling. "
                "Score 8.5/10 at iteration 1. Round 1/3 — still needs: "
                "accuracy 0.0/9.5 (not checked)"
            ),
            source="enhanced_swarm",
        ))

        # screening_answers has zero entries → recall must return []
        # (pre-fix: returned the writing-domain leak entry).
        result = store.recall("screening_answers")
        assert result == [], (
            f"Cross-domain bleed: recall('screening_answers') returned "
            f"{[p.strategy[:80] for p in result]} — expected []."
        )

    def test_recall_does_not_return_writing_strategies_for_screening(
        self, tmp_path,
    ):
        store = ProceduralMemory(storage_path=str(tmp_path / "p.json"))
        # Mix of cross-domain pollution sources.
        store.store(_make_entry(
            domain="writing",
            strategy="Enhanced swarm convergence: GRPO group sampling.",
            source="enhanced_swarm",
        ))
        store.store(_make_entry(
            domain="map_reduce",
            strategy="3 successes on map_reduce across 3 sessions",
            source="optimization_success_streak",
        ))
        store.store(_make_entry(
            domain="job_application",
            strategy="Hot strategy: SmartRecruiters file upload",
            source="optimization",
        ))

        result = store.recall("screening_answers")
        leaked = [p for p in result
                  if "Enhanced swarm" in p.strategy
                  or "GRPO" in p.strategy
                  or "map_reduce" in p.strategy]
        assert leaked == [], (
            f"Domain isolation broken: {[p.strategy[:80] for p in leaked]} "
            f"leaked into screening_answers."
        )

    def test_recall_in_domain_still_works(self, tmp_path):
        store = ProceduralMemory(storage_path=str(tmp_path / "p.json"))
        store.store(_make_entry(
            domain="screening_answers",
            strategy="Strategy: confirm visa from profile DB before LLM",
            source="screening_pipeline",
        ))
        store.store(_make_entry(
            domain="writing",
            strategy="Enhanced swarm convergence: GRPO",
            source="enhanced_swarm",
        ))

        result = store.recall("screening_answers")
        assert len(result) == 1, (
            f"Expected 1 in-domain result, got {len(result)}: "
            f"{[p.strategy[:60] for p in result]}"
        )
        assert "GRPO" not in result[0].strategy

    def test_format_for_prompt_empty_when_no_in_domain_entries(
        self, tmp_path,
    ):
        """``format_for_prompt`` is the agent-facing wrapper used by
        ``MemoryManager.get_context_for_agent``. Cross-domain bleed via
        the prompt-context path was just as poisonous as the L0 path."""
        store = ProceduralMemory(storage_path=str(tmp_path / "p.json"))
        store.store(_make_entry(
            domain="writing",
            strategy="Enhanced swarm convergence: GRPO group sampling.",
            source="enhanced_swarm",
        ))

        prompt = store.format_for_prompt("screening_answers")
        assert "Enhanced swarm" not in prompt
        assert "GRPO" not in prompt


class TestCognitiveEngineNoLeak:
    """End-to-end: ``MemoryManager.get_procedural_entries`` must not
    surface cross-domain entries to the cognitive engine, regardless of
    whether the request goes through the SQLite path or the JSON
    fallback path."""

    def test_get_procedural_entries_isolates_domains_via_json_fallback(
        self, tmp_path,
    ):
        """When SQLite is empty for the requested domain, the JSON
        fallback must respect domain isolation. Pre-S13 the JSON fallback
        leaked all procedures back to the cognitive engine."""
        from shared.memory_layer._manager import MemoryManager
        from shared.memory_layer._sqlite_store import SQLiteStore

        sqlite_store = SQLiteStore(str(tmp_path / "memories.db"))
        manager = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=None, neo4j=None, embedder=None,
        )
        # Simulate the production state: enhanced_swarm has written a
        # procedural entry under domain="writing" (the JSON store is the
        # source for the bleed; the SQLite store legitimately has no
        # screening_answers row).
        manager.learn_procedure(
            domain="writing",
            strategy=(
                "Enhanced swarm convergence: GRPO group sampling. "
                "Score 8.5/10 at iteration 1."
            ),
            context="",
            score=8.5,
            source="enhanced_swarm",
        )
        # Cognitive's read API for the screening domain must NOT receive
        # the writing-domain entry.
        result = manager.get_procedural_entries("screening_answers")
        leaked = [p for p in result
                  if "Enhanced swarm" in p.strategy or "GRPO" in p.strategy]
        assert leaked == [], (
            f"Cognitive engine read API leaked writing-domain templates "
            f"into screening_answers: "
            f"{[p.strategy[:80] for p in leaked]}"
        )
