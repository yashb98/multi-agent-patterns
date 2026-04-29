"""Tests for AutoRuleGenerator — correction→rule and trajectory→rule pipelines."""

from __future__ import annotations

import sqlite3

import pytest

from jobpulse.auto_rule_generator import AutoRuleGenerator, GeneratedRule


class TestTrivialDiff:
    def test_same_value(self):
        assert AutoRuleGenerator._is_trivial_diff("Yes", "yes") is True

    def test_whitespace_diff(self):
        assert AutoRuleGenerator._is_trivial_diff("  London  ", "London") is True

    def test_punctuation_diff(self):
        assert AutoRuleGenerator._is_trivial_diff("£45,000", "£45000") is True

    def test_meaningful_diff(self):
        assert AutoRuleGenerator._is_trivial_diff("Yes", "No") is False


class TestFieldToPattern:
    def test_basic_words(self):
        p = AutoRuleGenerator._field_to_pattern("What is your salary?")
        assert "salary" in p

    def test_short_words_ignored(self):
        p = AutoRuleGenerator._field_to_pattern("Do you have the right to work?")
        assert "right" in p
        assert "work" in p


class TestInferAction:
    def test_low_count_override(self):
        action, value = AutoRuleGenerator._infer_action("salary", "40k", "45000", 2)
        assert action == "override_answer"
        assert value == "45000"

    def test_high_count_escalate(self):
        action, value = AutoRuleGenerator._infer_action("salary", "40k", "45000", 5)
        assert action == "escalate"

    def test_long_text_template(self):
        action, value = AutoRuleGenerator._infer_action(
            "cover letter", "old", "a" * 150, 2,
        )
        assert action == "use_template"


