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


class Intent(str, Enum):
    CREATE_TASKS = "create_tasks"
    SHOW_TASKS = "show_tasks"
    COMPLETE_TASK = "complete_task"
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
    UNKNOWN = "unknown"


@dataclass
class ParsedCommand:
    intent: Intent
    args: str  # remaining text after intent keywords stripped
    raw: str   # original message


# ── Rule-based patterns (checked in order, first match wins) ──

PATTERNS: list[tuple[Intent, list[str]]] = [
    # Help
    (Intent.HELP, [
        r"^/?(help|commands|menu|what can you do)$",
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
        r"(spending|how much.+(spent|spend|earned)|weekly (budget|spend)|show budget|show spending|summary)",
        r"(today.?s|this week.?s)\s+(spend|budget|expenses?|money)",
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
    # Briefing
    (Intent.BRIEFING, [
        r"(briefing|morning update|daily update|send briefing|full report|summary of today)",
    ]),
    # Complete task
    (Intent.COMPLETE_TASK, [
        r"(mark|done|complete|finish|completed|checked)[:\s]+(.+)",
        r"^done[:\s]+(.+)",
        r"^✅\s*(.+)",
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
        r"(arxiv|research paper|ai paper|latest paper|top paper|weekly paper)",
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
        from openai import OpenAI
        from jobpulse.config import OPENAI_API_KEY

        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""Classify this Telegram message into ONE intent:

CREATE_TASKS — user wants to add tasks/todos
SHOW_TASKS — user wants to see their tasks
COMPLETE_TASK — user wants to mark a task as done
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
UNKNOWN — doesn't match any of the above

Message: "{text}"

Respond with ONLY the intent name. Nothing else."""}],
            max_tokens=15,
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
        print(f"[Router] LLM classification failed: {e}")
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

    Order:
      1. Rule-based pattern matching
      2. Multi-line task detection
      3. LLM fallback
    """
    # Strip bot mentions
    text = re.sub(r"@\w+bot\s*", "", text, flags=re.IGNORECASE).strip()

    if not text:
        return ParsedCommand(intent=Intent.UNKNOWN, args="", raw="")

    # Try rules first (free, instant)
    result = classify_rule_based(text)
    if result:
        return result

    # Multi-line messages without command prefix → probably tasks
    if is_task_list(text):
        return ParsedCommand(intent=Intent.CREATE_TASKS, args=text, raw=text)

    # LLM fallback (costs ~$0.001)
    return classify_llm(text)
