"""Tests for FieldAuditDB — per-field fill audit logging."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from jobpulse.field_audit import FieldAuditDB


@pytest.fixture
def db(tmp_path):
    return FieldAuditDB(db_path=str(tmp_path / "field_audit.db"))


class TestRecordAndRetrieve:
    def test_record_fill(self, db):
        db.record_fill(
            application_url="https://boards.greenhouse.io/acme/123",
            domain="boards.greenhouse.io",
            platform="greenhouse",
            field_label="Expected Salary",
            value="32000",
            method="pattern",
            tier=1,
            confidence=1.0,
        )
        audit = db.get_application_audit("https://boards.greenhouse.io/acme/123")
        assert len(audit) == 1
        assert audit[0]["field_label"] == "expected salary"
        assert audit[0]["method"] == "pattern"
        assert audit[0]["tier"] == 1
        assert audit[0]["confidence"] == pytest.approx(1.0)

    def test_multiple_fields(self, db):
        url = "https://lever.co/acme/456"
        db.record_fill(url, "lever.co", "lever", "salary", "32000", "pattern", 1, 1.0)
        db.record_fill(url, "lever.co", "lever", "notice", "Immediately", "llm", 4, 0.7, model="gpt-4o")
        audit = db.get_application_audit(url)
        assert len(audit) == 2

    def test_domain_normalization(self, db):
        db.record_fill("https://www.example.com/job", "", "generic",
                       "salary", "30000", "pattern", 1, 1.0)
        audit = db.get_application_audit("https://www.example.com/job")
        assert audit[0]["domain"] == "example.com"


class TestFieldStats:
    def test_method_distribution(self, db):
        for i in range(3):
            db.record_fill(f"https://a.com/{i}", "a.com", "generic",
                           "salary", "28000", "pattern", 1, 1.0)
        for i in range(2):
            db.record_fill(f"https://b.com/{i}", "b.com", "generic",
                           "salary", "30000", "llm", 4, 0.7)

        stats = db.get_field_stats("salary")
        assert stats["total"] == 5
        assert stats["by_method"]["pattern"] == 3
        assert stats["by_method"]["llm"] == 2
        assert stats["avg_confidence"] == pytest.approx(
            (1.0 * 3 + 0.7 * 2) / 5
        )

    def test_empty_stats(self, db):
        stats = db.get_field_stats("nonexistent")
        assert stats["total"] == 0
        assert stats["by_method"] == {}

    def test_fill_count(self, db):
        db.record_fill("https://a.com/1", "a.com", "generic",
                       "notice", "Immediately", "pattern", 1, 1.0)
        db.record_fill("https://a.com/2", "a.com", "generic",
                       "notice", "2 weeks", "llm", 4, 0.7)
        assert db.get_field_fill_count("notice") == 2
        assert db.get_field_fill_count("salary") == 0


class TestMethodDistribution:
    def test_aggregate(self, db):
        db.record_fill("https://a.com/1", "a.com", "generic",
                       "salary", "28000", "pattern", 1, 1.0)
        db.record_fill("https://a.com/2", "a.com", "generic",
                       "notice", "Immediately", "llm", 4, 0.7)
        db.record_fill("https://a.com/3", "a.com", "generic",
                       "location", "London", "pattern", 1, 1.0)

        dist = db.get_method_distribution(days=30)
        assert dist["pattern"] == 2
        assert dist["llm"] == 1


class TestAllFieldFillCounts:
    def test_returns_all_fields(self, db):
        db.record_fill("https://a.com/1", "a.com", "generic",
                       "salary", "28000", "pattern", 1, 1.0)
        db.record_fill("https://a.com/2", "a.com", "generic",
                       "salary", "30000", "llm", 4, 0.7)
        db.record_fill("https://a.com/3", "a.com", "generic",
                       "notice", "Immediately", "pattern", 1, 1.0)

        counts = db.get_all_field_fill_counts()
        assert counts["salary"] == 2
        assert counts["notice"] == 1
