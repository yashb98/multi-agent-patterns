"""Tests for CorrectionCapture — per-field correction tracking."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from jobpulse.correction_capture import CorrectionCapture


@pytest.fixture
def db(tmp_path):
    return CorrectionCapture(db_path=str(tmp_path / "corrections.db"))


class TestRecordCorrections:
    def test_detects_changes(self, db):
        result = db.record_corrections(
            domain="greenhouse.io",
            platform="greenhouse",
            agent_mapping={"salary": "28000", "notice": "2 weeks", "location": "London"},
            final_mapping={"salary": "32000", "notice": "2 weeks", "location": "London"},
        )
        assert len(result["corrections"]) == 1
        assert result["corrections"][0]["field"] == "salary"
        assert result["corrections"][0]["agent"] == "28000"
        assert result["corrections"][0]["user"] == "32000"
        assert result["unchanged"] == 2

    def test_no_changes(self, db):
        result = db.record_corrections(
            domain="lever.co",
            platform="lever",
            agent_mapping={"salary": "28000", "notice": "Immediately"},
            final_mapping={"salary": "28000", "notice": "Immediately"},
        )
        assert result["corrections"] == []
        assert result["unchanged"] == 2

    def test_multiple_corrections(self, db):
        result = db.record_corrections(
            domain="a.com",
            platform="generic",
            agent_mapping={"salary": "28000", "notice": "2 weeks", "location": "Remote"},
            final_mapping={"salary": "32000", "notice": "Immediately", "location": "Remote"},
        )
        assert len(result["corrections"]) == 2
        assert result["unchanged"] == 1

    def test_missing_field_in_final_treated_as_unchanged(self, db):
        result = db.record_corrections(
            domain="a.com",
            platform="generic",
            agent_mapping={"salary": "28000", "extra": "value"},
            final_mapping={"salary": "32000"},
        )
        assert len(result["corrections"]) == 1
        assert result["unchanged"] == 1


class TestCorrectionRate:
    def test_rate_calculation(self, db):
        db.record_corrections("a.com", "generic",
                              {"salary": "28000"}, {"salary": "32000"})
        db.record_corrections("b.com", "generic",
                              {"salary": "28000"}, {"salary": "35000"})
        rate = db.get_correction_rate("salary", total_fills=10, min_samples=1)
        assert rate == pytest.approx(0.2)

    def test_insufficient_samples_returns_none(self, db):
        db.record_corrections("a.com", "generic",
                              {"salary": "28000"}, {"salary": "32000"})
        rate = db.get_correction_rate("salary", total_fills=3, min_samples=5)
        assert rate is None

    def test_zero_corrections(self, db):
        rate = db.get_correction_rate("notice", total_fills=10, min_samples=1)
        assert rate == pytest.approx(0.0)

    def test_label_normalization(self, db):
        db.record_corrections("a.com", "generic",
                              {"  Salary  ": "28000"}, {"  Salary  ": "32000"})
        count = db.get_correction_count("salary")
        assert count == 1


class TestHighCorrectionFields:
    def test_filters_by_threshold(self, db):
        for i in range(4):
            db.record_corrections(f"d{i}.com", "generic",
                                  {"salary": "28000"}, {"salary": "32000"})
        db.record_corrections("e.com", "generic",
                              {"notice": "2 weeks"}, {"notice": "Immediately"})

        fills = {"salary": 5, "notice": 10}
        high = db.get_high_correction_fields(fills, threshold=0.5, min_samples=1)
        labels = [h["field"] for h in high]
        assert "salary" in labels
        assert "notice" not in labels

    def test_empty_when_no_corrections(self, db):
        fills = {"salary": 10, "notice": 10}
        high = db.get_high_correction_fields(fills, threshold=0.5, min_samples=1)
        assert high == []
