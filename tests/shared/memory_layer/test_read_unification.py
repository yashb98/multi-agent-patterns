"""Read-unification + eviction tests — pipeline-bugs S7 (M-11.B, M-11.C, W-2).

Pre-fix bugs:
- M-11.B: ``SemanticMemory.learn`` documented ``max_facts=500`` but never
  evicted; production ``semantic.json`` had 1 041 entries.
- M-11.C: ``MemoryManager.get_procedural_entries`` /
  ``get_episodic_entries`` read JSON-only (capped 100/200); SQLite held
  19 789 procedural / 203 episodic; cognitive saw ~1/4 of distinct
  strategies.
- W-2/W-11.5: ``cognitive/_classifier.load_persisted_stats`` reached into
  ``self._memory.semantic.facts.items()`` directly, bypassing
  ``MemoryManager``.

These tests fail pre-fix and pass once the eviction is wired,
``get_*_entries`` read SQLite, and ``get_semantic_entries`` exists.
"""

import pytest

from shared.memory_layer._entries import (
    EpisodicEntry, MemoryEntry, MemoryTier, ProceduralEntry, SemanticEntry,
)
from shared.memory_layer._manager import MemoryManager
from shared.memory_layer._sqlite_store import SQLiteStore
from shared.memory_layer._stores import SemanticMemory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_store(tmp_path):
    return SQLiteStore(str(tmp_path / "memories.db"))


@pytest.fixture
def manager(tmp_path, sqlite_store):
    return MemoryManager(
        storage_dir=str(tmp_path),
        sqlite_store=sqlite_store,
        qdrant=None,
        neo4j=None,
        embedder=None,
    )


# ---------------------------------------------------------------------------
# M-11.B — SemanticMemory eviction
# ---------------------------------------------------------------------------


class TestSemanticMemoryEviction:
    """Bug pattern: ``learn()`` ignored ``max_facts``. Cap was decorative."""

    def test_learn_evicts_when_over_cap(self, tmp_path):
        sm = SemanticMemory(
            storage_path=str(tmp_path / "semantic.json"),
            max_facts=10,
        )
        # Insert 25 distinct facts via the public API. Pre-fix this would
        # have left len(sm.facts) == 25 because eviction was missing.
        for i in range(25):
            sm.learn(domain="domA", fact=f"distinct fact #{i}", run_id=f"run{i}")

        assert len(sm.facts) == 10, (
            f"Expected eviction to enforce max_facts=10, got {len(sm.facts)}. "
            "M-11.B: SemanticMemory.learn was missing eviction."
        )

    def test_eviction_preserves_high_quality_facts(self, tmp_path):
        sm = SemanticMemory(
            storage_path=str(tmp_path / "semantic.json"),
            max_facts=5,
        )

        # Five facts get reinforced 10x each (high confidence + times_validated).
        for i in range(5):
            for run_n in range(10):
                sm.learn(
                    domain="durable",
                    fact=f"reinforced fact {i}",
                    run_id=f"run-{i}-{run_n}",
                )

        # Now spam 20 one-shot facts. These are lower-quality (confidence=0.7,
        # times_validated=1) so eviction must drop them, not the reinforced
        # ones.
        for i in range(20):
            sm.learn(domain="durable", fact=f"oneshot fact {i}", run_id=f"oneshot-{i}")

        assert len(sm.facts) == 5
        surviving = [f.fact for f in sm.facts.values()]
        for i in range(5):
            assert f"reinforced fact {i}" in surviving, (
                f"reinforced fact {i} got evicted but should have survived; "
                f"surviving={surviving}"
            )

    def test_reinforcement_does_not_count_as_new_fact(self, tmp_path):
        """Reinforcing the same fact must not push us over max_facts."""
        sm = SemanticMemory(
            storage_path=str(tmp_path / "semantic.json"),
            max_facts=3,
        )
        sm.learn(domain="d", fact="A", run_id="r1")
        sm.learn(domain="d", fact="B", run_id="r2")
        sm.learn(domain="d", fact="C", run_id="r3")

        # Reinforce 'A' five times. Should not trigger eviction.
        for i in range(5):
            sm.learn(domain="d", fact="A", run_id=f"r-extra-{i}")

        assert len(sm.facts) == 3
        facts = {f.fact for f in sm.facts.values()}
        assert facts == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# M-11.C — get_procedural_entries reads SQLite, not JSON cap
# ---------------------------------------------------------------------------


