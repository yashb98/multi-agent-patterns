"""
Pattern 3: Dynamic Swarm
=========================

ARCHITECTURE:
    Task Analyzer → Priority Queue → Dynamic Agent Selection → Execution → Re-evaluate
    
    There is NO fixed sequence and NO supervisor.
    Instead, a task queue holds work items, and the system dynamically
    picks which agent should handle the highest-priority task.

KEY DIFFERENCE FROM HIERARCHY AND DEBATE:
- Hierarchy: Fixed agent order decided by supervisor
- Debate: Fixed rounds, all agents participate every round
- Swarm: DYNAMIC agent selection based on what the task NEEDS right now

The swarm pattern is based on three principles:
1. TASK DECOMPOSITION: Break the goal into atomic tasks with priorities
2. DYNAMIC ROUTING: Pick the best agent for the highest-priority task
3. RE-EVALUATION: After each task, re-assess what's needed next

THIS IS THE PATTERN USED BY:
- OpenAI's Swarm framework
- Self-organising multi-agent research systems
- Production systems where task complexity is unknown upfront
- Your AgentForge Arena concept (agents adapt to tournament challenges)
- Autonomous coding agents that decide what to build/test/fix next

WHEN TO USE:
✅ Task complexity is unknown upfront
✅ Different sub-tasks need different specialist agents
✅ You want the system to ADAPT as it learns more about the problem
✅ Parallelisation opportunities emerge dynamically

WHEN NOT TO USE:
❌ Task flow is well-known and predictable (use hierarchy)
❌ Quality through debate matters more than adaptability (use debate)
❌ You need deterministic, reproducible execution for compliance
❌ Debugging opacity is unacceptable
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
    create_initial_state,
    get_llm,
    compute_cost_summary,
)
from langchain_core.messages import SystemMessage, HumanMessage
from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── TASK ANALYZER NODE ─────────────────────────────────────────
# This is the BRAIN of the swarm. Unlike the supervisor (which
# follows fixed rules), the task analyzer DECOMPOSES the current
# situation into a priority queue of tasks.
#
# Example output:
#   [
#     {"agent": "researcher", "priority": 1, "task": "Gather core facts about topic"},
#     {"agent": "researcher", "priority": 2, "task": "Find recent examples"},  
#     {"agent": "writer", "priority": 3, "task": "Write first draft"},
#     {"agent": "reviewer", "priority": 4, "task": "Review draft quality"},
#   ]
#
# The key insight: the task analyzer can CREATE NEW TASKS dynamically.
# After the reviewer runs and finds gaps, the task analyzer might add:
#   {"agent": "researcher", "priority": 1, "task": "Research the gap in section 3"}
#
# This is fundamentally different from the hierarchy (fixed routing rules)
# and debate (fixed round structure).

TASK_ANALYZER_PROMPT = """You are a Task Analyzer for a dynamic agent swarm.

Given the current state of a blog writing project, decompose what needs 
to happen next into a prioritised task queue.

AVAILABLE AGENTS:
- "researcher": Gathers information, finds facts, identifies sources
- "writer": Writes or revises blog article drafts  
- "reviewer": Evaluates quality and provides structured feedback

RULES:
1. Return a JSON array of task objects
2. Each task has: "agent" (string), "priority" (1=highest), "description" (string)
3. Only include tasks that are ACTUALLY NEEDED based on current state
4. Maximum 3 tasks at a time (keeps the swarm focused)
5. If everything is done, return an empty array []

Consider:
- What's missing from the current state?
- What's the highest-impact action right now?
- Are there tasks that could run in parallel?

Respond with ONLY the JSON array. No explanation."""


def task_analyzer_node(state: AgentState) -> dict:
    """
    Analyses the current state and generates a priority task queue.
    
    This is where the swarm's intelligence lives. It looks at:
    - What research exists (enough? gaps?)
    - Whether a draft exists (quality? completeness?)
    - What the reviewer said (specific issues to address?)
    - How many iterations have passed (diminishing returns?)
    
    Then it generates the MINIMUM set of tasks needed to make progress.
    """
    logger.info("TASK ANALYZER - Decomposing work...")
    
    iteration = state.get("iteration", 0)
    
    # Build state summary for the LLM
    state_summary = f"""PROJECT STATE:
