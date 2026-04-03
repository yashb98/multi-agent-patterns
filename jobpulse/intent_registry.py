"""Canonical intent registry — single source of truth for intent groupings.

Both dispatcher.py and swarm_dispatcher.py import from here so that new intents
only need to be added in ONE place (plus the handler function itself).

Usage in dispatcher.py:
    from jobpulse.intent_registry import INTENT_HANDLER_NAMES, JOBS_INTENTS, BUDGET_INTENTS

Usage in swarm_dispatcher.py:
    from jobpulse.intent_registry import SIMPLE_INTENTS, JOBS_INTENTS, BUDGET_INTENTS
"""

from jobpulse.command_router import Intent

# ---------------------------------------------------------------------------
# Intent groups (used by both dispatchers)
# ---------------------------------------------------------------------------

JOBS_INTENTS: frozenset[Intent] = frozenset({
    Intent.SCAN_JOBS,
    Intent.SHOW_JOBS,
    Intent.APPROVE_JOBS,
    Intent.REJECT_JOB,
    Intent.JOB_DETAIL,
    Intent.JOB_STATS,
    Intent.SEARCH_CONFIG,
    Intent.PAUSE_JOBS,
    Intent.RESUME_JOBS,
})

BUDGET_INTENTS: frozenset[Intent] = frozenset({
    Intent.LOG_SPEND,
    Intent.LOG_INCOME,
    Intent.LOG_SAVINGS,
    Intent.SET_BUDGET,
    Intent.SHOW_BUDGET,
    Intent.UNDO_BUDGET,
    Intent.RECURRING_BUDGET,
})

HOURS_INTENTS: frozenset[Intent] = frozenset({
    Intent.LOG_HOURS,
    Intent.SHOW_HOURS,
    Intent.CONFIRM_SAVINGS,
    Intent.UNDO_HOURS,
})

TASK_INTENTS: frozenset[Intent] = frozenset({
    Intent.SHOW_TASKS,
    Intent.CREATE_TASKS,
    Intent.COMPLETE_TASK,
    Intent.REMOVE_TASK,
    Intent.WEEKLY_PLAN,
})

SYSTEM_INTENTS: frozenset[Intent] = frozenset({
    Intent.REMOTE_SHELL,
    Intent.GIT_OPS,
    Intent.FILE_OPS,
    Intent.SYSTEM_STATUS,
    Intent.CLEAR_CHAT,
    Intent.HELP,
})

# Intents that map to a single agent with no swarm overhead
SIMPLE_INTENTS: frozenset[Intent] = (
    TASK_INTENTS
    | HOURS_INTENTS
    | JOBS_INTENTS
    | SYSTEM_INTENTS
    | frozenset({
        Intent.CREATE_EVENT,
        Intent.SHOW_BUDGET,
        Intent.CONVERSATION,
    })
)

# ---------------------------------------------------------------------------
# Canonical list of all dispatchable intents (in logical order)
# Used by both dispatchers to build their AGENT_MAP.
# ---------------------------------------------------------------------------

ALL_HANDLER_INTENTS: list[Intent] = [
    Intent.SHOW_TASKS,
    Intent.CREATE_TASKS,
    Intent.CALENDAR,
    Intent.GMAIL,
    Intent.GITHUB,
    Intent.TRENDING,
    Intent.BRIEFING,
    Intent.ARXIV,
    Intent.COMPLETE_TASK,
    Intent.REMOVE_TASK,
    Intent.CREATE_EVENT,
    Intent.LOG_SPEND,
    Intent.LOG_INCOME,
    Intent.LOG_SAVINGS,
    Intent.SET_BUDGET,
    Intent.SHOW_BUDGET,
    Intent.LOG_HOURS,
    Intent.SHOW_HOURS,
    Intent.CONFIRM_SAVINGS,
    Intent.UNDO_HOURS,
    Intent.UNDO_BUDGET,
    Intent.RECURRING_BUDGET,
    Intent.WEEKLY_PLAN,
    Intent.HELP,
    Intent.WEEKLY_REPORT,
    Intent.EXPORT,
    Intent.CONVERSATION,
    Intent.CLEAR_CHAT,
    Intent.REMOTE_SHELL,
    Intent.GIT_OPS,
    Intent.FILE_OPS,
    Intent.SYSTEM_STATUS,
    # Job autopilot
    Intent.SCAN_JOBS,
    Intent.SHOW_JOBS,
    Intent.APPROVE_JOBS,
    Intent.REJECT_JOB,
    Intent.JOB_STATS,
    Intent.SEARCH_CONFIG,
    Intent.PAUSE_JOBS,
    Intent.RESUME_JOBS,
    Intent.JOB_DETAIL,
]
