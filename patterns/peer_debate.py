"""
Pattern 2: Peer Debate
=======================

ARCHITECTURE:
    Round 1: Sequential pipeline (research → write → review)
    Round 2+: All agents see all outputs and cross-critique
    Convergence: Score check after each round → loop or finish

KEY DIFFERENCE FROM HIERARCHICAL:
- No supervisor. No single point of control.
- Agents see EACH OTHER'S work and respond to it.
- The Researcher can say "the Writer distorted my findings."
- The Writer can say "the Researcher's data was incomplete."
- Quality emerges from DISAGREEMENT + RESOLUTION, not top-down control.

THIS IS THE PATTERN USED BY:
- Constitutional AI (principle-based debate)
- Society of Mind approaches
- Multi-agent reasoning systems (like PRISM's recursive loop)
- Debate-based fact verification systems
- Red team / blue team security assessments

WHEN TO USE:
✅ Output quality matters more than speed
✅ The task has subjective elements (writing, design, strategy)
✅ You want to catch errors that a single reviewer might miss
✅ Different agents have different "perspectives" on the same work

WHEN NOT TO USE:
❌ Speed is critical (debate adds latency)
❌ The task is purely mechanical (no subjective judgment)
❌ Budget is tight (each round costs N agent calls)
❌ Agents have identical capabilities (nothing to debate)
"""

import sys
import json
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from langgraph.graph import StateGraph, START, END

from shared.state import AgentState
from shared.agents import (
    researcher_node,
    writer_node,
    reviewer_node,
    risk_aware_reviewer_node,
    fact_check_node,
    create_initial_state,
    get_llm,
    compute_cost_summary,
)
from shared.prompts import WRITER_PROMPT, REVIEWER_PROMPT
from langchain_core.messages import SystemMessage, HumanMessage
from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── DEBATE-SPECIFIC AGENT VARIANTS ─────────────────────────────
# In the debate pattern, agents need MODIFIED behaviour for rounds 2+.
# During the initial round, they work normally.
# During debate rounds, they receive ALL other agents' outputs
# and must respond to specific points of disagreement.
#
# We create wrapper functions that detect which round we're in
# and adjust the prompt accordingly.

def debate_researcher_node(state: AgentState) -> dict:
    """
    Researcher in debate mode.
    
    Round 1: Normal research (same as hierarchical).
    Round 2+: Reviews the Writer's draft and the Reviewer's feedback.
              Identifies factual errors, missing information, or
              misrepresented findings. Provides ADDITIONAL research
              to address gaps.
    
    KEY INSIGHT: In the hierarchy, the Researcher runs once and is done.
    In the debate, the Researcher gets to RESPOND to how their research
    was used. This catches a critical failure mode: the Writer
    misunderstanding or cherry-picking research findings.
    """
    iteration = state.get("iteration", 0)
    
    if iteration == 0:
        # Round 1: Normal research
        return researcher_node(state)
    
    # Round 2+: Debate mode — critique the draft using research expertise
    logger.info("RESEARCHER (Debate Round %d) - Cross-critiquing", iteration)
    
    draft = state.get("draft", "")
    review = state.get("review_feedback", "")
    existing_research = "\n\n".join(state.get("research_notes", []))
    
    debate_prompt = f"""You are the Research Analyst in a peer debate.

The Writer has produced a draft, and the Reviewer has evaluated it.
Your job is to CRITIQUE from the research perspective:

1. Did the Writer accurately represent your research findings?
2. Are there factual errors or unsupported claims in the draft?
3. What ADDITIONAL information should be included?
4. Do you agree or disagree with the Reviewer's assessment?

Be specific. Quote the exact parts of the draft you're critiquing.

YOUR ORIGINAL RESEARCH:
{existing_research}

THE WRITER'S DRAFT:
{draft}

THE REVIEWER'S FEEDBACK:
{review}

Respond with:
- AGREEMENTS: Points where you agree with the Reviewer
- DISAGREEMENTS: Points where you disagree (with evidence)
- MISSING INFO: Additional research findings to incorporate
- CORRECTIONS: Any factual errors in the draft"""
    
    llm = get_llm(temperature=0.3)
    response = llm.invoke([
        SystemMessage(content="You are a meticulous research analyst participating in a peer debate."),
        HumanMessage(content=debate_prompt)
    ])
    
    critique = response.content
    logger.info("Research critique: %d characters", len(critique))
    
    return {
        "research_notes": [f"\n[DEBATE ROUND {iteration} - Researcher Critique]\n{critique}"],
        "current_agent": "researcher",
        "agent_history": [f"Researcher debate critique (round {iteration})"]
    }


