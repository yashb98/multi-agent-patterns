"""Tests for jobpulse.skill_gap_tracker — records missing skills from pre-screened jobs."""

import csv
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def use_temp_db(monkeypatch, tmp_path):
    """Patch _DB_PATH to tmp_path so tests never touch production data/*.db."""
    db_path = tmp_path / "skill_gaps.db"
    monkeypatch.setattr("jobpulse.skill_gap_tracker._DB_PATH", db_path)
    # Re-initialize tables in the temp DB
    from jobpulse.skill_gap_tracker import _init_db
    _init_db()


def test_record_gap_creates_entries():
    """record_gap creates entries in both skill_gaps and skill_matches tables."""
    from jobpulse.skill_gap_tracker import record_gap, _get_conn

    record_gap(
        job_id="job-001",
        title="Software Engineer",
        company="Acme Corp",
        missing_skills=["rust", "kubernetes"],
        matched_skills=["python", "docker"],
        gate3_score=62.5,
    )

    conn = _get_conn()
    gaps = conn.execute("SELECT * FROM skill_gaps").fetchall()
    matches = conn.execute("SELECT * FROM skill_matches").fetchall()
    conn.close()

    assert len(gaps) == 2
    assert {r["skill"] for r in gaps} == {"rust", "kubernetes"}
    assert gaps[0]["job_title"] == "Software Engineer"
    assert gaps[0]["company"] == "Acme Corp"
    assert gaps[0]["gate3_score"] == 62.5

    assert len(matches) == 2
    assert {r["skill"] for r in matches} == {"python", "docker"}


def test_record_gap_is_idempotent():
    """Same skill+job_id pair does not duplicate — INSERT OR IGNORE."""
    from jobpulse.skill_gap_tracker import record_gap, _get_conn

    record_gap("job-001", "SE", "Acme", ["rust"], ["python"], 60.0)
    record_gap("job-001", "SE", "Acme", ["rust"], ["python"], 60.0)

    conn = _get_conn()
    gap_count = conn.execute("SELECT COUNT(*) FROM skill_gaps").fetchone()[0]
    match_count = conn.execute("SELECT COUNT(*) FROM skill_matches").fetchone()[0]
    conn.close()

    assert gap_count == 1
    assert match_count == 1


def test_record_gap_normalizes_case():
    """Skills are lowercased and stripped before storage."""
    from jobpulse.skill_gap_tracker import record_gap, _get_conn

    record_gap("job-002", "SE", "Acme", ["  Rust  ", "KUBERNETES"], [], 50.0)

    conn = _get_conn()
    skills = [r["skill"] for r in conn.execute("SELECT skill FROM skill_gaps").fetchall()]
    conn.close()

    assert set(skills) == {"rust", "kubernetes"}


def test_get_top_gaps_min_count():
    """get_top_gaps(min_count=2) only returns skills appearing in 2+ jobs."""
    from jobpulse.skill_gap_tracker import record_gap, get_top_gaps

    record_gap("job-001", "SE", "Acme", ["rust", "go"], ["python"], 60.0)
    record_gap("job-002", "SE", "Beta", ["rust", "java"], ["python"], 55.0)
    record_gap("job-003", "SE", "Gamma", ["rust"], ["python"], 70.0)

    gaps = get_top_gaps(min_count=2)
    skill_names = [g["skill"] for g in gaps]

    # rust appears in 3 jobs, go in 1, java in 1
    assert "rust" in skill_names
    assert "go" not in skill_names
    assert "java" not in skill_names
    assert gaps[0]["skill"] == "rust"
    assert gaps[0]["gap_count"] == 3


def test_get_top_gaps_have_it_flag():
    """have_it=True when a skill appears in both gaps and matches tables."""
    from jobpulse.skill_gap_tracker import record_gap, get_top_gaps

    # "python" is missing in job-001 but matched in job-002
    record_gap("job-001", "SE", "Acme", ["python", "rust"], [], 60.0)
    record_gap("job-002", "SE", "Beta", ["rust"], ["python"], 55.0)

    gaps = get_top_gaps(min_count=1)
    gap_map = {g["skill"]: g for g in gaps}

    assert gap_map["python"]["have_it"] is True
    assert gap_map["python"]["match_count"] == 1
    assert gap_map["rust"]["have_it"] is False
    assert gap_map["rust"]["match_count"] == 0


def test_get_top_gaps_sample_companies():
    """sample_companies are deduplicated and capped at 5."""
    from jobpulse.skill_gap_tracker import record_gap, get_top_gaps

    for i in range(7):
        record_gap(f"job-{i:03d}", "SE", f"Company{i}", ["rust"], [], 50.0)

    gaps = get_top_gaps(min_count=1)
    assert len(gaps[0]["sample_companies"]) == 5


def test_export_gap_report_creates_csv(tmp_path):
    """export_gap_report creates a valid CSV with correct headers."""
    from jobpulse.skill_gap_tracker import record_gap, export_gap_report

    record_gap("job-001", "SE", "Acme", ["rust", "go"], ["python"], 60.0)
    record_gap("job-002", "SE", "Beta", ["rust"], ["python"], 55.0)

    output = tmp_path / "report.csv"
    result = export_gap_report(output)

    assert result == output
    assert output.exists()

    with output.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        rows = list(reader)

    assert headers == [
        "Rank", "Skill", "Times Missing", "Times Matched", "Have It?",
        "Action", "Sample Companies", "First Seen", "Last Seen",
    ]
    # rust appears in 2 jobs (rank 1), go in 1 (rank 2)
    assert len(rows) == 2
    assert rows[0][1] == "rust"
    assert rows[0][2] == "2"  # Times Missing


def test_export_gap_report_default_path():
    """export_gap_report uses default path under DATA_DIR when no path given."""
    from jobpulse.skill_gap_tracker import record_gap, export_gap_report

    record_gap("job-001", "SE", "Acme", ["rust"], [], 60.0)

    result = export_gap_report()
    assert result.exists()
    assert "skill_gap_report_" in result.name
    assert result.suffix == ".csv"


def test_get_gap_stats_returns_correct_counts():
    """get_gap_stats returns correct unique skills, jobs, and top5."""
    from jobpulse.skill_gap_tracker import record_gap, get_gap_stats

    record_gap("job-001", "SE", "Acme", ["rust", "go", "java"], [], 60.0)
    record_gap("job-002", "SE", "Beta", ["rust", "go"], [], 55.0)
    record_gap("job-003", "SE", "Gamma", ["rust"], [], 70.0)

    stats = get_gap_stats()

    assert stats["unique_gap_skills"] == 3  # rust, go, java
    assert stats["jobs_tracked"] == 3
    assert stats["total_gap_entries"] == 6  # 3 + 2 + 1
    assert len(stats["top5_gaps"]) == 3
    assert stats["top5_gaps"][0]["skill"] == "rust"
    assert stats["top5_gaps"][0]["count"] == 3


def test_get_gap_stats_empty_db():
    """get_gap_stats returns zeros on empty database."""
    from jobpulse.skill_gap_tracker import get_gap_stats

    stats = get_gap_stats()

    assert stats["unique_gap_skills"] == 0
    assert stats["jobs_tracked"] == 0
    assert stats["total_gap_entries"] == 0
    assert stats["top5_gaps"] == []
