"""Tests for ghost job detection — 5 signal analyzers."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta


class TestFreshnessSignal:
    def test_recent_post_scores_high(self):
        from jobpulse.ghost_detector import _freshness_signal

        listing = MagicMock()
        listing.posted_at = (datetime.utcnow() - timedelta(days=2)).isoformat()
        signal = _freshness_signal(listing, "")
        assert signal.score >= 0.8
        assert signal.name == "freshness"

    def test_old_post_scores_low(self):
        from jobpulse.ghost_detector import _freshness_signal

        listing = MagicMock()
        listing.posted_at = (datetime.utcnow() - timedelta(days=60)).isoformat()
        signal = _freshness_signal(listing, "")
        assert signal.score <= 0.4

    def test_no_date_is_neutral(self):
        from jobpulse.ghost_detector import _freshness_signal

        listing = MagicMock()
        listing.posted_at = None
        signal = _freshness_signal(listing, "")
        assert signal.score == 0.5
        assert signal.confidence == "low"


class TestJdQualitySignal:
    def test_specific_jd_scores_high(self):
        from jobpulse.ghost_detector import _jd_quality_signal

        jd = (
            "We are looking for a Python developer with 3+ years experience in "
            "machine learning, NLP, and data pipelines. Must have experience with "
            "PyTorch, Docker, and AWS. Competitive salary range 45k-65k GBP."
        )
        signal = _jd_quality_signal(MagicMock(), jd)
        assert signal.score >= 0.7

    def test_vague_jd_scores_low(self):
        from jobpulse.ghost_detector import _jd_quality_signal

        jd = "Great opportunity. Apply now."
        signal = _jd_quality_signal(MagicMock(), jd)
        assert signal.score <= 0.4


class TestRepostSignal:
    def test_no_history_is_neutral(self):
        from jobpulse.ghost_detector import _repost_signal

        listing = MagicMock()
        listing.company = "NewCo"
        listing.title = "Data Analyst"
        signal = _repost_signal(listing, [])
        assert signal.score == 0.5

    def test_same_title_company_recently_is_suspicious(self):
        from jobpulse.ghost_detector import _repost_signal

        listing = MagicMock()
        listing.company = "RepeatCo"
        listing.title = "Data Analyst"
        history = [
            {"company": "RepeatCo", "title": "Data Analyst", "found_at": datetime.utcnow().isoformat()},
            {"company": "RepeatCo", "title": "Data Analyst", "found_at": (datetime.utcnow() - timedelta(days=30)).isoformat()},
        ]
        signal = _repost_signal(listing, history)
        assert signal.score <= 0.4


class TestDetectGhostJob:
    def test_returns_high_confidence_for_good_job(self):
        from jobpulse.ghost_detector import detect_ghost_job

        listing = MagicMock()
        listing.posted_at = datetime.utcnow().isoformat()
        listing.company = "Anthropic"
        listing.title = "ML Engineer"
        listing.url = "https://jobs.ashbyhq.com/anthropic/123"

        jd = (
            "Anthropic is hiring an ML Engineer. 3+ years Python, PyTorch, "
            "distributed training experience required. Salary 80-120k GBP."
        )
        result = detect_ghost_job(listing, jd)
        assert result.tier == "high_confidence"
        assert result.should_block is False

    def test_returns_suspicious_for_bad_signals(self):
        from jobpulse.ghost_detector import detect_ghost_job

        listing = MagicMock()
        listing.posted_at = (datetime.utcnow() - timedelta(days=90)).isoformat()
        listing.company = "Unknown Corp"
        listing.title = "Data Scientist"
        listing.url = "https://example.com/old-job"

        jd = "Great opportunity. Apply now."
        result = detect_ghost_job(listing, jd)
        assert result.tier in ("suspicious", "proceed_with_caution")
