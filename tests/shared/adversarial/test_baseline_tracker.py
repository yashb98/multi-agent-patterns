import pytest


class TestBaselineTracker:
    def test_record_and_detect_no_regression(self, baseline_db_path):
        from shared.adversarial._baseline_tracker import BaselineTracker
        tracker = BaselineTracker(db_path=baseline_db_path)
        tracker.record("suite_a", {"score_integrity": 1.0, "sanitization": 0.95})
        regressions = tracker.detect_regressions("suite_a", {"score_integrity": 1.0, "sanitization": 0.95})
        assert regressions == []

    def test_detect_regression_above_threshold(self, baseline_db_path):
        from shared.adversarial._baseline_tracker import BaselineTracker
        tracker = BaselineTracker(db_path=baseline_db_path)
        tracker.record("suite_a", {"score_integrity": 1.0})
        regressions = tracker.detect_regressions("suite_a", {"score_integrity": 0.8})
        assert len(regressions) == 1
        assert regressions[0].metric == "score_integrity"
        assert regressions[0].drop_pct == pytest.approx(0.2, abs=0.01)

    def test_no_regression_within_threshold(self, baseline_db_path):
        from shared.adversarial._baseline_tracker import BaselineTracker
        tracker = BaselineTracker(db_path=baseline_db_path)
        tracker.record("suite_a", {"metric": 1.0})
        regressions = tracker.detect_regressions("suite_a", {"metric": 0.95}, threshold=0.1)
        assert regressions == []

    def test_get_trend(self, baseline_db_path):
        from shared.adversarial._baseline_tracker import BaselineTracker
        tracker = BaselineTracker(db_path=baseline_db_path)
        for val in [0.8, 0.85, 0.9, 0.95]:
            tracker.record("suite_a", {"metric": val})
        trend = tracker.get_trend("suite_a", "metric", n=4)
        assert trend == [0.8, 0.85, 0.9, 0.95]

    def test_trend_with_limit(self, baseline_db_path):
        from shared.adversarial._baseline_tracker import BaselineTracker
        tracker = BaselineTracker(db_path=baseline_db_path)
        for val in [0.1, 0.2, 0.3, 0.4, 0.5]:
            tracker.record("suite_a", {"m": val})
        trend = tracker.get_trend("suite_a", "m", n=3)
        assert trend == [0.3, 0.4, 0.5]

    def test_regression_uses_median_of_last_3(self, baseline_db_path):
        from shared.adversarial._baseline_tracker import BaselineTracker
        tracker = BaselineTracker(db_path=baseline_db_path)
        tracker.record("s", {"m": 1.0})
        tracker.record("s", {"m": 0.5})
        tracker.record("s", {"m": 0.9})
        # median of [1.0, 0.5, 0.9] = 0.9
        regressions = tracker.detect_regressions("s", {"m": 0.85})
        assert len(regressions) == 0  # 0.85/0.9 = 5.5% drop, under 10% threshold
