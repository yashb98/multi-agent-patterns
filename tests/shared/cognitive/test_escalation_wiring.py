"""Cognitive-escalation wiring tests — pipeline-bugs S8 (M-D, M-E, W-1).

Pre-fix bugs in `shared/cognitive/_engine.py`:
- M-D: escalation cost-reporting drops the original level's spend
  (~$0.001 per L1→L2 escalation); ``ThinkResult.cost`` and the
  budget tracker both undercount.
- M-E: the L1 strategy-template batch-write block is skipped when the
  escalation path early-returns — ~13 % of L0→L1 escalated successes
  never land in ``flush()`` and the procedural memory.
- W-1: ``cognitive_outcomes(escalated=1)`` is written but no
  ``OptimizationEngine.emit('adaptation')`` fires. The aggregator
  never sees the escalation, so no learning loop downstream of
  cognitive can react.

These tests fail pre-fix and pass once `_engine.py` (a) sums
original + escalated cost, (b) queues L1 templates from the
escalation branch, and (c) emits an `adaptation` signal alongside
`record_cognitive_outcome`.
"""

from unittest.mock import AsyncMock, patch

import pytest

from shared.cognitive._budget import ThinkLevel
from shared.cognitive._engine import CognitiveEngine, _GENERATE_COST
from shared.cognitive._reflexion import ReflexionResult
from shared.optimization import (
    get_optimization_engine, reset_optimization_engine,
)


@pytest.fixture(autouse=True)
def _reset_optimization_engine_between_tests():
    """Drop the cached singleton so the autouse `OPTIMIZATION_DB=tmp` env
    from the root ``conftest.py`` actually applies to fresh state."""
    reset_optimization_engine()
    yield
    reset_optimization_engine()


# ---------------------------------------------------------------------------
# M-E: L0 → L1 escalation must queue the L1 template for flush()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l0_to_l1_escalation_queues_template_for_flush(mock_memory):
    """Pre-fix: early ``return escalated_result`` jumps past the L1 queue.

    Post-fix: ``_maybe_queue_l1_template`` runs from both return paths so the
    escalated L1 success ends up in ``_pending_writes`` and lands in memory
    via ``flush()``.
    """
    from tests.shared.cognitive.conftest import MockProceduralEntry

    # Strong templates make the classifier pick L0_MEMORY first. The L0 path
    # returns the (stale) ``Strategy 0`` answer, which the scorer rejects with
    # 4.0, triggering escalation to L1.
    for i in range(4):
        mock_memory._procedural.append(MockProceduralEntry(
            procedure_id=f"proc_{i}", domain="cv_tailoring",
            strategy=f"Strategy {i}", success_rate=0.95,
            times_used=5, avg_score_when_used=8.5,
        ))
    engine = CognitiveEngine(mock_memory, agent_name="test_agent")

    # L1 returns a passing answer.
    with patch(
        "shared.cognitive._engine._llm_generate",
        new_callable=AsyncMock, return_value="good escalated answer",
    ):
        result = await engine.think(
            task="tailor cv for senior data role",
            domain="cv_tailoring",
            scorer=lambda x: 8.0 if "good" in x else 4.0,
        )

    assert result.escalated_from == ThinkLevel.L0_MEMORY
    assert result.level == ThinkLevel.L1_SINGLE

    # M-E: post-fix the queue contains the escalated L1 success.
    assert len(engine._pending_writes) == 1, (
        f"Expected escalated L1 success queued for flush; got "
        f"{engine._pending_writes}. M-E: L1 batch-write block was skipped "
        "when the escalation path early-returned."
    )
    queued = engine._pending_writes[0]
    assert queued["domain"] == "cv_tailoring"
    assert queued["score"] == 8.0
    assert queued["source"] == "test_agent"

    # Flush actually writes to memory:
    await engine.flush()
    assert len(mock_memory.learn_procedure_calls) == 1
    assert mock_memory.learn_procedure_calls[0]["domain"] == "cv_tailoring"


# ---------------------------------------------------------------------------
# M-D: cost summing across escalation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l0_to_l1_escalation_cost_is_l0_plus_l1(mock_memory):
    """L0→L1: original cost is 0.0 so total == L1 cost. Trivial check, but
    locks the math contract."""
    from tests.shared.cognitive.conftest import MockProceduralEntry

    for i in range(4):
        mock_memory._procedural.append(MockProceduralEntry(
            procedure_id=f"proc_{i}", domain="email",
            strategy=f"Strategy {i}", success_rate=0.95,
            times_used=5, avg_score_when_used=8.5,
        ))
    engine = CognitiveEngine(mock_memory, agent_name="test_agent")
    with patch(
        "shared.cognitive._engine._llm_generate",
        new_callable=AsyncMock, return_value="better answer",
    ):
        result = await engine.think(
            task="classify email", domain="email",
            scorer=lambda x: 8.0 if "better" in x else 4.0,
        )
    assert result.escalated_from == ThinkLevel.L0_MEMORY
    assert result.cost == pytest.approx(_GENERATE_COST), (
        f"L0(0.0)+L1({_GENERATE_COST}) should sum to {_GENERATE_COST}; got {result.cost}"
    )


