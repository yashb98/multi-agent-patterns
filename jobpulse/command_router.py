"""Command router — classifies Telegram messages into intents and dispatches to agents.

Intent detection is two-tier:
  1. Rule-based pattern matching (fast, free, handles 90% of messages)
  2. LLM fallback for ambiguous messages (costs ~$0.001 per call)

Each intent maps to an agent function that returns a Telegram reply string.
"""

import re
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Callable
from shared.logging_config import get_logger

logger = get_logger(__name__)


class Intent(str, Enum):
    CREATE_TASKS = "create_tasks"
    SHOW_TASKS = "show_tasks"
    COMPLETE_TASK = "complete_task"
    REMOVE_TASK = "remove_task"
    CALENDAR = "calendar"
    CREATE_EVENT = "create_event"
    GMAIL = "gmail"
    GITHUB = "github"
    TRENDING = "trending"
    BRIEFING = "briefing"
    ARXIV = "arxiv"
    LOG_SPEND = "log_spend"
    LOG_INCOME = "log_income"
    LOG_SAVINGS = "log_savings"
    SET_BUDGET = "set_budget"
    SHOW_BUDGET = "show_budget"
    HELP = "help"
    WEEKLY_REPORT = "weekly_report"
    EXPORT = "export"
    CONVERSATION = "conversation"
    REMOTE_SHELL = "remote_shell"
    GIT_OPS = "git_ops"
    FILE_OPS = "file_ops"
    SYSTEM_STATUS = "system_status"
    CLEAR_CHAT = "clear_chat"
    LOG_HOURS = "log_hours"
    SHOW_HOURS = "show_hours"
    CONFIRM_SAVINGS = "confirm_savings"
    UNDO_HOURS = "undo_hours"
    UNDO_BUDGET = "undo_budget"
    RECURRING_BUDGET = "recurring_budget"
    WEEKLY_PLAN = "weekly_plan"
    STOP = "stop"
    SHOW_JOBS = "show_jobs"
    APPROVE_JOBS = "approve_jobs"
    REJECT_JOB = "reject_job"
    JOB_STATS = "job_stats"
    SEARCH_CONFIG = "search_config"
    PAUSE_JOBS = "pause_jobs"
    RESUME_JOBS = "resume_jobs"
    JOB_DETAIL = "job_detail"
    SCAN_JOBS = "scan_jobs"
    ENGINE_STATS = "engine_stats"
    ENGINE_COMPARE = "engine_compare"
    ENGINE_LEARNING = "engine_learning"
    ENGINE_RESET = "engine_reset"
    UNKNOWN = "unknown"


@dataclass
class ParsedCommand:
    intent: Intent
    args: str  # remaining text after intent keywords stripped
    raw: str   # original message


# ── Rule-based patterns (checked in order, first match wins) ──

