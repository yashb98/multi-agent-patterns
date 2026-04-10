"""Last action tracker — records what each command changed so 'stop' can undo it.

Stores the most recent action per bot. When user types 'stop', the appropriate
undo function is called to reverse the side effects (SQLite + Notion).
"""

import json
from datetime import datetime
from pathlib import Path
from shared.logging_config import get_logger

logger = get_logger(__name__)

from jobpulse.config import DATA_DIR

_LAST_ACTION_FILE = DATA_DIR / "last_action.json"

# Intents that have reversible side effects
UNDOABLE_INTENTS = {
    "log_spend", "log_income", "log_savings",  # budget transactions
    "log_hours",                                # salary/hours entries
    "create_tasks",                             # Notion task creation
    "complete_task",                            # Notion task check
    "set_budget",                               # budget plan change
}


def save_last_action(intent: str, raw_text: str, reply: str):
    """Record the last action so it can be undone with 'stop'."""
    if intent not in UNDOABLE_INTENTS:
        return

    action = {
        "intent": intent,
        "raw_text": raw_text,
        "reply": reply[:200],
        "timestamp": datetime.now().isoformat(),
    }

    try:
        _LAST_ACTION_FILE.write_text(json.dumps(action, indent=2))
    except Exception as e:
        logger.warning("Failed to save last action: %s", e)


def get_last_action() -> dict | None:
    """Get the last undoable action, or None if nothing to undo."""
    try:
        data = json.loads(_LAST_ACTION_FILE.read_text())
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def clear_last_action():
    """Clear the last action after it's been undone."""
    try:
        _LAST_ACTION_FILE.unlink(missing_ok=True)
    except OSError as e:
        logger.debug("Failed to clear last action file: %s", e)


def undo_last_action() -> str:
    """Undo the last command's side effects. Returns a Telegram reply string."""
    action = get_last_action()
    if not action:
        return "Nothing to undo."

    intent = action["intent"]
    raw = action.get("raw_text", "")
    ts = action.get("timestamp", "")

    try:
        if intent in ("log_spend", "log_income", "log_savings"):
            from jobpulse.budget_agent import undo_last_transaction
            result = undo_last_transaction(pick=1)
            clear_last_action()
            return f"⏪ Undone: {raw}\n\n{result}"

        elif intent == "log_hours":
            from jobpulse.budget_agent import undo_hours
            result = undo_hours(pick=1)
            clear_last_action()
            return f"⏪ Undone: {raw}\n\n{result}"

        elif intent == "create_tasks":
            # Remove the tasks that were just created
            from jobpulse.notion_agent import remove_task
            # Parse task names from the original text
            tasks = _parse_task_names(raw)
            results = []
            for task in tasks:
                r = remove_task(task)
                results.append(r)
            clear_last_action()
            if results:
                return f"⏪ Undone task creation:\n" + "\n".join(results)
            return "⏪ Could not find the tasks to remove."

        elif intent == "complete_task":
            # Uncheck the task that was just completed
            from jobpulse.notion_agent import uncomplete_task
            task_name = raw
            # Strip "mark done" type prefixes
            for prefix in ["mark ", "done ", "done: ", "complete ", "complete: ", "completed "]:
                if task_name.lower().startswith(prefix):
                    task_name = task_name[len(prefix):]
                    break
            result = uncomplete_task(task_name.strip())
            clear_last_action()
            return f"⏪ Undone: {raw}\n\n{result}"

        elif intent == "set_budget":
            clear_last_action()
            return "⏪ Budget plan changes can't be auto-undone. Use 'set budget <category> <amount>' to adjust."

        else:
            return f"Don't know how to undo '{intent}'."

    except Exception as e:
        logger.error("Undo failed: %s", e)
        from shared.agent_result import DispatchError, classify_error
        cat, retry = classify_error(e)
        return DispatchError(cat, str(e), retry, agent_name="last_action",
                             attempted_action="undo").to_user_message()


def _parse_task_names(text: str) -> list[str]:
    """Extract task names from a create_tasks message."""
    tasks = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Strip list prefixes
        for prefix in ["- ", "• ", "□ ", "* ", "✅ ", "☐ "]:
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        # Strip number prefixes like "1. " or "2) "
        if len(line) > 2 and line[0].isdigit() and line[1] in ".) ":
            line = line[2:].strip()
        # Strip "add task" prefix
        for prefix in ["add task ", "add ", "new task ", "task "]:
            if line.lower().startswith(prefix):
                line = line[len(prefix):].strip()
        if line and len(line) > 2:
            tasks.append(line)
    return tasks
