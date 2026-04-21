import pytest


class TestEvalRunner:
    def test_quick_run_returns_report(self, baseline_db_path):
        from shared.adversarial._eval_runner import EvalRunner
        runner = EvalRunner(baseline_db_path=baseline_db_path)
        report = runner.run(quick=True)
        assert report.total > 0
        assert report.passed + report.failed == report.total

    def test_full_run_covers_all_categories(self, baseline_db_path):
        from shared.adversarial._eval_runner import EvalRunner
        runner = EvalRunner(baseline_db_path=baseline_db_path)
        report = runner.run(quick=False)
        assert report.total >= 30
        assert report.failed == 0

    def test_report_has_duration(self, baseline_db_path):
        from shared.adversarial._eval_runner import EvalRunner
        runner = EvalRunner(baseline_db_path=baseline_db_path)
        report = runner.run(quick=True)
        assert report.duration_s >= 0

    def test_records_baseline(self, baseline_db_path):
        from shared.adversarial._eval_runner import EvalRunner
        from shared.adversarial._baseline_tracker import BaselineTracker
        runner = EvalRunner(baseline_db_path=baseline_db_path)
        runner.run(quick=True)
        tracker = BaselineTracker(db_path=baseline_db_path)
        trend = tracker.get_trend("adversarial", "pass_rate", n=1)
        assert len(trend) == 1
        assert trend[0] == 1.0
