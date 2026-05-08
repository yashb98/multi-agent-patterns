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


