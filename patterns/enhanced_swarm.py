"""
Pattern 4: Enhanced Adaptive Swarm
====================================

This is the PRODUCTION-GRADE pattern that combines all our innovations:

1. DYNAMIC AGENT FACTORY → Spawns agents based on task complexity
2. PERSONA EVOLUTION → Each agent's prompt evolves through search-synthesise-compress
3. TRAINING-FREE GRPO → Agents learn from their own successful outputs
4. PROMPT OPTIMIZATION → Meta-reflection improves prompts across runs

ARCHITECTURE:
    Task arrives
         ↓
    [Task Complexity Analyzer] → Determine required capabilities
         ↓
    [Dynamic Agent Factory] → Spawn specialist agents
         ↓
    [Persona Evolution] → Evolve each agent's prompt for this domain
         ↓
    [GRPO Group Sampling] → Generate multiple candidates per agent
         ↓
    [Convergence Check] → Score improved? Loop or finish
         ↓
    [Prompt Optimizer] → Store learnings for future runs
         ↓
    Final output

THIS IS THE CUTTING-EDGE PATTERN. It combines:
- Dynamic team composition (Question 2 answer)
- Self-improving personas (Question 3 answer from earlier)
- Experiential learning (Training-Free GRPO)
- Automated prompt optimization (DSPy/GEPA concepts)
"""

import json
import os

from pathlib import Path

from langgraph.graph import StateGraph, START, END

from shared.state import AgentState
from shared.agents import (
    researcher_node,
    writer_node,
    reviewer_node,
    risk_aware_reviewer_node,
    create_initial_state,
    get_llm,
    compute_cost_summary,
)
from shared.dynamic_agent_factory import (
    DynamicAgentFactory,
    DynamicAgentFactoryConfig,
    AgentTemplate,
)
from shared.experiential_learning import (
    TrainingFreeGRPO,
    GRPOConfig,
    get_shared_experience_memory,
)
from shared.persona_evolution import (
    PersonaEvolver,
    PersonaEvolutionConfig,
)
from shared.prompt_optimizer import PromptOptimizer
from shared.memory_layer import get_shared_memory_manager
from shared.convergence import ConvergenceController
from shared.cost_tracker import check_budget_from_state, BudgetExceededError

from langchain_core.messages import SystemMessage, HumanMessage
from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── GLOBAL LEARNING SYSTEMS ────────────────────────────────────
# SQLite-backed — experiences survive process restarts.

_experience_memory = get_shared_experience_memory()
_memory_manager = get_shared_memory_manager()
_grpo = TrainingFreeGRPO(llm=None)  # llm set lazily at first use
_convergence = ConvergenceController()  # Shared convergence controller


# ─── ENHANCED NODE FUNCTIONS ─────────────────────────────────────

def enhanced_task_analysis(state: AgentState) -> dict:
    """
    Enhanced task analysis that:
    1. Determines task complexity
    2. Spawns the right team of agents
    3. Stores team config in state for downstream use
    """
    logger.info("\n%s\n  ENHANCED TASK ANALYSIS\n%s", "=" * 60, "=" * 60)
    
    topic = state["topic"]

    # Inject memory context for task analysis before spawning agents
    memory_context = _memory_manager.get_context_for_agent("task_analysis", topic)

    # Create factory and analyse task
    llm = get_llm(temperature=0.2)
    factory = DynamicAgentFactory(llm)
    team = factory.assemble_team(topic)

    # Store team info in state
    team_info = json.dumps([
        {"name": a["name"], "tools": a["tools"], "max_actions": a["max_actions"]}
        for a in team
    ])

    # Determine if we need extra agents beyond the core 3
    agent_names = [a["name"] for a in team]
    has_code_expert = "code_expert" in agent_names
    has_fact_checker = "fact_checker" in agent_names

    logger.info("Team: %s", ", ".join(agent_names))
    logger.info("Code expert needed: %s", has_code_expert)
    logger.info("Fact checker needed: %s", has_fact_checker)
    if memory_context:
        logger.info("Memory context injected (%d chars)", len(memory_context))

    _memory_manager.record_step(
        "task_analysis",
        f"Assembled team: {', '.join(agent_names)}",
    )
    # Retire each agent from the factory after team is assembled
    for a in team:
        factory.retire_agent(a["name"])

    return {
        "pending_tasks": [{"team": team_info}],
        "current_agent": "task_analysis",
        "agent_history": [
            f"Task Analysis: spawned {len(team)} agents — {', '.join(agent_names)}"
        ]
    }


