"""
Pattern 6: Map-Reduce
======================

Splits input into chunks, processes each in parallel, then reduces results.

Topology: splitter → parallel_map (N workers) → reducer → [reconciler]?

Lightweight by design (~200 lines). Max 20 chunks.
"""

import os
import json
import re
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, START, END

from shared.agents import get_llm, smart_llm_call, reviewer_node, fact_check_node, compute_cost_summary
from shared.state import prune_state
from shared.cost_tracker import check_budget_from_state, BudgetExceededError
from shared.experiential_learning import Experience, get_shared_experience_memory
from shared.logging_config import get_logger, generate_run_id, set_run_id

logger = get_logger(__name__)

_experience_memory = get_shared_experience_memory()

MAX_CHUNKS = 20
WORKER_TIMEOUT_S = 30


class MapReduceState(TypedDict):
    topic: str
    chunks: list[str]
    map_results: list[str]
    reduced_output: str
    needs_reconciliation: bool
    final_output: str
    quality_score: float
    accuracy_score: float
    token_usage: Annotated[list[dict], operator.add]
    agent_history: Annotated[list[str], operator.add]


def create_initial_state(topic: str) -> MapReduceState:
    return MapReduceState(
        topic=topic,
        chunks=[],
        map_results=[],
        reduced_output="",
        needs_reconciliation=False,
        final_output="",
        quality_score=0.0,
        accuracy_score=0.0,
        token_usage=[],
        agent_history=[],
    )


def splitter_node(state: MapReduceState) -> dict:
    """Split input into chunks for parallel processing."""
    llm = get_llm()
    prompt = (
        f"Split this query into independent chunks for parallel analysis.\n"
        f"Each chunk should be one item, entity, or section that can be analyzed independently.\n"
        f"Return a JSON array of strings, each being one chunk.\n"
        f"Max {MAX_CHUNKS} chunks.\n\n"
        f"Query: {state['topic']}\n\n"
        f"Return ONLY the JSON array."
    )
    raw = smart_llm_call(llm, prompt)

    try:
        chunks = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        chunks = json.loads(match.group()) if match else [state["topic"]]

    chunks = [str(c) for c in chunks[:MAX_CHUNKS]]
    logger.info("Splitter created %d chunks", len(chunks))
    return {"chunks": chunks, "agent_history": [f"splitter: {len(chunks)} chunks"]}


def map_node(state: MapReduceState) -> dict:
    """Process each chunk independently."""
    # Budget check before parallel mapping
    try:
        check_budget_from_state(state, estimated_next_cost=0.02 * len(state["chunks"]))
    except BudgetExceededError as e:
        logger.warning("Budget exceeded in map_reduce: %s", e)
        return {
            "map_results": [],
            "agent_history": [f"mapper: budget cap exceeded (${e.spent:.2f} > ${e.cap:.2f}), stopping"]
        }

    llm = get_llm()
    results = []
    for i, chunk in enumerate(state["chunks"]):
        prompt = (
            f"Analyze this item as part of a batch research task.\n\n"
            f"Overall topic: {state['topic']}\n"
            f"Item to analyze: {chunk}\n\n"
            f"Provide a concise, factual analysis."
        )
        try:
            output = smart_llm_call(llm, prompt)
            results.append(output)
        except Exception as e:
            logger.warning("Map worker %d failed: %s", i, e)
            results.append(f"[Analysis failed for: {chunk}]")

    logger.info("Map completed: %d/%d chunks processed", len(results), len(state["chunks"]))
    return {"map_results": results, "agent_history": [f"mapper: processed {len(results)} chunks"]}


