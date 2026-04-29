"""Tests for the AI Assist Logger — learning from AI assistant interventions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jobpulse.ai_assist_logger import (
    AIAssistFix,
    AIAssistLogger,
    AIAssistSession,
    AIAssistStrategy,
    VALID_AGENTS,
    VALID_FIX_CATEGORIES,
    VALID_STRATEGY_TYPES,
)


@pytest.fixture
def tmp_logger(tmp_path: Path) -> AIAssistLogger:
    db_path = str(tmp_path / "ai_assist_test.db")
    return AIAssistLogger(db_path=db_path)


class TestSessionLifecycle:
    def test_start_session(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session(
            agent_name="kimi",
            job_id="job_123",
            domain="greenhouse.io",
            platform="greenhouse",
            original_mapping={"Name": "Alice"},
        )
        assert session.agent_name == "kimi"
        assert session.job_id == "job_123"
        assert session.domain == "greenhouse.io"
        assert session.original_mapping == {"Name": "Alice"}
        assert session.session_id.startswith("ai_kimi_")

    def test_invalid_agent_name_raises(self, tmp_logger: AIAssistLogger) -> None:
        with pytest.raises(ValueError, match="Invalid agent_name"):
            tmp_logger.start_session(agent_name="invalid_bot")

    def test_get_session(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("claude", domain="lever.co", platform="lever")
        retrieved = tmp_logger.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved["agent_name"] == "claude"
        assert retrieved["domain"] == "lever.co"

    def test_finalize_session(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("kimi", domain="workday.com", platform="workday")
        result = tmp_logger.finalize_session(
            session.session_id,
            final_mapping={"Name": "Alice", "Email": "alice@example.com"},
            summary="Fixed email and salary fields",
            success=True,
            push_to_learning=False,
        )
        assert result["session_id"] == session.session_id
        assert result["fixes_pushed"] == 0  # push_to_learning=False

        retrieved = tmp_logger.get_session(session.session_id)
        assert retrieved["success"] == 1
        assert retrieved["summary"] == "Fixed email and salary fields"


class TestFixRecording:
    def test_record_fix(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("kimi", domain="greenhouse.io", platform="greenhouse")
        fix = tmp_logger.record_fix(
            session_id=session.session_id,
            field_label="Salary Expectation",
            old_value="",
            new_value="80000",
            reasoning="JD stated £70-85k; midpoint is optimal",
            confidence=0.95,
        )
        assert fix.field_label == "Salary Expectation"
        assert fix.new_value == "80000"
        assert fix.reasoning == "JD stated £70-85k; midpoint is optimal"
        assert fix.confidence == 0.95

    def test_invalid_fix_category_raises(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("kimi", domain="greenhouse.io", platform="greenhouse")
        with pytest.raises(ValueError, match="Invalid fix_category"):
            tmp_logger.record_fix(
                session_id=session.session_id,
                field_label="X",
                old_value="",
                new_value="Y",
                fix_category="invalid_category",
            )

    def test_get_fixes(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("claude", domain="lever.co", platform="lever")
        tmp_logger.record_fix(session.session_id, "Name", "Bob", "Robert", reasoning="Full name preferred")
        tmp_logger.record_fix(session.session_id, "Email", "", "bob@example.com")
        fixes = tmp_logger.get_fixes(session.session_id)
        assert len(fixes) == 2
        assert fixes[0]["field_label"] == "Name"
        assert fixes[1]["field_label"] == "Email"

    def test_capture_page_delta(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("kimi", domain="greenhouse.io", platform="greenhouse")
        original = {"Name": "Alice", "Email": "", "Phone": "123"}
        current = {"Name": "Alice", "Email": "alice@example.com", "Phone": "456"}
        fixes = tmp_logger.capture_page_delta(
            session.session_id, original, current, auto_reasoning="Auto-detected delta"
        )
        assert len(fixes) == 2
        labels = {f.field_label for f in fixes}
        assert labels == {"Email", "Phone"}


class TestStrategyRecording:
    def test_record_strategy(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("kimi", domain="greenhouse.io", platform="greenhouse")
        strategy = tmp_logger.record_strategy(
            session_id=session.session_id,
            domain="greenhouse.io",
            strategy_type="fill_technique",
            description="Click label before input",
            selector_pattern='[data-qa="salary"]',
            old_solution="Direct fill fails",
            new_solution="Click label first, then type",
            applicability_pattern="greenhouse.io/*",
        )
        assert strategy.strategy_type == "fill_technique"
        assert strategy.domain == "greenhouse.io"

    def test_invalid_strategy_type_raises(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("kimi", domain="greenhouse.io", platform="greenhouse")
        with pytest.raises(ValueError, match="Invalid strategy_type"):
            tmp_logger.record_strategy(
                session_id=session.session_id,
                domain="greenhouse.io",
                strategy_type="invalid_type",
                description="Should fail",
            )

    def test_get_strategies_for_domain(self, tmp_logger: AIAssistLogger) -> None:
        session = tmp_logger.start_session("claude", domain="workday.com", platform="workday")
        tmp_logger.record_strategy(
            session_id=session.session_id,
            domain="workday.com",
            strategy_type="navigation_sequence",
            description="Click Next twice",
        )
        tmp_logger.record_strategy(
            session_id=session.session_id,
            domain="workday.com",
            strategy_type="platform_quirk",
            description="Shadow DOM requires CDP",
        )
        strategies = tmp_logger.get_strategies_for_domain("workday.com")
        assert len(strategies) == 2

        filtered = tmp_logger.get_strategies_for_domain("workday.com", strategy_type="platform_quirk")
        assert len(filtered) == 1
        assert filtered[0]["strategy_type"] == "platform_quirk"


class TestQueries:
    def test_get_fixes_for_field(self, tmp_logger: AIAssistLogger) -> None:
        s1 = tmp_logger.start_session("kimi", domain="greenhouse.io", platform="greenhouse")
        tmp_logger.record_fix(s1.session_id, "Salary Expectation", "", "70000", reasoning="Low end")
        tmp_logger.record_fix(s1.session_id, "Salary Expectation", "", "75000", reasoning="Midpoint")

        s2 = tmp_logger.start_session("claude", domain="greenhouse.io", platform="greenhouse")
        tmp_logger.record_fix(s2.session_id, "Salary Expectation", "", "80000", reasoning="High end")

        fixes = tmp_logger.get_fixes_for_field("Salary Expectation")
        assert len(fixes) == 3

        # Domain filter
        fixes_domain = tmp_logger.get_fixes_for_field("Salary Expectation", domain="greenhouse.io")
        assert len(fixes_domain) == 3

        fixes_none = tmp_logger.get_fixes_for_field("Salary Expectation", domain="lever.co")
        assert len(fixes_none) == 0

    def test_get_summary(self, tmp_logger: AIAssistLogger) -> None:
        s1 = tmp_logger.start_session("kimi", domain="a.com", platform="generic")
        tmp_logger.record_fix(s1.session_id, "X", "1", "2")
        tmp_logger.record_strategy(s1.session_id, "a.com", "platform_quirk", "Quirk")

        s2 = tmp_logger.start_session("claude", domain="b.com", platform="generic")
        tmp_logger.record_fix(s2.session_id, "Y", "3", "4")

        summary = tmp_logger.get_summary(days=1)
        assert summary["sessions"] == 2
        assert summary["fixes"] == 2
        assert summary["strategies"] == 1

        kimi_only = tmp_logger.get_summary(agent_name="kimi", days=1)
        assert kimi_only["sessions"] == 1
        assert kimi_only["fixes"] == 1
        assert kimi_only["strategies"] == 1


class TestLearningPipelineIntegration:
    def test_finalize_with_push_to_learning(self, tmp_logger: AIAssistLogger, tmp_path: Path, monkeypatch) -> None:
        """Test that finalize_session pushes fixes to CorrectionCapture and GotchasDB."""
        from jobpulse.correction_capture import CorrectionCapture
        from jobpulse.form_engine.gotchas import GotchasDB

        corrections_db = str(tmp_path / "corrections.db")
        gotchas_db = str(tmp_path / "gotchas.db")

        # Monkeypatch module-level defaults before instantiating
        import jobpulse.correction_capture as _cc_mod
        import jobpulse.form_engine.gotchas as _got_mod

        monkeypatch.setattr(_cc_mod, "_DEFAULT_DB", corrections_db)
        monkeypatch.setattr(_got_mod, "_DEFAULT_DB_PATH", gotchas_db)

        session = tmp_logger.start_session(
            "kimi",
            domain="greenhouse.io",
            platform="greenhouse",
            original_mapping={"Name": "Alice", "Email": ""},
        )
        tmp_logger.record_fix(session.session_id, "Email", "", "alice@example.com", reasoning="Required field")
        tmp_logger.record_strategy(
            session_id=session.session_id,
            domain="greenhouse.io",
            strategy_type="fill_technique",
            description="Click before typing",
            selector_pattern='[data-qa="email"]',
            new_solution="Click label first",
        )

        result = tmp_logger.finalize_session(
            session.session_id,
            final_mapping={"Name": "Alice", "Email": "alice@example.com"},
            push_to_learning=True,
        )

        assert result["fixes_pushed"] == 1
        assert result["strategies_pushed"] == 1
        assert result["corrections_stored"] == 1
        assert result["gotchas_stored"] == 1
        assert result["signals_emitted"] == 2  # 1 fix + 1 strategy

        # Verify corrections DB got the entry
        with sqlite3.connect(corrections_db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM field_corrections WHERE field_label = ?", ("email",)).fetchone()
        assert row is not None
        assert row["agent_value"] == ""
        assert row["user_value"] == "alice@example.com"

        # Verify gotchas DB got the entry
        with sqlite3.connect(gotchas_db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? AND selector_pattern = ?",
                ("greenhouse.io", '[data-qa="email"]'),
            ).fetchone()
        assert row is not None
        assert row["solution"] == "Click label first"
        assert row["engine"] == "ai_kimi"


class TestDataclassValidation:
    def test_ai_assist_session_defaults(self) -> None:
        s = AIAssistSession(
            session_id="test",
            agent_name="kimi",
            job_id="",
            domain="example.com",
            platform="generic",
        )
        assert s.started_at != ""
        assert s.original_mapping == {}

    def test_ai_assist_fix_post_init(self) -> None:
        f = AIAssistFix(
            session_id="s1",
            field_label="Name",
            old_value="A",
            new_value="B",
        )
        assert f.created_at != ""
        assert f.fix_category == "value_correction"

    def test_ai_assist_strategy_post_init(self) -> None:
        s = AIAssistStrategy(
            session_id="s1",
            domain="example.com",
            strategy_type="platform_quirk",
            description="Test",
        )
        assert s.created_at != ""


class TestConstants:
    def test_valid_agents(self) -> None:
        assert "kimi" in VALID_AGENTS
        assert "claude" in VALID_AGENTS
        assert "codex" in VALID_AGENTS

    def test_valid_fix_categories(self) -> None:
        assert "value_correction" in VALID_FIX_CATEGORIES
        assert "gotcha" in VALID_FIX_CATEGORIES

    def test_valid_strategy_types(self) -> None:
        assert "fill_technique" in VALID_STRATEGY_TYPES
        assert "platform_quirk" in VALID_STRATEGY_TYPES