def debate_writer_node(state: AgentState) -> dict:
    """
    Writer in debate mode.
    
    Round 1: Normal draft (same as hierarchical).
    Round 2+: Receives critique from BOTH the Researcher AND Reviewer.
              Must address specific points raised by each.
              Can push back on feedback it disagrees with (with reasoning).
    
    KEY INSIGHT: In the hierarchy, the Writer silently accepts feedback.
    In the debate, the Writer can ARGUE BACK. "The Reviewer said my intro
    is weak, but I disagree because..." This creates a richer revision
    because the Writer thinks critically about feedback rather than
    blindly applying it.
    """
    iteration = state.get("iteration", 0)
    
    if iteration == 0:
        # Round 1: Normal writing
        return writer_node(state)
    
    # Round 2+: Debate mode — revise while responding to all critiques
    logger.info("WRITER (Debate Round %d) - Responding to critiques", iteration)
    
    topic = state["topic"]
    draft = state.get("draft", "")
    research = "\n\n".join(state.get("research_notes", []))
    review = state.get("review_feedback", "")
    
    debate_prompt = f"""You are the Technical Writer in a peer debate.

You've received feedback from BOTH the Researcher and the Reviewer.
Your task:

1. CONSIDER each piece of feedback carefully
2. ACCEPT feedback you agree with and revise accordingly
3. PUSH BACK on feedback you disagree with (explain why)
4. Produce a COMPLETE revised article

Topic: {topic}

ALL RESEARCH (including debate critiques):
{research}

YOUR PREVIOUS DRAFT:
{draft}

REVIEWER FEEDBACK:
{review}

IMPORTANT: Produce the COMPLETE revised article, not just a response 
to feedback. The article should be better than the previous draft."""
    
    llm = get_llm(temperature=0.6)  # Slightly lower temp for focused revision
    response = llm.invoke([
        SystemMessage(content=WRITER_PROMPT),
        HumanMessage(content=debate_prompt)
    ])
    
    new_draft = response.content
    logger.info("Revised draft: %d words", len(new_draft.split()))
    
    return {
        "draft": new_draft,
        "iteration": iteration + 1,
        "current_agent": "writer",
        "agent_history": [f"Writer debate revision (round {iteration})"]
    }


def debate_reviewer_node(state: AgentState) -> dict:
    """
    Reviewer in debate mode.
    
    Behaves the same in all rounds — always produces structured 
    feedback. But in debate mode, the Reviewer also considers
    the Researcher's critique when evaluating.
    
    The Reviewer's score is the CONVERGENCE SIGNAL.
    If the score improves across rounds, the debate is working.
    If it stagnates, we should stop.
    """
    # The reviewer works the same way — score and critique
    # Uses risk-aware review for code-containing drafts
    return risk_aware_reviewer_node(state)


# ─── CONVERGENCE CHECK ───────────────────────────────────────────
# This is the "referee" of the debate.
# After each full round, it checks:
#   1. Has the score improved?
#   2. Have we hit max debate rounds?
#   3. Has the score passed the threshold?
#
# This replaces the supervisor's routing logic.
# Instead of one boss deciding, we use METRICS to decide.

