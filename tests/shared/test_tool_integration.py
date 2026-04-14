"""Tests for shared/tool_integration.py — permissions, rate limiting, audit persistence."""

import sqlite3
import time
from unittest.mock import MagicMock

import pytest

from shared.tool_integration import (
    AuditEntry,
    AuditLog,
    PermissionLevel,
    RiskLevel,
    ToolDefinition,
    ToolExecutor,
)


# ─── Fixtures ──────────────────────────────────────────────────────────


def _dummy_execute(action: str, params: dict) -> dict:
    return {"status": "ok", "data": f"ran {action}"}


def _make_tool(name: str = "test_tool", rate_limit: int = 30) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="test tool",
        category="test",
        actions={
            "read": {"risk": RiskLevel.LOW},
            "write": {"risk": RiskLevel.HIGH},
            "nuke": {"risk": RiskLevel.CRITICAL},
        },
        execute_fn=_dummy_execute,
        rate_limit_per_minute=rate_limit,
    )


def _make_executor(
    permission: PermissionLevel = PermissionLevel.READ_WRITE,
    approval_fn=None,
    rate_limit: int = 30,
    tool_name: str = "test_tool",
) -> ToolExecutor:
    tools = {tool_name: _make_tool(tool_name, rate_limit)}
    permissions = {"agent": {tool_name: permission}}
    return ToolExecutor(tools=tools, permissions=permissions, approval_fn=approval_fn)


# ─── Permission tests ─────────────────────────────────────────────────


class TestPermissions:
    def test_deny_blocks_access(self):
        executor = _make_executor(PermissionLevel.DENY)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "denied"

    def test_read_only_allows_low_risk(self):
        executor = _make_executor(PermissionLevel.READ_ONLY)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "ok"

    def test_read_only_blocks_high_risk(self):
        executor = _make_executor(PermissionLevel.READ_ONLY)
        result = executor.execute("agent", "test_tool", "write")
        assert result["status"] == "denied"
        assert "read-only" in result["message"]

    def test_read_only_blocks_critical_risk(self):
        executor = _make_executor(PermissionLevel.READ_ONLY)
        result = executor.execute("agent", "test_tool", "nuke")
        assert result["status"] == "denied"

    def test_read_write_allows_high_risk(self):
        executor = _make_executor(PermissionLevel.READ_WRITE)
        result = executor.execute("agent", "test_tool", "write")
        assert result["status"] == "ok"

    def test_requires_approval_calls_approval_fn(self):
        approval = MagicMock(return_value=True)
        executor = _make_executor(PermissionLevel.REQUIRES_APPROVAL, approval_fn=approval)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "ok"
        approval.assert_called_once_with("agent", "test_tool", "read", {})

    def test_requires_approval_denied_when_rejected(self):
        approval = MagicMock(return_value=False)
        executor = _make_executor(PermissionLevel.REQUIRES_APPROVAL, approval_fn=approval)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "denied"
        assert "rejected" in result["message"]

    def test_critical_risk_triggers_approval_even_with_read_write(self):
        approval = MagicMock(return_value=True)
        executor = _make_executor(PermissionLevel.READ_WRITE, approval_fn=approval)
        result = executor.execute("agent", "test_tool", "nuke")
        assert result["status"] == "ok"
        approval.assert_called_once()

    def test_critical_risk_denied_when_approval_rejects(self):
        approval = MagicMock(return_value=False)
        executor = _make_executor(PermissionLevel.READ_WRITE, approval_fn=approval)
        result = executor.execute("agent", "test_tool", "nuke")
        assert result["status"] == "denied"


# ─── Default approval tests ───────────────────────────────────────────


