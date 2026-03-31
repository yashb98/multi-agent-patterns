"""Tests for company blocklist — spam detection + Notion-backed blocklist cache."""

from __future__ import annotations

import pytest

from jobpulse.company_blocklist import (
    BlocklistCache,
    SpamDetectionResult,
    detect_spam_company,
)


# ---------------------------------------------------------------------------
# detect_spam_company
# ---------------------------------------------------------------------------


class TestDetectSpamCompany:
    """Spam keyword detection + high-listing-count heuristic."""

    def test_career_switch_is_spam(self):
        result = detect_spam_company("IT Career Switch")
        assert result.is_spam is True
        assert "career switch" in result.reason.lower()
        assert result.company == "IT Career Switch"

    def test_bootcamp_is_spam(self):
        result = detect_spam_company("Data Science Bootcamp Ltd")
        assert result.is_spam is True

    def test_recruitment_agency_is_spam(self):
        result = detect_spam_company("ABC Recruitment Agency")
        assert result.is_spam is True

    def test_google_is_not_spam(self):
        result = detect_spam_company("Google")
        assert result.is_spam is False

    def test_revolut_is_not_spam(self):
        result = detect_spam_company("Revolut")
        assert result.is_spam is False

    def test_high_listing_count_is_spam(self):
        result = detect_spam_company("Some Company", listing_count_7d=15)
        assert result.is_spam is True
        assert "listings" in result.reason.lower()

    def test_low_listing_count_is_not_spam(self):
        result = detect_spam_company("Some Company", listing_count_7d=3)
        assert result.is_spam is False


# ---------------------------------------------------------------------------
# BlocklistCache
# ---------------------------------------------------------------------------


class TestBlocklistCache:
    """In-memory cache backed by Notion blocklist DB."""

    def _make_cache(self, entries: dict[str, str]) -> BlocklistCache:
        cache = BlocklistCache()
        cache._entries = {k.lower(): v for k, v in entries.items()}
        return cache

    def test_blocked_company_is_blocked(self):
        cache = self._make_cache({"SpamCorp": "Blocked"})
        assert cache.is_blocked("SpamCorp") is True

    def test_approved_company_is_not_blocked(self):
        cache = self._make_cache({"Google": "Approved"})
        assert cache.is_blocked("Google") is False

    def test_pending_company_is_not_blocked(self):
        cache = self._make_cache({"NewCorp": "Pending"})
        assert cache.is_blocked("NewCorp") is False

    def test_unknown_company_is_not_blocked(self):
        cache = self._make_cache({})
        assert cache.is_blocked("NeverSeen") is False

    def test_approved_company_is_approved(self):
        cache = self._make_cache({"Google": "Approved"})
        assert cache.is_approved("Google") is True

    def test_unknown_company_is_not_approved(self):
        cache = self._make_cache({})
        assert cache.is_approved("NeverSeen") is False

    def test_is_known_true_for_any_status(self):
        cache = self._make_cache({"PendingCo": "Pending"})
        assert cache.is_known("PendingCo") is True

    def test_is_known_false_for_unknown(self):
        cache = self._make_cache({})
        assert cache.is_known("NeverSeen") is False
