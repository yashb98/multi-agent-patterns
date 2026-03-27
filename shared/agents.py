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

AGENTIC LOOP PATTERN (Claude Certified Architect Domain 1):
The run_agentic_loop() function implements proper stop_reason handling:
- Continues when stop_reason is "tool_use" (execute tools, feed results back)
- Terminates when stop_reason is "end_turn" (model is done)
- Tool results are appended to conversation history for next iteration
"""

import json
import os
from datetime import datetime
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from shared.state import AgentState
from shared.prompts import RESEARCHER_PROMPT, WRITER_PROMPT, REVIEWER_PROMPT
from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── STRUCTURED ERROR RESPONSE ───────────────────────────────────

class AgentError:
    """Structured error object for agent failures (Domain 2, Task 2.2)."""

    def __init__(self, error_category: str, message: str,
                 is_retryable: bool = False, partial_results: Any = None,
                 agent_name: str = "", attempted_action: str = ""):
        self.error_category = error_category   # transient | validation | permission | business
        self.message = message
        self.is_retryable = is_retryable
        self.partial_results = partial_results
        self.agent_name = agent_name
        self.attempted_action = attempted_action

    def to_dict(self) -> dict:
        return {
            "status": "error",
            "errorCategory": self.error_category,
            "message": self.message,
            "isRetryable": self.is_retryable,
            "partialResults": self.partial_results,
            "agentName": self.agent_name,
            "attemptedAction": self.attempted_action,
        }

    def __str__(self) -> str:
        retry = " (retryable)" if self.is_retryable else ""
        return f"[{self.error_category}]{retry} {self.agent_name}: {self.message}"


# ─── LLM INITIALISATION ─────────────────────────────────────────

def get_llm(temperature: float = 0.7, model: str = "gpt-4o-mini"):
    """
    Factory function for LLM instances.

    WHY a factory? Because different agents may need different configs:
    - Researcher: low temperature (0.3) for factual accuracy
    - Writer: medium temperature (0.7) for creative prose
    - Reviewer: low temperature (0.2) for consistent scoring
    """
    return ChatOpenAI(
        model=model,
        temperature=temperature,
    )


# ─── AGENTIC LOOP (Domain 1, Task 1.1) ──────────────────────────
# Proper stop_reason handling: continue on tool_use, stop on end_turn.
# Tool results are appended to conversation context between iterations.

# Registry of tools available to agents during agentic loops
AGENT_TOOLS = {}


def register_agent_tool(name: str, description: str, func: callable):
    """Register a tool that agents can invoke during agentic loops."""
    AGENT_TOOLS[name] = {
        "name": name,
        "description": description,
        "func": func,
    }


def run_agentic_loop(
    system_prompt: str,
    user_message: str,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_iterations: int = 10,
    model: str = "gpt-4o-mini",
) -> dict:
    """
    Run an agentic loop with proper stop_reason handling.

    Returns dict with:
        - content: str (final text output)
        - tool_calls_made: list[dict] (audit trail of tool invocations)
        - iterations: int (how many loop passes)
        - stop_reason: str (why the loop ended: "end_turn" | "max_iterations")

    PATTERN (from Claude Certified Architect exam, Domain 1 Task 1.1):
    1. Send request to LLM
    2. Inspect stop_reason: "tool_use" → execute tools, append results, loop
    3. stop_reason "end_turn" → return final content
    4. Max iterations is a SAFETY VALVE, not the primary stopping mechanism
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # Build OpenAI-format tool definitions
    openai_tools = None
    tool_map = {}
    if tools:
        openai_tools = []
        for t in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            })
            tool_map[t["name"]] = t["func"]

    tool_calls_made = []
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        kwargs = {"model": model, "messages": messages, "temperature": temperature}
        if openai_tools:
            kwargs["tools"] = openai_tools

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # ── Check stop_reason (finish_reason in OpenAI) ──
        if choice.finish_reason == "tool_calls":
            # Model wants to call tools — execute them and loop
            assistant_msg = choice.message
            messages.append(assistant_msg.model_dump())

            for tool_call in assistant_msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

                logger.info("Agentic loop: tool_call %s(%s)", fn_name, list(fn_args.keys()))

                # Execute the tool
                if fn_name in tool_map:
                    try:
                        result = tool_map[fn_name](**fn_args)
                        result_str = json.dumps(result) if not isinstance(result, str) else result
                    except Exception as e:
                        result_str = json.dumps(AgentError(
                            error_category="transient",
                            message=str(e),
                            is_retryable=True,
                            agent_name=fn_name,
                            attempted_action=f"{fn_name}({fn_args})",
                        ).to_dict())
                else:
                    result_str = json.dumps(AgentError(
                        error_category="validation",
                        message=f"Unknown tool: {fn_name}",
                        is_retryable=False,
                    ).to_dict())

                tool_calls_made.append({
                    "tool": fn_name,
                    "args": fn_args,
                    "result": result_str[:500],
                    "iteration": iteration,
                })

                # Append tool result to conversation for next iteration
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                })

        elif choice.finish_reason in ("stop", "end_turn"):
            # Model is done — return the final content
            return {
                "content": choice.message.content or "",
                "tool_calls_made": tool_calls_made,
                "iterations": iteration,
                "stop_reason": "end_turn",
            }
        else:
            # Unexpected finish_reason (length, content_filter, etc.)
            logger.warning("Agentic loop: unexpected finish_reason=%s", choice.finish_reason)
            return {
                "content": choice.message.content or "",
                "tool_calls_made": tool_calls_made,
                "iterations": iteration,
                "stop_reason": choice.finish_reason or "unknown",
            }

    # Safety valve: max iterations reached
    logger.warning("Agentic loop: max_iterations (%d) reached", max_iterations)
    return {
        "content": messages[-1].get("content", "") if isinstance(messages[-1], dict) else "",
        "tool_calls_made": tool_calls_made,
        "iterations": iteration,
        "stop_reason": "max_iterations",
    }


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
    # Use response_format to GUARANTEE valid JSON (Domain 4, Task 4.3)
    # This eliminates JSON syntax errors — no more markdown stripping fallbacks
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.2,
        model_kwargs={"response_format": {"type": "json_object"}},
    )
    response = llm.invoke([
        SystemMessage(content=REVIEWER_PROMPT),
        HumanMessage(content=user_msg)
    ])

    # Parse the structured response — guaranteed valid JSON by response_format
    raw = response.content.strip()

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
        # Structured error instead of silent fallback
        logger.warning("Could not parse review JSON: %s — raw: %s", e, raw[:200])
        score = 5.0
        passed = False
        feedback_text = json.dumps({
            "overall_score": 5.0,
            "passed": False,
            "parse_error": str(e),
            "raw_response": raw[:500],
            "improvements_needed": ["Review JSON could not be parsed — re-review needed"],
            "summary": "Automated review failed to produce valid JSON. Manual review recommended."
        }, indent=2)
    
    return {
        "review_feedback": feedback_text,
        "review_score": score,
        "review_passed": passed,
        "current_agent": "reviewer",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Reviewer: score={score}, passed={passed}"]
    }