@pytest.mark.asyncio
async def test_l1_to_l2_escalation_cost_sums_both_levels(mock_memory):
    """L1→L2: both levels charged. Pre-fix this dropped the L1 spend
    (~$0.001 per call) — the audit's headline cost number.
    """
    from tests.shared.cognitive.conftest import MockProceduralEntry

    # Weak templates make the classifier pick L1 (memory exists but is
    # not 'strong'). Empty domain+novel topic would route to L1 too via the
    # stakes path — same effect.
    mock_memory._procedural.append(MockProceduralEntry(
        domain="research", strategy="weak", success_rate=0.5,
        times_used=1, avg_score_when_used=6.0,
    ))
    engine = CognitiveEngine(mock_memory, agent_name="test_agent")
    reflexion_cost = 0.005
    mock_reflexion = ReflexionResult(
        answer="reflexion answer", score=8.0, attempts=2, cost=reflexion_cost,
    )
    with patch(
        "shared.cognitive._engine._llm_generate",
        new_callable=AsyncMock, return_value="low conf answer",
    ), patch.object(
        engine._reflexion, "run",
        new_callable=AsyncMock, return_value=mock_reflexion,
    ):
        result = await engine.think(
            task="hard reasoning task",
            domain="research",
            stakes="medium",
            scorer=lambda x: 4.0 if "low conf" in x else 8.0,
        )
    assert result.escalated_from == ThinkLevel.L1_SINGLE
    assert result.level == ThinkLevel.L2_REFLEXION
    expected = _GENERATE_COST + reflexion_cost
    assert result.cost == pytest.approx(expected), (
        f"L1({_GENERATE_COST})+L2({reflexion_cost}) should sum to {expected}; "
        f"got {result.cost}. M-D: original level's cost was being dropped."
    )

    # Budget tracker should have both levels charged.
    breakdown = engine.report()["budget"]
    # Schema isn't strictly typed so just check the totals reflect both spends.
    # If only L2 was recorded, total_cost would equal `reflexion_cost` alone.
    assert engine._total_cost == pytest.approx(expected), (
        f"Budget tracker total_cost={engine._total_cost} should equal "
        f"{expected} (L1+L2). M-D: original level's cost dropped from tracker."
    )


# ---------------------------------------------------------------------------
# W-1: adaptation signal emitted alongside cognitive_outcomes write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalation_emits_adaptation_signal(mock_memory):
    """Acceptance criterion (runner table S8):
    After L0→L1 escalation, ``cognitive_outcomes.escalated=1`` AND
    ``optimization_engine.signals`` has matching ``signal_type='adaptation'``.
    """
    from tests.shared.cognitive.conftest import MockProceduralEntry

    for i in range(4):
        mock_memory._procedural.append(MockProceduralEntry(
            procedure_id=f"proc_{i}", domain="screening_answers",
            strategy=f"Strategy {i}", success_rate=0.95,
            times_used=5, avg_score_when_used=8.5,
        ))
    engine = CognitiveEngine(mock_memory, agent_name="screening_agent")

    with patch(
        "shared.cognitive._engine._llm_generate",
        new_callable=AsyncMock, return_value="good answer",
    ):
        result = await engine.think(
            task="generate screening answer",
            domain="screening_answers",
            scorer=lambda x: 8.0 if "good" in x else 4.0,
        )
    assert result.escalated_from == ThinkLevel.L0_MEMORY

    opt = get_optimization_engine()
    sigs = opt._bus.query(signal_type="adaptation", limit=10)
    matching = [
        s for s in sigs
        if s.payload.get("from_level") == 0 and s.payload.get("to_level") == 1
    ]
    assert matching, (
        f"Expected adaptation signal with from_level=0, to_level=1; got "
        f"{[s.payload for s in sigs]}. W-1: signal emit was missing from "
        "the cognitive escalation path."
    )
    sig = matching[0]
    assert sig.source_loop == "cognitive_engine"
    assert sig.domain == "screening_answers"
    assert sig.agent_name == "screening_agent"
    assert sig.payload["score_before"] == 4.0
    assert sig.payload["score_after"] == 8.0


@pytest.mark.asyncio
async def test_non_escalated_path_does_not_emit_adaptation(mock_memory):
    """Negative control: when the call doesn't escalate, no adaptation
    signal should fire."""
    from tests.shared.cognitive.conftest import MockProceduralEntry

    mock_memory._procedural.append(MockProceduralEntry(
        procedure_id="strong_proc", domain="email",
        strategy="strong proven strategy", success_rate=0.95,
        times_used=10, avg_score_when_used=8.5,
    ))
    engine = CognitiveEngine(mock_memory, agent_name="test_agent")
    result = await engine.think(
        task="classify email", domain="email",
        scorer=lambda x: 8.5,  # passes on first try, no escalation
    )
    assert result.escalated_from is None

    opt = get_optimization_engine()
    sigs = opt._bus.query(signal_type="adaptation", limit=10)
    assert not sigs, (
        f"No adaptation signal should fire on non-escalated path; got {sigs}"
    )