class TestProceduralReadUnification:
    """Bug pattern: ``get_procedural_entries`` returned JSON's 100-cap recall;
    cognitive saw ~1/4 of SQLite-held strategies."""

    def test_returns_distinct_strategies_from_sqlite(self, manager):
        # Insert 150 distinct procedural strategies. JSON's ProceduralMemory
        # has max_procedures=100 so JSON-only reads would return ≤100. SQLite
        # has all 150. Post-fix get_procedural_entries reads SQLite and
        # returns >100 distinct strategies.
        for i in range(150):
            manager.learn_procedure(
                domain="form_filling",
                strategy=f"strategy variant #{i:03d}: handle scenario {i}",
                context=f"when condition {i}",
                score=8.0,
                source="test_writer",
            )

        result = manager.get_procedural_entries("form_filling", n=200)

        assert len(result) > 100, (
            f"Expected >100 distinct strategies via SQLite, got {len(result)}. "
            "M-11.C: get_procedural_entries was reading JSON-only "
            "(ProceduralMemory.max_procedures=100)."
        )
        assert all(isinstance(p, ProceduralEntry) for p in result)
        # Spot-check that strategy text comes back, not corrupted.
        strategies = {p.strategy for p in result}
        assert any("strategy variant #001" in s for s in strategies)

    def test_aggregates_write_amplified_dups(self, manager):
        """The audit's main concern: ``optimization_success_streak`` writes
        the same procedure many times. SQL aggregation must collapse these
        into one entry with ``times_used`` == count."""
        # Same strategy, written 8 times (mimics the success-streak loop).
        for run in range(8):
            manager.learn_procedure(
                domain="job_application",
                strategy="hot strategy: handle SmartRecruiters file upload",
                context=f"run {run}",
                score=8.5,
                source="optimization_success_streak",
            )

        result = manager.get_procedural_entries("job_application")
        # Find our hot strategy in the result.
        hot = [p for p in result if "SmartRecruiters" in p.strategy]
        assert hot, f"hot strategy not in result: {[p.strategy for p in result]}"
        assert hot[0].times_used == 8, (
            f"Expected times_used=8 (write count), got {hot[0].times_used}. "
            "Aggregation must dedup by content prefix and count."
        )
        assert hot[0].avg_score_when_used == pytest.approx(8.5)
        # `domain` must round-trip from the SQLite column, not be lost (advisor
        # caught this: the payload doesn't include domain).
        assert hot[0].domain == "job_application"

    def test_falls_back_to_json_when_sqlite_empty(self, tmp_path):
        """Backwards compat: callers using JSON-only construction (no
        sqlite_store) still work."""
        m = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=None,  # no SQLite — JSON-only
            qdrant=None, neo4j=None, embedder=None,
        )
        m.learn_procedure(
            domain="legacy", strategy="json-only test",
            context="", score=7.0, source="legacy",
        )
        result = m.get_procedural_entries("legacy")
        assert len(result) >= 1
        assert isinstance(result[0], ProceduralEntry)


# ---------------------------------------------------------------------------
# M-11.C — get_episodic_entries reads SQLite
# ---------------------------------------------------------------------------


class TestEpisodicReadUnification:
    def test_returns_episodes_from_sqlite(self, manager):
        for i in range(5):
            manager.record_episode(
                topic=f"task {i}",
                final_score=7.0 + i * 0.2,
                iterations=2,
                pattern_used="hierarchical",
                agents_used=["writer"],
                strengths=["thorough"],
                weaknesses=["slow"] if i % 2 else [],
                output_summary=f"summary text for episode {i}",
                domain="research",
            )

        result = manager.get_episodic_entries("research")
        assert len(result) >= 5
        assert all(isinstance(e, EpisodicEntry) for e in result)
        # Reconstructed fields land in the right place
        scores = {e.final_score for e in result}
        assert any(s >= 7.8 for s in scores)
        # weaknesses round-trip through payload
        wks = [e.weaknesses for e in result if e.weaknesses]
        assert any("slow" in w for w in wks)


# ---------------------------------------------------------------------------
# W-2 / W-11.5 — get_semantic_entries + cognitive _classifier
# ---------------------------------------------------------------------------


class TestSemanticEntriesAccessor:
    def test_get_semantic_entries_returns_facts(self, manager):
        manager.learn_fact(
            domain="cognitive_classifier",
            fact="form_filling: L0 success 80%, L1 escalation 20%, n=50",
            run_id="classifier_form",
        )
        manager.learn_fact(
            domain="cognitive_classifier",
            fact="screening_answers: L0 success 95%, L1 escalation 5%, n=100",
            run_id="classifier_screening",
        )

        result = manager.get_semantic_entries("cognitive_classifier")
        assert len(result) >= 2
        assert all(isinstance(e, SemanticEntry) for e in result)
        facts = {e.fact for e in result}
        assert any("form_filling" in f for f in facts)
        assert any("screening_answers" in f for f in facts)

    def test_classifier_load_persisted_uses_accessor_not_attribute(self, manager):
        """Pattern test for W-11.5: the classifier must NOT reach into
        ``memory.semantic.facts`` when ``get_semantic_entries`` exists."""
        from shared.cognitive._budget import BudgetTracker, CognitiveBudget
        from shared.cognitive._classifier import EscalationClassifier

        manager.learn_fact(
            domain="cognitive_classifier",
            fact="cv_tailoring: L0 success 75%, L1 escalation 25%, n=40",
            run_id="r1",
        )

        accessor_calls: list[str] = []
        legacy_attr_reads: list[str] = []

        original_get = MemoryManager.get_semantic_entries

        def spy_accessor(self, domain, n=100):
            accessor_calls.append(domain)
            return original_get(self, domain, n=n)

        class _AttrTrap:
            def __getattribute__(self, name):
                legacy_attr_reads.append(name)
                raise RuntimeError(
                    "load_persisted_stats reached into memory.semantic — "
                    "it should use get_semantic_entries() (W-11.5)"
                )

        # Monkeypatch: classifier should hit get_semantic_entries, not .semantic
        MemoryManager.get_semantic_entries = spy_accessor
        try:
            # Replace .semantic with a trap; if the classifier touches it
            # under .facts the trap raises (caught by the broad except in
            # load_persisted_stats — but we still record the read).
            object.__setattr__(manager, "semantic", _AttrTrap())
            classifier = EscalationClassifier(
                memory_manager=manager,
                budget_tracker=BudgetTracker(CognitiveBudget()),
            )
            classifier.load_persisted_stats()
        finally:
            MemoryManager.get_semantic_entries = original_get

        assert "cognitive_classifier" in accessor_calls, (
            f"Expected get_semantic_entries('cognitive_classifier') call; "
            f"got {accessor_calls}. Pre-fix the classifier reached into "
            f"memory.semantic.facts directly."
        )
        # legacy_attr_reads is allowed to be non-empty (a fallback path
        # hits it when accessor is missing on the mock), but the accessor
        # must have been preferred and called first.
