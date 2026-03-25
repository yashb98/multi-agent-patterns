"""
Agent Node Functions
====================

Each function here is a "node" in the LangGraph graph.
A node is simply: state in → LLM call → partial state update out.

KEY PATTERN:
    def agent_node(state: AgentState) -> dict:
        # 1. READ what you need from state
        # 2. BUILD your prompt (system + user message)
        # 3. CALL the LLM
        # 4. RETURN only the fields you're updating

These functions are PURE — they don't know about the graph topology.
They don't know if they're in a hierarchy, a debate, or a swarm.
The orchestration pattern imports these and wires them differently.

WHY THIS SEPARATION MATTERS:
- Same agent logic, three different architectures
- Easy to test agents in isolation
- Easy to swap an agent (e.g., upgrade Writer) without touching wiring
"""

import json
import os
from datetime import datetime

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from shared.state import AgentState
from shared.prompts import RESEARCHER_PROMPT, WRITER_PROMPT, REVIEWER_PROMPT
from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── LLM INITIALISATION ─────────────────────────────────────────
# We create two LLM instances:
# - A "smart" one for complex reasoning (review, research)
# - A "fast" one for generation (writing)
# In production, you'd use different models (e.g., Opus vs Sonnet)

def get_llm(temperature: float = 0.7, model: str = "gpt-4o-mini"):
    """
    Factory function for LLM instances.
    
    WHY a factory? Because different agents may need different configs:
    - Researcher: low temperature (0.3) for factual accuracy
    - Writer: medium temperature (0.7) for creative prose
    - Reviewer: low temperature (0.2) for consistent scoring
    
    In production, you'd also configure:
    - Retry logic with exponential backoff
    - Fallback models (if primary is down)
    - Cost tracking per agent
    """
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        # In production: add max_retries, request_timeout, etc.
    )


# ─── AGENT NODE: RESEARCHER ─────────────────────────────────────

def researcher_node(state: AgentState) -> dict:
    """
    The Researcher agent gathers information on the topic.
    
    READS: topic, review_feedback (if revision cycle)
    WRITES: research_notes (appended), agent_history (appended)
    
    IMPORTANT: research_notes uses Annotated[list, operator.add],
    so returning a list APPENDS to existing notes rather than
    replacing them. This means research accumulates across iterations.
    """
    logger.info("=" * 50)
    logger.info("RESEARCHER AGENT - Iteration %d", state.get('iteration', 0))
    logger.info("=" * 50)
    
    # Build the user message based on current state
    topic = state["topic"]
    feedback = state.get("review_feedback", "")
    
    if feedback and state.get("iteration", 0) > 0:
        # This is a REVISION cycle — we have feedback to address
        user_msg = f"""Topic: {topic}

PREVIOUS REVIEW FEEDBACK (address these gaps):
{feedback}

Conduct ADDITIONAL research specifically targeting the gaps identified above.
Focus on finding information that was missing from the previous research."""
    else:
        # First pass — fresh research
        user_msg = f"""Topic: {topic}

Conduct comprehensive research on this topic. Gather facts, technical 
details, current trends, and notable perspectives."""
    
    # Call the LLM
    llm = get_llm(temperature=0.3)  # Low temp for factual accuracy
    response = llm.invoke([
        SystemMessage(content=RESEARCHER_PROMPT),
        HumanMessage(content=user_msg)
    ])
    
    research = response.content
    logger.info("Research produced: %d characters", len(research))
    
    # Return PARTIAL state update
    # research_notes is Annotated[list, operator.add] → this APPENDS
    return {
        "research_notes": [research],
        "current_agent": "researcher",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Researcher completed"]
    }


# ─── AGENT NODE: WRITER ─────────────────────────────────────────

