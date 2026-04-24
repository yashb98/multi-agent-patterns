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

import inspect
import json
import os
from datetime import datetime
from jobpulse.command_router import Intent, ParsedCommand
from jobpulse import event_logger
from jobpulse.process_logger import ProcessTrail
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _first_name() -> str:
    try:
        from shared.profile_store import get_profile_store
        return get_profile_store().identity().first_name or "Yash"
    except Exception:
        return "Yash"


# ── Experience Memory (persists across runs in SQLite) ──

import sqlite3
from jobpulse.config import DATA_DIR
from shared.db import get_db_conn, get_pooled_db_conn

EXPERIENCE_DB = DATA_DIR / "swarm_experience.db"


def _get_exp_conn():
    """Pooled, thread-local connection. Callers must NOT close it."""
    return get_pooled_db_conn(EXPERIENCE_DB)


def _init_experience_db():
    # Use an owned (non-pooled) connection for the one-shot schema init so
    # we're not leaking the init connection into the pool for workloads that
    # never query this DB again (e.g. tests that import the module).
    conn = get_db_conn(EXPERIENCE_DB)
    try:
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
    finally:
        conn.close()


_init_experience_db()


def store_experience(intent: str, pattern: str, score: float):
    conn = _get_exp_conn()
    conn.execute(
        "INSERT INTO experiences (intent, pattern, score, created_at) VALUES (?,?,?,?)",
        (intent, pattern, score, datetime.now().isoformat())
    )
    conn.commit()