PATTERNS: list[tuple[Intent, list[str]]] = [
    # Remote shell (highest priority — explicit prefix)
    (Intent.REMOTE_SHELL, [
        r"^(run|shell|exec|cmd):\s*(.+)",
        r"^\$\s+(.+)",
    ]),
    # Git operations (before GITHUB to avoid conflict)
    (Intent.GIT_OPS, [
        r"^git\s+(status|log|diff|branch|stash|pull)",
        r"^commit:\s*(.+)",
        r"^push\s*$",
    ]),
    # File operations
    (Intent.FILE_OPS, [
        r"^(show|read|cat|view):\s*(.+)",
        r"^(logs?|show logs?|tail logs?)\s*$",
        r"^(errors?|show errors?|recent errors?)\s*$",
        r"^(more|next)\s*$",
    ]),
    # System status
    (Intent.SYSTEM_STATUS, [
        r"^status\s*$",
        r"^(system|daemon|health)\s+(status|check|info)",
    ]),
    # Clear chat / conversation history
    (Intent.CLEAR_CHAT, [
        r"^(clear (chat|history|conversation)|new (chat|conversation)|reset chat)",
    ]),
    # Stop / undo last action (high priority — before other patterns)
    (Intent.STOP, [
        r"^/?(stop|cancel|undo last|undo that|reverse|take that back|nope|oops)$",
        r"^stop\s+(that|last|it)$",
    ]),
    # Help
    (Intent.HELP, [
        r"^/?(help|commands|menu|what can you do)$",
    ]),
    # Work hours / salary (MUST be before income patterns)
    (Intent.LOG_HOURS, [
        r"(worked|working|work)\s+\d+(\.\d+)?\s*(hours?|hrs?|h)\b",
        r"^\d+(\.\d+)?\s*(hours?|hrs?|h)\s*(worked|working|work|today)?",
        r"^log\s+\d+(\.\d+)?\s*(hours?|hrs?|h)",
        r"(worked|working|work)\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+(and\s+)?(a\s+)?(half\s+|quarter\s+)?(hours?|hrs?)",
        r"(worked|working|work)\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+hours?\s+and\s+\w+\s+minutes?",
    ]),
    (Intent.SHOW_HOURS, [
        r"^(hours|work hours|my hours|show hours|timesheet|salary hours)",
    ]),
    (Intent.CONFIRM_SAVINGS, [
        r"^(saved|transferred|moved to savings|confirm savings|done saving)\s*$",
        r"^(saved|transferred|moved to savings|confirm savings|done saving)[.!]?\s*$",
    ]),
    # Undo hours (MUST be before undo budget)
    (Intent.UNDO_HOURS, [
        r"^undo\s+hours?\s*$",
        r"^undo\s+hours?\s+\d+",
        r"^undo\s+hours?\s+[\d,\s]+",
        r"^undo (last )?(shift|salary|work|hours? log)",
    ]),
    # Undo budget (MUST be before other budget patterns)
    (Intent.UNDO_BUDGET, [
        r"^undo\s*$",
        r"^undo\s+\d+",
        r"^undo (last )?(transaction|spend|expense|budget)",
    ]),
    # Recurring budget
    (Intent.RECURRING_BUDGET, [
        r"^recurring:\s*(.+)",
        r"^(show |list )recurring",
        r"^(stop|cancel|remove) recurring",
    ]),
    # Weekly plan / carry forward
    (Intent.WEEKLY_PLAN, [
        r"^(plan|planning|plan week|weekly plan|carry forward|carryover)",
    ]),
    # Budget — set planned budget (MUST be before show_budget)
    (Intent.SET_BUDGET, [
        r"set\s+budget",
        r"budget\s+\w+\s+\d+",
        r"plan\s+\d+\s+(for|on)\s+\w+",
        r"limit\s+\w+\s+(to\s+)?\d+",
    ]),
    # Budget — show (after set_budget so "set budget" matches first)
    (Intent.SHOW_BUDGET, [
        r"^budget\s*$",
        r"(spending|how much.+(spent|spend|earned)|(weekly|period) (budget|spend)|show budget|show spending|^summary$)",
        r"(today.?s|this week.?s)\s+(spend|budget|expenses?|money)",
        r"(budget|spending)\s*(compare|comparison|vs|versus|vs last)",
        r"^compare\s+(budget|spending)",
    ]),
    # Budget — log income
    (Intent.LOG_INCOME, [
        r"(earned|income|received|got paid|salary|freelance)\s+\d",
        r"(earned|income|received|got paid|salary)\s+[£$€]?\s*\d+",
    ]),
    # Budget — log savings
    (Intent.LOG_SAVINGS, [
        r"(saved|saving|invest|moved to savings)\s+\d",
        r"(saved|invest)\s+[£$€]?\s*\d+",
    ]),
    # Budget — log spend (must come last so income/savings match first)
    (Intent.LOG_SPEND, [
        r"(spent|spend|paid|bought)\s+\d",
        r"[£$€]\s*\d+",
        r"\d+(\.\d{1,2})?\s+(on|for|at)\s+\w+",
    ]),
    # Period / weekly report
    (Intent.WEEKLY_REPORT, [
        r"((weekly|period) (report|summary)|week.?s? (report|summary)|this (week|period)|last (week|period).?s? (summary|report))",
    ]),
    # Export
    (Intent.EXPORT, [
        r"(export|backup|download data|save data|dump)",
    ]),
    # Job Autopilot
    (Intent.SCAN_JOBS, [
        r"^scan\s*(jobs?|for jobs?)?\s*$",
        r"^(start|run)\s*(scan|scanning|autopilot|auto.?pilot)\s*$",
        r"^(find|search for|look for|check for)\s*new\s*jobs?\s*$",
        r"^(find|search for)\s*jobs?\s*$",
    ]),
    (Intent.APPROVE_JOBS, [
        r"^apply\s+([\d,\s\-]+|all)\s*$",
        r"^approve\s+([\d,\s\-]+|all)\s*$",
    ]),
    (Intent.REJECT_JOB, [
        r"^(reject|skip|pass on|pass)\s+(\d+)\s*$",
    ]),
    (Intent.JOB_DETAIL, [
        r"^job\s+(\d+)\s*$",
        r"^details?\s+(\d+)\s*$",
    ]),
    (Intent.JOB_STATS, [
        r"(job|application|apply|applied)\s*(stats?|statistics|numbers|metrics|count)",
        r"how many (applied|applications|jobs)",
    ]),
    (Intent.SEARCH_CONFIG, [
        r"^search:\s*(.+)",
        r"^job search:\s*(.+)",
    ]),
    (Intent.PAUSE_JOBS, [
        r"^(pause|stop)\s*(jobs?|applying|autopilot|auto.?pilot)\s*$",
    ]),
    (Intent.RESUME_JOBS, [
        r"^(resume|start|unpause)\s*(jobs?|applying|autopilot|auto.?pilot)\s*$",
    ]),
    (Intent.SHOW_JOBS, [
        r"^(jobs?|show jobs?|new jobs?|available jobs?|what.?s available)\s*$",
        r"(pending|review)\s*jobs?\s*$",
    ]),
    (Intent.ENGINE_STATS, [
        r"(engine|ab|a/b|a-b)\s*(stats?|results?|comparison|compare)\s*(\d+)?",
        r"job engine stats?",
    ]),
    (Intent.ENGINE_COMPARE, [
        r"engine compare\s*(.*)",
        r"compare engines?\s*(.*)",
        r"per.?platform\s*(breakdown)?",
    ]),
    (Intent.ENGINE_LEARNING, [
        r"engine learning",
        r"engine (curve|trend|progress)",
    ]),
    (Intent.ENGINE_RESET, [
        r"engine reset",
        r"(clear|reset)\s*(ab|a/b|engine)\s*(data|tracking)?",
    ]),
    # Briefing
    (Intent.BRIEFING, [
        r"(briefing|morning update|daily update|send briefing|full report|summary of today)",
    ]),
    # Priority tasks (!! or ! prefix)
    (Intent.CREATE_TASKS, [
        r"^!!.+",
        r"^!(?!!)\s*.+",
    ]),
    # Complete task (requires colon/prefix — "done: X", "mark: X", "complete: X")
    (Intent.COMPLETE_TASK, [
        r"(mark|done|complete|completed|checked)[:\s]+(.+)",
        r"^done[:\s]+(.+)",
        r"^✅\s*(.+)",
    ]),
    # Remove task
    (Intent.REMOVE_TASK, [
        r"(remove|delete|drop|cancel)[:\s]+(.+)",
        r"^🗑️?\s*(.+)",
    ]),
    # Show tasks
    (Intent.SHOW_TASKS, [
        r"(show|list|view|get|see|display|fetch)\s+(my\s+)?(tasks?|todo|to.?do|checklist)",
        r"^/?(my\s+)?(tasks?|todo|to.?do|checklist)\s*$",
        r"what.+(tasks?|todo|to.?do)",
        r"what (do i|should i|have i).+(today|do)",
    ]),
    # Create tasks (multi-line or prefixed)
    (Intent.CREATE_TASKS, [
        r"^(add|new|create)\s+(task|todo)[s:]?\s*(.+)",
        r"^task[s:]?\s+(.+)",
    ]),
    # Calendar
    (Intent.CALENDAR, [
        r"(calendar|schedule|what.?s (on )?today|what.?s (on )?tomorrow|events?|my day)",
        r"(today.?s|tomorrow.?s)\s+(calendar|schedule|events?)",
    ]),
    # Create event
    (Intent.CREATE_EVENT, [
        r"(remind|set event|add event|schedule|book)\s+(me\s+)?(at|for|to)\s+(.+)",
        r"(remind me|set reminder|add reminder)\s+(.+)",
    ]),
    # Gmail
    (Intent.GMAIL, [
        r"(email|mail|inbox|recruiter|gmail|check (my )?mail)",
        r"any.+(email|mail|recruiter|interview)",
    ]),
    # GitHub commits
    (Intent.GITHUB, [
        r"(commit|github|push|what did i (push|commit)|yesterday.?s (code|commits?))",
        r"(how many|my) commits?",
    ]),
    # Trending
    (Intent.TRENDING, [
        r"(trending|hot repos?|popular repos?|github trending|top repos?)",
    ]),
    # arXiv
    (Intent.ARXIV, [
        r"(arxiv|research papers?|ai papers?|latest papers?|top papers?|weekly papers?)",
        r"^(and\s+)?papers?\s*$",
        r"^paper\s+\d+",
        r"^blog\s+\d+",
        r"^regenerate\s+\d+",
        r"^read\s+\d+",
        r"(papers?|reading)\s+stats?",
    ]),
]


