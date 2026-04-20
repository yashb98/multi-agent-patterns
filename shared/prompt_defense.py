"""Prompt injection defense — input boundary markers and sanitization.

Wraps untrusted user/agent input in XML delimiters before injection into
LLM prompts. This makes it harder for injected instructions to be
interpreted as system-level directives.
"""

import re
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Max length for user input in prompts (prevent context flooding)
MAX_USER_INPUT_LENGTH = 8000


def sanitize_user_input(text: str, source: str = "user") -> str:
    """Wrap untrusted input in XML boundary markers.

    Args:
        text: The untrusted input text
        source: Label for the source (e.g., "user", "telegram", "jd_text", "agent_output")

    Returns:
        Text wrapped in XML delimiters with basic sanitization
    """
    if not text:
        return ""

    # Truncate to prevent context flooding
    if len(text) > MAX_USER_INPUT_LENGTH:
        text = text[:MAX_USER_INPUT_LENGTH] + "\n[TRUNCATED]"
        logger.warning("Input from %s truncated to %d chars", source, MAX_USER_INPUT_LENGTH)

    # Strip any existing XML-like boundary markers that could confuse parsing
    text = re.sub(r'</?(user_input|system|assistant|instruction|agent_output)[^>]*>', '', text, flags=re.IGNORECASE)

    return f"<user_input source=\"{source}\">\n{text}\n</user_input>"


def wrap_agent_output(text: str, agent_name: str) -> str:
    """Wrap agent output before passing to another agent.

    In multi-agent systems, one agent's output is another's input.
    This prevents a compromised agent from injecting instructions.
    """
    if not text:
        return ""

    # Strip any existing markers
    text = re.sub(r'</?(user_input|system|assistant|instruction|agent_output)[^>]*>', '', text, flags=re.IGNORECASE)

    return f"<agent_output from=\"{agent_name}\">\n{text}\n</agent_output>"
