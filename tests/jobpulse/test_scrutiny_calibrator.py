"""Tests for CV scrutiny calibration."""

from __future__ import annotations

import pytest

from jobpulse.cv_templates.scrutiny_calibrator import ScrutinyCalibrator


class TestScrutinyCalibrator:
    def test_calibrate_and_insufficient_data_returns_default(self, tmp_path):
        cal = ScrutinyCalibrator(db_path=str(tmp_path / "cal.db"))
        cal.calibrate(llm_score=8, b1_warnings=[], got_interview=True, job_id="j1")
        threshold = cal.adjusted_threshold()
        assert threshold == 7.0  # Default with <10 samples

    def test_calibrate_sufficient_data_suggests_threshold(self, tmp_path):
        cal = ScrutinyCalibrator(db_path=str(tmp_path / "cal.db"))
        # Simulate: scores >= 8 have 80% interview rate, scores < 8 have 20%
        for i in range(20):
            cal.calibrate(
                llm_score=8 + (i % 3),  # 8, 9, 10
                b1_warnings=[],
                got_interview=True,
                job_id=f"high_{i}",
            )
        for i in range(20):
            cal.calibrate(
                llm_score=5 + (i % 3),  # 5, 6, 7
                b1_warnings=["weak"],
                got_interview=False,
                job_id=f"low_{i}",
            )
        threshold = cal.adjusted_threshold()
        assert threshold >= 7.0  # Should suggest higher threshold given data

    def test_insight_structure(self, tmp_path):
        cal = ScrutinyCalibrator(db_path=str(tmp_path / "cal.db"))
        for i in range(15):
            cal.calibrate(
                llm_score=7 + (i % 4),
                b1_warnings=[],
                got_interview=i % 2 == 0,
                job_id=f"j{i}",
            )
        insight = cal.get_insight()
        assert insight.current_threshold == 7.0
        assert 4.0 <= insight.suggested_threshold <= 9.0
        assert insight.sample_size == 15
        assert 0.0 <= insight.confidence <= 1.0

    def test_stats(self, tmp_path):
        cal = ScrutinyCalibrator(db_path=str(tmp_path / "cal.db"))
        cal.calibrate(llm_score=8, b1_warnings=[], got_interview=True, got_offer=True, job_id="j1")
        cal.calibrate(llm_score=6, b1_warnings=["weak"], got_interview=False, job_id="j2")
        cal.calibrate(llm_score=9, b1_warnings=[], got_interview=True, user_overrode=True, job_id="j3")

        stats = cal.get_stats()
        assert stats["total_recorded"] == 3
        assert stats["interviews"] == 2
        assert stats["offers"] == 1
        assert stats["user_overrides"] == 1
        assert stats["avg_llm_score"] == pytest.approx(7.67, 0.1)

    def test_b1_warnings_stored(self, tmp_path):
        cal = ScrutinyCalibrator(db_path=str(tmp_path / "cal.db"))
        cal.calibrate(llm_score=6, b1_warnings=["too short", "no metrics"], job_id="j1")
        # Verify by checking DB directly
        import sqlite3
        with sqlite3.connect(cal._db_path) as conn:
            row = conn.execute(
                "SELECT b1_warning_count, b1_warnings FROM cv_scrutiny_calibration"
            ).fetchone()
        assert row[0] == 2
        assert "too short" in row[1]

    def test_update_outcome(self, tmp_path):
        cal = ScrutinyCalibrator(db_path=str(tmp_path / "cal.db"))
        cal.calibrate(llm_score=8, b1_warnings=[], job_id="j1")
        updated = cal.update_outcome("j1", got_interview=True)
        assert updated is True

        stats = cal.get_stats()
        assert stats["interviews"] == 1

    def test_update_outcome_no_record(self, tmp_path):
        cal = ScrutinyCalibrator(db_path=str(tmp_path / "cal.db"))
        updated = cal.update_outcome("nonexistent", got_interview=True)
        assert updated is False