def writer_node(state: AgentState) -> dict:
    """
    The Writer agent drafts or revises the blog article.
    
    READS: topic, research_notes, review_feedback (if revision), draft
    WRITES: draft (replaced), iteration (incremented), agent_history
    
    IMPORTANT: Unlike research_notes, 'draft' is a plain string.
    Returning a new draft REPLACES the old one — we only keep the
    latest version. This is intentional: we want the Writer to
    produce a complete, standalone article each time.
    """
    logger.info("=" * 50)
    logger.info("WRITER AGENT - Iteration %d", state.get('iteration', 0))
    logger.info("=" * 50)
    
    topic = state["topic"]
    research = "\n\n---\n\n".join(state.get("research_notes", []))
    feedback = state.get("review_feedback", "")
    current_draft = state.get("draft", "")
    iteration = state.get("iteration", 0)
    
    if feedback and current_draft:
        # REVISION mode — improve existing draft based on feedback
        user_msg = f"""Topic: {topic}

RESEARCH NOTES:
{research}

YOUR PREVIOUS DRAFT:
{current_draft}

REVIEWER FEEDBACK TO ADDRESS:
{feedback}

Revise the draft to address EACH piece of feedback. Maintain what was 
good, fix what was flagged. Produce the COMPLETE revised article."""
    else:
        # FIRST DRAFT mode — write from scratch
        user_msg = f"""Topic: {topic}

RESEARCH NOTES:
{research}

Write a complete, polished technical blog article based on these research notes."""
    
    # Call the LLM
    llm = get_llm(temperature=0.7)  # Medium temp for creative writing
    response = llm.invoke([
        SystemMessage(content=WRITER_PROMPT),
        HumanMessage(content=user_msg)
    ])
    
    draft = response.content
    logger.info("Draft produced: %d characters, ~%d words", len(draft), len(draft.split()))
    
    return {
        "draft": draft,
        "iteration": iteration + 1,
        "current_agent": "writer",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Writer completed (iteration {iteration + 1})"]
    }


# ─── AGENT NODE: REVIEWER ───────────────────────────────────────

def reviewer_node(state: AgentState) -> dict:
    """
    The Reviewer agent evaluates the draft and produces structured feedback.
    
    READS: draft, topic, research_notes
    WRITES: review_feedback, review_score, review_passed, agent_history
    
    CRITICAL DESIGN DECISION: We use structured JSON output here.
    The Reviewer's output needs to be MACHINE-READABLE because the
    orchestrator (supervisor/debate/swarm) makes routing decisions
    based on the score and pass/fail status.
    
    If we used free-form text, the orchestrator would need another
    LLM call just to interpret the review. Structured output
    eliminates that overhead and ambiguity.
    """
    logger.info("=" * 50)
    logger.info("REVIEWER AGENT - Evaluating draft")
    logger.info("=" * 50)
    
    draft = state.get("draft", "")
    topic = state["topic"]
    research = "\n\n".join(state.get("research_notes", []))
    
    user_msg = f"""Evaluate this blog article draft.

ORIGINAL TOPIC: {topic}

RESEARCH NOTES (for accuracy checking):
{research[:2000]}  

ARTICLE DRAFT TO REVIEW:
{draft}

Evaluate against all criteria and respond with ONLY the JSON structure 
specified in your instructions."""
    
    # Low temperature for consistent, reliable scoring
    llm = get_llm(temperature=0.2)
    response = llm.invoke([
        SystemMessage(content=REVIEWER_PROMPT),
        HumanMessage(content=user_msg)
    ])
    
    # Parse the structured response
    raw = response.content.strip()
    
    # Handle potential markdown wrapping
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0]
    
    try:
        review = json.loads(raw)
        score = float(review.get("overall_score", 0))
        passed = review.get("passed", False)
        feedback_text = json.dumps(review, indent=2)
        
        logger.info("Score: %s/10 | Passed: %s", score, passed)
        if not passed:
            improvements = review.get("improvements_needed", [])
            for imp in improvements[:3]:
                logger.info("   -> %s", imp)
    except (json.JSONDecodeError, ValueError) as e:
        # Fallback if JSON parsing fails
        logger.warning("Could not parse review JSON: %s", e)
        score = 5.0
        passed = False
        feedback_text = raw
    
    return {
        "review_feedback": feedback_text,
        "review_score": score,
        "review_passed": passed,
        "current_agent": "reviewer",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Reviewer: score={score}, passed={passed}"]
    }


# ─── UTILITY: STATE INITIALISER ─────────────────────────────────

def create_initial_state(topic: str) -> AgentState:
    """
    Creates a clean initial state for any pattern.
    
    This ensures all fields have sensible defaults so agents
    don't crash on missing keys during the first iteration.
    """
    return {
        "topic": topic,
        "research_notes": [],
        "draft": "",
        "review_feedback": "",
        "review_score": 0.0,
        "review_passed": False,
        "iteration": 0,
        "current_agent": "",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] System initialised with topic: {topic}"],
        "pending_tasks": [],
        "final_output": ""
    }
