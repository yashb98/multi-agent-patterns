"""Tests for WeeklyOptimizer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jobpulse.weekly_optimize import WeeklyOptimizer, Recommendation, WeeklyReport
import jobpulse.weekly_optimize as _wo_module


@pytest.fixture(autouse=True)
def patch_data_dir(tmp_path, monkeypatch):
    """Patch DATA_DIR for all tests in this module."""
    monkeypatch.setattr(_wo_module, "DATA_DIR", tmp_path)


class TestWeeklyReport:
    def test_to_markdown_format(self):
        report = WeeklyReport(
            generated_at="2026-04-26T12:00:00",
            period_start="2026-04-19T12:00:00",
            period_end="2026-04-26T12:00:00",
            recommendations=[
                Recommendation(
                    category="gate", action="raise_threshold", target="gate_3_tech",
                    current_value="0.65", suggested_value="0.70",
                    confidence=0.85, evidence="Low interview rate",
                ),
            ],
        )
        md = report.to_markdown()
        assert "Weekly Optimization Report" in md
        assert "gate_3_tech" in md
        assert "raise_threshold" in md

    def test_to_dict_roundtrip(self):
        report = WeeklyReport(
            generated_at="2026-04-26T12:00:00",
            period_start="2026-04-19T12:00:00",
            period_end="2026-04-26T12:00:00",
            recommendations=[
                Recommendation(category="cv", action="lower_threshold", target="scrutiny", confidence=0.7),
            ],
        )
        d = report.to_dict()
        assert d["generated_at"] == "2026-04-26T12:00:00"
        assert len(d["recommendations"]) == 1
        assert d["recommendations"][0]["category"] == "cv"


class TestAnalyzeGateThresholds:
    def test_suggests_raise_when_low_rate(self, tmp_path):
        db_path = tmp_path / "gate_thresholds.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE gate_threshold_outcomes (
                    id INTEGER PRIMARY KEY,
                    family TEXT, domain TEXT,
                    jd_quality REAL, got_interview INTEGER,
                    recorded_at TEXT
                )
            """)
            for _ in range(10):
                conn.execute(
                    "INSERT INTO gate_threshold_outcomes (family, jd_quality, got_interview) VALUES (?, ?, ?)",
                    ("tech", 0.7, 0),
                )

        opt = WeeklyOptimizer()
        opt._analyze_gate_thresholds()

        recs = [r for r in opt._recommendations if r.category == "gate"]
        assert len(recs) == 1
        assert recs[0].action == "raise_threshold"
        assert recs[0].target == "gate_3_tech"

    def test_suggests_lower_when_high_rate(self, tmp_path):
        db_path = tmp_path / "gate_thresholds.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE gate_threshold_outcomes (
                    id INTEGER PRIMARY KEY,
                    family TEXT, domain TEXT,
                    jd_quality REAL, got_interview INTEGER,
                    recorded_at TEXT
                )
            """)
            for _ in range(10):
                conn.execute(
                    "INSERT INTO gate_threshold_outcomes (family, jd_quality, got_interview) VALUES (?, ?, ?)",
                    ("ml_ai", 0.7, 1),
                )

        opt = WeeklyOptimizer()
        opt._analyze_gate_thresholds()

        recs = [r for r in opt._recommendations if r.category == "gate"]
        assert len(recs) == 1
        assert recs[0].action == "lower_threshold"


class TestAnalyzeCVScrutiny:
    def test_suggests_threshold_change(self, tmp_path):
        db_path = tmp_path / "cv_scrutiny_calibration.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE cv_scrutiny_calibration (
                    job_id TEXT, llm_score REAL, b1_warnings TEXT,
                    got_interview INTEGER, user_overrode INTEGER, applied_at TEXT
                )
            """)
            for _ in range(10):
                conn.execute(
                    "INSERT INTO cv_scrutiny_calibration VALUES (?, ?, ?, ?, ?, ?)",
                    ("j1", 5.5, "[]", 1, 0, "2026-04-20"),
                )

        opt = WeeklyOptimizer()
        opt._analyze_cv_scrutiny()

        recs = [r for r in opt._recommendations if r.category == "cv"]
        assert len(recs) >= 1
        assert "threshold" in recs[0].action


