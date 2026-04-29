"""Centralized prompt registry with versioning, validation, and few-shot management.

Usage:
    from shared.prompts import get_prompt

    prompt = get_prompt("jobpulse", "skill_extraction", version="latest")
    rendered = prompt.render(jd_text="...")

    # With few-shot examples
    prompt = get_prompt("jobpulse", "field_mapping")
    few_shot = prompt.few_shot_examples(query="salary expectation", k=3)
    rendered = prompt.render(fields=[...], profile={...}, examples=few_shot)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from shared.logging_config import get_logger

logger = get_logger(__name__)

_REGISTRY: dict[str, dict[str, "PromptTemplate"]] = {}
_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class PromptTemplate:
    """A versioned prompt template with schema validation."""

    name: str
    domain: str
    version: str
    system_prompt: str
    user_prompt_template: str
    output_schema: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    temperature: float = 0.0
    max_tokens: int = 2000
    response_format: str | None = None  # "json_object" or None
    few_shot_examples: list[dict[str, str]] = field(default_factory=list)

    def render(self, **kwargs: Any) -> dict[str, Any]:
        """Render the prompt with variables substituted.

        Returns a dict ready for LLM calls:
            {"messages": [{"role": "system", ...}, {"role": "user", ...}],
             "temperature": ..., "max_tokens": ..., "response_format": ...}
        """
        # Validate required inputs
        self._validate_inputs(kwargs)

        user_content = self.user_prompt_template.format(**kwargs)

        messages: list[dict[str, str]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        # Inject few-shot examples if provided
        examples = kwargs.get("examples", self.few_shot_examples)
        if examples:
            for ex in examples:
                messages.append({"role": "user", "content": ex.get("input", "")})
                messages.append({"role": "assistant", "content": ex.get("output", "")})

        messages.append({"role": "user", "content": user_content})

        result: dict[str, Any] = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.response_format:
            result["response_format"] = {"type": self.response_format}
        return result

    def few_shot_examples_for(self, query: str, k: int = 3) -> list[dict[str, str]]:
        """Retrieve the k most relevant few-shot examples for a query.

        Simple keyword overlap ranking. Override with embedding-based
        retrieval for production use.
        """
        if not self.few_shot_examples:
            return []
        query_words = set(query.lower().split())

        scored = []
        for ex in self.few_shot_examples:
            text = (ex.get("input", "") + " " + ex.get("output", "")).lower()
            score = len(query_words & set(text.split()))
            scored.append((score, ex))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ex for _, ex in scored[:k]]

    def _validate_inputs(self, kwargs: dict[str, Any]) -> None:
        """Check that all required template variables are provided."""
        # Extract {var_name} patterns from template
        required = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", self.user_prompt_template))
        provided = set(kwargs.keys())
        missing = required - provided
        if missing:
            raise PromptRenderError(
                f"Prompt '{self.name}' missing required variables: {missing}"
            )


class PromptRenderError(ValueError):
    """Raised when prompt rendering fails due to missing variables."""


class PromptNotFoundError(KeyError):
    """Raised when a requested prompt is not in the registry."""


def _load_yaml_templates() -> None:
    """Load all YAML prompt templates from the templates directory."""
    if _REGISTRY:
        return  # Already loaded

    for domain_dir in _TEMPLATES_DIR.iterdir():
        if not domain_dir.is_dir():
            continue
        domain = domain_dir.name
        _REGISTRY[domain] = {}

        for prompt_file in domain_dir.glob("*.yaml"):
            try:
                with open(prompt_file) as f:
                    data = yaml.safe_load(f)

                if not data or "name" not in data:
                    logger.warning("Skipping invalid prompt file: %s", prompt_file)
                    continue

                template = PromptTemplate(
                    name=data["name"],
                    domain=domain,
                    version=data.get("version", "1.0.0"),
                    system_prompt=data.get("system_prompt", ""),
                    user_prompt_template=data.get("user_prompt", ""),
                    output_schema=data.get("output_schema", {}),
                    input_schema=data.get("input_schema", {}),
                    temperature=data.get("temperature", 0.0),
                    max_tokens=data.get("max_tokens", 2000),
                    response_format=data.get("response_format"),
                    few_shot_examples=data.get("few_shot_examples", []),
                )
                _REGISTRY[domain][template.name] = template
                logger.debug(
                    "Loaded prompt '%s' v%s for domain '%s'",
                    template.name, template.version, domain,
                )
            except Exception as exc:
                logger.warning("Failed to load prompt %s: %s", prompt_file, exc)

    total = sum(len(v) for v in _REGISTRY.values())
    logger.info("PromptRegistry: loaded %d templates across %d domains", total, len(_REGISTRY))


def get_prompt(domain: str, name: str, version: str | None = None) -> PromptTemplate:
    """Retrieve a prompt template by domain and name.

    Args:
        domain: Domain namespace (e.g., "jobpulse", "shared")
        name: Prompt template name (e.g., "skill_extraction")
        version: Specific version, or "latest" / None for latest.

    Raises:
        PromptNotFoundError: If prompt doesn't exist.
    """
    _load_yaml_templates()

    if domain not in _REGISTRY:
        raise PromptNotFoundError(f"Domain '{domain}' not found in prompt registry")
    if name not in _REGISTRY[domain]:
        available = list(_REGISTRY[domain].keys())
        raise PromptNotFoundError(
            f"Prompt '{name}' not found in domain '{domain}'. Available: {available}"
        )

    return _REGISTRY[domain][name]


def list_prompts(domain: str | None = None) -> dict[str, list[str]]:
    """List all registered prompts. Returns {domain: [prompt_names]}."""
    _load_yaml_templates()
    if domain:
        return {domain: list(_REGISTRY.get(domain, {}).keys())}
    return {d: list(p.keys()) for d, p in _REGISTRY.items()}


def reload_registry() -> None:
    """Force reload of all prompt templates from disk."""
    _REGISTRY.clear()
    _load_yaml_templates()
