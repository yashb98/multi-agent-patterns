"""Centralized prompt registry for all LLM prompts in the system.

Provides versioned, typed prompt templates with few-shot example management
and input validation. All LLM prompts should be defined here rather than
hardcoded in agent modules.

Also exports legacy orchestration prompts for backward compatibility.

Usage:
    from shared.prompts import get_prompt, list_prompts, RESEARCHER_PROMPT

    prompt = get_prompt("jobpulse", "skill_extraction")
    call_params = prompt.render(jd_text="We need a Python developer...")
"""

# New registry
from shared.prompts.registry import (
    get_prompt,
    list_prompts,
    reload_registry,
    PromptTemplate,
    PromptRenderError,
    PromptNotFoundError,
)

# Legacy orchestration prompts (backward compatibility)
from shared.prompts.orchestration import (
    RESEARCHER_PROMPT,
    WRITER_PROMPT,
    REVIEWER_PROMPT,
    SUPERVISOR_PROMPT,
    DEBATE_MODERATOR_PROMPT,
)

__all__ = [
    # Registry
    "get_prompt",
    "list_prompts",
    "reload_registry",
    "PromptTemplate",
    "PromptRenderError",
    "PromptNotFoundError",
    # Legacy prompts
    "RESEARCHER_PROMPT",
    "WRITER_PROMPT",
    "REVIEWER_PROMPT",
    "SUPERVISOR_PROMPT",
    "DEBATE_MODERATOR_PROMPT",
]
