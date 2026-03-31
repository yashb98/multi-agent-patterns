"""Tests for recruiter email extraction from job descriptions."""

from __future__ import annotations

from jobpulse.jd_analyzer import extract_recruiter_email


def test_extracts_recruiter_email():
    """Personal recruiter email is extracted from JD text."""
    jd = "Contact john.smith@google.com for details"
    assert extract_recruiter_email(jd) == "john.smith@google.com"


def test_skips_noreply():
    """noreply addresses are discarded entirely."""
    jd = "Send applications to noreply@company.com"
    assert extract_recruiter_email(jd) is None


def test_skips_info_email():
    """info@ addresses are discarded entirely."""
    jd = "For enquiries email info@company.com"
    assert extract_recruiter_email(jd) is None


def test_prefers_recruiter_over_generic():
    """Personal recruiter email is preferred over generic HR address."""
    jd = "Apply at careers@company.com or contact sarah@company.com directly"
    assert extract_recruiter_email(jd) == "sarah@company.com"


def test_returns_generic_hr_when_no_recruiter():
    """Generic HR email is returned when no personal recruiter email exists."""
    jd = "Send your CV to careers@company.com"
    assert extract_recruiter_email(jd) == "careers@company.com"


def test_no_email_returns_none():
    """Returns None when no email address is present."""
    jd = "No contact info here"
    assert extract_recruiter_email(jd) is None


def test_multiple_recruiters_returns_first():
    """When multiple recruiter emails exist, returns the first one found."""
    jd = "Reach out to john@co.com and jane@co.com for more info"
    assert extract_recruiter_email(jd) == "john@co.com"
