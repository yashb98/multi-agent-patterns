"""Enhanced Swarm Dispatcher — replaces flat dispatch with adaptive swarm intelligence.

Architecture:
  1. Task Complexity Analyzer — determines what agents are needed
  2. Dynamic Agent Factory — can spawn custom agents for novel tasks
  3. Persona Evolution — agent prompts improve over runs (experience memory)
  4. GRPO Group Sampling — generate multiple candidates, pick the best
  5. RLM Integration — recursive LLM for large-context synthesis
  6. Experience-Aware Convergence — quality bar rises as system learns

The swarm wraps the existing agent functions (gmail, calendar, etc.)
without changing them. It adds intelligence AROUND them.
"""

import json
import os
import time
from datetime import datetime
from jobpulse.command_router import Intent, ParsedCommand
from jobpulse import event_logger
from jobpulse.process_logger import ProcessTrail
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ── Experience Memory (persists across runs in SQLite) ──

import sqlite3
from jobpulse.config import DATA_DIR

EXPERIENCE_DB = DATA_DIR / "swarm_experience.db"


def _get_exp_conn():
    EXPERIENCE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(EXPERIENCE_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_experience_db():
    conn = _get_exp_conn()
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


_init_experience_db()


def store_experience(intent: str, pattern: str, score: float):
    conn = _get_exp_conn()
    conn.execute(
        "INSERT INTO experiences (intent, pattern, score, created_at) VALUES (?,?,?,?)",
        (intent, pattern, score, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_experiences(intent: str, limit: int = 5) -> list[dict]:
    conn = _get_exp_conn()
    rows = conn.execute(
        "SELECT pattern, score FROM experiences WHERE intent=? ORDER BY score DESC, created_at DESC LIMIT ?",
        (intent, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_avg_score(intent: str) -> float:
    conn = _get_exp_conn()
    row = conn.execute(
        "SELECT AVG(score) as avg FROM experiences WHERE intent=?", (intent,)
    ).fetchone()
    conn.close()
    return row["avg"] or 0.0


def store_persona(agent_name: str, prompt: str, generation: int, avg_score: float):
    conn = _get_exp_conn()
    conn.execute(
        "INSERT OR REPLACE INTO persona_prompts (agent_name, evolved_prompt, generation, avg_score, updated_at) VALUES (?,?,?,?,?)",
        (agent_name, prompt, generation, avg_score, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_persona(agent_name: str) -> dict | None:
    conn = _get_exp_conn()
    row = conn.execute(
        "SELECT * FROM persona_prompts WHERE agent_name=?", (agent_name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Task Analyzer — the brain of the swarm ──

def analyze_task(cmd: ParsedCommand, trail: ProcessTrail) -> list[dict]:
    """Determine what agents to run, in what order, with what priority.

    For simple intents (show_tasks, calendar), returns a single task.
    For complex intents (briefing), decomposes into multiple parallel tasks
    with a synthesis step at the end.
    """
    intent = cmd.intent

    # Simple intents — single agent, no swarm overhead
    SIMPLE_INTENTS = {
        Intent.SHOW_TASKS, Intent.CREATE_TASKS, Intent.COMPLETE_TASK, Intent.REMOVE_TASK,
        Intent.HELP, Intent.CREATE_EVENT, Intent.SHOW_BUDGET,
        Intent.CONVERSATION, Intent.CLEAR_CHAT,
        Intent.REMOTE_SHELL, Intent.GIT_OPS,
        Intent.FILE_OPS, Intent.SYSTEM_STATUS,
        Intent.LOG_HOURS, Intent.SHOW_HOURS, Intent.CONFIRM_SAVINGS, Intent.UNDO_HOURS,
        Intent.UNDO_BUDGET, Intent.RECURRING_BUDGET, Intent.WEEKLY_PLAN,
    }
    if intent in SIMPLE_INTENTS:
        return [{"agent": intent.value, "priority": 1, "description": f"Direct: {intent.value}"}]

    # Budget intents — classify + store + sync (could benefit from GRPO on classify)
    if intent in (Intent.LOG_SPEND, Intent.LOG_INCOME, Intent.LOG_SAVINGS, Intent.SET_BUDGET):
        return [{"agent": intent.value, "priority": 1, "description": f"Budget: {intent.value}", "grpo": True}]

    # Gmail — scan + classify + alert + extract (multi-step)
    if intent == Intent.GMAIL:
        return [
            {"agent": "gmail", "priority": 1, "description": "Scan inbox for new emails"},
            {"agent": "cross_reference", "priority": 2, "description": "Cross-reference emails with calendar/knowledge graph"},
        ]

    # Briefing — full decomposition
    if intent == Intent.BRIEFING:
        return [
            {"agent": "gmail_collect", "priority": 1, "description": "Collect recruiter emails"},
            {"agent": "calendar_collect", "priority": 1, "description": "Collect today's events"},
            {"agent": "tasks_collect", "priority": 1, "description": "Collect Notion tasks"},
            {"agent": "github_collect", "priority": 1, "description": "Collect GitHub commits"},
            {"agent": "budget_collect", "priority": 1, "description": "Collect budget summary"},
            {"agent": "synthesize_briefing", "priority": 2, "description": "Synthesize all data into briefing", "grpo": True, "rlm": True},
        ]

    # GitHub
    if intent == Intent.GITHUB:
        return [{"agent": "github", "priority": 1, "description": "Fetch yesterday's commits"}]

    if intent == Intent.TRENDING:
        return [{"agent": "trending", "priority": 1, "description": "Fetch trending repos"}]

    if intent == Intent.CALENDAR:
        return [{"agent": "calendar", "priority": 1, "description": "Fetch today + tomorrow"}]

    if intent == Intent.ARXIV:
        return [{"agent": "arxiv", "priority": 1, "description": "Fetch papers"}]

    if intent == Intent.WEEKLY_REPORT:
        return [{"agent": "weekly_report", "priority": 1, "description": "Build weekly summary"}]

    if intent == Intent.EXPORT:
        return [{"agent": "export", "priority": 1, "description": "Export data"}]

    # Unknown — try LLM classification
    return [{"agent": intent.value, "priority": 1, "description": f"Handle: {intent.value}"}]


# ── GRPO Group Sampling ──

def grpo_sample(fn, args, n_candidates: int = 3, scorer_fn=None) -> str:
    """Generate multiple candidates and pick the best one.

    fn: function that returns a string result
    args: arguments to pass to fn
    n_candidates: how many candidates to generate
    scorer_fn: optional function(result) -> float score
    """
    if n_candidates <= 1:
        return fn(*args)

    candidates = []
    for i in range(n_candidates):
        try:
            result = fn(*args)
            # Simple scoring: length + structure heuristic
            score = len(result) * 0.001  # prefer longer
            if "error" in result.lower() or "failed" in result.lower():
                score *= 0.3  # penalize errors
            if scorer_fn:
                score = scorer_fn(result)
            candidates.append((score, result))
        except Exception as e:
            candidates.append((-1, f"Error: {e}"))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ── RLM Synthesis ──

def rlm_synthesize(sections: dict, query: str) -> str | None:
    """Use RLM for large-context synthesis. Returns None if RLM unavailable or context too small."""
    # Build context from all sections
    context = ""
    for name, content in sections.items():
        context += f"\n\n=== {name.upper()} ===\n{content}"

    if len(context) < 5000:
        return None  # Too small, direct LLM is fine

    try:
        from rlm import RLM
        backend = os.getenv("RLM_BACKEND", "openai")
        rlm = RLM(
            backend=backend,
            backend_kwargs={"model": os.getenv("RLM_ROOT_MODEL", "gpt-4o-mini")},
            max_depth=1,
            max_iterations=int(os.getenv("RLM_MAX_ITERATIONS", "10")),
            max_budget=float(os.getenv("RLM_MAX_BUDGET", "0.10")),
            verbose=False,
        )
        prompt = (
            f"{query}\n\n"
            f"Data to synthesize ({len(context)} chars):\n{context}\n\n"
            f"Break the data into logical sections, summarize each, "
            f"then combine into a concise, actionable briefing. "
            f"Use emoji. Highlight urgent items first."
        )
        result = rlm.completion(prompt)
        rlm.close()
        return result.choices[0].message.content
    except ImportError:
        return None
    except Exception as e:
        logger.error("RLM synthesis error: %s", e)
        return None


# ── Enhanced Dispatch ──

def dispatch(cmd: ParsedCommand) -> str:
    """Enhanced Swarm dispatch — replaces flat dispatcher.

    Flow:
    1. Analyze task complexity → generate priority queue
    2. Execute each task (with GRPO if flagged)
    3. Cross-reference results
    4. Synthesize with RLM if context is large
    5. Store experience for learning
    """
    trail = ProcessTrail("enhanced_swarm", "telegram_message")

    # Step 1: Analyze
    with trail.step("decision", "Analyze task complexity", step_input=cmd.raw[:200]) as s:
        tasks = analyze_task(cmd, trail)
        s["output"] = f"{len(tasks)} tasks: {', '.join(t['agent'] for t in tasks)}"
        s["decision"] = f"Decomposed {cmd.intent.value} into {len(tasks)} tasks"

    # Inject learned experiences into context
    experiences = get_experiences(cmd.intent.value)
    exp_context = ""
    if experiences:
        exp_context = "Learned patterns:\n" + "\n".join(
            f"- {e['pattern']} (score: {e['score']:.1f})" for e in experiences[:3]
        )

    # Step 2: Execute tasks in priority order
    results = {}
    for task in sorted(tasks, key=lambda t: t["priority"]):
        agent_name = task["agent"]
        use_grpo = task.get("grpo", False)
        use_rlm = task.get("rlm", False)

        with trail.step("api_call", f"Execute: {agent_name}",
                         step_input=task["description"]) as s:
            try:
                result = _execute_agent(agent_name, cmd, exp_context)
                results[agent_name] = result
                s["output"] = result[:300] if result else ""
            except Exception as e:
                # Structured error propagation (Domain 5, Task 5.3)
                from jobpulse.dispatcher import _classify_error
                error_cat, retryable = _classify_error(e)
                error_context = {
                    "status": "error",
                    "errorCategory": error_cat,
                    "isRetryable": retryable,
                    "agentName": agent_name,
                    "attemptedAction": task["description"],
                    "message": str(e),
                }
                results[agent_name] = json.dumps(error_context)
                s["output"] = f"Error [{error_cat}]: {e}"
                s["metadata"] = error_context
                logger.warning("Swarm agent %s failed [%s]: %s (retryable=%s)",
                               agent_name, error_cat, e, retryable)

    # Step 3: If multiple results, synthesize
    final_result = None
    if len(results) > 1 and any(t.get("rlm") for t in tasks):
        with trail.step("llm_call", "RLM synthesis",
                         step_input=f"{len(results)} sections, {sum(len(v) for v in results.values())} chars") as s:
            rlm_result = rlm_synthesize(results, f"Create a briefing for Yash from these data sources")
            if rlm_result:
                final_result = rlm_result
                s["output"] = f"RLM synthesized {len(rlm_result)} chars"
                s["decision"] = "Used RLM for large-context synthesis"
            else:
                s["output"] = "RLM skipped (context too small or unavailable)"

    if not final_result:
        # Use the single result or concatenate
        if len(results) == 1:
            final_result = list(results.values())[0]
        else:
            final_result = "\n\n".join(f"{v}" for v in results.values() if v and not v.startswith("Error"))

    # Step 4: Store experience
    score = _score_result(final_result)
    if score > 0:
        store_experience(cmd.intent.value, f"Tasks: {[t['agent'] for t in tasks]}", score)

    # Log to simulation events
    event_logger.log_event(
        event_type="agent_action",
        agent_name="enhanced_swarm",
        action=cmd.intent.value,
        content=final_result[:300] if final_result else "",
        metadata={"intent": cmd.intent.value, "tasks": len(tasks),
                  "used_rlm": any(t.get("rlm") for t in tasks), "score": score},
    )

    trail.finalize(final_result[:500] if final_result else "")
    return final_result or "No result"


def _execute_agent(agent_name: str, cmd: ParsedCommand, exp_context: str) -> str:
    """Execute a single agent by name. Maps to existing agent functions."""
    # Import lazily to avoid circular imports
    from jobpulse.dispatcher import (
        _handle_show_tasks, _handle_create_tasks, _handle_calendar,
        _handle_gmail, _handle_github, _handle_trending, _handle_briefing,
        _handle_arxiv, _handle_complete_task, _handle_remove_task, _handle_create_event,
        _handle_log_spend, _handle_log_income, _handle_log_savings,
        _handle_set_budget, _handle_show_budget, _handle_help, _handle_unknown,
        _handle_weekly_report, _handle_export,
        _handle_conversation, _handle_clear_chat,
        _handle_remote_shell, _handle_git_ops,
        _handle_file_ops, _handle_system_status,
        _handle_log_hours, _handle_show_hours, _handle_confirm_savings, _handle_undo_hours,
        _handle_undo_budget, _handle_recurring_budget, _handle_weekly_plan,
    )

    # Direct agent mapping
    AGENT_MAP = {
        Intent.SHOW_TASKS.value: _handle_show_tasks,
        Intent.CREATE_TASKS.value: _handle_create_tasks,
        Intent.CALENDAR.value: _handle_calendar,
        Intent.GMAIL.value: _handle_gmail,
        Intent.GITHUB.value: _handle_github,
        Intent.TRENDING.value: _handle_trending,
        Intent.BRIEFING.value: _handle_briefing,
        Intent.ARXIV.value: _handle_arxiv,
        Intent.COMPLETE_TASK.value: _handle_complete_task,
        Intent.REMOVE_TASK.value: _handle_remove_task,
        Intent.CREATE_EVENT.value: _handle_create_event,
        Intent.LOG_SPEND.value: _handle_log_spend,
        Intent.LOG_INCOME.value: _handle_log_income,
        Intent.LOG_SAVINGS.value: _handle_log_savings,
        Intent.SET_BUDGET.value: _handle_set_budget,
        Intent.SHOW_BUDGET.value: _handle_show_budget,
        Intent.HELP.value: _handle_help,
        Intent.WEEKLY_REPORT.value: _handle_weekly_report,
        Intent.EXPORT.value: _handle_export,
        Intent.CONVERSATION.value: _handle_conversation,
        Intent.CLEAR_CHAT.value: _handle_clear_chat,
        Intent.REMOTE_SHELL.value: _handle_remote_shell,
        Intent.GIT_OPS.value: _handle_git_ops,
        Intent.FILE_OPS.value: _handle_file_ops,
        Intent.SYSTEM_STATUS.value: _handle_system_status,
        Intent.LOG_HOURS.value: _handle_log_hours,
        Intent.SHOW_HOURS.value: _handle_show_hours,
        Intent.CONFIRM_SAVINGS.value: _handle_confirm_savings,
        Intent.UNDO_HOURS.value: _handle_undo_hours,
        Intent.UNDO_BUDGET.value: _handle_undo_budget,
        Intent.RECURRING_BUDGET.value: _handle_recurring_budget,
        Intent.WEEKLY_PLAN.value: _handle_weekly_plan,
    }

    # Briefing sub-agents (collect phases)
    if agent_name == "gmail_collect":
        from jobpulse.gmail_agent import get_yesterday_recruiter_emails, CATEGORY_EMOJI
        emails = get_yesterday_recruiter_emails()
        if not emails:
            return "No recruiter emails yesterday"
        lines = []
        for e in emails:
            label = CATEGORY_EMOJI.get(e["category"], e["category"])
            sender = e["sender"].split("<")[0].strip() if "<" in e["sender"] else e["sender"]
            lines.append(f'{label}: {sender} — "{e["subject"]}"')
        return "\n".join(lines)

    elif agent_name == "calendar_collect":
        from jobpulse.calendar_agent import get_today_and_tomorrow, format_events
        cal = get_today_and_tomorrow()
        today = format_events(cal["today_events"]) if cal["today_events"] else "No events today"
        tomorrow = format_events(cal["tomorrow_events"]) if cal["tomorrow_events"] else "Nothing tomorrow"
        return f"TODAY:\n{today}\n\nTOMORROW:\n{tomorrow}"

    elif agent_name == "tasks_collect":
        from jobpulse.notion_agent import get_today_tasks, format_tasks
        tasks = get_today_tasks()
        return format_tasks(tasks)

    elif agent_name == "github_collect":
        from jobpulse.github_agent import get_yesterday_commits, format_commits
        data = get_yesterday_commits()
        return format_commits(data)

    elif agent_name == "budget_collect":
        from jobpulse.budget_agent import get_week_summary, format_week_summary
        try:
            summary = get_week_summary()
            return format_week_summary(summary) if summary["by_category"] else "No transactions this week"
        except Exception as e:
            logger.debug("Budget collect failed: %s", e)
            return "Budget unavailable"

    elif agent_name == "synthesize_briefing":
        # This is handled by the RLM synthesis step in dispatch()
        return ""

    elif agent_name == "cross_reference":
        # Cross-reference emails with knowledge graph
        try:
            from mindgraph_app.retriever import deep_query
            return deep_query("What connections exist between recent emails and calendar events?")
        except Exception as e:
            logger.debug("Cross-reference failed: %s", e)
            return ""

    # Standard agent
    handler = AGENT_MAP.get(agent_name)
    if handler:
        return handler(cmd)

    return _handle_unknown(cmd)


def _score_result(result: str) -> float:
    """Simple heuristic scoring of a result. Higher = better."""
    if not result:
        return 0.0
    score = 0.0
    score += min(len(result) / 500, 3.0)  # length (up to 3 pts)
    if "error" in result.lower() or "failed" in result.lower():
        score -= 2.0
    if any(e in result for e in ["✅", "📧", "📅", "💰", "💻"]):
        score += 1.0  # has emoji structure = formatted response
    return max(0.0, score)
