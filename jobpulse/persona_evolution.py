"""Persona Evolution — agent prompts improve over runs via search-synthesize-compress.

Each agent has a base prompt. After each run, if the result scored well,
we extract what worked and compress it into an evolved prompt.
Over weeks, prompts become highly specialized to this user's patterns.

Two optimization modes:
  - QUICK (every run): Single-step evolve from latest experience
  - DEEP (every N runs): Multi-iteration meta-optimization with reflection

Example evolution:
  Week 1: "Classify emails into 4 categories"
  Week 4: "Classify emails into 4 categories. Skip Workday auto-rejections.
           Prioritize emails with person names. Barclays and Google are active pipelines."
"""

from datetime import datetime
from shared.logging_config import get_logger
from jobpulse.swarm_dispatcher import get_persona, store_persona, get_experiences, get_avg_score

logger = get_logger(__name__)

# How often to run deep meta-optimization (every N generations)
DEEP_OPTIMIZE_EVERY = 10


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

    Quick mode (every run): single-step search-synthesize-compress.
    Deep mode (every Nth generation): multi-iteration meta-optimization
    that reflects on failures and rewrites the prompt iteratively.
    """
    if score < 5.0:
        return  # Only learn from decent results

    current_prompt = get_evolved_prompt(agent_name)
    experiences = get_experiences(agent_name, limit=10)
    persona = get_persona(agent_name)
    generation = (persona["generation"] if persona else 0) + 1

    # Every Nth generation, run deep meta-optimization
    if generation > 1 and generation % DEEP_OPTIMIZE_EVERY == 0 and len(experiences) >= 5:
        logger.info("%s generation %d — triggering deep meta-optimization", agent_name, generation)
        _deep_optimize(agent_name, current_prompt, experiences, generation)
        return

    # Quick mode: single-step evolve
    _quick_evolve(agent_name, current_prompt, experiences, current_result, score, generation)


def _quick_evolve(agent_name: str, current_prompt: str, experiences: list,
                  current_result: str, score: float, generation: int):
    """Single-step prompt evolution from latest experience."""
    exp_lines = "\n".join(f"- {e['pattern']} (score: {e['score']:.1f})" for e in experiences[:5])

    try:
        from shared.agents import get_openai_client, get_model_name, is_local_llm
        client = get_openai_client()

        _result_preview = current_result[:2000] if is_local_llm() else current_result[:500]
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{
                "role": "user",
                "content": f"""Evolve this agent prompt based on learned experiences.

CURRENT PROMPT (generation {generation-1}):
{current_prompt}

RECENT SUCCESSFUL PATTERNS:
{exp_lines}

LATEST RESULT (score {score:.1f}):
{_result_preview}

Rules:
1. Keep the core instructions intact
2. ADD specific learned patterns (e.g., "skip Workday auto-rejections")
3. COMPRESS — remove redundancy, keep it under 200 words
4. Don't add speculative patterns, only confirmed learnings

