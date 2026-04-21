"""Output sanitization — strip dangerous tags, wrap in XML boundaries."""

from __future__ import annotations

import re

from shared.logging_config import get_logger

logger = get_logger(__name__)

_DANGEROUS_TAG_PATTERN = re.compile(
    r'</?(user_input|system|assistant|instruction|agent_output|script)[^>]*>',
    flags=re.IGNORECASE,
)

SANITIZE_FIELDS = frozenset({"draft", "research_notes", "feedback", "review", "agent_response"})


def strip_dangerous_tags(text: str) -> str:
    """Remove dangerous XML/HTML tags that could enable cross-agent injection."""
    return _DANGEROUS_TAG_PATTERN.sub("", text)


def sanitize_agent_output(text: str, agent_name: str) -> str:
    """Strip dangerous tags from agent output and wrap in XML boundary."""
    if not text:
        return ""
    cleaned = strip_dangerous_tags(text)
    return f'<agent_output from="{agent_name}">\n{cleaned}\n</agent_output>'


def create_state_sanitizer(agent_name: str):
    """Return a function that sanitizes SANITIZE_FIELDS in a state dict."""
    def sanitize_state(state_update: dict) -> dict:
        result = {}
        for key, value in state_update.items():
            if key in SANITIZE_FIELDS and isinstance(value, str):
                result[key] = sanitize_agent_output(value, agent_name)
            elif key in SANITIZE_FIELDS and isinstance(value, list):
                result[key] = [
                    sanitize_agent_output(v, agent_name) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                result[key] = value
        return result
    return sanitize_state