- Topic: {state['topic']}
- Research notes: {len(state.get('research_notes', []))} entries ({sum(len(r) for r in state.get('research_notes', []))} total chars)
- Draft exists: {bool(state.get('draft', ''))}
- Draft word count: {len(state.get('draft', '').split())}
- Review score: {state.get('review_score', 0)}/10
- Review passed: {state.get('review_passed', False)}
- Review feedback summary: {state.get('review_feedback', 'None')[:500]}
- Iterations completed: {iteration}
- Max iterations: 3

AGENT HISTORY (last 5 actions):
{chr(10).join(state.get('agent_history', [])[-5:])}"""
    
    # Check for obvious terminal condition first (saves an LLM call)
    if state.get("review_passed", False) and state.get("accuracy_passed", True):
        logger.info("Review and accuracy passed -- no tasks needed")
        return {
            "pending_tasks": [],
            "agent_history": ["Task Analyzer: No tasks needed, review and accuracy passed"]
        }
    
    if iteration >= 3:
        logger.info("Max iterations reached -- no more tasks")
        return {
            "pending_tasks": [],
            "agent_history": ["Task Analyzer: Max iterations reached, stopping"]
        }
    
    # Ask the LLM to decompose
    llm = get_llm(temperature=0.2)
    response = llm.invoke([
        SystemMessage(content=TASK_ANALYZER_PROMPT),
        HumanMessage(content=state_summary)
    ])
    
    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0]
    
    try:
        tasks = json.loads(raw)
        if not isinstance(tasks, list):
            tasks = []
    except json.JSONDecodeError:
        logger.info("Could not parse tasks, using fallback")
        # Fallback: determine tasks from state programmatically
        tasks = _fallback_task_decomposition(state)
    
    # Sort by priority
    tasks.sort(key=lambda t: t.get("priority", 99))
    
    logger.info("Generated %d tasks:", len(tasks))
    for t in tasks:
        logger.info("  [%s] %s: %s", t.get("priority", "?"), t.get("agent", "?"), t.get("description", "?"))
    
    return {
        "pending_tasks": tasks,
        "agent_history": [f"Task Analyzer: generated {len(tasks)} tasks"]
    }


def _fallback_task_decomposition(state: AgentState) -> list:
    """
    Deterministic fallback if LLM task decomposition fails.
    
    This ensures the swarm never gets stuck even if the LLM
    produces unparseable output. It follows simple rules
    similar to the hierarchical supervisor.
    """
    tasks = []
    
    if not state.get("research_notes", []):
        tasks.append({
            "agent": "researcher",
            "priority": 1,
            "description": "Conduct initial research on the topic"
        })
    elif not state.get("draft", ""):
        tasks.append({
            "agent": "writer",
            "priority": 1,
            "description": "Write the first draft based on research"
        })
    elif not state.get("review_feedback", ""):
        tasks.append({
            "agent": "reviewer",
            "priority": 1,
            "description": "Review the current draft"
        })
    elif not state.get("review_passed", False):
        tasks.append({
            "agent": "writer",
            "priority": 1,
            "description": "Revise draft based on review feedback"
        })
    
    return tasks


# ─── TASK EXECUTOR NODE ─────────────────────────────────────────
# This node picks the highest-priority task from the queue
# and routes to the appropriate agent.
#
# KEY DIFFERENCE FROM SUPERVISOR:
# The supervisor has hardcoded routing logic.
# The task executor simply reads the queue and dispatches.
# The INTELLIGENCE is in the task analyzer, not the executor.

def task_executor_node(state: AgentState) -> dict:
    """
    Picks the highest-priority task and executes it.
    
    This is a dispatcher — it reads the task queue and calls
    the appropriate agent function directly.
    
    WHY NOT USE CONDITIONAL EDGES?
    Because the swarm needs to execute tasks that were dynamically
    generated. We can't pre-register conditional edges for tasks
    that don't exist yet. Instead, the executor calls agent functions
    directly as regular Python function calls.
    
    This is a key architectural decision: the swarm trades LangGraph's
    built-in routing for runtime flexibility.
    """
    logger.info("TASK EXECUTOR - Processing queue...")
    
    tasks = state.get("pending_tasks", [])
    
    if not tasks:
        logger.info("No tasks in queue")
        return {
            "current_agent": "FINISH",
            "agent_history": ["Task Executor: empty queue, finishing"]
        }
    
    # Pick the highest priority task (already sorted)
    task = tasks[0]
    remaining = tasks[1:]  # Remove from queue
    
    agent_name = task.get("agent", "")
    description = task.get("description", "No description")
    
    logger.info("Executing: [%s] %s", agent_name, description)
    
    # Dispatch to the appropriate agent
    # We call the agent functions DIRECTLY here.
    # This is the swarm pattern: dynamic dispatch at runtime.
    agent_map = {
        "researcher": researcher_node,
        "writer": writer_node,
        "reviewer": risk_aware_reviewer_node,
    }
    
    agent_fn = agent_map.get(agent_name)
    
    if agent_fn is None:
        logger.info("Unknown agent '%s', skipping", agent_name)
        return {
            "pending_tasks": remaining,
            "agent_history": [f"Task Executor: unknown agent '{agent_name}', skipped"]
        }
    
    # Execute the agent and merge its state updates
    agent_result = agent_fn(state)
    
    # Combine executor metadata with agent results
    result = {
        **agent_result,
        "pending_tasks": remaining,
        "current_agent": agent_name,
    }
    
    # Ensure agent_history is properly merged (both executor and agent entries)
    executor_history = [f"Task Executor: ran {agent_name} — {description}"]
    agent_history = agent_result.get("agent_history", [])
    result["agent_history"] = executor_history + agent_history
    
    return result


# ─── SWARM ROUTING ──────────────────────────────────────────────

def should_continue_swarm(state: AgentState) -> str:
    """
    After the task executor runs, decide: analyse more tasks or finish?
    
    If there are remaining tasks in the queue, go back to executor.
    If the queue is empty, go to task_analyzer to re-evaluate.
    If the task analyzer produced no tasks, we're done.
    """
    tasks = state.get("pending_tasks", [])
    current = state.get("current_agent", "")
    review_passed = state.get("review_passed", False)
    iteration = state.get("iteration", 0)
    
    if review_passed and state.get("accuracy_passed", True):
        return "finish"

    if iteration >= 3:
        return "finish"
    
    if current == "FINISH":
        return "finish"
    
    if tasks:
        # More tasks in queue — keep executing
        return "executor"
    else:
        # Queue empty — re-analyse to see if more work is needed
        return "analyzer"


def should_continue_after_analysis(state: AgentState) -> str:
    """
    After the task analyzer runs, decide: execute tasks or finish?
    
    If the analyzer generated tasks, go to executor.
    If no tasks (everything is done), finish.
    """
    tasks = state.get("pending_tasks", [])
    
    if tasks:
        return "executor"
    return "finish"


# ─── FINISH NODE ─────────────────────────────────────────────────

def swarm_finish_node(state: AgentState) -> dict:
    """
    Terminal node for the swarm.
    """
    logger.info("SWARM FINISH - Packaging output")
    
    draft = state.get("draft", "No draft produced")
    score = state.get("review_score", 0)
    iterations = state.get("iteration", 0)
    history = state.get("agent_history", [])
    
    # Count total agent executions
    executions = sum(1 for h in history if "Task Executor: ran" in h)
    
    cost = compute_cost_summary(state.get("token_usage", []))

    logger.info("Final score: %s/10", score)
    logger.info("Total iterations: %d", iterations)
    logger.info("Total agent executions: %d", executions)
    logger.info("Total cost: $%.4f (%d LLM calls)", cost["total_cost_usd"], cost["calls"])

    return {
        "final_output": draft,
        "total_cost_usd": cost["total_cost_usd"],
        "agent_history": [f"Swarm complete. Score: {score}/10, {executions} agent runs, Cost: ${cost['total_cost_usd']:.4f}"]
    }


# ─── BUILD THE GRAPH ────────────────────────────────────────────
# 
# The swarm graph looks fundamentally different:
#
# HIERARCHICAL:  supervisor ←→ workers (hub and spoke)
# DEBATE:        pipeline → debate_loop → synthesis
# SWARM:         analyzer ←→ executor → analyzer (task queue loop)
#
#     START
#       ↓
#   task_analyzer ──→ (no tasks) ──→ finish
#       ↓ (has tasks)
#   task_executor ──→ (queue empty) ──→ task_analyzer
#       ↓ (more in queue)
#   task_executor (loop to self via analyzer)
#
# The swarm is the most ADAPTIVE pattern because:
# 1. The task analyzer can generate DIFFERENT tasks each time
# 2. The executor processes them one by one
# 3. After the queue drains, re-analysis might find NEW tasks
#    based on what the agents discovered

def build_swarm_graph():
    """
    Constructs the LangGraph StateGraph for the dynamic swarm pattern.
    
    KEY STRUCTURAL DIFFERENCES:
    - No supervisor, no debate rounds
    - Task analyzer generates dynamic work items
    - Task executor dispatches to agent functions directly
    - Two conditional edges (after executor AND after analyzer)
    """
    graph = StateGraph(AgentState)
    
    # ── Add nodes ──
    graph.add_node("analyzer", task_analyzer_node)
    graph.add_node("executor", task_executor_node)
    graph.add_node("finish", swarm_finish_node)
    
    # ── Entry point ──
    # Start with task analysis
    graph.add_edge(START, "analyzer")
    
    # ── After analysis: execute or finish ──
    graph.add_conditional_edges(
        "analyzer",
        should_continue_after_analysis,
        {
            "executor": "executor",
            "finish": "finish",
        }
    )
    
    # ── After execution: more tasks, re-analyse, or finish ──
    graph.add_conditional_edges(
        "executor",
        should_continue_swarm,
        {
            "executor": "executor",   # More tasks in queue
            "analyzer": "analyzer",   # Queue empty, re-evaluate
            "finish": "finish",       # Done
        }
    )
    
    # ── Terminal ──
    graph.add_edge("finish", END)
    
    compiled = graph.compile()
    
    logger.info("Dynamic Swarm graph compiled successfully")
    logger.info("Nodes: analyzer, executor, finish")
    logger.info("Loop: analyzer -> executor -> (executor | analyzer | finish)")
    
    return compiled


# ─── RUN THE PATTERN ─────────────────────────────────────────────

def run_swarm(topic: str):
    """
    End-to-end execution of the dynamic swarm pattern.
    """
    from shared.logging_config import generate_run_id, set_run_id
    run_id = generate_run_id()
    set_run_id(run_id)
    logger.info("Starting dynamic swarm [%s] topic=%s", run_id, topic[:80])
    
    initial_state = create_initial_state(topic)
    graph = build_swarm_graph()
    final_state = graph.invoke(initial_state)
    
    # Log summary
    logger.info("SWARM COMPLETE")
    logger.info("Swarm execution history:")
    for entry in final_state.get("agent_history", []):
        logger.info("  %s", entry)

    logger.info("Final article: %d words", len(final_state.get("final_output", "").split()))
    logger.info("Final score: %s/10", final_state.get("review_score", 0))
    logger.info("Iterations: %d", final_state.get("iteration", 0))
    
    return final_state


# ─── MAIN ────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_swarm("How AI Agents Are Changing Software Development in 2026")
    
    _output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")
    os.makedirs(_output_dir, exist_ok=True)
    with open(os.path.join(_output_dir, "swarm_output.md"), "w") as f:
        f.write(result.get("final_output", "No output"))

    logger.info("Output saved to outputs/swarm_output.md")