Return ONLY the evolved prompt text. No explanation."""
            }],
            max_tokens=800 if is_local_llm() else 300,
            temperature=0.3,
        )

        evolved = response.choices[0].message.content.strip()
        if len(evolved) > 50:  # Sanity check
            store_persona(agent_name, evolved, generation, score)
            logger.info("%s evolved to generation %d (quick)", agent_name, generation)
            try:
                from shared.optimization import get_optimization_engine
                get_optimization_engine().emit(
                    signal_type="score_change",
                    source_loop="persona_evolution",
                    domain=agent_name,
                    agent_name=agent_name,
                    payload={"old_score": get_avg_score(agent_name) or 0.0, "new_score": score, "generation": generation, "source": "quick_evolve"},
                    session_id=f"pe_{agent_name}_{generation}",
                )
            except Exception as e2:
                logger.debug("Optimization signal failed: %s", e2)

    except Exception as e:
        logger.warning("Evolution failed for %s: %s", agent_name, e)


def _deep_optimize(agent_name: str, current_prompt: str, experiences: list, generation: int):
    """Multi-iteration meta-optimization with reflective prompt rewriting.

    Uses shared/prompt_optimizer.py's Meta-Optimization backend:
    1. Run agent prompt on past experiences as training data
    2. Identify which experiences scored low (failures)
    3. LLM reflects on WHY failures happened
    4. LLM rewrites prompt to fix failures while preserving successes
    5. Repeat for up to 5 iterations
    """
    try:
        from shared.agents import get_llm
        from shared.prompt_optimizer import PromptOptimizer

        llm = get_llm(temperature=0.3)
        optimizer = PromptOptimizer(llm)

        # Build training data from stored experiences
        training_data = [
            {"input": e["pattern"][:200], "expected_quality": e["score"]}
            for e in experiences
        ]

        # Evaluator: score a prompt against an experience pattern
        def evaluator(prompt: str, input_text: str) -> tuple[str, float]:
            """Evaluate how well a prompt would handle this input."""
            from shared.agents import get_openai_client, get_model_name, is_local_llm
            client = get_openai_client()
            try:
                response = client.chat.completions.create(
                    model=get_model_name(),
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": f"Process this: {input_text}"},
                    ],
                    max_tokens=200,
                    temperature=0.3,
                )
                output = response.choices[0].message.content.strip()

                # Score: ask LLM to rate the output
                score_resp = client.chat.completions.create(
                    model=get_model_name(),
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Rate this agent output 0-10 for quality and relevance.\n\n"
                            f"Agent prompt: {prompt[:200]}\n"
                            f"Input: {input_text[:200]}\n"
                            f"Output: {output[:300]}\n\n"
                            f"Return ONLY a number 0-10."
                        ),
                    }],
                    max_tokens=20 if is_local_llm() else 5,
                    temperature=0,
                )
                try:
                    score = float(score_resp.choices[0].message.content.strip())
                except ValueError:
                    score = 5.0
                return (output, min(10.0, max(0.0, score)))
            except Exception:
                return ("", 5.0)

        result = optimizer.optimize(
            agent_role=agent_name,
            current_prompt=current_prompt,
            training_data=training_data,
            evaluator_fn=evaluator,
            method="meta",
        )

        if result.optimized_score > result.original_score and len(result.optimized_prompt) > 50:
            store_persona(agent_name, result.optimized_prompt, generation, result.optimized_score)
            logger.info(
                "%s deep-optimized to gen %d: %.1f → %.1f (%+.1f%%)",
                agent_name, generation,
                result.original_score, result.optimized_score, result.improvement_pct,
            )
            try:
                from shared.optimization import get_optimization_engine
                get_optimization_engine().emit(
                    signal_type="score_change",
                    source_loop="persona_evolution",
                    domain=agent_name,
                    agent_name=agent_name,
                    payload={"old_score": result.original_score, "new_score": result.optimized_score, "generation": generation, "source": "deep_optimize"},
                    session_id=f"pe_{agent_name}_{generation}",
                )
            except Exception as e2:
                logger.debug("Optimization signal failed: %s", e2)
        else:
            # No improvement — keep current prompt but bump generation
            store_persona(agent_name, current_prompt, generation, result.original_score)
            logger.info(
                "%s deep-optimization found no improvement (%.1f → %.1f), keeping current",
                agent_name, result.original_score, result.optimized_score,
            )
            try:
                from shared.optimization import get_optimization_engine
                get_optimization_engine().emit(
                    signal_type="score_change",
                    source_loop="persona_evolution",
                    domain=agent_name,
                    agent_name=agent_name,
                    payload={"old_score": result.original_score, "new_score": result.optimized_score, "generation": generation, "source": "deep_optimize"},
                    session_id=f"pe_{agent_name}_{generation}",
                )
            except Exception as e2:
                logger.debug("Optimization signal failed: %s", e2)

    except Exception as e:
        logger.warning("Deep optimization failed for %s: %s", agent_name, e)
