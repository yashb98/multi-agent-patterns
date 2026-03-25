"""Persona Evolution — agent prompts improve over runs via search-synthesize-compress.

Each agent has a base prompt. After each run, if the result scored well,
we extract what worked and compress it into an evolved prompt.
Over weeks, prompts become highly specialized to this user's patterns.

Example evolution:
  Week 1: "Classify emails into 4 categories"
  Week 4: "Classify emails into 4 categories. Skip Workday auto-rejections.
           Prioritize emails with person names. Barclays and Google are active pipelines."
"""

import os
from datetime import datetime
from jobpulse.swarm_dispatcher import get_persona, store_persona, get_experiences


# Base prompts for each agent
BASE_PROMPTS = {
    "gmail_agent": (
        "You classify recruiter emails. Categories: SELECTED_NEXT_ROUND, "
        "INTERVIEW_SCHEDULING, REJECTED, OTHER. Be precise about distinguishing "
        "automated rejections from personal ones."
    ),
    "budget_agent": (
        "You categorize financial transactions. Match to: Income (Salary, Freelance, Other), "
        "Fixed (Rent, Utilities, Phone, Subscriptions, Insurance), "
        "Variable (Groceries, Eating out, Transport, Shopping, Entertainment, Health, Misc), "
        "Savings (Savings, Investments, Credit card). Be strict about categories."
    ),
    "briefing_synthesizer": (
        "You create concise morning briefings. Lead with urgent items (interviews, deadlines). "
        "Use emoji for visual scanning. Keep each section to 2-3 lines max. "
        "End with an encouraging note."
    ),
}


def get_evolved_prompt(agent_name: str) -> str:
    """Get the current evolved prompt for an agent, or the base prompt if none exists."""
    persona = get_persona(agent_name)
    if persona and persona.get("evolved_prompt"):
        return persona["evolved_prompt"]
    return BASE_PROMPTS.get(agent_name, "")


def evolve_prompt(agent_name: str, current_result: str, score: float):
    """Evolve an agent's prompt based on a successful run.

    The search-synthesize-compress cycle:
    1. SEARCH: look at recent experiences for this agent
    2. SYNTHESIZE: merge learnings with current prompt
    3. COMPRESS: distill to essential instructions
    """
    if score < 5.0:
        return  # Only learn from decent results

    current_prompt = get_evolved_prompt(agent_name)
    experiences = get_experiences(agent_name, limit=5)
    persona = get_persona(agent_name)
    generation = (persona["generation"] if persona else 0) + 1

    # Build synthesis context
    exp_lines = "\n".join(f"- {e['pattern']} (score: {e['score']:.1f})" for e in experiences)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"""Evolve this agent prompt based on learned experiences.

CURRENT PROMPT (generation {generation-1}):
{current_prompt}

RECENT SUCCESSFUL PATTERNS:
{exp_lines}

LATEST RESULT (score {score:.1f}):
{current_result[:500]}

Rules:
1. Keep the core instructions intact
2. ADD specific learned patterns (e.g., "skip Workday auto-rejections")
3. COMPRESS — remove redundancy, keep it under 200 words
4. Don't add speculative patterns, only confirmed learnings

Return ONLY the evolved prompt text. No explanation."""
            }],
            max_tokens=300,
            temperature=0.3,
        )

        evolved = response.choices[0].message.content.strip()
        if len(evolved) > 50:  # Sanity check
            store_persona(agent_name, evolved, generation, score)
            print(f"[Persona] {agent_name} evolved to generation {generation}")

    except Exception as e:
        print(f"[Persona] Evolution failed for {agent_name}: {e}")
