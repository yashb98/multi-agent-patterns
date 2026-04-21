"""Prompt templates for cognitive reasoning components."""

CRITIQUE_PROMPT = """You are reviewing an agent's output. The task was:
{task}

The agent produced:
{output}

The score was {score}/10 (threshold: {threshold}).

What specifically went wrong? Identify the concrete mistake in one sentence.
Then suggest a specific fix in one sentence.

Format:
MISTAKE: [what went wrong]
FIX: [what to do differently]"""

BRANCH_STRATEGIES = [
    "Approach step by step from first principles.",
    "Think about what could go wrong first, then work backwards.",
    "Find the simplest possible solution. Minimum that works.",
    "What would a domain expert do? Think from their perspective.",
]

EXTENSION_PROMPT = """Build on this approach: {reasoning}

Refine and improve it. Keep what works, fix what doesn't."""

SCORING_PROMPT = """Rate this output on a scale of 0-10.

Task: {task}

Output: {output}

Consider: accuracy, completeness, clarity, actionability.
Return ONLY a number between 0 and 10."""

COMPOSED_SECTIONS = {
    "strategies": "\n## Learned Strategies\n{strategies}",
    "anti_patterns": "\n## Avoid These Mistakes\n{anti_patterns}",
    "task": "\n## Task\n{task}",
}
