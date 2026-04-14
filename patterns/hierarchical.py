"""
Pattern 1: Hierarchical Supervisor
===================================

ARCHITECTURE:
    Supervisor (hub) ←→ Worker agents (spokes)
    
The Supervisor is a special node that:
1. Reads the current state
2. Decides which worker should run next
3. Routes execution to that worker
4. Receives control back after the worker finishes
5. Repeats until the task is complete

IN LANGGRAPH TERMS:
- The Supervisor is a node with CONDITIONAL EDGES
- Workers are nodes with FIXED EDGES (always return to supervisor)
- The graph loops: supervisor → worker → supervisor → worker → ...
- The loop terminates when the supervisor says "FINISH"

THIS IS THE PATTERN USED BY:
- AutoGen's GroupChatManager
- CrewAI's default sequential/hierarchical mode
- Most production agentic systems (customer support, RAG pipelines)
- Your DataMind Enterprise architecture (the orchestrator tier)

WHEN TO USE:
✅ Clear hierarchy of tasks (research → write → review)
✅ You need a single point of control and observability  
✅ Workers don't need to talk to each other directly
✅ You want predictable, debuggable execution flow

WHEN NOT TO USE:
❌ Agents need to negotiate or debate (use peer debate)
❌ Task complexity is unknown upfront (use dynamic swarm)
❌ The supervisor becomes a bottleneck in high-throughput systems
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
from shared.prompts import SUPERVISOR_PROMPT
from shared.experiential_learning import Experience, get_shared_experience_memory
from shared.memory_layer import get_shared_memory_manager
from shared.convergence import ConvergenceController
from langchain_core.messages import SystemMessage, HumanMessage
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ─── MODULE-LEVEL MEMORY SINGLETON ──────────────────────────────
# Shared singleton: all patterns share the same episodic/semantic/
# procedural stores and GRPO experiences across the process lifetime.
_experience_memory = get_shared_experience_memory()
_memory_manager = get_shared_memory_manager()
_convergence = ConvergenceController()


# ─── THE SUPERVISOR NODE ────────────────────────────────────────
# This is the BRAIN of the hierarchical pattern.
# Unlike worker nodes (which do LLM work), the supervisor's job
# is purely ROUTING — it decides who goes next.
#
# TWO APPROACHES to implementing a supervisor:
#
# Approach A: LLM-based (flexible, can handle unexpected states)
#   → Ask an LLM "given this state, who should run next?"
#   → Pro: Adapts to novel situations
#   → Con: Slower, costs money, can hallucinate invalid agent names
#
# Approach B: Rule-based (fast, deterministic, predictable)
#   → Use if/elif logic on state fields
#   → Pro: Instant, free, always valid
#   → Con: Can't handle unexpected situations
#
# We'll implement BOTH so you understand the tradeoffs.
# Production systems usually use Approach B with Approach A as fallback.

def supervisor_node_rule_based(state: AgentState) -> dict:
    """
    APPROACH B: Deterministic rule-based supervisor.
    
    This is what most production systems use. The routing logic is
    explicit, testable, and costs zero LLM calls.
    
    DECISION TREE:
    1. No research yet?           → researcher
    2. Research exists, no draft?  → writer  
    3. Draft exists, not reviewed? → reviewer
    4. Review failed, iter < 3?    → writer (revise)
    5. Review passed OR iter >= 3? → FINISH
    """
    logger.info("=" * 50)
    logger.info("SUPERVISOR (rule-based) - Evaluating state...")
    logger.info("=" * 50)

    research = state.get("research_notes", [])
    draft = state.get("draft", "")
    review_passed = state.get("review_passed", False)
    review_feedback = state.get("review_feedback", "")
    iteration = state.get("iteration", 0)

    # Log current state for observability
    logger.info("Research notes: %d entries", len(research))
    logger.info("Draft exists: %s", bool(draft))
    logger.info("Review passed: %s", review_passed)
    logger.info("Iteration: %d", iteration)
    
    # ── Decision logic ──
    if not research:
        next_agent = "researcher"
        reason = "No research gathered yet"
    elif not draft:
        next_agent = "writer"
        reason = "Research complete, need first draft"
    elif not review_feedback:
        next_agent = "reviewer"
        reason = "Draft needs review"
    elif not review_passed and iteration < 3:
        # Quality gate: overall score < 8.0
        next_agent = "writer"
        reason = f"Review failed (score: {state.get('review_score', 0)}), revision needed"
    elif review_passed and not state.get("accuracy_passed", False) and state.get("accuracy_score", 0) == 0:
        # Quality passed but no fact-check yet — run fact checker
        next_agent = "fact_checker"
        reason = "Quality passed, running fact-check"
    elif not state.get("accuracy_passed", False) and iteration < 3:
        # Fact-check failed — revise with specific fix instructions
        next_agent = "writer"
        reason = f"Accuracy failed ({state.get('accuracy_score', 0):.1f}/10), fact revision needed"
    else:
        # Delegate final finish/continue decision to unified controller
        convergence = _convergence.check(state)
        next_agent = "FINISH"
        reason = convergence.reason
    
    logger.info("Decision: %s", next_agent)
    logger.info("Reason: %s", reason)

    # Prune state between iterations to prevent context bloat
    from shared.state import prune_state
    result = {
        "current_agent": next_agent,
        "agent_history": [f"Supervisor → {next_agent} ({reason})"]
    }
    result.update(prune_state(state))
    return result


def supervisor_node_llm_based(state: AgentState) -> dict:
    """
    APPROACH A: LLM-based supervisor.
    
    Asks the LLM to decide the next agent. More flexible but slower.
    Useful when routing logic is too complex for if/elif rules,
    or when you want the supervisor to consider nuances in the
    review feedback that simple rules would miss.
    
    IMPORTANT SAFETY MEASURE: We validate the LLM's output against
    a whitelist of valid agent names. LLMs can hallucinate invalid
    names ("editor", "fact_checker"), so we MUST validate.
    """
    logger.info("=" * 50)
    logger.info("SUPERVISOR (LLM-based) - Evaluating state...")
    logger.info("=" * 50)
    
    # Build a state summary for the LLM
    state_summary = f"""Current state:
- Topic: {state['topic']}
- Research notes: {len(state.get('research_notes', []))} entries collected
- Draft exists: {bool(state.get('draft', ''))}
- Draft length: {len(state.get('draft', '').split())} words
- Review feedback exists: {bool(state.get('review_feedback', ''))}
- Review score: {state.get('review_score', 0)}/10
- Review passed: {state.get('review_passed', False)}
- Current iteration: {state.get('iteration', 0)} of max 3
- Agent history: {state.get('agent_history', [])[-3:]}"""
    
    llm = get_llm(temperature=0.0)  # Zero temp for deterministic routing
    response = llm.invoke([
        SystemMessage(content=SUPERVISOR_PROMPT),
        HumanMessage(content=state_summary)
    ])
    
    raw_decision = response.content.strip().lower()
    
    # ── VALIDATION: Only accept known agent names ──
    valid_agents = {"researcher", "writer", "reviewer", "finish"}
    
    # Extract the agent name (LLM might add extra text)
    next_agent = "FINISH"  # Safe default
    for valid in valid_agents:
        if valid in raw_decision:
            next_agent = valid if valid != "finish" else "FINISH"
            break
    
    logger.info("LLM raw output: '%s'", raw_decision)
    logger.info("Validated decision: %s", next_agent)
    
    return {
        "current_agent": next_agent,
        "agent_history": [f"Supervisor (LLM) → {next_agent}"]
    }


# ─── ROUTING FUNCTION ───────────────────────────────────────────
# This is the function that LangGraph calls to determine which
# edge to follow from the supervisor node.
#
# It reads the 'current_agent' field (set by the supervisor)
# and returns the name of the next node in the graph.
#
# The mapping MUST match the node names registered in the graph.

def route_from_supervisor(state: AgentState) -> str:
    """
    Conditional edge function: reads supervisor's decision from state
    and returns the next node name.
    
    LangGraph calls this after the supervisor runs. The return value
    must be one of the keys in the routing map passed to
    add_conditional_edges().
    
    WHY A SEPARATE FUNCTION?
    Because LangGraph's conditional edges need a pure function
    that maps state → string. The supervisor sets the decision
    in state; this function reads it. Clean separation.
    """
    next_agent = state.get("current_agent", "FINISH")
    
    if next_agent == "FINISH":
        return "finish"
    return next_agent


# ─── FINISH NODE ─────────────────────────────────────────────────

def finish_node(state: AgentState) -> dict:
    """
    Terminal node: packages the final output.
    
    This node runs when the supervisor decides we're done.
    It takes the current draft and marks it as the final output.
    """
    logger.info("=" * 50)
    logger.info("FINISH - Packaging final output")
    logger.info("=" * 50)

    draft = state.get("draft", "No draft produced")
    score = state.get("review_score", 0)
    iterations = state.get("iteration", 0)

    cost = compute_cost_summary(state.get("token_usage", []))

    logger.info("Final score: %s/10", score)
    logger.info("Total iterations: %d", iterations)
    logger.info("Article length: %d words", len(draft.split()))
    logger.info("Total cost: $%.4f (%d LLM calls)", cost['total_cost_usd'], cost['calls'])
    for agent, c in cost.get("cost_per_agent", {}).items():
        logger.info("  %s: $%.4f", agent, c)

    # Extract experiential learning from high-scoring runs
    if score >= 7.0:
        exp = Experience(
            task_description=state.get("topic", "")[:200],
            successful_pattern=(
                f"Hierarchical pattern scored {score}/10 in {iterations} iterations. "
                f"Feedback: {state.get('review_feedback', '')[:300]}"
            ),
            score=score,
            domain="writing",
        )
        _experience_memory.add(exp)
        logger.info("Stored hierarchical experience (score: %s)", score)

    return {
        "final_output": draft,
        "total_cost_usd": cost["total_cost_usd"],
        "agent_history": [f"System complete. Score: {score}/10, Iterations: {iterations}, Cost: ${cost['total_cost_usd']:.4f}"]
    }


# ─── BUILD THE GRAPH ────────────────────────────────────────────
# This is where the architecture comes alive.
# 
# The graph has 5 nodes:
#   supervisor → researcher → writer → reviewer → finish
#
# But the EDGES are what make it hierarchical:
#   - supervisor has CONDITIONAL edges (can go to any worker or finish)
#   - ALL workers have FIXED edges (always return to supervisor)
#   - finish is a terminal node connected to END
#
# This creates the hub-and-spoke pattern.

def build_hierarchical_graph(use_llm_supervisor: bool = False):
    """
    Constructs the LangGraph StateGraph for the hierarchical pattern.
    
    Parameters:
        use_llm_supervisor: If True, use LLM-based routing.
                           If False, use deterministic rules.
    
    Returns:
        A compiled LangGraph ready to invoke.
    """
    # 1. Create the graph with our state schema
    graph = StateGraph(AgentState)
    
    # 2. Add all nodes
    #    Each node is a (name, function) pair.
    #    The name is used in edges to reference this node.
    supervisor_fn = supervisor_node_llm_based if use_llm_supervisor else supervisor_node_rule_based
    
    graph.add_node("supervisor", supervisor_fn)
    graph.add_node("researcher", researcher_node)
    graph.add_node("writer", writer_node)
    graph.add_node("reviewer", risk_aware_reviewer_node)
    graph.add_node("fact_checker", fact_check_node)
    graph.add_node("finish", finish_node)
    
    # 3. Set the entry point
    #    When the graph starts, it goes to the supervisor first.
    graph.add_edge(START, "supervisor")
    
    # 4. Add CONDITIONAL edges from supervisor
    #    The route_from_supervisor function reads state and returns
    #    one of the keys in this mapping dict.
    #    
    #    Key insight: the dict maps function return values → node names.
    #    If route_from_supervisor returns "researcher", execution goes
    #    to the "researcher" node.
    graph.add_conditional_edges(
        "supervisor",                    # From this node...
        route_from_supervisor,           # Call this function...
        {                                # And map results to nodes:
            "researcher": "researcher",
            "writer": "writer",
            "reviewer": "reviewer",
            "fact_checker": "fact_checker",
            "finish": "finish",
        }
    )
    
    # 5. Add FIXED edges: all workers return to supervisor
    #    This is the "spoke → hub" connection.
    #    No matter what a worker does, control flows back to
    #    the supervisor for the next routing decision.
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("writer", "supervisor")
    graph.add_edge("reviewer", "supervisor")
    graph.add_edge("fact_checker", "supervisor")
    
    # 6. Finish node goes to END (terminal)
    graph.add_edge("finish", END)
    
    # 7. Compile and return
    #    Compilation validates the graph structure:
    #    - All referenced nodes exist
    #    - All conditional edge outputs are valid node names
    #    - The graph is reachable from START to END
    compiled = graph.compile()
    
    logger.info("Hierarchical graph compiled successfully")
    logger.info("Nodes: supervisor, researcher, writer, reviewer, finish")
    logger.info("Supervisor type: %s", "LLM-based" if use_llm_supervisor else "Rule-based")
    
    return compiled


# ─── RUN THE PATTERN ─────────────────────────────────────────────

def run_hierarchical(topic: str, use_llm_supervisor: bool = False, domain: str = ""):
    """
    End-to-end execution of the hierarchical supervisor pattern.

    Implements all 3 operational principles:
    1. Memory before action — search patterns before starting
    2. 3-tier routing — check cache/booster before each agent
    3. Learn after success — store pattern if score >= 7.0
    """
    from shared.logging_config import generate_run_id, set_run_id
    run_id = generate_run_id()
    set_run_id(run_id)
    logger.info("Starting hierarchical pattern [%s] topic=%s", run_id, topic[:80])

    logger.info("=" * 60)
    logger.info("HIERARCHICAL SUPERVISOR PATTERN")
    logger.info("Topic: %s", topic)
    logger.info("=" * 60)

    # ── Operational Principle #1: Memory before action ──
    memory = _memory_manager
    memory.start_new_session()
    pattern, pattern_score = memory.search_patterns(topic, domain)

    if pattern and pattern_score > 0.7:
        logger.info("[REUSE] Found pattern from '%s' (score: %.2f)", pattern.topic, pattern_score)
        logger.info("[REUSE] Original agents: %s", pattern.agents_used)
        logger.info("[REUSE] Original routing: %s", pattern.routing_decisions)
        logger.info("[REUSE] Applying learned strengths: %s", pattern.strengths)

    # Create initial state
    initial_state = create_initial_state(topic)

    # Build and compile the graph
    graph = build_hierarchical_graph(use_llm_supervisor)

    # Invoke the graph
    final_state = graph.invoke(initial_state)

    # Record per-agent steps in short-term memory for this run
    for entry in final_state.get("agent_history", []):
        if "Supervisor →" in entry:
            agent = entry.split("→")[1].strip().split(" ")[0].lower()
            memory.record_step(agent, entry)

    # ── Operational Principle #4: Learn after success ──
    final_score = final_state.get("review_score", 0)
    if final_score >= 7.0:
        memory.learn_from_success(
            topic=topic,
            domain=domain,
            agents_used=["researcher", "writer", "reviewer"],
            routing_decisions=final_state.get("agent_history", []),
            final_score=final_score,
            iterations=final_state.get("iteration", 0),
            strengths=_extract_strengths(final_state),
            output_summary=final_state.get("final_output", "")[:500],
        )
        # ── Operational Principle #4b: Store successful procedure ──
        memory.learn_procedure(
            domain=domain or "writing",
            strategy=(
                f"Hierarchical supervisor pattern: research → write → review. "
                f"Converged in {final_state.get('iteration', 0)} iterations "
                f"with score {final_score}/10."
            ),
            context=topic[:200],
            score=final_score,
            source="hierarchical",
        )

    # Also record as episodic memory
    memory.record_episode(
        topic=topic,
        final_score=final_score,
        iterations=final_state.get("iteration", 0),
        pattern_used="hierarchical",
        agents_used=["researcher", "writer", "reviewer"],
        strengths=_extract_strengths(final_state),
        weaknesses=_extract_weaknesses(final_state),
        output_summary=final_state.get("final_output", "")[:500],
        domain=domain,
    )

    # Log summary
    logger.info("=" * 60)
    logger.info("EXECUTION COMPLETE")
    logger.info("=" * 60)
    logger.info("Agent execution history:")
    for entry in final_state.get("agent_history", []):
        logger.info("  %s", entry)

    logger.info("Final article: %d words", len(final_state.get('final_output', '').split()))
    logger.info("Final score: %s/10", final_score)
    logger.info("Iterations: %d", final_state.get('iteration', 0))
    if final_score >= 7.0:
        logger.info("Pattern STORED for future reuse")

    return final_state


def _extract_strengths(state: dict) -> list[str]:
    """Extract strengths from a completed run for pattern storage."""
    strengths = []
    score = state.get("review_score", 0)
    if score >= 8.0:
        strengths.append(f"High quality score ({score}/10)")
    if state.get("iteration", 0) <= 1:
        strengths.append("Converged in first iteration")
    output = state.get("final_output", "")
    word_count = len(output.split())
    if word_count > 500:
        strengths.append(f"Comprehensive output ({word_count} words)")
    if not strengths:
        strengths.append(f"Completed with score {score}/10")
    return strengths


def _extract_weaknesses(state: dict) -> list[str]:
    """Extract weaknesses from a completed run for episodic memory."""
    weaknesses = []
    score = state.get("review_score", 0)
    if score < 7.0:
        weaknesses.append(f"Below threshold (score: {score}/10)")
    if state.get("iteration", 0) >= 3:
        weaknesses.append("Hit max iterations without passing")
    if not weaknesses:
        weaknesses.append("No significant weaknesses")
    return weaknesses


# ─── MAIN ────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_hierarchical("How AI Agents Are Changing Software Development in 2026")
    
    # Save the output
    _output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")
    os.makedirs(_output_dir, exist_ok=True)
    with open(os.path.join(_output_dir, "hierarchical_output.md"), "w") as f:
        f.write(result.get("final_output", "No output"))

    logger.info("Output saved to outputs/hierarchical_output.md")