def classify_rule_based(text: str) -> Optional[ParsedCommand]:
    """Try to match message against rule-based patterns. Returns None if no match."""
    text_lower = text.lower().strip()

    for intent, patterns in PATTERNS:
        for pattern in patterns:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                # Extract args from capture groups if available
                args = match.group(match.lastindex) if match.lastindex else ""
                return ParsedCommand(intent=intent, args=args.strip(), raw=text)

    return None


def classify_llm(text: str) -> ParsedCommand:
    """Use LLM to classify ambiguous messages. Fallback when rules don't match."""
    try:
        from shared.agents import get_openai_client, get_model_name, is_local_llm

        client = get_openai_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": f"""Classify this Telegram message into ONE intent:

CREATE_TASKS — user wants to add tasks/todos
SHOW_TASKS — user wants to see their tasks
COMPLETE_TASK — user wants to mark a task as done
REMOVE_TASK — user wants to delete or remove a task
CALENDAR — user wants to see their schedule
CREATE_EVENT — user wants to add a calendar event or reminder
GMAIL — user wants to check email or recruiter updates
GITHUB — user wants to see commits or code activity
TRENDING — user wants trending GitHub repos
BRIEFING — user wants the full morning briefing
ARXIV — user wants AI research papers
LOG_SPEND — user is logging money they spent (mentions amount + item)
LOG_INCOME — user is logging money they earned/received
LOG_SAVINGS — user is logging money saved or invested or debt repaid
SET_BUDGET — user wants to set a planned budget for a category
SHOW_BUDGET — user wants to see their budget/spending summary
WEEKLY_REPORT — user wants a weekly summary report
EXPORT — user wants to export or back up data
CONVERSATION — user is chatting, asking a question, greeting, or having a general conversation
REMOTE_SHELL — user wants to run a shell command (prefixed with run:, shell:, cmd:, exec:, or $)
GIT_OPS — user wants git status, log, diff, branch, commit, or push
FILE_OPS — user wants to view a file, see logs, see errors, or paginate
SYSTEM_STATUS — user wants system/daemon health status
CLEAR_CHAT — user wants to clear chat history or start a new conversation
STOP — user wants to undo/reverse/cancel their last command (said "stop", "cancel", "undo that", "oops", "nope", "take that back")
SCAN_JOBS — user wants to scan for new jobs, run the autopilot, or start a job search
SHOW_JOBS — user wants to see available/pending job applications
APPROVE_JOBS — user wants to approve specific jobs for application (e.g., "apply 1,3,5" or "apply all")
REJECT_JOB — user wants to skip/reject a specific job (e.g., "reject 3")
JOB_STATS — user wants job application statistics
SEARCH_CONFIG — user wants to modify job search settings (e.g., "search: add title X")
PAUSE_JOBS — user wants to pause the job autopilot
RESUME_JOBS — user wants to resume the job autopilot
JOB_DETAIL — user wants details on a specific job number
UNKNOWN — doesn't match any of the above