def enhanced_researcher_node(state: AgentState) -> dict:
    """
    Researcher with GRPO-enhanced output.
    
    Instead of generating one research output, generates multiple
    candidates and returns the best one. Learned patterns from
    previous successes are injected into the prompt.
    """
    logger.info("\n%s\n  ENHANCED RESEARCHER (with experiential learning)\n%s", "=" * 60, "=" * 60)
    
    topic = state["topic"]

    # Inject memory context before LLM call
    memory_context = _memory_manager.get_context_for_agent("researcher", topic)

    # Check for existing experiences
    experience_context = _experience_memory.format_for_prompt("research")

    base_prompt = """You are an elite Research Analyst. Gather comprehensive,
accurate information on the given topic. Focus on:
- Verified facts with clear sourcing
- Technical depth appropriate to the topic
- Current trends and recent developments
- Multiple perspectives and expert opinions
- Quantitative data points where available

Structure output as: Key Facts, Technical Details, Trends, Expert Views, Data."""

    # Enhance with learned experiences (TrainingFreeGRPO.enhance_prompt)
    llm = get_llm(temperature=0.3)
    _grpo.llm = llm  # Lazy bind LLM to GRPO instance
    enhanced_prompt = _grpo.enhance_prompt(base_prompt, domain="research")
    if memory_context:
        enhanced_prompt = f"{enhanced_prompt}\n\nMEMORY CONTEXT:\n{memory_context}"
    if experience_context:
        enhanced_prompt = f"{enhanced_prompt}\n\n{experience_context}"
        logger.info("Injected %d learned experiences", len(_experience_memory))

    # Generate research
    feedback = state.get("review_feedback", "")
    if feedback and state.get("iteration", 0) > 0:
        user_msg = f"Topic: {topic}\n\nAddress these gaps:\n{feedback}"
    else:
        user_msg = f"Topic: {topic}\n\nConduct comprehensive research."

    # Use group_sample_and_learn for GRPO pipeline
    def _research_evaluator(output: str) -> float:
        """Simple heuristic: longer, structured output scores higher."""
        score = min(len(output) / 2000, 1.0) * 6.0  # Up to 6 for length
        score += min(output.count("\n-") + output.count("\n•"), 10) * 0.3  # Bullet points
        score += 2.0 if any(kw in output.lower() for kw in ["key facts", "trends", "data"]) else 0.0
        return min(score, 10.0)

    try:
        research, _ = _grpo.group_sample_and_learn(
            system_prompt=enhanced_prompt,
            user_message=user_msg,
            evaluator_fn=_research_evaluator,
            domain="research",
        )
        logger.info("GRPO group_sample_and_learn: selected best research candidate")
    except Exception as _e:
        logger.warning("GRPO group sampling failed, falling back to single call: %s", _e)
        response = llm.invoke([
            SystemMessage(content=enhanced_prompt),
            HumanMessage(content=user_msg)
        ])
        research = response.content

    logger.info("Research: %d chars", len(research))

    _memory_manager.record_step(
        "researcher",
        f"Enhanced research produced {len(research)} chars",
    )

    return {
        "research_notes": [research],
        "current_agent": "researcher",
        "agent_history": [f"Enhanced Researcher completed (GRPO)"]
    }


