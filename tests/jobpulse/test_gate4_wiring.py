"""Tests proving gate4 records decisions to gate_effectiveness table."""
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpulse.gate4_quality import check_jd_quality, check_company_background


@pytest.fixture
def gate_db(tmp_path):
    """Return path to tmp applications DB."""
    return tmp_path / "applications.db"


def test_check_jd_quality_records_pass(gate_db):
    """check_jd_quality must record 'pass' decision for valid JDs."""
    from jobpulse.job_db import JobDB

    jdb = JobDB(db_path=gate_db)

    with patch("jobpulse.gate4_quality.JobDB", return_value=jdb):
        check_jd_quality(
            jd_text="A" * 300,
            extracted_skills=["Python", "SQL", "Pandas", "NumPy", "Scikit-learn"],
        )

    effectiveness = jdb.get_gate_effectiveness("jd_quality")
    assert len(effectiveness) >= 1, "check_jd_quality must record a gate decision"
    assert effectiveness[0]["decision"] == "pass"


def test_check_jd_quality_records_fail(gate_db):
    """check_jd_quality must record 'fail' decision for short JDs."""
    from jobpulse.job_db import JobDB

    jdb = JobDB(db_path=gate_db)

    with patch("jobpulse.gate4_quality.JobDB", return_value=jdb):
        check_jd_quality(jd_text="Short JD", extracted_skills=["Python"])

    effectiveness = jdb.get_gate_effectiveness("jd_quality")
    assert len(effectiveness) >= 1
    assert effectiveness[0]["decision"] == "fail"


def test_check_company_background_records_decision(gate_db):
    """check_company_background must record its decision."""
    from jobpulse.job_db import JobDB

    jdb = JobDB(db_path=gate_db)

    with patch("jobpulse.gate4_quality.JobDB", return_value=jdb):
        check_company_background("Acme Corp", [])

    effectiveness = jdb.get_gate_effectiveness("company_background")
    assert len(effectiveness) >= 1, "check_company_background must record a gate decision"
