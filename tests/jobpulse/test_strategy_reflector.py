"""Tests for strategy_reflector — two-pass heuristic extraction pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.strategy_reflector import (
    _compute_strategy_score,
    _extract_correction_heuristics,
    _extract_slow_field_heuristics,
    _extract_strategy_distribution_heuristics,
    _feed_experience_memory,
    extract_deterministic_heuristics,
    reflect_on_application,
    reflect_with_llm,
)
from jobpulse.trajectory_store import (
    ApplicationStrategy,
    FieldTrajectory,
    Heuristic,
    StrategyTier,
    TrajectoryStore,
    _reset_shared_store,
)


@pytest.fixture
def store(tmp_path):
    _reset_shared_store()
    return TrajectoryStore(db_path=str(tmp_path / "test_reflect.db"))


def _make_trajectory(**overrides) -> FieldTrajectory:
    defaults = dict(
        job_id="job1", domain="greenhouse.io", page_index=0,
        field_label="First Name", field_type="text", strategy="pattern_match",
        value_filled="Yash", confidence=0.95,
        corrected=False, corrected_value="", time_ms=50,
        created_at="2026-04-24T12:00:00",
    )
    defaults.update(overrides)
    return FieldTrajectory(**defaults)


def _make_strategy(**overrides) -> ApplicationStrategy:
    defaults = dict(
        job_id="job1", domain="greenhouse.io", platform="greenhouse",
        adapter="extension", navigation_strategy="replay",
        fields_total=10, fields_pattern=6, fields_llm=2,
        fields_cached=2, fields_corrected=1,
        total_time_seconds=45.0, success=True,
        reflection="", heuristics="",
    )
    defaults.update(overrides)
    return ApplicationStrategy(**defaults)


class TestCorrectionHeuristics:
    def test_extracts_from_corrections(self):
        trajectories = [
            _make_trajectory(corrected=True, corrected_value="Manchester",
                             value_filled="London", field_label="City"),
        ]
        result = _extract_correction_heuristics(trajectories)
        assert len(result) == 1
        assert "City" in result[0]["trigger"]
        assert "Manchester" in result[0]["action"]
        assert result[0]["confidence"] == 0.95

    def test_skips_uncorrected(self):
        trajectories = [_make_trajectory(corrected=False)]
        assert _extract_correction_heuristics(trajectories) == []

    def test_skips_corrected_without_value(self):
        trajectories = [_make_trajectory(corrected=True, corrected_value=None)]
        assert _extract_correction_heuristics(trajectories) == []


class TestStrategyDistributionHeuristics:
    def test_flags_high_correction_rate(self):
        trajectories = [
            _make_trajectory(strategy="llm_tier3", corrected=True, corrected_value="x"),
            _make_trajectory(strategy="llm_tier3", corrected=True, corrected_value="y"),
            _make_trajectory(strategy="pattern_match", corrected=False),
        ]
        result = _extract_strategy_distribution_heuristics(trajectories)
        assert len(result) == 1
        assert "avoid" in result[0]["action"]
        assert "llm_tier3" in result[0]["trigger"]

    def test_flags_reliable_strategy(self):
        trajectories = [
            _make_trajectory(strategy="pattern_match", corrected=False)
            for _ in range(4)
        ]
        result = _extract_strategy_distribution_heuristics(trajectories)
        assert len(result) == 1
        assert "reliable" in result[0]["action"]

    def test_needs_minimum_trajectories(self):
        trajectories = [_make_trajectory(), _make_trajectory()]
        assert _extract_strategy_distribution_heuristics(trajectories) == []


class TestSlowFieldHeuristics:
    def test_flags_slow_fields(self):
        trajectories = [
            _make_trajectory(field_label="Salary", time_ms=6000),
            _make_trajectory(field_label="Salary", time_ms=7000),
        ]
        result = _extract_slow_field_heuristics(trajectories)
        assert len(result) == 1
        assert "Salary" in result[0]["trigger"]
        assert "pre-cache" in result[0]["action"]

    def test_ignores_fast_fields(self):
        trajectories = [_make_trajectory(time_ms=100)]
        assert _extract_slow_field_heuristics(trajectories) == []

    def test_needs_repeated_slowness(self):
        trajectories = [_make_trajectory(field_label="Salary", time_ms=6000)]
        assert _extract_slow_field_heuristics(trajectories) == []


class TestDeterministicPipeline:
    def test_combines_all_extractors(self):
        trajectories = [
            _make_trajectory(corrected=True, corrected_value="Fixed",
                             value_filled="Wrong", field_label="City",
                             strategy="llm_tier3"),
            _make_trajectory(strategy="cache_hit", corrected=False, field_label="Name"),
            _make_trajectory(strategy="cache_hit", corrected=False, field_label="Email"),
            _make_trajectory(strategy="cache_hit", corrected=False, field_label="Phone"),
            _make_trajectory(field_label="Desc", time_ms=8000),
            _make_trajectory(field_label="Desc", time_ms=9000),
        ]
        result = extract_deterministic_heuristics(trajectories)
        sources = {h.get("source") for h in result}
        assert "correction" in sources
        assert "strategy_distribution" in sources
        assert "slow_field" in sources


class TestComputeStrategyScore:
    def test_successful_high_pattern(self):
        s = _make_strategy(fields_total=10, fields_pattern=8,
                           fields_corrected=0, total_time_seconds=30)
        score = _compute_strategy_score(s)
        assert score >= 7.5

    def test_many_corrections_penalized(self):
        s = _make_strategy(fields_total=10, fields_pattern=2,
                           fields_corrected=5, total_time_seconds=45)
        score = _compute_strategy_score(s)
        assert score < 5.0

    def test_failure_gets_low_score(self):
        s = _make_strategy(success=False)
        assert _compute_strategy_score(s) == 2.0

    def test_slow_application_penalty(self):
        fast = _make_strategy(total_time_seconds=30)
        slow = _make_strategy(total_time_seconds=400)
        assert _compute_strategy_score(fast) > _compute_strategy_score(slow)

    def test_score_clamped_0_10(self):
        s = _make_strategy(fields_total=10, fields_pattern=10,
                           fields_corrected=0, total_time_seconds=10)
        assert 0.0 <= _compute_strategy_score(s) <= 10.0


class TestReflectWithLLM:
    @patch("shared.cost_tracker.track_llm_usage", return_value={})
    @patch("jobpulse.strategy_reflector.smart_llm_call")
    @patch("jobpulse.strategy_reflector.get_llm")
    def test_parses_valid_json(self, _mock_llm, mock_smart_call, _mock_track):
        mock_response = MagicMock()
        mock_response.content = json.dumps([
            {"trigger": "city field", "action": "use ArrowDown", "confidence": 0.8},
        ])
        mock_smart_call.return_value = mock_response

        result = reflect_with_llm(
            _make_strategy(),
            [_make_trajectory()],
        )
        assert len(result) == 1
        assert result[0]["trigger"] == "city field"

    @patch("shared.cost_tracker.track_llm_usage", return_value={})
    @patch("jobpulse.strategy_reflector.smart_llm_call")
    @patch("jobpulse.strategy_reflector.get_llm")
    def test_handles_invalid_json(self, _mock_llm, mock_smart_call, _mock_track):
        mock_response = MagicMock()
        mock_response.content = "not valid json"
        mock_smart_call.return_value = mock_response

        result = reflect_with_llm(_make_strategy(), [_make_trajectory()])
        assert result == []

    @patch("shared.cost_tracker.track_llm_usage", return_value={})
    @patch("jobpulse.strategy_reflector.smart_llm_call")
    @patch("jobpulse.strategy_reflector.get_llm")
    def test_filters_malformed_heuristics(self, _mock_llm, mock_smart_call, _mock_track):
        mock_response = MagicMock()
        mock_response.content = json.dumps([
            {"trigger": "good", "action": "good"},
            {"bad": "no trigger or action"},
            "not even a dict",
        ])
        mock_smart_call.return_value = mock_response

        result = reflect_with_llm(_make_strategy(), [_make_trajectory()])
        assert len(result) == 1


class TestFeedExperienceMemory:
    def test_stores_high_score(self):
        mock_em = MagicMock()
        mock_exp_cls = MagicMock(side_effect=lambda **kw: MagicMock(**kw))

        strategy = _make_strategy(fields_total=10, fields_pattern=8,
                                  fields_corrected=0, total_time_seconds=30)
        heuristics = [{"trigger": "t", "action": "a"}]

        with patch.dict("sys.modules", {
            "shared.experiential_learning": MagicMock(
                get_shared_experience_memory=MagicMock(return_value=mock_em),
                Experience=mock_exp_cls,
            ),
        }):
            _feed_experience_memory(strategy, heuristics)
        mock_em.store.assert_called_once()

    def test_skips_below_threshold(self):
        """Score 6.0 strategies should NOT be stored (threshold is 7.5)."""
        mock_em = MagicMock()

        strategy = _make_strategy(fields_total=10, fields_pattern=5,
                                  fields_corrected=1, total_time_seconds=90)
        score = _compute_strategy_score(strategy)
        assert score < 7.5

        with patch.dict("sys.modules", {
            "shared.experiential_learning": MagicMock(
                get_shared_experience_memory=MagicMock(return_value=mock_em),
                Experience=MagicMock(),
            ),
        }):
            _feed_experience_memory(strategy, [{"trigger": "t", "action": "a"}])
        mock_em.store.assert_not_called()

    def test_skips_failed_strategy(self):
        strategy = _make_strategy(success=False)
        _feed_experience_memory(strategy, [{"trigger": "t", "action": "a"}])

    def test_skips_empty_heuristics(self):
        _feed_experience_memory(_make_strategy(), [])


class TestReflectOnApplication:
    def test_full_pipeline_deterministic_only(self, store):
        store.log_field("job1", "greenhouse.io", "Name", "pattern_match", time_ms=50)
        store.log_field("job1", "greenhouse.io", "Email", "pattern_match", time_ms=30)
        store.log_field("job1", "greenhouse.io", "Phone", "pattern_match", time_ms=40)
        store.mark_corrected("job1", "greenhouse.io", "Phone", "+44123456789")

        with patch("jobpulse.strategy_reflector._feed_experience_memory"):
            result = reflect_on_application(
                store, "job1",
                {"platform": "greenhouse", "domain": "greenhouse.io"},
            )

        assert result.fields_total == 3
        saved = store.get_strategy("job1")
        assert saved is not None
        reflection = json.loads(saved.reflection)
        assert reflection["deterministic"] >= 1

    def test_llm_pass_triggered_when_few_deterministic(self, store):
        store.log_field("job1", "g.io", "Name", "pattern_match", time_ms=50)
        store.log_field("job1", "g.io", "Email", "pattern_match", time_ms=30)
        store.log_field("job1", "g.io", "Phone", "pattern_match", time_ms=40)

        with patch("jobpulse.strategy_reflector.reflect_with_llm", return_value=[
            {"trigger": "llm_trigger", "action": "llm_action", "confidence": 0.7},
        ]) as mock_llm, patch("jobpulse.strategy_reflector._feed_experience_memory"):
            result = reflect_on_application(
                store, "job1",
                {"platform": "greenhouse"},
                llm_threshold=5,
            )
        mock_llm.assert_called_once()
        heuristics_json = json.loads(result.heuristics)
        assert any(h["trigger"] == "llm_trigger" for h in heuristics_json)

    def test_saves_typed_heuristics(self, store):
        store.log_field("job1", "g.io", "City", "pattern_match",
                        value_filled="London", time_ms=50)
        store.mark_corrected("job1", "g.io", "City", "Manchester")
        store.log_field("job1", "g.io", "Name", "pattern_match", time_ms=30)
        store.log_field("job1", "g.io", "Email", "pattern_match", time_ms=20)

        with patch("jobpulse.strategy_reflector._feed_experience_memory"):
            reflect_on_application(store, "job1", {"platform": "greenhouse"})

        heuristics = store.get_heuristics("g.io")
        assert len(heuristics) >= 1
