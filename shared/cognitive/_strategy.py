"""Strategy templates, prompt composition, and template lifecycle."""

from dataclasses import dataclass, field

from shared.logging_config import get_logger
from shared.cognitive._prompts import COMPOSED_SECTIONS

logger = get_logger(__name__)

STRATEGY_PAYLOAD_KEYS = {
    "agent_name", "trigger", "composable_fragments", "times_used",
    "times_succeeded", "success_rate", "avg_score", "avg_latency_ms",
    "source", "anti_patterns",
}


def _get_base_prompt(agent_name: str) -> str:
    try:
        from jobpulse.persona_evolution import get_evolved_prompt
        return get_evolved_prompt(agent_name)
    except (ImportError, Exception):
        return ""


@dataclass
class ComposedPrompt:
    text: str
    templates_used: list[str] = field(default_factory=list)
    anti_patterns_used: list[str] = field(default_factory=list)
    token_count: int = 0
    source_breakdown: dict = field(default_factory=dict)


class StrategyComposer:
    """Assembles a prompt from strategy templates + anti-patterns + task context."""

    def compose(
        self,
        task: str,
        domain: str,
        agent_name: str,
        memory_manager,
        max_templates: int = 5,
        max_anti_patterns: int = 3,
        max_strategy_tokens: int = 500,
    ) -> ComposedPrompt:
        base_prompt = _get_base_prompt(agent_name)

        # Step 1: Retrieve and rank strategy templates
        procs = memory_manager.get_procedural_entries(domain) \
            if hasattr(memory_manager, "get_procedural_entries") else []

        own = [p for p in procs if getattr(p, "source", "") == agent_name]
        cross = [p for p in procs if p not in own]

        def rank_key(p):
            return getattr(p, "success_rate", 0.5) * 0.6 + \
                   getattr(p, "avg_score_when_used", 5.0) / 10.0 * 0.3

        own.sort(key=rank_key, reverse=True)
        cross.sort(key=rank_key, reverse=True)

        selected = own[:max_templates]
        remaining = max_templates - len(selected)
        if remaining > 0:
            selected.extend(cross[:remaining])

        source_breakdown = {
            "own": min(len(own), max_templates),
            "cross_agent": len(selected) - min(len(own), max_templates),
            "anti_patterns": 0,
        }

        # Step 2: Retrieve anti-patterns (failure memories)
        episodic = memory_manager.get_episodic_entries(domain) \
            if hasattr(memory_manager, "get_episodic_entries") else []

        failures = [e for e in episodic if e.final_score < 5.0]
        failures.sort(key=lambda e: e.timestamp if hasattr(e, "timestamp") else "",
                      reverse=True)
        failures = failures[:max_anti_patterns]
        source_breakdown["anti_patterns"] = len(failures)

        # Step 3: Compose sections
        sections = []
        if base_prompt:
            sections.append(base_prompt)

        if selected:
            strategy_lines = []
            char_budget = max_strategy_tokens * 4
            chars_used = 0
            template_ids = []
            for p in selected:
                line = f"- {p.strategy} (success: {p.success_rate:.0%}, used: {p.times_used}x)"
                if chars_used + len(line) > char_budget:
                    break
                strategy_lines.append(line)
                chars_used += len(line)
                template_ids.append(getattr(p, "procedure_id", "unknown"))
            if strategy_lines:
                sections.append(
                    COMPOSED_SECTIONS["strategies"].format(
                        strategies="\n".join(strategy_lines)
                    )
                )
        else:
            template_ids = []

        anti_ids = []
        if failures:
            anti_lines = []
            for f in failures:
                weakness = f.weaknesses[0] if f.weaknesses else f.output_summary[:100]
                anti_lines.append(f"- {weakness}")
                anti_ids.append(getattr(f, "run_id", "unknown"))
            sections.append(
                COMPOSED_SECTIONS["anti_patterns"].format(
                    anti_patterns="\n".join(anti_lines)
                )
            )

        sections.append(COMPOSED_SECTIONS["task"].format(task=task))

        text = "\n".join(sections)

        try:
            from shared.context_compression import count_tokens
            token_count = count_tokens(text)
        except ImportError:
            token_count = len(text) // 4

        return ComposedPrompt(
            text=text,
            templates_used=template_ids,
            anti_patterns_used=anti_ids,
            token_count=token_count,
            source_breakdown=source_breakdown,
        )

    @staticmethod
    def record_template_outcome(template: dict, success: bool, score: float):
        template["times_used"] = template.get("times_used", 0) + 1
        if success:
            template["times_succeeded"] = template.get("times_succeeded", 0) + 1
        template["success_rate"] = template.get("times_succeeded", 0) / \
            max(template["times_used"], 1)
