"""
Pattern 5: Plan-and-Execute
============================

Decomposes complex queries into a multi-step plan, executes steps sequentially,
evaluates after each step, and replans if needed.

Topology: planner → [step_executor → evaluator → replanner?]* → synthesizer

Convergence: max 7 steps, max 3 replans, 7-minute total timeout.
"""

import os
import time
from typing import TypedDict, Annotated, Optional
import operator

from langgraph.graph import StateGraph, START, END

from shared.agents import get_llm, smart_llm_call, reviewer_node, fact_check_node, compute_cost_summary
from shared.cost_tracker import check_budget_from_state, BudgetExceededError
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
    eval_decision: str


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
        eval_decision="",
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


def evaluator_node(state: PlanExecuteState) -> dict:
    """Evaluate after each step — continue, replan, or synthesize."""
    elapsed = time.time() - state["start_time"]
    idx = state["current_step_index"]
    total = len(state["plan"])

    # Timeout check
    if elapsed > TOTAL_TIMEOUT_S:
        logger.warning("Total timeout reached (%.0fs), proceeding to synthesis", elapsed)
        return {"eval_decision": "synthesize", "agent_history": ["evaluator: timeout → synthesize"]}

    # All steps completed
    if idx >= total:
        return {"eval_decision": "synthesize", "agent_history": ["evaluator: all steps done → synthesize"]}

    # Check if last step failed
    if state["completed_steps"]:
        last = state["completed_steps"][-1]
        if not last["success"] or not last["output"].strip():
            if state["replan_count"] < MAX_REPLANS:
                return {"eval_decision": "replan", "agent_history": ["evaluator: step failed → replan"]}
            return {"eval_decision": "synthesize", "agent_history": ["evaluator: step failed, max replans → synthesize"]}

    return {"eval_decision": "continue", "agent_history": ["evaluator: continue to next step"]}


def replanner_node(state: PlanExecuteState) -> dict:
    """Regenerate the remaining plan based on completed steps."""
    import json as _json

    # Budget check before expensive replan
    try:
        check_budget_from_state(state, estimated_next_cost=0.05)
    except BudgetExceededError as e:
        logger.warning("Budget exceeded in plan_and_execute: %s", e)
        return {
            "plan": state["plan"][:state["current_step_index"]],
            "replan_count": state["replan_count"],
            "agent_history": [f"replanner: budget cap exceeded (${e.spent:.2f} > ${e.cap:.2f}), stopping"]
        }

    completed_summary = "\n".join(
        f"Step {s['step_index']}: {s['output'][:200]}" for s in state["completed_steps"]
    )
    remaining = state["plan"][state["current_step_index"]:]
    remaining_summary = "\n".join(f"- {s['goal']}" for s in remaining)

    llm = get_llm()
    prompt = (
        f"You are a replanning agent. The original plan needs adjustment.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Completed steps:\n{completed_summary}\n\n"
        f"Remaining plan:\n{remaining_summary}\n\n"
        f"Based on what we've learned, regenerate the remaining steps.\n"
        f"Return a JSON array of step objects (goal, expected_output, dependencies, delegate_to).\n"
        f"Max {MAX_STEPS - state['current_step_index']} steps. Return ONLY the JSON array."
    )
    raw = smart_llm_call(llm, prompt)

    try:
        new_steps = _json.loads(raw)
    except _json.JSONDecodeError:
        import re
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        new_steps = _json.loads(match.group()) if match else [{"goal": state["topic"], "expected_output": "analysis", "dependencies": [], "delegate_to": None}]

    new_steps = new_steps[:MAX_STEPS - state["current_step_index"]]
    new_plan = list(state["plan"][:state["current_step_index"]]) + [
        Step(goal=s.get("goal", ""), expected_output=s.get("expected_output", ""),
             dependencies=s.get("dependencies", []), delegate_to=s.get("delegate_to"))
        for s in new_steps
    ]

    new_count = state["replan_count"] + 1
    logger.info("Replan %d: %d remaining steps regenerated", new_count, len(new_steps))

    try:
        exp = Experience(
            task_description=f"Replan #{new_count}: {state['topic'][:200]}",
            successful_pattern=f"Replanned after step {state['current_step_index']}: {len(new_steps)} new steps",
            score=5.0,
            domain="plan_and_execute",
        )
        _experience_memory.add(exp)
    except Exception:
        pass

    return {
        "plan": new_plan,
        "replan_count": new_count,
        "agent_history": [f"replanner: replan #{new_count}, {len(new_steps)} new steps"],
    }