def reducer_node(state: MapReduceState) -> dict:
    """Reduce map results into a single output."""
    llm = get_llm()

    chunk_results = "\n\n".join(
        f"### Chunk {i + 1}: {state['chunks'][i] if i < len(state['chunks']) else 'Unknown'}\n{result}"
        for i, result in enumerate(state["map_results"])
    )

    prompt = (
        f"You are a reduction agent. Synthesize these parallel analysis results "
        f"into a coherent output.\n\n"
        f"Original query: {state['topic']}\n\n"
        f"Chunk results:\n{chunk_results}\n\n"
        f"Synthesize into a comprehensive, well-structured response. "
        f"Flag any contradictions between chunks with [CONTRADICTION]."
    )
    output = smart_llm_call(llm, prompt)

    has_contradictions = "[CONTRADICTION]" in output
    logger.info("Reducer completed, contradictions=%s", has_contradictions)
    return {
        "reduced_output": output,
        "needs_reconciliation": has_contradictions,
        "agent_history": [f"reducer: synthesized, contradictions={has_contradictions}"],
    }


def reconciler_node(state: MapReduceState) -> dict:
    """Resolve contradictions if present, otherwise pass through."""
    if not state["needs_reconciliation"]:
        output = state["reduced_output"]
        review = reviewer_node({**state, "draft": output})
        quality = review.get("review_score", 0.0)
        fact = fact_check_node({**state, "draft": output})
        accuracy = fact.get("accuracy_score", 0.0)
        logger.info("Reconciler pass-through: quality=%.1f, accuracy=%.1f", quality, accuracy)
        return {
            "final_output": output,
            "quality_score": quality,
            "accuracy_score": accuracy,
            "agent_history": [f"reconciler: no conflicts, quality={quality}, accuracy={accuracy}"],
        }

    llm = get_llm()
    prompt = (
        f"The following analysis contains contradictions (marked with [CONTRADICTION]). "
        f"Resolve each contradiction by determining which position is more supported by evidence.\n\n"
        f"{state['reduced_output']}\n\n"
        f"Produce a clean, consistent final output with all contradictions resolved."
    )
    output = smart_llm_call(llm, prompt)

    review = reviewer_node({**state, "draft": output})
    quality = review.get("review_score", 0.0)
    fact = fact_check_node({**state, "draft": output})
    accuracy = fact.get("accuracy_score", 0.0)

    try:
        exp = Experience(
            task_description=state["topic"][:300],
            successful_pattern=f"Map-reduce: {len(state['chunks'])} chunks, reconciliation needed",
            score=quality,
            domain="map_reduce",
        )
        _experience_memory.add(exp)
    except Exception:
        pass

    cost = compute_cost_summary(state.get("token_usage", []))
    prune_state(state)

    logger.info("Reconciler completed: quality=%.1f, accuracy=%.1f, cost=$%.4f", quality, accuracy, cost["total_cost_usd"])
    return {
        "final_output": output,
        "quality_score": quality,
        "accuracy_score": accuracy,
        "cost_estimate": cost,
        "agent_history": [f"reconciler: resolved contradictions, quality={quality}, accuracy={accuracy}, cost=${cost['total_cost_usd']:.4f}"],
    }


# ── Graph Construction ──

def build_map_reduce_graph():
    """Build the map-reduce LangGraph."""
    graph = StateGraph(MapReduceState)

    graph.add_node("splitter", splitter_node)
    graph.add_node("mapper", map_node)
    graph.add_node("reducer", reducer_node)
    graph.add_node("reconciler", reconciler_node)

    graph.add_edge(START, "splitter")
    graph.add_edge("splitter", "mapper")
    graph.add_edge("mapper", "reducer")
    graph.add_edge("reducer", "reconciler")
    graph.add_edge("reconciler", END)

    return graph.compile()


def run_map_reduce(topic: str) -> dict:
    """Run the map-reduce pattern."""
    run_id = generate_run_id()
    set_run_id(run_id)
    logger.info("Starting map-reduce [%s] topic=%s", run_id, topic[:80])

    initial_state = create_initial_state(topic)
    graph = build_map_reduce_graph()
    final_state = graph.invoke(initial_state)

    logger.info("Map-reduce complete. Chunks: %d, quality=%.1f, accuracy=%.1f",
                len(final_state.get("chunks", [])),
                final_state.get("quality_score", 0),
                final_state.get("accuracy_score", 0))
    return final_state


if __name__ == "__main__":
    result = run_map_reduce("Summarize the top 5 trending AI papers this week")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    with open(f"{output_dir}/map_reduce_output.md", "w") as f:
        f.write(result.get("final_output", "No output"))