class TestFromCorrections:
    def test_generates_rule_for_repeated_correction(self, tmp_path):
        corrections_db = str(tmp_path / "corrections.db")
        # Seed with corrections
        with sqlite3.connect(corrections_db) as conn:
            conn.execute("""
                CREATE TABLE field_corrections (
                    id INTEGER PRIMARY KEY,
                    domain TEXT, platform TEXT,
                    field_label TEXT, agent_value TEXT, user_value TEXT,
                    created_at TEXT
                )
            """)
            for _ in range(4):
                conn.execute(
                    """INSERT INTO field_corrections
                       (domain, platform, field_label, agent_value, user_value, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("greenhouse", "gh", "salary", "40000", "45000", "2026-01-01T00:00:00"),
                )

        gen = AutoRuleGenerator(corrections_db=corrections_db)
        rules = gen.from_corrections(min_samples=3)
        assert len(rules) == 1
        assert rules[0].category == "salary"
        assert rules[0].action == "override_answer"
        assert rules[0].value == "45000"
        assert rules[0].sample_count == 4

    def test_skips_trivial_diffs(self, tmp_path):
        corrections_db = str(tmp_path / "corrections.db")
        with sqlite3.connect(corrections_db) as conn:
            conn.execute("""
                CREATE TABLE field_corrections (
                    id INTEGER PRIMARY KEY,
                    domain TEXT, platform TEXT,
                    field_label TEXT, agent_value TEXT, user_value TEXT,
                    created_at TEXT
                )
            """)
            for _ in range(5):
                conn.execute(
                    """INSERT INTO field_corrections
                       (domain, platform, field_label, agent_value, user_value, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("greenhouse", "gh", "salary", "Yes", "yes", "2026-01-01T00:00:00"),
                )

        gen = AutoRuleGenerator(corrections_db=corrections_db)
        rules = gen.from_corrections(min_samples=3)
        assert len(rules) == 0

    def test_respects_max_rules(self, tmp_path):
        corrections_db = str(tmp_path / "corrections.db")
        with sqlite3.connect(corrections_db) as conn:
            conn.execute("""
                CREATE TABLE field_corrections (
                    id INTEGER PRIMARY KEY,
                    domain TEXT, platform TEXT,
                    field_label TEXT, agent_value TEXT, user_value TEXT,
                    created_at TEXT
                )
            """)
            for i in range(5):
                for _ in range(3):
                    conn.execute(
                        """INSERT INTO field_corrections
                           (domain, platform, field_label, agent_value, user_value, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        ("greenhouse", "gh", f"field_{i}", "bad", "good", "2026-01-01T00:00:00"),
                    )

        gen = AutoRuleGenerator(corrections_db=corrections_db)
        rules = gen.from_corrections(min_samples=3, max_rules=2)
        assert len(rules) == 2


class TestValidateRule:
    def test_valid_rule_passes(self):
        gen = AutoRuleGenerator()
        rule = GeneratedRule(
            rule_type="correction_override",
            source="test",
            category="salary",
            pattern="salary",
            action="override_answer",
            value="45000",
            confidence=0.8,
            sample_count=5,
            evidence="test",
        )
        assert gen.validate_rule(rule) is True

    def test_low_confidence_fails(self):
        gen = AutoRuleGenerator()
        rule = GeneratedRule(
            rule_type="correction_override",
            source="test",
            category="salary",
            pattern="salary",
            action="override_answer",
            value="45000",
            confidence=0.3,
            sample_count=5,
            evidence="test",
        )
        assert gen.validate_rule(rule) is False

    def test_empty_value_fails(self):
        gen = AutoRuleGenerator()
        rule = GeneratedRule(
            rule_type="correction_override",
            source="test",
            category="salary",
            pattern="salary",
            action="override_answer",
            value="",
            confidence=0.8,
            sample_count=5,
            evidence="test",
        )
        assert gen.validate_rule(rule) is False

    def test_test_cases_filter_non_matching(self):
        gen = AutoRuleGenerator()
        rule = GeneratedRule(
            rule_type="correction_override",
            source="test",
            category="salary",
            pattern="salary.*expectation",
            action="override_answer",
            value="45000",
            confidence=0.8,
            sample_count=5,
            evidence="test",
        )
        test_cases = [{"question": "What is your notice period?"}]
        assert gen.validate_rule(rule, test_cases) is False


class TestDeployRule:
    def test_deploys_and_upserts(self, tmp_path):
        rules_db = str(tmp_path / "agent_rules.db")
        gen = AutoRuleGenerator()
        # Patch the DB path
        from jobpulse import agent_rules as _ar
        orig_default = _ar._DEFAULT_DB
        _ar._DEFAULT_DB = rules_db
        try:
            rule = GeneratedRule(
                rule_type="correction_override",
                source="auto_rule_generator",
                category="salary",
                pattern="salary",
                action="override_answer",
                value="45000",
                confidence=0.8,
                sample_count=5,
                evidence="test",
            )
            result = gen.deploy_rule(rule)
            assert result["deployed"] is True
            assert result["rule_id"] is not None

            # Upsert should return same rule_id
            result2 = gen.deploy_rule(rule)
            assert result2["rule_id"] == result["rule_id"]
        finally:
            _ar._DEFAULT_DB = orig_default


class TestDeployBatch:
    def test_mixed_valid_and_invalid(self, tmp_path):
        rules_db = str(tmp_path / "agent_rules.db")
        from jobpulse import agent_rules as _ar
        orig_default = _ar._DEFAULT_DB
        _ar._DEFAULT_DB = rules_db
        try:
            gen = AutoRuleGenerator()
            rules = [
                GeneratedRule(
                    rule_type="correction_override",
                    source="auto_rule_generator",
                    category="salary",
                    pattern="salary",
                    action="override_answer",
                    value="45000",
                    confidence=0.8,
                    sample_count=5,
                    evidence="test",
                ),
                GeneratedRule(
                    rule_type="correction_override",
                    source="auto_rule_generator",
                    category="bad",
                    pattern="bad",
                    action="override_answer",
                    value="",
                    confidence=0.8,
                    sample_count=5,
                    evidence="test",
                ),
            ]
            results = gen.deploy_batch(rules)
            assert results[0]["deployed"] is True
            assert results[1]["deployed"] is False
        finally:
            _ar._DEFAULT_DB = orig_default