def enhanced_writer_node(state: AgentState) -> dict:
    """
    Writer with GRPO group sampling.
    
    Generates multiple draft candidates and selects the best one.
    This is where Training-Free GRPO has the most impact — the
    quality difference between candidate drafts is large because
    writing is inherently variable.
    """
    logger.info("\n%s\n  ENHANCED WRITER (with GRPO group sampling)\n%s", "=" * 60, "=" * 60)
    
    topic = state["topic"]
    research = "\n\n---\n\n".join(state.get("research_notes", []))
    feedback = state.get("review_feedback", "")
    current_draft = state.get("draft", "")
    iteration = state.get("iteration", 0)

    # Inject memory context before LLM call
    memory_context = _memory_manager.get_context_for_agent("writer", topic)

    base_prompt = """You are an elite Technical Writer. Transform research
into polished, engaging articles. Use ONLY provided research.
Write clearly for technical professionals with concrete examples.
Include a compelling title, structured sections, and strong conclusion.
Target 800-1200 words. Active voice, short paragraphs."""

    # Enhance with TrainingFreeGRPO.enhance_prompt + experiences
    llm = get_llm(temperature=0.7)
    _grpo.llm = llm  # Lazy bind LLM to GRPO instance
    enhanced_prompt = _grpo.enhance_prompt(base_prompt, domain="writing")
    experience_context = _experience_memory.format_for_prompt("writing")
    if experience_context:
        enhanced_prompt = f"{enhanced_prompt}\n\n{experience_context}"
    if memory_context:
        enhanced_prompt = f"{enhanced_prompt}\n\nMEMORY CONTEXT:\n{memory_context}"

    if feedback and current_draft:
        user_msg = f"""Topic: {topic}
Research: {research}
Previous draft: {current_draft}
Feedback to address: {feedback}
Produce the COMPLETE revised article."""
    else:
        user_msg = f"""Topic: {topic}
Research: {research}
Write a complete technical blog article."""

    # Use group_sample_and_learn for full GRPO pipeline
    def _writer_evaluator(output: str) -> float:
        """Heuristic scoring: length + structure + title."""
        word_count = len(output.split())
        has_title = output.strip().startswith("#") or output.strip().startswith("**")
        section_count = output.count("\n#") + output.count("\n**")
        score = min(word_count / 1000, 1.0) * 5.0
        score += min(section_count, 5) * 0.8
        score += 2.0 if has_title else 0.0
        return min(score, 10.0)

    try:
        best_draft, _ = _grpo.group_sample_and_learn(
            system_prompt=enhanced_prompt,
            user_message=user_msg,
            evaluator_fn=_writer_evaluator,
            domain="writing",
        )
        logger.info("GRPO group_sample_and_learn: selected best writing candidate")
    except Exception as _e:
        logger.warning("GRPO group sampling failed, falling back to parallel candidates: %s", _e)
        from shared.parallel_executor import parallel_grpo_candidates

        temps = [0.5, 0.7, 0.9]
        model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        def make_llm(temp):
            from shared.agents import get_llm
            return get_llm(model=model_name, temperature=temp, timeout=30.0)

        candidates = parallel_grpo_candidates(make_llm, enhanced_prompt, user_msg, temps)
        scored = [
            (
                min(len(c.split()) / 1000, 1.0) * 5
                + min(c.count("\n#") + c.count("\n**"), 5)
                + (2 if c.strip().startswith("#") or c.strip().startswith("**") else 0),
                c,
            )
            for c in candidates
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_draft = scored[0][1]
        logger.info("Fallback: selected best of %d candidates", len(candidates))

    logger.info("Best candidate: %d words", len(best_draft.split()))

    _memory_manager.record_step(
        "writer",
        f"Enhanced writer produced draft: {len(best_draft.split())} words (iteration {iteration + 1})",
    )

    return {
        "draft": best_draft,
        "iteration": iteration + 1,
        "current_agent": "writer",
        "agent_history": [
            f"Enhanced Writer: GRPO best candidate "
            f"(iteration {iteration + 1})"
        ]
    }


def enhanced_reviewer_node(state: AgentState) -> dict:
    """
    Reviewer that also extracts experiential learnings.
    
    After scoring, it analyses WHY the draft scored the way it did
    and stores the insight as an experience for future runs.
    """
    logger.info("\n%s\n  ENHANCED REVIEWER (with experience extraction)\n%s", "=" * 60, "=" * 60)
    
    # Run standard review
    result = risk_aware_reviewer_node(state)
    
    score = result.get("review_score", 0)
    
    # Extract experiential learning if score is notable
    if score >= 7.0:
        # Good output — extract what worked
        from shared.experiential_learning import Experience
        exp = Experience(
            task_description=state["topic"][:200],
            successful_pattern=(
                f"Draft scored {score}/10. "
                f"Key strengths from review: {result.get('review_feedback', '')[:300]}"
            ),
            score=score,
            domain="writing",
        )
        _experience_memory.add(exp)
        logger.info("Stored positive experience (score: %s)", score)
    
    return result


def enhanced_convergence(state: AgentState) -> dict:
    """Enhanced convergence — delegates to ConvergenceController.

    Preserves the adaptive threshold: if the system has accumulated enough
    experience, the quality bar rises to push for better outputs over time.
    The controller handles dual-gate, patience, and max-iterations logic.
    """
    score = state.get("review_score", 0.0)
    accuracy_score = state.get("accuracy_score", 0.0)
    iteration = state.get("iteration", 0)

    # Budget check before deciding to continue
    try:
        check_budget_from_state(state, estimated_next_cost=0.05)
    except BudgetExceededError as e:
        logger.warning("Budget exceeded in enhanced swarm: %s", e)
        return {
            "current_agent": "finish",
            "agent_history": [f"Convergence: finish (budget cap exceeded: ${e.spent:.2f} > ${e.cap:.2f})"]
        }

    # Adaptive threshold: raise bar when we have enough historical experience
    if len(_experience_memory) > 5:
        avg_historical = sum(
            e.score for e in _experience_memory.experiences
        ) / len(_experience_memory)
        adaptive_threshold = max(_convergence.quality_threshold, avg_historical * 0.9)
        # Temporarily clone controller with raised threshold for this check
        from shared.convergence import ConvergenceController
        checker = ConvergenceController(quality_threshold=adaptive_threshold)
        checker._score_history = list(_convergence._score_history)
    else:
        checker = _convergence

    decision_obj = checker.check(state)
    decision = "finish" if decision_obj.should_stop else "continue"

    logger.info(
        "Convergence: quality=%.1f, accuracy=%.1f, iter=%d -> %s (%s)",
        score, accuracy_score, iteration, decision, decision_obj.reason,
    )

    # Learn procedure from high-scoring convergence
    if score >= 7.0:
        _memory_manager.learn_procedure(
            domain="writing",
            strategy=(
                f"Enhanced swarm convergence: GRPO group sampling. "
                f"Score {score:.1f}/10 at iteration {iteration}. {decision_obj.reason}"
            ),
            context=state.get("topic", "")[:200],
            score=score,
            source="enhanced_swarm",
        )
    _memory_manager.record_step(
        "convergence",
        f"Enhanced convergence: {decision}, score={score:.1f}",
        score=score,
    )

    # Prune state between iterations
    from shared.state import prune_and_return
    return prune_and_return(state, {
        "current_agent": decision,
        "agent_history": [f"Convergence: {decision} ({decision_obj.outcome.value})"],
    })


def enhanced_finish(state: AgentState) -> dict:
    """Final packaging with learning summary."""
    draft = state.get("draft", "")
    score = state.get("review_score", 0)
    cost = compute_cost_summary(state.get("token_usage", []))

    logger.info("\n%s\n  ENHANCED SWARM COMPLETE\n%s", "=" * 60, "=" * 60)
    logger.info("Score: %s/10", score)
    logger.info("Experiences stored: %d", len(_experience_memory))
    logger.info("Total cost: $%.4f (%d LLM calls)", cost["total_cost_usd"], cost["calls"])

    _memory_manager.record_step("finish", f"Enhanced swarm complete: score={score}/10", score=score)

    # Emit success signal to optimization engine
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit(
            signal_type="success",
            source_loop="experience_memory",
            domain=state.get("topic", "unknown"),
            agent_name="enhanced_swarm",
            payload={
                "score": state.get("review_score", 0.0),
                "iterations": state.get("iteration", 0),
            },
            session_id=f"pattern_{state.get('topic', 'unknown')}",
        )
    except Exception:
        pass

    return {
        "final_output": draft,
        "total_cost_usd": cost["total_cost_usd"],
        "agent_history": [
            f"Enhanced swarm complete. Score: {score}/10, "
            f"Experiences: {len(_experience_memory)}, "
            f"Cost: ${cost['total_cost_usd']:.4f}"
        ]
    }


# ─── ROUTING ────────────────────────────────────────────────────

def route_after_convergence(state: AgentState) -> str:
    decision = state.get("current_agent", "finish")
    return "enhanced_researcher" if decision == "continue" else "finish"


# ─── BUILD THE GRAPH ────────────────────────────────────────────

def build_enhanced_swarm_graph(checkpointer=None):
    """
    Build the enhanced adaptive swarm graph.
    
    Flow:
    START → task_analysis → researcher → writer → reviewer 
          → convergence → (loop to researcher | finish)
    """
    graph = StateGraph(AgentState)
    
    graph.add_node("task_analysis", enhanced_task_analysis)
    graph.add_node("enhanced_researcher", enhanced_researcher_node)
    graph.add_node("enhanced_writer", enhanced_writer_node)
    graph.add_node("enhanced_reviewer", enhanced_reviewer_node)
    graph.add_node("convergence", enhanced_convergence)
    graph.add_node("finish", enhanced_finish)
    
    # Flow
    graph.add_edge(START, "task_analysis")
    graph.add_edge("task_analysis", "enhanced_researcher")
    graph.add_edge("enhanced_researcher", "enhanced_writer")
    graph.add_edge("enhanced_writer", "enhanced_reviewer")
    graph.add_edge("enhanced_reviewer", "convergence")
    
    graph.add_conditional_edges(
        "convergence",
        route_after_convergence,
        {
            "enhanced_researcher": "enhanced_researcher",
            "finish": "finish",
        }
    )
    
    graph.add_edge("finish", END)
    
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("Enhanced Adaptive Swarm compiled successfully")
    return compiled


def run_enhanced_swarm(topic: str):
    """Run the enhanced adaptive swarm."""
    from shared.logging_config import generate_run_id, set_run_id
    run_id = generate_run_id()
    set_run_id(run_id)
    logger.info("Starting enhanced swarm [%s] topic=%s", run_id, topic[:80])
    logger.info(
        "\n%s\n  ENHANCED ADAPTIVE SWARM\n  Topic: %s\n  Accumulated experiences: %d\n%s",
        "=" * 60, topic, len(_experience_memory), "=" * 60,
    )
    
    initial_state = create_initial_state(topic)
    graph = build_enhanced_swarm_graph()
    final_state = graph.invoke(initial_state)
    
    logger.info("Execution history:")
    for entry in final_state.get("agent_history", []):
        logger.info("   %s", entry)
    
    return final_state


if __name__ == "__main__":
    result = run_enhanced_swarm(
        "How AI Agents Are Changing Software Development in 2026"
    )
    
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    
    with open(f"{output_dir}/enhanced_swarm_output.md", "w") as f:
        f.write(result.get("final_output", "No output"))
