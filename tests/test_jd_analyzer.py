"""Tests for jd_analyzer — rule-based extraction functions only (no LLM calls).

All 7 tests cover the deterministic functions. LLM-based extract_skills_llm
and the full analyze_jd orchestrator are tested via integration tests only.
"""

from __future__ import annotations

import pytest

from jobpulse.jd_analyzer import (
    detect_ats_platform,
    detect_easy_apply,
    detect_remote,
    detect_seniority,
    extract_location,
    extract_salary,
    generate_job_id,
)


# ---------------------------------------------------------------------------
# 1. extract_salary
# ---------------------------------------------------------------------------


def test_extract_salary_range() -> None:
    """extract_salary handles common UK/US salary formats and non-numeric values."""
    assert extract_salary("£30,000 - £35,000") == (30000.0, 35000.0)
    assert extract_salary("30K-35K") == (30000.0, 35000.0)
    assert extract_salary("£28k - £32k per annum") == (28000.0, 32000.0)
    assert extract_salary("Competitive salary") == (None, None)
    assert extract_salary("$50,000-$60,000") == (50000.0, 60000.0)


# ---------------------------------------------------------------------------
# 2. extract_location
# ---------------------------------------------------------------------------


def test_extract_location() -> None:
    """extract_location finds explicit 'Location:' labels and freestanding city names."""
    assert extract_location("Location: London, UK (Hybrid)") == "London, UK"
    assert extract_location("Remote, UK") == "Remote, UK"


# ---------------------------------------------------------------------------
# 3. detect_remote
# ---------------------------------------------------------------------------


def test_detect_remote() -> None:
    """detect_remote returns True for remote/hybrid mentions, False for office-only."""
    assert detect_remote("Remote, UK") is True
    assert detect_remote("Hybrid working from London") is True
    assert detect_remote("Office-based in Manchester") is False


# ---------------------------------------------------------------------------
# 4. detect_seniority
# ---------------------------------------------------------------------------


def test_detect_seniority() -> None:
    """detect_seniority maps keywords to canonical levels; returns None when ambiguous."""
    assert detect_seniority("Junior Data Scientist") == "junior"
    assert detect_seniority("Graduate ML Engineer") == "graduate"
    assert detect_seniority("ML Engineer Intern") == "intern"
    assert detect_seniority("Data Scientist") is None


# ---------------------------------------------------------------------------
# 5. detect_ats_platform
# ---------------------------------------------------------------------------


def test_detect_ats_platform() -> None:
    """detect_ats_platform recognises greenhouse, lever, and workday from URL domains."""
    assert detect_ats_platform("https://boards.greenhouse.io/barclays/123") == "greenhouse"
    assert detect_ats_platform("https://jobs.lever.co/revolut/456") == "lever"
    assert detect_ats_platform("https://barclays.wd3.myworkdayjobs.com/en-US/jobs/1") == "workday"
    assert detect_ats_platform("https://linkedin.com/jobs/123") is None


# ---------------------------------------------------------------------------
# 6. detect_easy_apply
# ---------------------------------------------------------------------------


def test_detect_easy_apply() -> None:
    """detect_easy_apply requires both the right platform domain AND the apply keyword."""
    assert detect_easy_apply("https://linkedin.com/jobs/123", "Easy Apply available") is True
    assert detect_easy_apply("https://indeed.co.uk/jobs/456", "Quick Apply") is True
    assert detect_easy_apply("https://greenhouse.io/jobs/789", "Apply now") is False


# ---------------------------------------------------------------------------
# 7. generate_job_id
# ---------------------------------------------------------------------------


def test_generate_job_id() -> None:
    """generate_job_id is deterministic, URL-unique, and always 64 hex chars (SHA-256)."""
    id1 = generate_job_id("https://linkedin.com/jobs/123")
    id2 = generate_job_id("https://linkedin.com/jobs/123")
    id3 = generate_job_id("https://linkedin.com/jobs/456")

    assert id1 == id2          # same URL → same ID
    assert id1 != id3          # different URL → different ID
    assert len(id1) == 64      # SHA-256 hex digest is always 64 chars
    assert id1.isalnum()       # hex chars only (0-9a-f)
