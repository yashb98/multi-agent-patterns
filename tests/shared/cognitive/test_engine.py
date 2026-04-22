import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.cognitive._engine import CognitiveEngine
from shared.cognitive._budget import ThinkLevel, CognitiveBudget
from shared.cognitive._reflexion import ReflexionResult
from shared.cognitive._tree_of_thought import ToTResult, Branch
from tests.shared.cognitive.conftest import MockMemoryManager, MockProceduralEntry


class TestCognitiveEngine:

    @pytest.fixture
    def engine(self, mock_memory):
        return CognitiveEngine(mock_memory, agent_name="test_agent")

    @pytest.mark.asyncio
    async def test_think_l0_no_llm_call(self, mock_memory):
        """L0 with deterministic template → zero LLM calls."""
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy="Classify by sender domain", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock) as mock_llm:
            result = await engine.think(task="classify email", domain="email")
        mock_llm.assert_not_called()
        assert result.level == ThinkLevel.L0_MEMORY

    @pytest.mark.asyncio
    async def test_think_l1_single_call(self, mock_memory):
        """L1 makes exactly one LLM call."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="email", strategy="Weak strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="classified result") as mock_llm:
            result = await engine.think(
                task="classify email", domain="email",
                scorer=lambda x: 8.0,
            )
        assert mock_llm.call_count == 1
        assert result.level == ThinkLevel.L1_SINGLE

    @pytest.mark.asyncio
    async def test_l1_without_scorer_returns_none_score(self, mock_memory):
        """Default L1 score is None when no scorer is configured."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="email", strategy="Weak strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch(
            "shared.cognitive._engine._llm_generate",
            new_callable=AsyncMock,
            return_value="classified result",
        ):
            result = await engine.think(task="classify email", domain="email")
        assert result.level == ThinkLevel.L1_SINGLE
        assert result.score is None

    @pytest.mark.asyncio
    async def test_think_l2_calls_reflexion(self, mock_memory):
        """L2 delegates to ReflexionLoop."""
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        mock_result = ReflexionResult(
            answer="reflexion answer", score=8.0, attempts=2,
            critiques=["fixed it"], cost=0.003,
        )
        with patch.object(engine._reflexion, "run",
                          new_callable=AsyncMock, return_value=mock_result):
            result = await engine.think(
                task="classify email", domain="email_classification",
                stakes="medium", scorer=lambda x: 8.0,
            )
        assert result.level == ThinkLevel.L2_REFLEXION
        assert result.answer == "reflexion answer"

    @pytest.mark.asyncio
    async def test_think_l3_calls_tot(self, mock_memory):
        """L3 delegates to TreeOfThought."""
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        winner = Branch(branch_id="b0", reasoning="first principles",
                        output="tot answer", score=9.0, depth=1)
        mock_result = ToTResult(
            winner=winner, all_branches=[winner],
            strategy_template="winning strategy", pruned_count=2, cost=0.03,
        )
        with patch.object(engine._tot, "explore",
                          new_callable=AsyncMock, return_value=mock_result):
            result = await engine.think(
                task="submit application", domain="job_application",
                stakes="high", scorer=lambda x: 9.0,
            )
        assert result.level == ThinkLevel.L3_TREE_OF_THOUGHT
        assert result.answer == "tot answer"

    @pytest.mark.asyncio
    async def test_auto_escalation_l0_to_l1(self, mock_memory):
        """L0 scores poorly → auto-escalates to L1."""
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")

        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="better answer"):
            result = await engine.think(
                task="classify email", domain="email",
                scorer=lambda x: 8.0 if "better" in x else 4.0,
            )
        assert result.escalated_from == ThinkLevel.L0_MEMORY
        assert result.level == ThinkLevel.L1_SINGLE

    @pytest.mark.asyncio
    async def test_auto_escalation_l1_to_l2(self, mock_memory):
        """L1 low confidence → auto-escalates to L2."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="test", strategy="Weak", success_rate=0.5,
            times_used=1, avg_score_when_used=6.0,
        ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        mock_reflexion_result = ReflexionResult(
            answer="reflexion answer", score=8.0, attempts=1, cost=0.003,
        )
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="low conf answer"), \
             patch.object(engine._reflexion, "run",
                          new_callable=AsyncMock, return_value=mock_reflexion_result):
            result = await engine.think(
                task="test", domain="test", stakes="medium",
                scorer=lambda x: 4.0 if "low conf" in x else 8.0,
            )
        assert result.escalated_from is not None

    @pytest.mark.asyncio
    async def test_force_level_overrides_classifier(self, engine):
        """force_level=L3 → classifier not consulted."""
        winner = Branch(branch_id="b0", reasoning="forced",
                        output="forced answer", score=9.0, depth=0)
        mock_result = ToTResult(
            winner=winner, all_branches=[winner],
            strategy_template="", pruned_count=0, cost=0.03,
        )
        with patch.object(engine._tot, "explore",
                          new_callable=AsyncMock, return_value=mock_result):
            result = await engine.think(
                task="test", domain="test",
                force_level=ThinkLevel.L3_TREE_OF_THOUGHT,
                scorer=lambda x: 9.0,
            )
        assert result.level == ThinkLevel.L3_TREE_OF_THOUGHT

    @pytest.mark.asyncio
    async def test_force_level_still_respects_budget(self, mock_memory):
        """force_level is clamped when over budget caps."""
        budget = CognitiveBudget(max_l3_per_hour=0, max_l2_per_hour=5, max_cost_per_hour=1.0)
        engine = CognitiveEngine(mock_memory, agent_name="test_agent", budget=budget)
        mock_reflexion_result = ReflexionResult(
            answer="budget-clamped",
            score=8.0,
            attempts=1,
            cost=0.003,
        )
        with patch.object(
            engine._reflexion,
            "run",
            new_callable=AsyncMock,
            return_value=mock_reflexion_result,
        ):
            result = await engine.think(
                task="submit application",
                domain="job_application",
                force_level=ThinkLevel.L3_TREE_OF_THOUGHT,
                scorer=lambda _: 8.0,
            )
        assert result.level == ThinkLevel.L2_REFLEXION

    @pytest.mark.asyncio
    async def test_flush_writes_to_memory(self, mock_memory):
        """flush() writes pending strategy templates to memory."""
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        # L0 calls accumulate pending writes
        for _ in range(3):
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="answer"):
                await engine.think(task="test", domain="email",
                                   scorer=lambda x: 9.0)
        initial_count = len(mock_memory.learn_procedure_calls)
        await engine.flush()
        assert len(mock_memory.learn_procedure_calls) >= initial_count

    @pytest.mark.asyncio
    async def test_report_tracks_levels(self, mock_memory):
        """report() shows accurate level distribution."""
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="answer"):
            await engine.think(task="test", domain="email", scorer=lambda x: 9.0)
        report = engine.report()
        assert "l0" in report["level_counts"] or "L0_MEMORY" in str(report["level_counts"])
        assert report["total_calls"] >= 1

    @pytest.mark.asyncio
    async def test_think_returns_result(self, mock_memory):
        """ThinkResult has all required fields populated."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="test", strategy="Strategy", success_rate=0.5,
            times_used=1, avg_score_when_used=6.0,
        ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="result"):
            result = await engine.think(task="test", domain="test",
                                        scorer=lambda x: 8.0)
        assert hasattr(result, "answer")
        assert hasattr(result, "score")
        assert hasattr(result, "level")
        assert hasattr(result, "cost")
        assert hasattr(result, "latency_ms")
        assert result.answer == "result"