class TestDefaultApproval:
    def test_default_denies_without_env(self, monkeypatch):
        monkeypatch.delenv("TOOL_AUTO_APPROVE", raising=False)
        executor = _make_executor(PermissionLevel.REQUIRES_APPROVAL)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "denied"

    def test_default_approves_with_env(self, monkeypatch):
        monkeypatch.setenv("TOOL_AUTO_APPROVE", "1")
        executor = _make_executor(PermissionLevel.REQUIRES_APPROVAL)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "ok"

    def test_default_denies_with_wrong_env_value(self, monkeypatch):
        monkeypatch.setenv("TOOL_AUTO_APPROVE", "yes")
        executor = _make_executor(PermissionLevel.REQUIRES_APPROVAL)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "denied"

    def test_critical_action_denied_without_env(self, monkeypatch):
        monkeypatch.delenv("TOOL_AUTO_APPROVE", raising=False)
        executor = _make_executor(PermissionLevel.READ_WRITE)
        result = executor.execute("agent", "test_tool", "nuke")
        assert result["status"] == "denied"

    def test_critical_action_approved_with_env(self, monkeypatch):
        monkeypatch.setenv("TOOL_AUTO_APPROVE", "1")
        executor = _make_executor(PermissionLevel.READ_WRITE)
        result = executor.execute("agent", "test_tool", "nuke")
        assert result["status"] == "ok"


# ─── Rate limiting tests ──────────────────────────────────────────────


class TestRateLimiting:
    def test_within_limit_allowed(self):
        executor = _make_executor(rate_limit=5)
        for _ in range(5):
            result = executor.execute("agent", "test_tool", "read")
            assert result["status"] == "ok"

    def test_exceeds_limit_blocked(self):
        executor = _make_executor(rate_limit=3)
        for _ in range(3):
            executor.execute("agent", "test_tool", "read")
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "rate_limited"

    def test_window_resets_after_60s(self):
        executor = _make_executor(rate_limit=2)
        # Fill the window
        executor.execute("agent", "test_tool", "read")
        executor.execute("agent", "test_tool", "read")
        assert executor.execute("agent", "test_tool", "read")["status"] == "rate_limited"

        # Manually age the timestamps to simulate 60s passing
        rate_key = "agent:test_tool"
        executor._call_timestamps[rate_key] = [
            t - 61 for t in executor._call_timestamps[rate_key]
        ]

        # Should be allowed again
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "ok"

    def test_sliding_window_partial_expiry(self):
        executor = _make_executor(rate_limit=2)
        now = time.time()
        # One old call (expired) and one recent call
        executor._call_timestamps["agent:test_tool"] = [now - 61, now - 5]
        # After purge: 1 recent call, limit is 2, so one more should be allowed
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "ok"
        # Now at 2 recent calls — next should be blocked
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "rate_limited"


# ─── Audit persistence tests ──────────────────────────────────────────


