"""Failed applications must record an episode so failure learning enters the memory stack."""
from unittest.mock import patch, MagicMock
import pytest
from jobpulse.strategy_reflector import _record_failure_episode


def _make_failed_strategy():
    strategy = MagicMock()
    strategy.success = False
    strategy.domain = "greenhouse.io"
    strategy.platform = "greenhouse"
    strategy.fields_total = 12
    strategy.fields_pattern = 3
    strategy.fields_llm = 6
    strategy.fields_corrected = 8
    strategy.failure_reason = "captcha_blocked_after_3_attempts"
    return strategy


class TestFailureEpisodeRecording:
    def test_failure_calls_record_episode(self):
        captured = {}
        fake_mm = MagicMock()
        def capture_record_episode(**kwargs):
            captured.update(kwargs)
        fake_mm.record_episode = MagicMock(side_effect=capture_record_episode)

        with patch("jobpulse.strategy_reflector.get_memory_manager", return_value=fake_mm):
            _record_failure_episode(_make_failed_strategy(), [
                {"trigger": "captcha", "action": "wait_human", "confidence": 0.6},
            ])

        assert fake_mm.record_episode.called
        assert captured["domain"] == "job_application"
        assert captured["final_score"] < 5.0  # failures must score below mid
        assert "greenhouse" in captured["topic"].lower() or "greenhouse" in str(captured.get("output_summary", "")).lower()

    def test_success_does_not_call_record_episode(self):
        fake_mm = MagicMock()
        with patch("jobpulse.strategy_reflector.get_memory_manager", return_value=fake_mm):
            strategy = _make_failed_strategy()
            strategy.success = True
            _record_failure_episode(strategy, [])
        assert not fake_mm.record_episode.called

    def test_failure_with_no_heuristics_still_records(self):
        """Even without heuristics, a failure should produce an episode so we learn from it."""
        fake_mm = MagicMock()
        with patch("jobpulse.strategy_reflector.get_memory_manager", return_value=fake_mm):
            _record_failure_episode(_make_failed_strategy(), [])
        assert fake_mm.record_episode.called