def get_experiences(intent: str, limit: int = 5) -> list[dict]:
    conn = _get_exp_conn()
    rows = conn.execute(
        "SELECT pattern, score FROM experiences WHERE intent=? ORDER BY score DESC, created_at DESC LIMIT ?",
        (intent, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_avg_score(intent: str) -> float:
    conn = _get_exp_conn()
    row = conn.execute(
        "SELECT AVG(score) as avg FROM experiences WHERE intent=?", (intent,)
    ).fetchone()
    return row["avg"] or 0.0


def store_persona(agent_name: str, prompt: str, generation: int, avg_score: float):
    conn = _get_exp_conn()
    conn.execute(
        "INSERT OR REPLACE INTO persona_prompts (agent_name, evolved_prompt, generation, avg_score, updated_at) VALUES (?,?,?,?,?)",
        (agent_name, prompt, generation, avg_score, datetime.now().isoformat())
    )
    conn.commit()


def get_persona(agent_name: str) -> dict | None:
    conn = _get_exp_conn()
    row = conn.execute(
        "SELECT * FROM persona_prompts WHERE agent_name=?", (agent_name,)
    ).fetchone()
    return dict(row) if row else None


# ── Task Analyzer — the brain of the swarm ──

def analyze_task(cmd: ParsedCommand, trail: ProcessTrail) -> list[dict]:
    """Determine what agents to run, in what order, with what priority.

    For simple intents (show_tasks, calendar), returns a single task.
    For complex intents (briefing), decomposes into multiple parallel tasks
    with a synthesis step at the end.
    """
    intent = cmd.intent

    # Research queries — route through pattern auto-router
    from jobpulse.pattern_router import is_research_query
    if is_research_query(cmd):
        return [{"agent": "pattern_router", "priority": 1, "description": "Research via LangGraph pattern"}]

    # Simple intents — single agent, no swarm overhead
    SIMPLE_INTENTS = {
        Intent.SHOW_TASKS, Intent.CREATE_TASKS, Intent.COMPLETE_TASK, Intent.REMOVE_TASK,
        Intent.HELP, Intent.CREATE_EVENT, Intent.SHOW_BUDGET,
        Intent.CONVERSATION, Intent.CLEAR_CHAT,
        Intent.REMOTE_SHELL, Intent.GIT_OPS,
        Intent.FILE_OPS, Intent.SYSTEM_STATUS,
        Intent.LOG_HOURS, Intent.SHOW_HOURS, Intent.CONFIRM_SAVINGS, Intent.UNDO_HOURS,
        Intent.UNDO_BUDGET, Intent.RECURRING_BUDGET, Intent.WEEKLY_PLAN,
        Intent.SCAN_JOBS, Intent.SHOW_JOBS, Intent.APPROVE_JOBS, Intent.REJECT_JOB,
        Intent.JOB_DETAIL, Intent.JOB_STATS, Intent.SEARCH_CONFIG,
        Intent.PAUSE_JOBS, Intent.RESUME_JOBS,
        Intent.ENGINE_STATS, Intent.ENGINE_COMPARE, Intent.ENGINE_LEARNING, Intent.ENGINE_RESET,
        Intent.JOB_PATTERNS, Intent.FOLLOW_UPS, Intent.INTERVIEW_PREP,
        Intent.CANCEL,
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
        return [{"agent": "weekly_report", "priority": 1, "description": "Build period summary"}]

    if intent == Intent.EXPORT:
        return [{"agent": "export", "priority": 1, "description": "Export data"}]

    # Unknown — try LLM classification
    return [{"agent": intent.value, "priority": 1, "description": f"Handle: {intent.value}"}]


# ── N-Sample Diversity Sampling (GRPO-compatible) ──

_DEFAULT_TEMP_SPREAD = [0.2, 0.4, 0.7, 0.9]


def _diversity_bonus(candidate: str, previous_candidates: list[str]) -> float:
    """Reward lexical novelty to avoid near-duplicate candidate groups."""
    if not previous_candidates:
        return 0.0
    tokens = set(candidate.lower().split())
    if not tokens:
        return 0.0
    max_overlap = 0.0
    for prev in previous_candidates:
        prev_tokens = set(prev.lower().split())
        if not prev_tokens:
            continue
        overlap = len(tokens & prev_tokens) / max(len(tokens | prev_tokens), 1)
        max_overlap = max(max_overlap, overlap)
    # 0.0 overlap => +0.4, 100% overlap => +0.0
    return max(0.0, 0.4 * (1.0 - max_overlap))


def n_sample_diversity(
    fn,
    args,
    n_candidates: int = 3,
    scorer_fn=None,
    temperature_spread: list[float] | None = None,
) -> str:
    """Generate diverse candidates and return the best-scoring result.

    Diversity knobs:
    - Temperature spread when the target callable accepts `temperature` or `temp`.
    - Lexical novelty bonus to avoid picking repeated candidates.
    """
    if n_candidates <= 1:
        return fn(*args)

    spread = temperature_spread or _DEFAULT_TEMP_SPREAD
    try:
        fn_params = inspect.signature(fn).parameters
    except Exception:
        fn_params = {}
    supports_temperature = "temperature" in fn_params
    supports_temp = "temp" in fn_params

    candidates: list[tuple[float, str]] = []
    seen_outputs: list[str] = []
    for i in range(n_candidates):
        kwargs = {}
        temp = spread[i % len(spread)]
        if supports_temperature:
            kwargs["temperature"] = temp
        elif supports_temp:
            kwargs["temp"] = temp

        try:
            result = fn(*args, **kwargs)
            base_score = scorer_fn(result) if scorer_fn else _score_result(result)
            diversity = _diversity_bonus(result, seen_outputs)
            seen_outputs.append(result)
            candidates.append((base_score + diversity, result))
        except Exception as e:
            candidates.append((-1, f"Error: {e}"))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def grpo_sample(fn, args, n_candidates: int = 3, scorer_fn=None) -> str:
    """Backward-compatible wrapper around `n_sample_diversity`."""
    return n_sample_diversity(
        fn=fn,
        args=args,
        n_candidates=n_candidates,
        scorer_fn=scorer_fn,
    )


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
        from jobpulse.config import RLM_BACKEND, RLM_ROOT_MODEL, RLM_MAX_ITERATIONS, RLM_MAX_BUDGET
        rlm = RLM(
            backend=RLM_BACKEND,
            backend_kwargs={"model": RLM_ROOT_MODEL},
            max_depth=1,
            max_iterations=RLM_MAX_ITERATIONS,
            max_budget=RLM_MAX_BUDGET,
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
        logger.error(
            "RLM synthesis error: %s",
            e,
            extra={"component": "rlm_synthesize", "error_type": type(e).__name__},
        )
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
    # Stop / undo last action — handled before swarm dispatch
    if cmd.intent == Intent.STOP:
        from jobpulse.last_action import undo_last_action
        return undo_last_action()

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
                from shared.agent_result import DispatchError, classify_error
                error_cat, retryable = classify_error(e)
                dispatch_error = DispatchError(
                    error_category=error_cat,
                    message=str(e),
                    is_retryable=retryable,
                    agent_name=agent_name,
                    attempted_action=task["description"],
                )
                results[agent_name] = dispatch_error.to_user_message()
                s["output"] = f"Error [{error_cat}]: {e}"
                s["metadata"] = dispatch_error.to_dict()
                logger.warning("Swarm agent %s failed [%s]: %s (retryable=%s)",
                               agent_name, error_cat, e, retryable,
                               extra={
                                   "agent_name": agent_name,
                                   "error_category": error_cat,
                                   "retryable": retryable,
                               })

    # Step 3: If multiple results, synthesize
    final_result = None
    if len(results) > 1 and any(t.get("rlm") for t in tasks):
        with trail.step("llm_call", "RLM synthesis",
                         step_input=f"{len(results)} sections, {sum(len(v) for v in results.values())} chars") as s:
            rlm_result = rlm_synthesize(results, f"Create a briefing for {_first_name()} from these data sources")
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

    # Step 4: Store experience (LLM scorer kicks in for ambiguous mid-range results)
    score = _score_result(final_result, intent=cmd.intent.value)
    if score > 0:
        store_experience(cmd.intent.value, f"Tasks: {[t['agent'] for t in tasks]}", score)

    # Audit log — captures every dispatch regardless of whether agent used ToolExecutor
    try:
        from shared.tool_integration import get_shared_tool_executor
        _is_err = not final_result or "⚠️" in final_result[:20]
        get_shared_tool_executor().record_dispatch(
            intent=cmd.intent.value,
            agent_name=",".join(t["agent"] for t in tasks) or "unknown",
            result_summary=final_result[:200] if final_result else "",
            success=not _is_err,
            error=final_result[:200] if _is_err else None,
        )
    except Exception:
        pass  # Audit is best-effort — never block a dispatch

    # Record action for undo ("stop" command)
    from jobpulse.last_action import save_last_action
    save_last_action(cmd.intent.value, cmd.raw, final_result or "")

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
    from jobpulse.handler_registry import get_handler_map_by_value
    from jobpulse.dispatcher import _handle_unknown

    AGENT_MAP = get_handler_map_by_value()

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
            return format_week_summary(summary) if summary["by_category"] else "No transactions this period"
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

    elif agent_name == "pattern_router":
        from jobpulse.pattern_router import select_pattern, run_with_pattern, log_pattern_selection
        pattern, reason = select_pattern(cmd.raw)
        logger.info("Pattern router: %s — %s", pattern, reason)
        result = run_with_pattern(pattern, cmd.raw)
        log_pattern_selection(cmd.raw, pattern, override=("override" in reason.lower()), quality_score=0.0)
        return result

    # Standard agent
    handler = AGENT_MAP.get(agent_name)
    if handler:
        return handler(cmd)

    return _handle_unknown(cmd)


_FACT_GROUNDED_INTENTS = {
    Intent.BRIEFING.value,
    Intent.ARXIV.value,
    Intent.RESEARCH.value,
    Intent.GITHUB.value,
    Intent.TRENDING.value,
    Intent.WEEKLY_REPORT.value,
}


def _heuristic_score_result(result: str) -> float:
    """Cheap fallback score when judge/grounding are unavailable."""
    lower = result.lower()
    if "error" in lower or "failed" in lower or "exception" in lower:
        return max(0.0, min(len(result) / 1000, 1.5) - 2.0)

    heuristic = 0.0
    heuristic += min(len(result) / 500, 3.0)
    if any(e in result for e in ["✅", "📧", "📅", "💰", "💻", "📋", "🔍"]):
        heuristic += 1.0
    if "\n" in result and len(result.split("\n")) >= 3:
        heuristic += 0.5
    return max(0.0, min(10.0, heuristic))


def _fact_checker_grounding(result: str, intent: str) -> dict | None:
    """Ground factual outputs with the shared FactChecker pipeline."""
    if intent not in _FACT_GROUNDED_INTENTS or len(result) < 400:
        return None
    try:
        from shared.fact_checker import extract_claims, verify_claims, compute_accuracy_score

        claims = extract_claims(result, topic=intent)
        if not claims:
            return None

        verifications = verify_claims(claims[:8], sources=[], web_search=True)
        if not verifications:
            return None

        accuracy = compute_accuracy_score(verifications)
        issue_count = sum(
            1
            for v in verifications
            if v.get("verdict", "UNVERIFIED").upper() != "VERIFIED"
        )
        return {
            "fact_checker_score": round(accuracy, 3),
            "claims_checked": len(verifications),
            "issue_count": issue_count,
        }
    except Exception as exc:
        logger.debug(
            "FactChecker grounding skipped: %s",
            exc,
            extra={"intent": intent, "error_type": type(exc).__name__},
        )
        return None


def _llm_judge_score(result: str, intent: str, grounding: dict | None) -> float | None:
    """Ask an LLM judge to score quality using a fixed rubric."""
    try:
        from langchain_core.messages import HumanMessage
        from shared.agents import get_llm
        from shared.streaming import smart_llm_call

        llm = get_llm(model="gpt-5-mini", temperature=0, timeout=20.0)
        grounding_json = json.dumps(grounding, ensure_ascii=True) if grounding else "null"
        prompt = (
            f"You are a strict response-quality judge for intent '{intent}'.\n"
            f"Score the candidate on a 0-10 rubric:\n"
            f"- completeness (0-3)\n"
            f"- factual_accuracy (0-3)\n"
            f"- structure_clarity (0-2)\n"
            f"- actionability (0-2)\n"
            "If FactChecker grounding is present, penalize factual_accuracy when it is low.\n\n"
            f"FactChecker grounding (nullable): {grounding_json}\n\n"
            f"Candidate response:\n{result[:1800]}\n\n"
            "Return strict JSON only: "
            "{\"score\": <float 0-10>, \"reason\": \"<short sentence>\", \"confidence\": <0-1>}"
        )
        raw_response = smart_llm_call(llm, [HumanMessage(content=prompt)])
        raw = raw_response.content if hasattr(raw_response, "content") else str(raw_response)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            import re

            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return None
            payload = json.loads(match.group(0))

        score = float(payload.get("score"))
        return max(0.0, min(10.0, score))
    except Exception as exc:
        logger.debug(
            "LLM judge scoring failed: %s",
            exc,
            extra={"intent": intent, "error_type": type(exc).__name__},
        )
        return None


def _score_result(result: str | None, intent: str = "") -> float:
    """Score a result with LLM judge + optional FactChecker grounding.

    Falls back to a lightweight heuristic when model scoring is unavailable.
    """
    if not result:
        return 0.0

    heuristic = _heuristic_score_result(result)
    if os.getenv("JOBPULSE_DISABLE_LLM_JUDGE", "0") == "1":
        return heuristic
    if not intent:
        return heuristic

    grounding = _fact_checker_grounding(result, intent)
    judge_score = _llm_judge_score(result, intent, grounding)
    if judge_score is None:
        return heuristic

    if grounding and grounding.get("fact_checker_score") is not None:
        blended = 0.7 * judge_score + 0.3 * float(grounding["fact_checker_score"])
        return max(0.0, min(10.0, blended))
    return judge_score
