"""Tests for shared/code_intel_mcp.py and shared/code_intel_cli.py."""

import pytest


class TestMCPModule:
    def test_module_imports(self):
        import shared.code_intel_mcp as mcp_mod

        assert hasattr(mcp_mod, "create_mcp_server")
        assert hasattr(mcp_mod, "TOOL_NAMES")

    def test_has_at_least_8_tools(self):
        from shared.code_intel_mcp import TOOL_NAMES

        assert len(TOOL_NAMES) >= 8

    def test_tool_names_correct(self):
        from shared.code_intel_mcp import TOOL_NAMES

        core_tools = {
            "find_symbol",
            "callers_of",
            "callees_of",
            "impact_analysis",
            "risk_report",
            "semantic_search",
            "module_summary",
            "recent_changes",
        }
        assert core_tools.issubset(set(TOOL_NAMES))

    def test_file_watcher_function_exists(self):
        from shared.code_intel_mcp import _start_file_watcher

        assert callable(_start_file_watcher)


class TestCLIFastPath:
    """Tests for graph-only fast path in code_intel_cli."""

    def test_graph_only_mode_skips_embeddings(self):
        """_get_ci(graph_only=True) should NOT load embeddings into memory."""
        from shared.code_intel_cli import _get_ci

        ci = _get_ci(graph_only=True)
        # HybridSearch stores loaded embeddings in _embedding_matrix
        # graph_only mode should leave it as None (not loaded)
        assert ci._search._embedding_matrix is None
        ci.close()

    def test_graph_only_mode_can_query_callers(self):
        """Graph-only mode should still support structural queries."""
        from shared.code_intel_cli import _get_ci

        ci = _get_ci(graph_only=True)
        # Should not error — just queries SQLite edges table
        result = ci.callers_of("get_llm")
        assert "callers" in result
        ci.close()

    def test_full_mode_loads_embeddings(self):
        """_get_ci(graph_only=False) should load embeddings (default behavior)."""
        from shared.code_intel_cli import _get_ci

        ci = _get_ci(graph_only=False)
        # Full mode should have embeddings loaded
        assert ci._search._embedding_matrix is not None
        ci.close()

    def test_graph_commands_use_fast_path(self):
        """Structural commands should be in GRAPH_ONLY_COMMANDS set."""
        from shared.code_intel_cli import GRAPH_ONLY_COMMANDS

        expected = {"find_symbol", "callers_of", "callees_of", "impact_analysis",
                    "risk_report", "module_summary", "recent_changes", "dead_code"}
        assert expected.issubset(GRAPH_ONLY_COMMANDS)