def convergence_check(state: AgentState) -> dict:
    """
    Evaluates whether the debate should continue or conclude.
    
    CONVERGENCE CRITERIA:
    1. Score >= 7.0 (quality threshold met)
    2. Iteration >= 3 (max rounds reached)
    3. Score decreased from previous round (debate is hurting, not helping)
    
    Returns updated state with the decision.
    """
    logger.info("CONVERGENCE CHECK")

    score = state.get("review_score", 0)
    passed = state.get("review_passed", False)
    iteration = state.get("iteration", 0)

    logger.info("Current score: %s/10", score)
    logger.info("Passed threshold: %s", passed)
    logger.info("Debate round: %d", iteration)
    
    accuracy_passed = state.get("accuracy_passed", False)
    accuracy_score = state.get("accuracy_score", 0)

    # Dual gate: quality AND accuracy must pass
    quality_ok = passed and score >= 8.0
    accuracy_ok = accuracy_passed

    should_continue = (
        (not quality_ok or not accuracy_ok)
        and iteration < 3
        and score < 9.0
    )

    if should_continue:
        if quality_ok and not accuracy_ok:
            decision = "continue"
            reason = f"Quality passed ({score}/10) but accuracy failed ({accuracy_score:.1f}/10)"
        else:
            decision = "continue"
            reason = f"Score {score}/10 below threshold, round {iteration}/3"
    elif quality_ok and accuracy_ok:
        decision = "finish"
        reason = f"Both gates passed: quality={score}/10, accuracy={accuracy_score:.1f}/10"
    else:
        decision = "finish"
        reason = f"Max rounds reached. Final score: {score}/10, accuracy: {accuracy_score:.1f}/10"
    
    logger.info("Decision: %s", decision)
    logger.info("Reason: %s", reason)
    
    return {
        "current_agent": decision,
        "agent_history": [f"Convergence: {decision} ({reason})"]
    }


# ─── SYNTHESIS NODE ──────────────────────────────────────────────
# After the debate concludes, we take the final draft
# and package it as the output.

def synthesis_node(state: AgentState) -> dict:
    """
    Final synthesis after debate concludes.
    
    Takes the last Writer draft (which has been refined through
    multiple rounds of cross-critique) and packages it as output.
    """
    logger.info("SYNTHESIS - Packaging debate result")

    draft = state.get("draft", "")
    score = state.get("review_score", 0)
    iterations = state.get("iteration", 0)
    history = state.get("agent_history", [])

    # Count debate rounds from history
    debate_rounds = sum(1 for h in history if "debate" in h.lower())

    cost = compute_cost_summary(state.get("token_usage", []))

    logger.info("Final score: %s/10", score)
    logger.info("Total iterations: %d", iterations)
    logger.info("Debate exchanges: %d", debate_rounds)
    logger.info("Total cost: $%.4f (%d LLM calls)", cost["total_cost_usd"], cost["calls"])

    return {
        "final_output": draft,
        "total_cost_usd": cost["total_cost_usd"],
        "agent_history": [f"Debate complete. Score: {score}/10 after {iterations} rounds, Cost: ${cost['total_cost_usd']:.4f}"]
    }


# ─── ROUTING FUNCTION ───────────────────────────────────────────

def route_after_convergence(state: AgentState) -> str:
    """
    Routes based on convergence check result.
    
    "continue" → back to debate_researcher (start new debate round)
    "finish" → synthesis (package output)
    """
    decision = state.get("current_agent", "finish")
    if decision == "continue":
        return "debate_researcher"
    return "synthesis"


# ─── BUILD THE GRAPH ────────────────────────────────────────────
# 
# The peer debate graph looks very different from the hierarchy:
#
# HIERARCHICAL:  supervisor ←→ workers (hub and spoke)
# PEER DEBATE:   pipeline → debate_loop → synthesis (linear with loop)
#
# Round 1: researcher → writer → reviewer (sequential, like a pipeline)
# Round 2+: debate_researcher → debate_writer → reviewer → convergence
#           ↑_______________________________________________|  (loop)
#
# The loop is between the debate agents and convergence check.
# When convergence says "continue", we loop back.
# When it says "finish", we break out to synthesis.

