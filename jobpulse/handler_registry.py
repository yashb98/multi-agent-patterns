"""Shared handler registry — single source of truth for intent-to-handler mapping.

Both dispatcher.py (flat) and swarm_dispatcher.py (enhanced swarm) import from
here, eliminating the duplicated AGENT_MAP that previously caused production bugs
when one was updated without the other.

When adding a new intent:
1. Add the handler function to the appropriate handler module
2. Import it here and add to HANDLER_MAP
3. Both dispatchers automatically pick it up
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from jobpulse.command_router import Intent

if TYPE_CHECKING:
    from jobpulse.command_router import ParsedCommand


def _build_handler_map() -> dict[Intent, Callable[["ParsedCommand"], str]]:
    """Build the intent→handler map with lazy imports to avoid circular deps."""
    # All handlers live in dispatcher.py — import them lazily
    from jobpulse.dispatcher import (
        _handle_show_tasks, _handle_create_tasks, _handle_calendar,
        _handle_gmail, _handle_github, _handle_trending, _handle_briefing,
        _handle_arxiv, _handle_complete_task, _handle_remove_task,
        _handle_create_event,
        _handle_log_spend, _handle_log_income, _handle_log_savings,
        _handle_set_budget, _handle_show_budget,
        _handle_help, _handle_weekly_report, _handle_export,
        _handle_conversation, _handle_clear_chat,
        _handle_remote_shell, _handle_git_ops, _handle_file_ops,
        _handle_system_status,
        _handle_log_hours, _handle_show_hours, _handle_confirm_savings,
        _handle_undo_hours, _handle_undo_budget, _handle_recurring_budget,
        _handle_weekly_plan,
        _handle_scan_jobs, _handle_show_jobs, _handle_approve_jobs,
        _handle_reject_job, _handle_job_stats, _handle_search_config,
        _handle_pause_jobs, _handle_resume_jobs, _handle_job_detail,
        _handle_engine_stats, _handle_engine_compare, _handle_engine_learning,
        _handle_engine_reset,
        _handle_job_patterns, _handle_follow_ups, _handle_interview_prep,
    )

    return {
        Intent.SHOW_TASKS: _handle_show_tasks,
        Intent.CREATE_TASKS: _handle_create_tasks,
        Intent.CALENDAR: _handle_calendar,
        Intent.GMAIL: _handle_gmail,
        Intent.GITHUB: _handle_github,
        Intent.TRENDING: _handle_trending,
        Intent.BRIEFING: _handle_briefing,
        Intent.ARXIV: _handle_arxiv,
        Intent.COMPLETE_TASK: _handle_complete_task,
        Intent.REMOVE_TASK: _handle_remove_task,
        Intent.CREATE_EVENT: _handle_create_event,
        Intent.LOG_SPEND: _handle_log_spend,
        Intent.LOG_INCOME: _handle_log_income,
        Intent.LOG_SAVINGS: _handle_log_savings,
        Intent.SET_BUDGET: _handle_set_budget,
        Intent.SHOW_BUDGET: _handle_show_budget,
        Intent.LOG_HOURS: _handle_log_hours,
        Intent.SHOW_HOURS: _handle_show_hours,
        Intent.CONFIRM_SAVINGS: _handle_confirm_savings,
        Intent.UNDO_HOURS: _handle_undo_hours,
        Intent.UNDO_BUDGET: _handle_undo_budget,
        Intent.RECURRING_BUDGET: _handle_recurring_budget,
        Intent.WEEKLY_PLAN: _handle_weekly_plan,
        Intent.HELP: _handle_help,
        Intent.WEEKLY_REPORT: _handle_weekly_report,
        Intent.EXPORT: _handle_export,
        Intent.CONVERSATION: _handle_conversation,
        Intent.CLEAR_CHAT: _handle_clear_chat,
        Intent.REMOTE_SHELL: _handle_remote_shell,
        Intent.GIT_OPS: _handle_git_ops,
        Intent.FILE_OPS: _handle_file_ops,
        Intent.SYSTEM_STATUS: _handle_system_status,
        Intent.SCAN_JOBS: _handle_scan_jobs,
        Intent.SHOW_JOBS: _handle_show_jobs,
        Intent.APPROVE_JOBS: _handle_approve_jobs,
        Intent.REJECT_JOB: _handle_reject_job,
        Intent.JOB_STATS: _handle_job_stats,
        Intent.SEARCH_CONFIG: _handle_search_config,
        Intent.PAUSE_JOBS: _handle_pause_jobs,
        Intent.RESUME_JOBS: _handle_resume_jobs,
        Intent.JOB_DETAIL: _handle_job_detail,
        Intent.ENGINE_STATS: _handle_engine_stats,
        Intent.ENGINE_COMPARE: _handle_engine_compare,
        Intent.ENGINE_LEARNING: _handle_engine_learning,
        Intent.ENGINE_RESET: _handle_engine_reset,
        Intent.JOB_PATTERNS: _handle_job_patterns,
        Intent.FOLLOW_UPS: _handle_follow_ups,
        Intent.INTERVIEW_PREP: _handle_interview_prep,
    }


def get_handler_map() -> dict[Intent, Callable[["ParsedCommand"], str]]:
    """Return the intent→handler map. Built fresh each call to support test patching."""
    return _build_handler_map()


def get_handler_map_by_value() -> dict[str, Callable[["ParsedCommand"], str]]:
    """Return intent.value→handler map (used by swarm_dispatcher)."""
    return {intent.value: handler for intent, handler in get_handler_map().items()}