class TestAuditPersistence:
    def test_records_survive_recreation(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        log1 = AuditLog(db_path=db_path)
        log1.record(AuditEntry(
            timestamp="12:00:00", agent_name="agent1", tool_name="tool1",
            action="read", input_summary="in", output_summary="out",
            risk_level="low", approved_by="system", success=True,
        ))
        # In-memory entries are gone after recreation
        log2 = AuditLog(db_path=db_path)
        assert len(log2.entries) == 0  # in-memory is fresh

        # But SQLite has the record
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        conn.close()
        assert rows[0] == 1

    def test_multiple_records_persisted(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        log = AuditLog(db_path=db_path)
        for i in range(5):
            log.record(AuditEntry(
                timestamp=f"12:0{i}:00", agent_name=f"agent{i}", tool_name="tool",
                action="read", input_summary="", output_summary="",
                risk_level="low", approved_by="system", success=True,
            ))
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        conn.close()
        assert rows[0] == 5

    def test_error_field_persisted(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        log = AuditLog(db_path=db_path)
        log.record(AuditEntry(
            timestamp="12:00:00", agent_name="agent", tool_name="tool",
            action="read", input_summary="", output_summary="",
            risk_level="low", approved_by="system", success=False, error="boom",
        ))
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT error, success FROM audit_log").fetchone()
        conn.close()
        assert row[0] == "boom"
        assert row[1] == 0  # False stored as 0

    def test_wal_mode_enabled(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        AuditLog(db_path=db_path)
        conn = sqlite3.connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


# ─── Audit method tests ───────────────────────────────────────────────


class TestAuditMethods:
    def _make_log(self, tmp_path) -> AuditLog:
        log = AuditLog(db_path=str(tmp_path / "audit.db"))
        entries = [
            ("agent1", "tool1", True),
            ("agent1", "tool2", False),
            ("agent2", "tool1", True),
            ("agent2", "tool2", True),
            ("agent3", "tool3", False),
        ]
        for agent, tool, success in entries:
            log.record(AuditEntry(
                timestamp="12:00:00", agent_name=agent, tool_name=tool,
                action="read", input_summary="", output_summary="",
                risk_level="low", approved_by="system", success=success,
            ))
        return log

    def test_get_recent(self, tmp_path):
        log = self._make_log(tmp_path)
        recent = log.get_recent(2)
        assert len(recent) == 2
        assert recent[-1].agent_name == "agent3"

    def test_get_by_agent(self, tmp_path):
        log = self._make_log(tmp_path)
        entries = log.get_by_agent("agent1")
        assert len(entries) == 2
        assert all(e.agent_name == "agent1" for e in entries)

    def test_get_failures(self, tmp_path):
        log = self._make_log(tmp_path)
        failures = log.get_failures()
        assert len(failures) == 2
        assert all(not e.success for e in failures)

    def test_summary(self, tmp_path):
        log = self._make_log(tmp_path)
        s = log.summary()
        assert s["total_calls"] == 5
        assert s["failures"] == 2
        assert s["success_rate"] == pytest.approx(0.6)
        assert s["unique_agents"] == 3
        assert s["unique_tools"] == 3

    def test_summary_empty(self, tmp_path):
        log = AuditLog(db_path=str(tmp_path / "audit.db"))
        s = log.summary()
        assert s["total_calls"] == 0
        assert s["success_rate"] == 1.0


# ─── Edge case tests ──────────────────────────────────────────────────


class TestEdgeCases:
    def test_unknown_tool(self):
        executor = _make_executor()
        result = executor.execute("agent", "nonexistent", "read")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_unknown_action(self):
        executor = _make_executor()
        result = executor.execute("agent", "test_tool", "nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_unknown_agent_defaults_to_deny(self):
        executor = _make_executor()
        result = executor.execute("stranger", "test_tool", "read")
        assert result["status"] == "denied"

    def test_grant_permission_new_agent(self):
        executor = _make_executor()
        executor.grant_permission("new_agent", "test_tool", PermissionLevel.READ_WRITE)
        result = executor.execute("new_agent", "test_tool", "read")
        assert result["status"] == "ok"

    def test_grant_permission_upgrades_existing(self):
        executor = _make_executor(PermissionLevel.DENY)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "denied"
        executor.grant_permission("agent", "test_tool", PermissionLevel.READ_WRITE)
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "ok"

    def test_execute_fn_exception_recorded(self):
        def explode(action, params):
            raise RuntimeError("kaboom")

        tool = _make_tool()
        tool.execute_fn = explode
        executor = ToolExecutor(
            tools={"test_tool": tool},
            permissions={"agent": {"test_tool": PermissionLevel.READ_WRITE}},
        )
        result = executor.execute("agent", "test_tool", "read")
        assert result["status"] == "error"
        assert "kaboom" in result["message"]
        assert len(executor.audit.get_failures()) == 1


# ─── record_dispatch test ─────────────────────────────────────────────


class TestRecordDispatch:
    def test_dispatch_event_recorded(self):
        executor = _make_executor()
        executor.record_dispatch(
            intent="log_spend",
            agent_name="budget_agent",
            result_summary="Logged 10 on coffee",
            success=True,
        )
        entries = executor.audit.get_by_agent("budget_agent")
        assert len(entries) == 1
        assert entries[0].tool_name == "dispatch"
        assert entries[0].action == "log_spend"
        assert entries[0].success is True

    def test_dispatch_failure_recorded(self):
        executor = _make_executor()
        executor.record_dispatch(
            intent="arxiv",
            agent_name="arxiv_agent",
            result_summary="",
            success=False,
            error="timeout",
        )
        failures = executor.audit.get_failures()
        assert len(failures) == 1
        assert failures[0].error == "timeout"