def synthesizer_node(state: PlanExecuteState) -> dict:
    """Combine all step outputs into a coherent final response."""
    step_outputs = "\n\n".join(
        f"## Step {s['step_index'] + 1}: {state['plan'][s['step_index']]['goal'] if s['step_index'] < len(state['plan']) else 'Unknown'}\n{s['output']}"
        for s in state["completed_steps"]
        if s["success"]
    )

    llm = get_llm()
    prompt = (
        f"You are a synthesis agent. Combine these research step outputs into a coherent, "
        f"well-structured final response.\n\n"
        f"Original query: {state['topic']}\n\n"
        f"Step outputs:\n{step_outputs}\n\n"
        f"Synthesize into a comprehensive response. Preserve key findings and insights."
    )
    final = smart_llm_call(llm, prompt)

    review = reviewer_node({**state, "draft": final})
    quality = review.get("review_score", 0.0)
    fact = fact_check_node({**state, "draft": final})
    accuracy = fact.get("accuracy_score", 0.0)

    if quality >= 7.0:
        try:
            plan_summary = " → ".join(s["goal"] for s in state["plan"])
            exp = Experience(
                task_description=state["topic"][:300],
                successful_pattern=f"Plan: {plan_summary}. Replans: {state['replan_count']}",
                score=quality,
                domain="plan_and_execute",
            )
            _experience_memory.add(exp)
        except Exception:
            pass

    cost = compute_cost_summary(state.get("token_usage", []))

    logger.info("Synthesizer completed: quality=%.1f, accuracy=%.1f, cost=$%.4f", quality, accuracy, cost["total_cost_usd"])
    return {
        "final_output": final,
        "quality_score": quality,
        "accuracy_score": accuracy,
        "cost_estimate": cost,
        "agent_history": [f"synthesizer: quality={quality}, accuracy={accuracy}, cost=${cost['total_cost_usd']:.4f}"],
    }


# ── Graph Construction ──

def _route_after_eval(state: PlanExecuteState) -> str:
    """Route based on evaluator decision."""
    decision = state.get("eval_decision", "synthesize")
    if decision == "continue":
        return "step_executor"
    if decision == "replan" and state.get("replan_count", 0) < MAX_REPLANS:
        return "replanner"
    return "synthesizer"


def build_plan_execute_graph(checkpointer=None):
    """Build the plan-and-execute LangGraph."""
    graph = StateGraph(PlanExecuteState)

    graph.add_node("planner", planner_node)
    graph.add_node("step_executor", step_executor_node)
    graph.add_node("evaluator", evaluator_node)
    graph.add_node("replanner", replanner_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "step_executor")
    graph.add_edge("step_executor", "evaluator")
    graph.add_conditional_edges("evaluator", _route_after_eval, {
        "step_executor": "step_executor",
        "replanner": "replanner",
        "synthesizer": "synthesizer",
    })
    graph.add_edge("replanner", "step_executor")
    graph.add_edge("synthesizer", END)

    return graph.compile(checkpointer=checkpointer)


def run_plan_execute(topic: str) -> dict:
    """Run the plan-and-execute pattern."""
    run_id = generate_run_id()
    set_run_id(run_id)
    logger.info("Starting plan-and-execute [%s] topic=%s", run_id, topic[:80])

    initial_state = create_initial_state(topic)
    graph = build_plan_execute_graph()
    final_state = graph.invoke(initial_state)

    logger.info("Plan-and-execute complete. Steps: %d, Replans: %d",
                len(final_state.get("completed_steps", [])), final_state.get("replan_count", 0))
    return final_state


if __name__ == "__main__":
    result = run_plan_execute("Compare FastAPI vs Django vs Flask for building webhook endpoints")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    with open(f"{output_dir}/plan_execute_output.md", "w") as f:
        f.write(result.get("final_output", "No output"))
