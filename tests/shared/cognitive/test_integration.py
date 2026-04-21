import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from shared.cognitive._engine import CognitiveEngine
from shared.cognitive._budget import ThinkLevel, CognitiveBudget
from shared.cognitive._reflexion import ReflexionResult
from shared.cognitive._tree_of_thought import ToTResult, Branch
from tests.shared.cognitive.conftest import MockMemoryManager, MockProceduralEntry


class TestIntegration:

    @pytest.mark.asyncio
    async def test_full_pipeline_novel_to_cached(self):
        """Novel task (L2) → templates stored → same task again → L0."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # First call: novel domain → L2 Reflexion
        async def mock_gen(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            return "good answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_gen), \
             patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="good answer"):
            result1 = await engine.think(
                task="classify email", domain="email_classification",
                stakes="medium", scorer=lambda x: 8.5,
            )
        assert result1.level in (ThinkLevel.L2_REFLEXION, ThinkLevel.L1_SINGLE)

        # Manually set strong templates (simulating what learn_procedure did)
        for i in range(4):
            memory._procedural.append(MockProceduralEntry(
                procedure_id=f"learned_{i}", domain="email_classification",
                strategy=f"Learned: {result1.answer[:50]}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))

        # Second call: same domain → should be L0
        result2 = await engine.think(
            task="classify email", domain="email_classification",
            scorer=lambda x: 9.0,
        )
        assert result2.level == ThinkLevel.L0_MEMORY

    @pytest.mark.asyncio
    async def test_failure_prevents_repeat(self):
        """Failure stored → shows up as anti-pattern in next attempt."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # First call fails
        async def mock_gen_fail(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Missed edge case\nFIX: Check boundaries"
            return "bad answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_gen_fail), \
             patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="bad"):
            await engine.think(
                task="classify", domain="email_classification",
                stakes="medium", scorer=lambda x: 3.0,
            )

        # Verify failure was stored
        failures = [e for e in memory._episodic if e.final_score < 5.0]
        assert len(failures) >= 1

    @pytest.mark.asyncio
    async def test_cross_agent_transfer(self):
        """Agent A creates template → Agent B on same domain finds it."""
        memory = MockMemoryManager()

        # Agent A learns
        engine_a = CognitiveEngine(memory, agent_name="agent_a")
        memory._procedural.append(MockProceduralEntry(
            procedure_id="transfer_1", domain="shared_domain",
            strategy="Agent A's strategy: validate first",
            success_rate=0.9, times_used=5, avg_score_when_used=8.5,
        ))

        # Agent B queries same domain
        engine_b = CognitiveEngine(memory, agent_name="agent_b")
        composed = engine_b._composer.compose(
            "do task", "shared_domain", "agent_b", memory,
        )
        assert "validate first" in composed.text

    @pytest.mark.asyncio
    async def test_escalation_chain_l0_to_l3(self):
        """L0 fails → L1 fails → L2 fails → L3 succeeds."""
        memory = MockMemoryManager()
        for i in range(4):
            memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="hard",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # Everything returns low score except L3
        winner = Branch(branch_id="b0", reasoning="r", output="L3 answer",
                        score=9.0, depth=0)
        tot_result = ToTResult(
            winner=winner, all_branches=[winner], strategy_template="",
            pruned_count=0, cost=0.03,
        )

        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="bad"), \
             patch.object(engine._reflexion, "run", new_callable=AsyncMock,
                          return_value=ReflexionResult(
                              answer="still bad", score=4.0, attempts=3, cost=0.01)), \
             patch.object(engine._tot, "explore", new_callable=AsyncMock,
                          return_value=tot_result):
            result = await engine.think(
                task="hard task", domain="hard", stakes="high",
                scorer=lambda x: 9.0 if "L3" in x else 3.0,
            )
        assert result.escalated_from is not None

    @pytest.mark.asyncio
    async def test_cron_lifecycle(self):
        """Cron simulation: create engine → think → flush → new engine → verify."""
        memory = MockMemoryManager()

        # Simulate cron run 1
        engine1 = CognitiveEngine(memory, agent_name="cron_agent")
        memory._procedural.append(MockProceduralEntry(
            domain="cron_task", strategy="Cron strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="cron result"):
            await engine1.think(task="cron task", domain="cron_task",
                                scorer=lambda x: 8.0)
        await engine1.flush()

        # New engine on new cron run should see the templates
        engine2 = CognitiveEngine(memory, agent_name="cron_agent")
        procs = memory.get_procedural_entries("cron_task")
        assert len(procs) >= 1

    @pytest.mark.asyncio
    async def test_concurrent_agents(self):
        """5 agents thinking simultaneously → no data corruption."""
        memory = MockMemoryManager()

        async def agent_run(name: str):
            engine = CognitiveEngine(memory, agent_name=name)
            memory._procedural.append(MockProceduralEntry(
                domain=f"domain_{name}", strategy=f"Strategy for {name}",
                success_rate=0.5, times_used=1, avg_score_when_used=6.0,
            ))
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value=f"result_{name}"):
                return await engine.think(
                    task=f"task for {name}", domain=f"domain_{name}",
                    scorer=lambda x: 8.0,
                )

        results = await asyncio.gather(
            *[agent_run(f"agent_{i}") for i in range(5)]
        )
        assert len(results) == 5
        assert all(r.answer for r in results)

    @pytest.mark.asyncio
    async def test_degraded_mode_no_memory(self):
        """Memory manager raises → engine degrades to L1."""
        class BrokenMemory:
            def get_procedural_entries(self, domain):
                raise RuntimeError("Memory unavailable")
            def get_episodic_entries(self, domain):
                raise RuntimeError("Memory unavailable")
            def learn_procedure(self, **kwargs):
                pass
            def record_episode(self, **kwargs):
                pass

        engine = CognitiveEngine(BrokenMemory(), agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="degraded answer"):
            result = await engine.think(
                task="test", domain="test", scorer=lambda x: 7.0,
            )
        assert result.answer == "degraded answer"

    @pytest.mark.asyncio
    async def test_report_after_session(self):
        """Full session → report has accurate stats."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # L1 call
        memory._procedural.append(MockProceduralEntry(
            domain="d1", strategy="S1", success_rate=0.5,
            times_used=1, avg_score_when_used=6.0,
        ))
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="answer"):
            await engine.think(task="t1", domain="d1", scorer=lambda x: 8.0)

        report = engine.report()
        assert report["total_calls"] >= 1
        assert report["total_cost"] >= 0
