"""Tests for pattern auto-router — selects best LangGraph pattern for research queries."""
from unittest.mock import patch, MagicMock
from jobpulse.command_router import Intent, ParsedCommand


class TestOverrideSyntax:
    """Test explicit override prefix parsing."""

    def test_debate_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("debate: React vs Vue")
        assert pattern == "peer_debate"
        assert query == "React vs Vue"

    def test_swarm_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("swarm: quantum computing")
        assert pattern == "enhanced_swarm"
        assert query == "quantum computing"

    def test_deep_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("deep: transformer architecture")
        assert pattern == "hierarchical"
        assert query == "transformer architecture"

    def test_plan_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("plan: research VDBs then benchmark")
        assert pattern == "plan_and_execute"
        assert query == "research VDBs then benchmark"

    def test_batch_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("batch: summarize all papers")
        assert pattern == "map_reduce"
        assert query == "summarize all papers"

    def test_dynamic_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("dynamic: analyze Postgres, MongoDB, Redis")
        assert pattern == "dynamic_swarm"
        assert query == "analyze Postgres, MongoDB, Redis"

    def test_no_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("what is quantum computing")
        assert pattern is None
        assert query == "what is quantum computing"


class TestRuleBasedTier:
    """Test rule-based pattern selection signals."""

    def test_comparative_routes_to_debate(self):
        from jobpulse.pattern_router import select_pattern
        pattern, reason = select_pattern("React vs Vue for dashboards")
        assert pattern == "peer_debate"
        assert "comparative" in reason.lower() or "vs" in reason.lower()

    def test_compare_keyword_routes_to_debate(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("compare React and Vue")
        assert pattern == "peer_debate"

    def test_opinion_routes_to_debate(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("should I learn Rust or Go?")
        assert pattern == "peer_debate"

    def test_structured_routes_to_hierarchical(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("break down transformer architecture")
        assert pattern == "hierarchical"

    def test_report_routes_to_hierarchical(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("explain in depth how GPT works")
        assert pattern == "hierarchical"

    def test_multi_entity_routes_to_dynamic(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("analyze Postgres, MongoDB, and Redis for caching")
        assert pattern == "dynamic_swarm"

    def test_default_routes_to_enhanced_swarm(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("quantum ML advances")
        assert pattern == "enhanced_swarm"

    def test_override_wins(self):
        from jobpulse.pattern_router import select_pattern
        pattern, reason = select_pattern("swarm: compare React vs Vue")
        assert pattern == "enhanced_swarm"
        assert "override" in reason.lower()


class TestIsResearchQuery:
    """Test research query detection."""

    def test_arxiv_intent_is_research(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.ARXIV, args="papers", raw="papers")
        assert is_research_query(cmd) is True

    def test_research_intent_is_research(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.RESEARCH, args="quantum computing", raw="research quantum computing")
        assert is_research_query(cmd) is True

    def test_budget_intent_is_not_research(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.LOG_SPEND, args="5 on coffee", raw="spent 5 on coffee")
        assert is_research_query(cmd) is False

    def test_conversation_with_research_signals(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.CONVERSATION, args="", raw="compare React vs Vue for enterprise dashboards")
        assert is_research_query(cmd) is True

    def test_conversation_without_research_signals(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.CONVERSATION, args="", raw="hello how are you")
        assert is_research_query(cmd) is False

    def test_jobs_intent_is_not_research(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.SCAN_JOBS, args="", raw="scan jobs")
        assert is_research_query(cmd) is False


class TestResponseHeader:
    """Test pattern response header formatting."""

    def test_header_format(self):
        from jobpulse.pattern_router import format_response_header
        header = format_response_header("peer_debate", 3, 8.4)
        assert "[Peer Debate]" in header
        assert "3 rounds" in header
        assert "8.4" in header
        assert "Override:" in header

    def test_header_with_swarm(self):
        from jobpulse.pattern_router import format_response_header
        header = format_response_header("enhanced_swarm", 1, 7.5)
        assert "[Enhanced Swarm]" in header


class TestNewPatternRouting:
    """Test routing and execution of plan-and-execute and map-reduce patterns."""

    def test_multi_step_routes_to_plan_and_execute(self):
        from jobpulse.pattern_router import select_pattern
        pattern, reason = select_pattern("first research quantum computing then summarize findings")
        assert pattern == "plan_and_execute"

    def test_batch_routes_to_map_reduce(self):
        from jobpulse.pattern_router import select_pattern
        pattern, reason = select_pattern("summarize all 10 papers from this week")
        assert pattern == "map_reduce"

    def test_plan_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("plan: analyze and recommend a database")
        assert pattern == "plan_and_execute"
        assert query == "analyze and recommend a database"

    def test_batch_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("batch: process all applications")
        assert pattern == "map_reduce"
        assert query == "process all applications"

    def test_run_with_plan_and_execute(self, monkeypatch):
        from jobpulse.pattern_router import run_with_pattern

        monkeypatch.setattr(
            "patterns.plan_and_execute.run_plan_execute",
            lambda topic: {"final_output": "plan result"},
        )
        result = run_with_pattern("plan_and_execute", "test query")
        assert "plan result" in result

    def test_run_with_map_reduce(self, monkeypatch):
        from jobpulse.pattern_router import run_with_pattern

        monkeypatch.setattr(
            "patterns.map_reduce.run_map_reduce",
            lambda topic: {"final_output": "map result"},
        )
        result = run_with_pattern("map_reduce", "test query")
        assert "map result" in result