def build_debate_graph():
    """
    Constructs the LangGraph StateGraph for the peer debate pattern.
    
    KEY STRUCTURAL DIFFERENCE FROM HIERARCHICAL:
    - No supervisor node
    - The loop is between debate agents and convergence check
    - Conditional edge is on the convergence node, not a supervisor
    """
    graph = StateGraph(AgentState)
    
    # ── Add nodes ──
    # Round 1 nodes (initial pipeline)
    graph.add_node("researcher", researcher_node)
    graph.add_node("writer", writer_node)
    graph.add_node("reviewer", risk_aware_reviewer_node)
    
    # Debate round nodes (cross-critique versions)
    graph.add_node("debate_researcher", debate_researcher_node)
    graph.add_node("debate_writer", debate_writer_node)
    
    # Fact checker node
    graph.add_node("fact_checker", fact_check_node)

    # Control nodes
    graph.add_node("convergence", convergence_check)
    graph.add_node("synthesis", synthesis_node)
    
    # ── Round 1: Sequential pipeline ──
    # This establishes the initial positions before the debate begins.
    graph.add_edge(START, "researcher")
    graph.add_edge("researcher", "writer")
    graph.add_edge("writer", "reviewer")
    
    # After review, run fact-check before convergence
    graph.add_edge("reviewer", "fact_checker")
    graph.add_edge("fact_checker", "convergence")
    
    # ── Convergence decision ──
    # This is the ONLY conditional edge in the debate pattern.
    # It replaces the supervisor's routing logic.
    graph.add_conditional_edges(
        "convergence",
        route_after_convergence,
        {
            "debate_researcher": "debate_researcher",
            "synthesis": "synthesis",
        }
    )
    
    # ── Debate loop ──
    # If convergence says "continue":
    #   debate_researcher → debate_writer → reviewer → convergence
    # This is a fixed pipeline within the loop.
    graph.add_edge("debate_researcher", "debate_writer")
    graph.add_edge("debate_writer", "reviewer")
    # reviewer → convergence is already set above (reused)
    
    # ── Terminal ──
    graph.add_edge("synthesis", END)
    
    compiled = graph.compile()
    
    logger.info("Peer Debate graph compiled successfully")
    logger.info("Nodes: researcher, writer, reviewer, debate_researcher, debate_writer, convergence, synthesis")
    logger.info("Loop: debate_researcher -> debate_writer -> reviewer -> convergence -> (loop or finish)")
    
    return compiled


# ─── RUN THE PATTERN ─────────────────────────────────────────────

def run_debate(topic: str):
    """
    End-to-end execution of the peer debate pattern.
    """
    from shared.logging_config import generate_run_id, set_run_id
    run_id = generate_run_id()
    set_run_id(run_id)
    logger.info("Starting peer debate [%s] topic=%s", run_id, topic[:80])
    
    initial_state = create_initial_state(topic)
    graph = build_debate_graph()
    final_state = graph.invoke(initial_state)
    
    # Log summary
    logger.info("DEBATE COMPLETE")
    for entry in final_state.get("agent_history", []):
        logger.info("History: %s", entry)

    logger.info("Final article: %d words", len(final_state.get("final_output", "").split()))
    logger.info("Final score: %s/10", final_state.get("review_score", 0))
    logger.info("Debate rounds: %d", final_state.get("iteration", 0))
    
    return final_state


# ─── MAIN ────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_debate("How AI Agents Are Changing Software Development in 2026")
    
    _output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")
    os.makedirs(_output_dir, exist_ok=True)
    with open(os.path.join(_output_dir, "debate_output.md"), "w") as f:
        f.write(result.get("final_output", "No output"))

    logger.info("Output saved to outputs/debate_output.md")
