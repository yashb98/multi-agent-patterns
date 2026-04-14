"""Tests for swarm_dispatcher.py — task analysis, scoring, GRPO, experience storage."""

import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from jobpulse.command_router import Intent, ParsedCommand


# ── analyze_task tests ──

class TestAnalyzeTask:
    def _make_cmd(self, intent: Intent, raw: str = "test") -> ParsedCommand:
        return ParsedCommand(intent=intent, args=raw, raw=raw)

    def _analyze(self, intent: Intent, raw: str = "test") -> list:
        from jobpulse.swarm_dispatcher import analyze_task
        trail = MagicMock()
        return analyze_task(self._make_cmd(intent, raw), trail)

    def test_simple_intent_returns_single_task(self):
        for intent in [Intent.SHOW_TASKS, Intent.CREATE_TASKS, Intent.COMPLETE_TASK,
                        Intent.HELP, Intent.CREATE_EVENT, Intent.SHOW_BUDGET]:
            tasks = self._analyze(intent)
            assert len(tasks) == 1
            assert tasks[0]["priority"] == 1
            assert tasks[0]["agent"] == intent.value

    def test_budget_intents_have_grpo_flag(self):
        for intent in [Intent.LOG_SPEND, Intent.LOG_INCOME, Intent.LOG_SAVINGS, Intent.SET_BUDGET]:
            tasks = self._analyze(intent)
            assert len(tasks) == 1
            assert tasks[0].get("grpo") is True

    def test_gmail_decomposes_into_two_tasks(self):
        tasks = self._analyze(Intent.GMAIL)
        assert len(tasks) == 2
        agents = [t["agent"] for t in tasks]
        assert "gmail" in agents
        assert "cross_reference" in agents

    def test_briefing_decomposes_into_six_tasks(self):
        tasks = self._analyze(Intent.BRIEFING)
        assert len(tasks) == 6
        agents = [t["agent"] for t in tasks]
        assert "gmail_collect" in agents
        assert "calendar_collect" in agents
        assert "tasks_collect" in agents
        assert "github_collect" in agents
        assert "budget_collect" in agents
        assert "synthesize_briefing" in agents

    def test_briefing_has_priority_ordering(self):
        tasks = self._analyze(Intent.BRIEFING)
        collect_tasks = [t for t in tasks if t["priority"] == 1]
        synth_tasks = [t for t in tasks if t["priority"] == 2]
        assert len(collect_tasks) == 5
        assert len(synth_tasks) == 1

    def test_briefing_synth_has_rlm_flag(self):
        tasks = self._analyze(Intent.BRIEFING)
        synth = [t for t in tasks if t["agent"] == "synthesize_briefing"][0]
        assert synth.get("rlm") is True
        assert synth.get("grpo") is True

    def test_github_single_task(self):
        tasks = self._analyze(Intent.GITHUB)
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "github"

    def test_trending_single_task(self):
        tasks = self._analyze(Intent.TRENDING)
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "trending"

    def test_calendar_single_task(self):
        tasks = self._analyze(Intent.CALENDAR)
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "calendar"

    def test_arxiv_routes_to_pattern_router(self):
        tasks = self._analyze(Intent.ARXIV)
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "pattern_router"

    def test_unknown_intent_returns_task(self):
        tasks = self._analyze(Intent.UNKNOWN)
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "unknown"


# ── _score_result tests ──

class TestScoreResult:
    def test_empty_result_scores_zero(self):
        from jobpulse.swarm_dispatcher import _score_result
        assert _score_result("") == 0.0
        assert _score_result(None) == 0.0

    def test_short_result_low_score(self):
        from jobpulse.swarm_dispatcher import _score_result
        score = _score_result("ok")
        assert score > 0.0
        assert score < 1.0

    def test_longer_result_higher_score(self):
        from jobpulse.swarm_dispatcher import _score_result
        short_score = _score_result("A short reply")
        long_score = _score_result("A " * 300)
        assert long_score > short_score

    def test_error_penalized(self):
        from jobpulse.swarm_dispatcher import _score_result
        normal = _score_result("Everything looks good, here are your tasks")
        with_error = _score_result("Error: something failed to load properly")
        assert with_error < normal

    def test_emoji_structure_bonus(self):
        from jobpulse.swarm_dispatcher import _score_result
        plain = _score_result("Here are your 3 emails from today")
        with_emoji = _score_result("Here are your 3 emails from today " + "📧")
        assert with_emoji > plain

    def test_score_capped_length(self):
        from jobpulse.swarm_dispatcher import _score_result
        # Length component caps at 3.0 (1500 chars)
        huge = _score_result("x" * 5000)
        assert huge <= 4.0  # 3.0 (length cap) + 1.0 (emoji) max