Message: "{text}"

Respond with ONLY the intent name. Nothing else."""}],
            max_tokens=60 if is_local_llm() else 15,
            temperature=0,
        )
        intent_str = response.choices[0].message.content.strip().upper()

        # Map to enum
        try:
            intent = Intent(intent_str.lower())
        except ValueError:
            # Try partial match
            for i in Intent:
                if i.value.upper() in intent_str:
                    intent = i
                    break
            else:
                intent = Intent.UNKNOWN

        return ParsedCommand(intent=intent, args=text, raw=text)

    except Exception as e:
        logger.error("LLM classification failed: %s", e)
        return ParsedCommand(intent=Intent.UNKNOWN, args=text, raw=text)


def is_task_list(text: str) -> bool:
    """Detect if a multi-line message is a task list (no explicit command prefix)."""
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return False
    # If most lines are short (< 80 chars) and there are 2+, treat as tasks
    short_lines = sum(1 for l in lines if len(l) < 80)
    return short_lines >= len(lines) * 0.7


def classify(text: str) -> ParsedCommand:
    """Main entry: classify a Telegram message into an intent.

    3-Tier classification:
      1. Rule-based regex (instant, free) — exact commands
      2. Semantic NLP embeddings (5ms, free) — natural language
      3. LLM fallback (~1s, $0.001) — truly ambiguous
    """
    # Strip bot mentions
    text = re.sub(r"@\w+bot\s*", "", text, flags=re.IGNORECASE).strip()

    # Strip trailing punctuation added by voice transcription (Whisper)
    text = re.sub(r"[.!?]+$", "", text).strip()

    if not text:
        return ParsedCommand(intent=Intent.UNKNOWN, args="", raw="")

    # Tier 1: Rule-based regex (free, instant)
    result = classify_rule_based(text)
    if result:
        return result

    # Multi-line messages without command prefix → probably tasks
    if is_task_list(text):
        return ParsedCommand(intent=Intent.CREATE_TASKS, args=text, raw=text)

    # Tier 2: Semantic NLP classifier (free, 5ms, local)
    try:
        from jobpulse.nlp_classifier import classify_semantic, GOOD_CONFIDENCE
        intent_name, confidence = classify_semantic(text)
        if confidence >= GOOD_CONFIDENCE and intent_name != "unknown":
            try:
                intent = Intent(intent_name)
                logger.debug("NLP Tier 2: '%s' → %s (%.3f)", text[:50], intent_name, confidence)
                return ParsedCommand(intent=intent, args=text, raw=text)
            except ValueError:
                pass  # invalid intent name, fall through
    except ImportError:
        pass  # sentence-transformers not installed, skip Tier 2

    # Tier 3: LLM fallback (~1s, $0.001)
    result = classify_llm(text)

    # Learn from LLM for future Tier 2 matches
    if result.intent not in (Intent.UNKNOWN, Intent.CONVERSATION):
        try:
            from jobpulse.nlp_classifier import add_learned_example
            add_learned_example(result.intent.value, text)
        except ImportError:
            pass

    if result.intent == Intent.UNKNOWN:
        return ParsedCommand(intent=Intent.CONVERSATION, args=text, raw=text)
    return result
