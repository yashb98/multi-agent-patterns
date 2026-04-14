"""
Pattern 5: Plan-and-Execute
============================

Decomposes complex queries into a multi-step plan, executes steps sequentially,
evaluates after each step, and replans if needed.

Topology: planner → [step_executor → evaluator → replanner?]* → synthesizer

Convergence: max 7 steps, max 3 replans, 7-minute total timeout.
"""

import os
import sys
import time
from typing import TypedDict, Annotated, Optional
import operator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from langgraph.graph import StateGraph, START, END

from shared.agents import get_llm, smart_llm_call
from shared.state import prune_state
from shared.experiential_learning import Experience, get_shared_experience_memory
from shared.logging_config import get_logger, generate_run_id, set_run_id

logger = get_logger(__name__)

_experience_memory = get_shared_experience_memory()

# ── Constants ──
MAX_STEPS = 7
MAX_REPLANS = 3
STEP_TIMEOUT_S = 60
TOTAL_TIMEOUT_S = 420  # 7 minutes
QUALITY_THRESHOLD = 8.0
ACCURACY_THRESHOLD = 9.5


class Step(TypedDict):
    goal: str
    expected_output: str
    dependencies: list[int]
    delegate_to: str | None


class StepResult(TypedDict):
    step_index: int
    output: str
    success: bool


class PlanExecuteState(TypedDict):
    topic: str
    plan: list[Step]
    completed_steps: list[StepResult]
    current_step_index: int
    replan_count: int
    research_notes: Annotated[list[str], operator.add]
    final_output: str
    quality_score: float
    accuracy_score: float
    token_usage: Annotated[list[dict], operator.add]
    agent_history: Annotated[list[str], operator.add]
    start_time: float


def create_initial_state(topic: str) -> PlanExecuteState:
    return PlanExecuteState(
        topic=topic,
        plan=[],
        completed_steps=[],
        current_step_index=0,
        replan_count=0,
        research_notes=[],
        final_output="",
        quality_score=0.0,
        accuracy_score=0.0,
        token_usage=[],
        agent_history=[],
        start_time=time.time(),
    )


def _build_planner_prompt(topic: str) -> str:
    experiences = _experience_memory.retrieve("plan_and_execute", n=3)
    exp_context = ""
    if experiences:
        exp_context = "\n\nPast successful plans:\n" + "\n".join(
            f"- {e.successful_pattern}" for e in experiences
        )

    return (
        f"You are a planning agent. Break down this research query into 2-{MAX_STEPS} sequential steps.\n"
        f"Each step should have a clear goal and expected output.\n"
        f"Return a JSON array of objects with keys: goal, expected_output, dependencies (list of step indices), delegate_to (null or pattern name).\n"
        f"Only use delegate_to for steps that clearly match: peer_debate (comparisons), hierarchical (deep analysis), enhanced_swarm (general research).\n"
        f"{exp_context}\n\n"
        f"Query: {topic}\n\n"
        f"Return ONLY the JSON array, no markdown."
    )


def planner_node(state: PlanExecuteState) -> dict:
    """Decompose the topic into a multi-step plan."""
    import json as _json

    llm = get_llm()
    prompt = _build_planner_prompt(state["topic"])
    raw = smart_llm_call(llm, prompt)

    try:
        steps = _json.loads(raw)
    except _json.JSONDecodeError:
        import re
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            steps = _json.loads(match.group())
        else:
            steps = [{"goal": state["topic"], "expected_output": "Complete analysis", "dependencies": [], "delegate_to": None}]

    steps = steps[:MAX_STEPS]

    plan = [
        Step(
            goal=s.get("goal", ""),
            expected_output=s.get("expected_output", ""),
            dependencies=s.get("dependencies", []),
            delegate_to=s.get("delegate_to"),
        )
        for s in steps
    ]

    logger.info("Planner created %d steps for: %s", len(plan), state["topic"][:80])
    return {
        "plan": plan,
        "agent_history": [f"planner: created {len(plan)}-step plan"],
    }


def step_executor_node(state: PlanExecuteState) -> dict:
    """Execute the current step in the plan."""
    idx = state["current_step_index"]
    step = state["plan"][idx]

    context_parts = []
    for dep_idx in step["dependencies"]:
        if dep_idx < len(state["completed_steps"]):
            dep = state["completed_steps"][dep_idx]
            context_parts.append(f"Step {dep_idx} result: {dep['output'][:500]}")

    context = "\n".join(context_parts) if context_parts else "No prior context."

    if step["delegate_to"]:
        try:
            from jobpulse.pattern_router import run_with_pattern
            output = run_with_pattern(step["delegate_to"], f"{step['goal']}\n\nContext:\n{context}")
        except Exception as e:
            logger.warning("Delegation to %s failed: %s, using direct execution", step["delegate_to"], e)
            output = _execute_step_directly(step, context, state["topic"])
    else:
        output = _execute_step_directly(step, context, state["topic"])

    result = StepResult(step_index=idx, output=output, success=bool(output))

    logger.info("Step %d/%d executed: %s", idx + 1, len(state["plan"]), step["goal"][:60])
    return {
        "completed_steps": state["completed_steps"] + [result],
        "current_step_index": idx + 1,
        "research_notes": [f"Step {idx}: {output[:300]}"],
        "agent_history": [f"executor: step {idx} — {step['goal'][:60]}"],
    }


def _execute_step_directly(step: Step, context: str, topic: str) -> str:
    """Execute a step using a direct LLM call."""
    llm = get_llm()
    prompt = (
        f"You are executing one step of a research plan.\n\n"
        f"Overall topic: {topic}\n"
        f"Current step goal: {step['goal']}\n"
        f"Expected output: {step['expected_output']}\n\n"
        f"Context from previous steps:\n{context}\n\n"
        f"Execute this step thoroughly. Provide detailed, factual output."
    )
    return smart_llm_call(llm, prompt)