# ── grpo_sample tests ──

class TestGrpoSample:
    def test_single_candidate_returns_directly(self):
        from jobpulse.swarm_dispatcher import grpo_sample
        fn = MagicMock(return_value="result")
        result = grpo_sample(fn, (), n_candidates=1)
        fn.assert_called_once()
        assert result == "result"

    def test_picks_best_candidate(self):
        from jobpulse.swarm_dispatcher import grpo_sample
        call_count = 0

        def varying_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "short"
            elif call_count == 2:
                return "a much longer and more detailed response with useful content"
            else:
                return "medium length response"

        result = grpo_sample(varying_fn, (), n_candidates=3)
        # Should pick the longest (highest default score)
        assert "longer" in result

    def test_error_candidate_penalized(self):
        from jobpulse.swarm_dispatcher import grpo_sample
        call_count = 0

        def fn_with_error():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "Error: something went wrong"
            else:
                return "Good result with enough content to outscore the penalized error candidate"

        result = grpo_sample(fn_with_error, (), n_candidates=2)
        assert "Good result" in result

    def test_exception_candidate_gets_negative_score(self):
        from jobpulse.swarm_dispatcher import grpo_sample
        call_count = 0

        def fn_sometimes_raises():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            return "success"

        result = grpo_sample(fn_sometimes_raises, (), n_candidates=2)
        assert result == "success"

    def test_custom_scorer(self):
        from jobpulse.swarm_dispatcher import grpo_sample
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            return f"result_{call_count}"

        def scorer(r):
            # Prefer result_2
            return 10.0 if "2" in r else 1.0

        result = grpo_sample(fn, (), n_candidates=3, scorer_fn=scorer)
        assert result == "result_2"

    def test_passes_args_to_fn(self):
        from jobpulse.swarm_dispatcher import grpo_sample

        def fn(a, b):
            return f"{a}+{b}"

        result = grpo_sample(fn, ("hello", "world"), n_candidates=1)
        assert result == "hello+world"


# ── Experience storage and retrieval tests ──