class TestAnalyzeProjectSelection:
    def test_boosts_high_performing_archetype(self, tmp_path):
        db_path = tmp_path / "project_selection_outcomes.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE project_selection_outcomes (
                    id INTEGER PRIMARY KEY,
                    project_id TEXT, project_name TEXT, archetype TEXT,
                    times_selected INTEGER, interviews INTEGER, offers INTEGER,
                    total_ats_score REAL, last_selected_at TEXT
                )
            """)
            conn.execute(
                "INSERT INTO project_selection_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "p1", "Agent System", "ml_engineer", 5, 3, 1, 400.0, "2026-04-20"),
            )

        opt = WeeklyOptimizer()
        opt._analyze_project_selection()

        recs = [r for r in opt._recommendations if r.category == "project"]
        assert len(recs) == 1
        assert recs[0].action == "boost_archetype"


class TestAnalyzeCorrections:
    def test_suggests_profile_update(self, tmp_path):
        db_path = tmp_path / "field_corrections.db"
        # Use a recent timestamp (within the rolling 7-day cutoff) so the
        # query picks it up regardless of when the test runs.
        from datetime import datetime, timezone, timedelta
        recent_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE field_corrections (
                    id INTEGER PRIMARY KEY,
                    domain TEXT, platform TEXT,
                    field_label TEXT, agent_value TEXT, user_value TEXT,
                    created_at TEXT
                )
            """)
            for i in range(5):
                conn.execute(
                    "INSERT INTO field_corrections VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (i + 1, "greenhouse", "gh", "salary", "40000", "45000", recent_ts),
                )

        opt = WeeklyOptimizer()
        opt._analyze_corrections()

        recs = [r for r in opt._recommendations if r.category == "profile"]
        assert len(recs) == 1
        assert recs[0].action == "update_profile"
        assert recs[0].target == "salary"


class TestAnalyzeCompanyReliability:
    def test_suggests_block_for_zero_rate(self, tmp_path):
        db_path = tmp_path / "applications.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE applications (
                    job_id TEXT, company TEXT, status TEXT, applied_at TEXT
                )
            """)
            for _ in range(12):
                conn.execute(
                    "INSERT INTO applications VALUES (?, ?, ?, ?)",
                    ("j1", "BadCorp", "Rejected", "2026-04-20"),
                )

        opt = WeeklyOptimizer()
        opt._analyze_company_reliability()

        recs = [r for r in opt._recommendations if r.category == "company"]
        assert len(recs) == 1
        assert recs[0].action == "block"
        assert recs[0].target == "BadCorp"

    def test_suggests_boost_for_good_rate(self, tmp_path):
        db_path = tmp_path / "applications.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE applications (
                    job_id TEXT, company TEXT, status TEXT, applied_at TEXT
                )
            """)
            for i in range(10):
                status = "Interview" if i < 4 else "Rejected"
                conn.execute(
                    "INSERT INTO applications VALUES (?, ?, ?, ?)",
                    (f"j{i}", "GoodCorp", status, "2026-04-20"),
                )

        opt = WeeklyOptimizer()
        opt._analyze_company_reliability()

        recs = [r for r in opt._recommendations if r.category == "company"]
        assert len(recs) == 1
        assert recs[0].action == "boost"


class TestGenerateReport:
    def test_full_report(self, tmp_path):
        opt = WeeklyOptimizer()
        report = opt.generate_report(days=7)
        assert report.generated_at
        assert report.period_start
        assert report.period_end
        assert isinstance(report.recommendations, list)
        assert isinstance(report.stats, dict)

    def test_report_markdown_nonempty(self, tmp_path):
        opt = WeeklyOptimizer()
        report = opt.generate_report(days=7)
        md = report.to_markdown()
        assert "Weekly Optimization Report" in md