# ─── AGENT NODE: FACT CHECKER ──────────────────────────────────────

def fact_check_node(state: AgentState) -> dict:
    """
    The Fact Checker agent extracts claims and verifies them against sources.

    READS: draft, topic, research_notes
    WRITES: extracted_claims, claim_verifications, accuracy_score, accuracy_passed, fact_revision_notes, agent_history

    Uses the unified fact-checker from shared/fact_checker.py.
    Runs AFTER reviewer but BEFORE convergence check.
    """
    from shared.fact_checker import (
        extract_claims, verify_claims, compute_accuracy_score, generate_revision_notes
    )

    logger.info("=" * 50)
    logger.info("FACT CHECKER AGENT - Verifying claims")
    logger.info("=" * 50)

    draft = state.get("draft", "")
    topic = state["topic"]
    research = state.get("research_notes", [])

    # Step 1: Extract claims
    claims = extract_claims(draft, topic)
    logger.info("Extracted %d claims from draft", len(claims))

    # Step 2: Verify claims against research notes + web search
    verifications = verify_claims(claims, research, web_search=True)
    logger.info("Verified %d claims", len(verifications))

    # Step 3: Compute accuracy score
    score = compute_accuracy_score(verifications)
    passed = score >= 9.5
    logger.info("Accuracy score: %.1f/10 | Passed (>=9.5): %s", score, passed)

    # Step 4: Generate revision notes if needed
    revision_notes = generate_revision_notes(verifications) if not passed else None

    return {
        "extracted_claims": claims,
        "claim_verifications": verifications,
        "accuracy_score": score,
        "accuracy_passed": passed,
        "fact_revision_notes": revision_notes,
        "current_agent": "fact_checker",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Fact Checker: accuracy={score:.1f}, passed={passed}"]
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
        "final_output": "",
        "extracted_claims": [],
        "claim_verifications": [],
        "accuracy_score": 0.0,
        "accuracy_passed": False,
        "fact_revision_notes": None,
    }