class TestExperienceStorage:
    @patch("jobpulse.swarm_dispatcher.EXPERIENCE_DB")
    def test_store_and_get_experiences(self, mock_db, tmp_path):
        db_file = tmp_path / "test_exp.db"
        mock_db.__str__ = MagicMock(return_value=str(db_file))
        mock_db.parent = tmp_path

        # Create a real connection to temp DB
        conn = sqlite3.connect(str(db_file))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intent TEXT NOT NULL,
                pattern TEXT NOT NULL,
                score REAL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS persona_prompts (
                agent_name TEXT PRIMARY KEY,
                evolved_prompt TEXT NOT NULL,
                generation INTEGER DEFAULT 1,
                avg_score REAL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_exp_intent ON experiences(intent);
        """)
        conn.commit()
        conn.close()

        with patch("jobpulse.swarm_dispatcher._get_exp_conn") as mock_conn:
            def make_conn():
                c = sqlite3.connect(str(db_file))
                c.row_factory = sqlite3.Row
                return c
            mock_conn.side_effect = make_conn

            from jobpulse.swarm_dispatcher import store_experience, get_experiences, get_avg_score

            # Store experiences
            store_experience("briefing", "Use gmail + calendar first", 8.5)
            store_experience("briefing", "Skip github if weekend", 6.0)
            store_experience("gmail", "Check recruiter emails only", 7.0)

            # Retrieve by intent
            exps = get_experiences("briefing")
            assert len(exps) == 2
            # Should be ordered by score DESC
            assert exps[0]["score"] >= exps[1]["score"]

            # Avg score
            avg = get_avg_score("briefing")
            assert abs(avg - 7.25) < 0.01  # (8.5 + 6.0) / 2

            # Different intent
            gmail_exps = get_experiences("gmail")
            assert len(gmail_exps) == 1
            assert gmail_exps[0]["score"] == 7.0

            # Non-existent intent
            empty = get_experiences("nonexistent")
            assert len(empty) == 0

            # Avg of non-existent
            assert get_avg_score("nonexistent") == 0.0

    @patch("jobpulse.swarm_dispatcher.EXPERIENCE_DB")
    def test_store_and_get_persona(self, mock_db, tmp_path):
        db_file = tmp_path / "test_persona.db"
        mock_db.__str__ = MagicMock(return_value=str(db_file))
        mock_db.parent = tmp_path

        conn = sqlite3.connect(str(db_file))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intent TEXT NOT NULL,
                pattern TEXT NOT NULL,
                score REAL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS persona_prompts (
                agent_name TEXT PRIMARY KEY,
                evolved_prompt TEXT NOT NULL,
                generation INTEGER DEFAULT 1,
                avg_score REAL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()

        with patch("jobpulse.swarm_dispatcher._get_exp_conn") as mock_conn:
            def make_conn():
                c = sqlite3.connect(str(db_file))
                c.row_factory = sqlite3.Row
                return c
            mock_conn.side_effect = make_conn

            from jobpulse.swarm_dispatcher import store_persona, get_persona

            # No persona yet
            assert get_persona("gmail_agent") is None

            # Store persona
            store_persona("gmail_agent", "Classify emails focusing on recruiter signals", 1, 7.5)
            p = get_persona("gmail_agent")
            assert p is not None
            assert p["evolved_prompt"] == "Classify emails focusing on recruiter signals"
            assert p["generation"] == 1
            assert p["avg_score"] == 7.5

            # Update persona (REPLACE)
            store_persona("gmail_agent", "Evolved: skip Workday auto-rejections", 2, 8.0)
            p2 = get_persona("gmail_agent")
            assert p2["generation"] == 2
            assert "Workday" in p2["evolved_prompt"]


# ── rlm_synthesize tests ──

class TestRlmSynthesize:
    def test_small_context_returns_none(self):
        from jobpulse.swarm_dispatcher import rlm_synthesize
        result = rlm_synthesize({"email": "one email", "calendar": "one event"}, "briefing")
        assert result is None

    def test_missing_rlm_returns_none(self):
        """When rlm package is not installed, returns None gracefully."""
        import sys
        from jobpulse.swarm_dispatcher import rlm_synthesize
        sections = {"data": "x" * 6000}
        # Temporarily hide the rlm module so ImportError is raised inside the function
        saved = sys.modules.get("rlm")
        sys.modules["rlm"] = None  # forces ImportError on "from rlm import RLM"
        try:
            result = rlm_synthesize(sections, "test query")
            assert result is None
        finally:
            if saved is not None:
                sys.modules["rlm"] = saved
            else:
                sys.modules.pop("rlm", None)


# ── Full dispatch integration test (mocked agents) ──

class TestSwarmDispatch:
    @patch("jobpulse.swarm_dispatcher.event_logger")
    @patch("jobpulse.swarm_dispatcher.ProcessTrail")
    @patch("jobpulse.swarm_dispatcher._execute_agent")
    @patch("jobpulse.swarm_dispatcher.get_experiences", return_value=[])
    @patch("jobpulse.swarm_dispatcher.store_experience")
    def test_simple_dispatch_returns_result(self, mock_store, mock_get_exp,
                                             mock_exec, mock_trail, mock_evt):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        mock_exec.return_value = "Here are your tasks"

        from jobpulse.swarm_dispatcher import dispatch
        cmd = ParsedCommand(intent=Intent.SHOW_TASKS, args="", raw="show tasks")
        result = dispatch(cmd)
        assert "Here are your tasks" in result

    @patch("jobpulse.swarm_dispatcher.event_logger")
    @patch("jobpulse.swarm_dispatcher.ProcessTrail")
    @patch("jobpulse.swarm_dispatcher._execute_agent")
    @patch("jobpulse.swarm_dispatcher.get_experiences", return_value=[])
    @patch("jobpulse.swarm_dispatcher.store_experience")
    def test_multi_task_concatenates_results(self, mock_store, mock_get_exp,
                                              mock_exec, mock_trail, mock_evt):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        mock_exec.side_effect = ["Gmail results here", "Cross ref results"]

        from jobpulse.swarm_dispatcher import dispatch
        cmd = ParsedCommand(intent=Intent.GMAIL, args="", raw="check emails")
        result = dispatch(cmd)
        assert "Gmail results here" in result

    @patch("jobpulse.swarm_dispatcher.event_logger")
    @patch("jobpulse.swarm_dispatcher.ProcessTrail")
    @patch("jobpulse.swarm_dispatcher._execute_agent")
    @patch("jobpulse.swarm_dispatcher.get_experiences")
    @patch("jobpulse.swarm_dispatcher.store_experience")
    def test_experiences_injected(self, mock_store, mock_get_exp,
                                   mock_exec, mock_trail, mock_evt):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        mock_get_exp.return_value = [{"pattern": "Check recruiter emails first", "score": 8.0}]
        mock_exec.return_value = "Emails checked"

        from jobpulse.swarm_dispatcher import dispatch
        cmd = ParsedCommand(intent=Intent.GMAIL, args="", raw="check emails")
        dispatch(cmd)

        # Verify experiences were fetched for the right intent
        mock_get_exp.assert_called_once_with("gmail")

    @patch("jobpulse.swarm_dispatcher.event_logger")
    @patch("jobpulse.swarm_dispatcher.ProcessTrail")
    @patch("jobpulse.swarm_dispatcher._execute_agent")
    @patch("jobpulse.swarm_dispatcher.get_experiences", return_value=[])
    @patch("jobpulse.swarm_dispatcher.store_experience")
    def test_stores_experience_on_good_result(self, mock_store, mock_get_exp,
                                               mock_exec, mock_trail, mock_evt):
        trail = MagicMock()
        mock_trail.return_value = trail
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx

        # Return a result that will score > 0
        mock_exec.return_value = "Here is a detailed response with lots of useful content " * 10

        from jobpulse.swarm_dispatcher import dispatch
        cmd = ParsedCommand(intent=Intent.SHOW_TASKS, args="", raw="show tasks")
        dispatch(cmd)

        # store_experience should have been called since score > 0
        mock_store.assert_called_once()


class TestResearchRouting:
    """Test that research queries route through pattern router."""

    def _make_cmd(self, intent: Intent, raw: str = "test") -> ParsedCommand:
        return ParsedCommand(intent=intent, args=raw, raw=raw)

    def _analyze(self, intent: Intent, raw: str = "test") -> list:
        from jobpulse.swarm_dispatcher import analyze_task
        trail = MagicMock()
        return analyze_task(self._make_cmd(intent, raw), trail)

    def test_research_intent_routes_to_pattern_router(self):
        tasks = self._analyze(Intent.RESEARCH, "research quantum computing")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "pattern_router"

    def test_conversation_with_research_routes_to_pattern_router(self):
        tasks = self._analyze(Intent.CONVERSATION, "compare React vs Vue for dashboards")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "pattern_router"

    def test_conversation_without_research_stays_simple(self):
        tasks = self._analyze(Intent.CONVERSATION, "hello how are you")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "conversation"

    def test_budget_still_works(self):
        tasks = self._analyze(Intent.LOG_SPEND, "spent 5 on coffee")
        assert len(tasks) == 1
        assert tasks[0].get("grpo") is True

    def test_arxiv_routes_to_pattern_router(self):
        tasks = self._analyze(Intent.ARXIV, "papers")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "pattern_router"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
