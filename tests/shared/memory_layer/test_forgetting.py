import math
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from shared.memory_layer._entries import (
    MemoryEntry, MemoryTier, Lifecycle, ProtectionLevel,
)
from shared.memory_layer._forgetting import ForgettingEngine

BASE_STABILITY = 48.0


@pytest.fixture
def neo4j_mock():
    mock = MagicMock()
    mock.degree.return_value = 0
    mock.avg_downstream_score.return_value = 0.0
    mock.count_similar.return_value = 0
    return mock


@pytest.fixture
def engine(neo4j_mock):
    return ForgettingEngine(neo4j=neo4j_mock)


class TestDecayScore:
    def test_fresh_memory_is_near_1(self, engine, make_memory):
        entry = make_memory()
        score = engine.compute_decay(entry)
        assert score > 0.9

    def test_drops_over_time(self, engine, make_memory):
        entry = make_memory()
        entry.last_accessed = datetime.now() - timedelta(hours=48)
        score = engine.compute_decay(entry)
        assert score < 0.6

    def test_access_count_increases_stability(self, engine, make_memory):
        low_access = make_memory(access_count=0)
        high_access = make_memory(access_count=10)
        low_access.last_accessed = datetime.now() - timedelta(hours=48)
        high_access.last_accessed = datetime.now() - timedelta(hours=48)
        low_score = engine.compute_decay(low_access)
        high_score = engine.compute_decay(high_access)
        assert high_score > low_score

    def test_quality_signal_boosts_decay(self, engine, make_memory):
        low_q = make_memory(score=3.0)
        high_q = make_memory(score=9.0)
        low_q.last_accessed = datetime.now() - timedelta(hours=24)
        high_q.last_accessed = datetime.now() - timedelta(hours=24)
        assert engine.compute_decay(high_q) > engine.compute_decay(low_q)

    def test_connectivity_signal(self, engine, neo4j_mock, make_memory):
        entry = make_memory()
        neo4j_mock.degree.return_value = 6
        score = engine.compute_decay(entry)
        assert score > 0.95

    def test_uniqueness_last_survivor(self, engine, neo4j_mock, make_memory):
        entry = make_memory()
        neo4j_mock.count_similar.return_value = 0
        score = engine.compute_decay(entry)
        neo4j_mock.count_similar.return_value = 5
        score_redundant = engine.compute_decay(entry)
        assert score > score_redundant

    def test_impact_from_descendants(self, engine, neo4j_mock, make_memory):
        entry = make_memory()
        neo4j_mock.avg_downstream_score.return_value = 9.0
        score = engine.compute_decay(entry)
        assert score > 0.95


class TestProtection:
    def test_pinned_never_forgotten(self, engine, make_memory):
        entry = make_memory(payload={"pinned": True})
        assert engine.get_protection(entry) == ProtectionLevel.PINNED

    def test_last_survivor(self, engine, neo4j_mock, make_memory):
        neo4j_mock.count_similar.return_value = 0
        entry = make_memory()
        assert engine.get_protection(entry) == ProtectionLevel.PROTECTED

    def test_hub_node_elevated(self, engine, neo4j_mock, make_memory):
        neo4j_mock.degree.return_value = 6
        neo4j_mock.count_similar.return_value = 3
        entry = make_memory()
        assert engine.get_protection(entry) == ProtectionLevel.ELEVATED


class TestSweep:
    def test_stm_tombstoned_below_threshold(self, engine, make_memory):
        entry = make_memory(lifecycle=Lifecycle.STM, decay_score=0.2)
        entry.last_accessed = datetime.now() - timedelta(hours=100)
        actions = engine.evaluate_single(entry)
        assert actions.get("tombstone") is True

    def test_ltm_not_tombstoned(self, engine, neo4j_mock, make_memory):
        neo4j_mock.count_similar.return_value = 5
        entry = make_memory(lifecycle=Lifecycle.LTM, decay_score=0.05)
        actions = engine.evaluate_single(entry)
        assert actions.get("tombstone") is not True

    def test_promotion_stm_to_mtm(self, engine, make_memory):
        entry = make_memory(lifecycle=Lifecycle.STM, access_count=4)
        actions = engine.evaluate_single(entry)
        assert actions.get("promote_to") == Lifecycle.MTM

    def test_promotion_mtm_to_ltm(self, engine, make_memory):
        entry = make_memory(
            lifecycle=Lifecycle.MTM, access_count=12,
            payload={"times_validated": 6},
        )
        actions = engine.evaluate_single(entry)
        assert actions.get("promote_to") == Lifecycle.LTM

    def test_demotion_ltm_to_cold(self, engine, neo4j_mock, make_memory):
        neo4j_mock.count_similar.return_value = 5
        entry = make_memory(lifecycle=Lifecycle.LTM, decay_score=0.05)
        entry.last_accessed = datetime.now() - timedelta(days=60)
        entry.confidence = 0.5
        actions = engine.evaluate_single(entry)
        assert actions.get("demote_to") == Lifecycle.COLD
