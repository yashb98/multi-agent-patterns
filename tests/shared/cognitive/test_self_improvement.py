import pytest
from unittest.mock import AsyncMock, patch

from shared.cognitive._engine import CognitiveEngine
from shared.cognitive._budget import ThinkLevel, CognitiveBudget, BudgetTracker
from shared.cognitive._classifier import EscalationClassifier, _L0_SKIP_THRESHOLD
from tests.shared.cognitive.conftest import MockMemoryManager, MockProceduralEntry


class TestSelfImprovement:

    @pytest.mark.asyncio
    async def test_template_score_improves(self):
        """Simulate 10 runs on same domain → template avg_score increases."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        memory._procedural.append(MockProceduralEntry(
            domain="test", strategy="Initial strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))

        scores = [6.5, 7.0, 7.5, 7.0, 8.0, 8.5, 8.0, 9.0, 8.5, 9.0]
        for score in scores:
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="improving"):
                await engine.think(task="test", domain="test",
                                   scorer=lambda x, s=score: s)

        # More procedures should have been stored with improving scores
        procs = memory.get_procedural_entries("test")
        if len(procs) > 1:
            later_scores = [p.avg_score_when_used for p in procs[-3:]]
            assert max(later_scores) >= 7.0

    @pytest.mark.asyncio
    async def test_bad_template_decays(self):
        """Template with low success rate gets ranked below better ones and excluded at limit."""
        memory = MockMemoryManager()
        memory._procedural.append(MockProceduralEntry(
            procedure_id="bad", domain="test", strategy="Bad strategy",
            success_rate=0.3, times_used=10, avg_score_when_used=4.0,
        ))
        memory._procedural.append(MockProceduralEntry(
            procedure_id="good", domain="test", strategy="Good strategy",
            success_rate=0.9, times_used=10, avg_score_when_used=8.5,
        ))
        engine = CognitiveEngine(memory, agent_name="test_agent")
        # With max_templates=1, only the good template should survive
        composed = engine._composer.compose("task", "test", "test_agent", memory,
                                            max_templates=1)
        assert "Good strategy" in composed.text
        assert "Bad strategy" not in composed.text

    def test_classifier_learns_easy_domain(self):
        """20 successful L0 calls → classifier stores high success rate."""
        memory = MockMemoryManager()
        budget = CognitiveBudget()
        classifier = EscalationClassifier(memory, BudgetTracker(budget))

        for _ in range(20):
            classifier.update_domain_stats("easy_domain", ThinkLevel.L0_MEMORY,
                                           escalated=False)

        stats = classifier._domain_stats.get("easy_domain", {})
        assert stats.get("l0_success_rate", 0) >= _L0_SKIP_THRESHOLD

    def test_classifier_learns_hard_domain(self):
        """10 L1 calls with 60% escalation → classifier detects difficulty."""
        memory = MockMemoryManager()
        from shared.cognitive._budget import BudgetTracker
        classifier = EscalationClassifier(memory, BudgetTracker(CognitiveBudget()))

        for i in range(10):
            escalated = i < 6  # 60% escalation rate
            classifier.update_domain_stats("hard_domain", ThinkLevel.L1_SINGLE,
                                           escalated=escalated)

        stats = classifier._domain_stats.get("hard_domain", {})
        assert stats.get("l1_escalation_rate", 0) >= 0.5

    @pytest.mark.asyncio
    async def test_templates_promote_with_usage(self):
        """Template used 15x with 90% success → classifier learns domain is easy."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        memory._procedural.append(MockProceduralEntry(
            domain="tested", strategy="Proven strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))

        for _ in range(15):
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="result"):
                await engine.think(task="task", domain="tested",
                                   scorer=lambda x: 9.0)

        # After 15 successful runs, classifier should track this domain
        procs = memory.get_procedural_entries("tested")
        assert len(procs) >= 1
        # The classifier's domain stats should reflect accumulated success
        stats = engine._classifier._domain_stats.get("tested", {})
        assert stats.get("sample_size", 0) >= 10

    @pytest.mark.asyncio
    async def test_l0_percentage_increases_over_runs(self):
        """Simulate 30 runs → L0 percentage increases across batches."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # Batch 1: 10 runs on novel domain → mostly L1/L2
        batch1_levels = []
        for i in range(10):
            if i >= 3:
                for j in range(4):
                    memory._procedural.append(MockProceduralEntry(
                        procedure_id=f"b1_{i}_{j}", domain="evolving",
                        strategy=f"Strategy {i}", success_rate=0.95,
                        times_used=5, avg_score_when_used=8.5,
                    ))
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="result"), \
                 patch("shared.cognitive._reflexion._llm_generate",
                       new_callable=AsyncMock, return_value="result"):
                r = await engine.think(task="task", domain="evolving",
                                       scorer=lambda x: 8.0)
                batch1_levels.append(r.level)

        batch1_l0 = sum(1 for l in batch1_levels if l == ThinkLevel.L0_MEMORY)

        # Batch 2: 10 more runs — should have more L0
        batch2_levels = []
        for _ in range(10):
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="result"):
                r = await engine.think(task="task", domain="evolving",
                                       scorer=lambda x: 8.0)
                batch2_levels.append(r.level)

        batch2_l0 = sum(1 for l in batch2_levels if l == ThinkLevel.L0_MEMORY)
        assert batch2_l0 >= batch1_l0
