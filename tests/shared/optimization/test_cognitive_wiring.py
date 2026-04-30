"""Tests proving CognitiveEngine.think() records cognitive outcomes."""
import asyncio
import sqlite3
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from shared.optimization._engine import OptimizationEngine


@pytest.fixture
def opt_engine(tmp_path):
    return OptimizationEngine(db_path=str(tmp_path / "optimization.db"))


@pytest.fixture
def mock_memory():
    mm = MagicMock()
    mm.get_procedural_entries.return_value = []
    mm.get_episodic_entries.return_value = []
    mm.recall.return_value = []
    mm.search.return_value = []
    return mm


def test_think_records_cognitive_outcome(opt_engine, mock_memory):
    """CognitiveEngine.think() must call record_cognitive_outcome after execution."""
    from shared.cognitive._engine import CognitiveEngine, ThinkLevel

    engine = CognitiveEngine(memory_manager=mock_memory, agent_name="test_agent")

    with patch("shared.cognitive._engine._llm_generate", new_callable=AsyncMock,
               return_value="Test answer"), \
         patch("shared.optimization.get_optimization_engine", return_value=opt_engine):
        result = asyncio.run(engine.think(
            task="Test question",
            domain="test_domain",
            stakes="low",
            force_level=ThinkLevel.L1_SINGLE,
        ))

    conn = sqlite3.connect(opt_engine._db_path)
    conn.row_factory = sqlite3.Row
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM cognitive_outcomes"
    ).fetchone()["cnt"]
    conn.close()
    assert count >= 1, "think() must record a cognitive outcome via OptimizationEngine"


def test_think_records_escalated_outcome(opt_engine, mock_memory):
    """When think() auto-escalates, it must record the escalated level."""
    from shared.cognitive._engine import CognitiveEngine, ThinkLevel

    engine = CognitiveEngine(memory_manager=mock_memory, agent_name="test_agent")

    call_count = 0

    async def mock_generate(prompt, model=None):
        nonlocal call_count
        call_count += 1
        return "Test answer"

    # scorer returns low score to trigger escalation
    def low_scorer(answer):
        return 3.0

    with patch("shared.cognitive._engine._llm_generate", side_effect=mock_generate), \
         patch("shared.cognitive._reflexion._llm_generate", side_effect=mock_generate), \
         patch("shared.optimization.get_optimization_engine", return_value=opt_engine):
        result = asyncio.run(engine.think(
            task="Test question",
            domain="test_domain",
            stakes="low",
            force_level=ThinkLevel.L1_SINGLE,
            scorer=low_scorer,
        ))

    conn = sqlite3.connect(opt_engine._db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM cognitive_outcomes").fetchall()
    conn.close()
    assert len(rows) >= 1, "think() must record outcome even when escalating"
