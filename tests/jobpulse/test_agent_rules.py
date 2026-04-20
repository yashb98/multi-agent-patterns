"""Tests for AgentRulesDB — auto-generated avoidance rules."""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from jobpulse.agent_rules import AgentRulesDB


@pytest.fixture
def db(tmp_path):
    return AgentRulesDB(db_path=str(tmp_path / "agent_rules.db"))


class TestAutoGenerateFromBlocker:
    def test_creates_rule(self, db):
        result = db.auto_generate_from_blocker(
            category="geo-restriction",
            pattern="visa",
            count=5,
            total=20,
        )
        assert result["rule_id"] is not None
        assert result["category"] == "geo-restriction"
        assert result["pattern"] == "visa"
        assert result["action"] == "exclude_keyword"

    def test_upserts_same_pattern(self, db):
        r1 = db.auto_generate_from_blocker("geo-restriction", "visa", 3, 10)
        r2 = db.auto_generate_from_blocker("geo-restriction", "visa", 7, 20)
        assert r1["rule_id"] == r2["rule_id"]

        rules = db.get_active_rules("blocker_avoidance")
        assert len(rules) == 1
        assert rules[0]["sample_count"] == 7
        assert rules[0]["confidence"] == pytest.approx(0.35)

    def test_confidence_calculation(self, db):
        db.auto_generate_from_blocker("stack-mismatch", "java", 8, 10)
        rules = db.get_active_rules("blocker_avoidance")
        assert rules[0]["confidence"] == pytest.approx(0.8)


class TestAutoGenerateFromCorrection:
    def test_first_correction_is_override(self, db):
        result = db.auto_generate_from_correction(
            field_label="salary expectation",
            agent_value="28000",
            user_value="32000",
            domain="greenhouse.io",
            platform="greenhouse",
        )
        assert result["action"] == "override_answer"

    def test_repeated_corrections_escalate(self, db):
        for i in range(3):
            result = db.auto_generate_from_correction(
                field_label="salary expectation",
                agent_value="28000",
                user_value=str(30000 + i * 1000),
                domain="greenhouse.io",
                platform="greenhouse",
            )
        assert result["action"] == "escalate"

    def test_different_fields_independent(self, db):
        db.auto_generate_from_correction("salary", "28000", "32000", "a.com", "generic")
        db.auto_generate_from_correction("notice period", "2 weeks", "Immediately", "a.com", "generic")
        rules = db.get_active_rules()
        assert len(rules) == 2


class TestGetExcludeKeywords:
    def test_returns_active_blocker_keywords(self, db):
        db.auto_generate_from_blocker("geo-restriction", "visa", 5, 10)
        db.auto_generate_from_blocker("stack-mismatch", "java", 3, 10)
        keywords = db.get_exclude_keywords()
        assert "visa" in keywords
        assert "java" in keywords

    def test_excludes_correction_rules(self, db):
        db.auto_generate_from_correction("salary", "28000", "32000", "a.com", "generic")
        keywords = db.get_exclude_keywords()
        assert len(keywords) == 0


class TestRuleExpiry:
    def test_expired_rules_not_returned(self, db):
        import sqlite3

        db.auto_generate_from_blocker("geo-restriction", "visa", 5, 10)
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        with sqlite3.connect(db._db_path) as conn:
            conn.execute("UPDATE agent_rules SET expires_at = ?", (past,))
        assert db.get_active_rules() == []
        assert db.get_exclude_keywords() == []


class TestGetEscalationFields:
    def test_returns_escalated_fields(self, db):
        for _ in range(3):
            db.auto_generate_from_correction("salary", "28000", "32000", "a.com", "generic")
        fields = db.get_escalation_fields()
        assert "salary" in fields

    def test_override_not_in_escalation(self, db):
        db.auto_generate_from_correction("salary", "28000", "32000", "a.com", "generic")
        fields = db.get_escalation_fields()
        assert fields == []
