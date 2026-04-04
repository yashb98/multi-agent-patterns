"""Tests for shared/code_intel_mcp.py — MCP server tool registration."""

import pytest


class TestMCPModule:
    def test_module_imports(self):
        import shared.code_intel_mcp as mcp_mod

        assert hasattr(mcp_mod, "create_mcp_server")
        assert hasattr(mcp_mod, "TOOL_NAMES")

    def test_has_8_tools(self):
        from shared.code_intel_mcp import TOOL_NAMES

        assert len(TOOL_NAMES) == 8

    def test_tool_names_correct(self):
        from shared.code_intel_mcp import TOOL_NAMES

        expected = {
            "find_symbol",
            "callers_of",
            "callees_of",
            "impact_analysis",
            "risk_report",
            "semantic_search",
            "module_summary",
            "recent_changes",
        }
        assert set(TOOL_NAMES) == expected

    def test_file_watcher_function_exists(self):
        from shared.code_intel_mcp import _start_file_watcher

        assert callable(_start_file_watcher)
