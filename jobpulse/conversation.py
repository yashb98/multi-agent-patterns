"""Conversation mode — free-form chat with an LLM that knows your project."""

import os
from datetime import datetime
from shared.logging_config import get_logger
from jobpulse.config import OPENAI_API_KEY, PROJECT_DIR

logger = get_logger(__name__)

# In-memory conversation history (resets on daemon restart)
_history: list[dict] = []
MAX_HISTORY = 10


def _build_system_prompt() -> str:
    """Build system prompt with project context and recent activity."""
    # Static project context (loaded once)
    project_context = ""
    claude_md = PROJECT_DIR / "CLAUDE.md"
    if claude_md.exists():
        project_context = claude_md.read_text()[:2000]

    # Recent agent activity — log failures instead of swallowing them
    recent_activity = ""
    try:
        from jobpulse.event_logger import get_events_for_day
        events = get_events_for_day(datetime.now().strftime("%Y-%m-%d"))
        if events:
            lines = []
            for e in events[-5:]:
                lines.append(f"- [{e.get('event_type')}] {e.get('agent_name')}: {e.get('content', '')[:100]}")
            recent_activity = "\nRecent agent activity today:\n" + "\n".join(lines)
    except Exception as e:
        logger.debug("Could not load recent activity for conversation context: %s", e)
        recent_activity = "\nRecent activity: unavailable (event logger error)"

    # Current status — propagate error context instead of silent suppression
    status_info = ""
    try:
        from jobpulse.healthcheck import check_daemon_health
        health = check_daemon_health()
        status_info = f"\nDaemon: {'alive' if health['alive'] else 'DOWN'} (last heartbeat: {health.get('age_minutes', '?')}min ago)"
    except Exception as e:
        logger.debug("Could not check daemon health for conversation context: %s", e)
        status_info = "\nDaemon status: unknown (healthcheck unavailable)"

    return f"""You are Yash's personal AI assistant running on his Mac via the JobPulse system.
You have access to his project context and recent agent activity.
Answer questions helpfully and concisely. Use emoji sparingly.
If asked to do something you can't do in chat, suggest the right Telegram command.

Available commands the user can type:
- "tasks" / "calendar" / "emails" / "commits" / "trending" / "budget" / "briefing" / "papers"
- "weekly report" / "export" / "help"
- "run: <command>" for shell commands (coming soon)
- "git status" / "git log" / "commit: <msg>" / "push" (coming soon)
- "show: <filepath>" / "logs" / "errors" / "status" (coming soon)

PROJECT CONTEXT:
{project_context}
{recent_activity}
{status_info}

Current time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"""


def chat(user_message: str) -> str:
    """Send a message to the conversational LLM and get a reply."""
    global _history

    if not OPENAI_API_KEY:
        return "Conversation mode needs OPENAI_API_KEY to be set."

    # Add user message to history
    _history.append({"role": "user", "content": user_message})

    # Trim history
    if len(_history) > MAX_HISTORY * 2:
        _history = _history[-MAX_HISTORY * 2:]

    try:
        from shared.agents import get_openai_client
        client = get_openai_client()

        _default_model = "gpt-4.1-mini"
        model = os.getenv("CONVERSATION_MODEL", _default_model)
        # When using local LLM, swap the default model for the local one
        if os.getenv("LLM_PROVIDER", "openai").lower() == "local" and model == _default_model:
            model = os.getenv("LOCAL_LLM_MODEL", "gemma4:31b")

        messages = [{"role": "system", "content": _build_system_prompt()}]
        messages.extend(_history)

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=500,
            temperature=0.7,
        )

        reply = response.choices[0].message.content.strip()

        # Add assistant reply to history
        _history.append({"role": "assistant", "content": reply})

        logger.debug("Conversation: user='%s' reply='%s'", user_message[:50], reply[:50])
        return reply

    except Exception as e:
        logger.error("Conversation error: %s", e)
        return f"Sorry, I couldn't process that: {e}"


def clear_history():
    """Clear conversation history."""
    global _history
    _history.clear()
    logger.info("Conversation history cleared")
    return "Conversation history cleared."
