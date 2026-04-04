"""
AgentState: The shared state that ALL agents read from and write to.
=================================================================

KEY CONCEPT: In LangGraph, state is the communication channel between agents.
Agents don't talk to each other directly — they write to state, and other
agents read from state. Think of it as a shared whiteboard.

Every agent function signature looks like:
    def my_agent(state: AgentState) -> dict:
        # read from state, do work, return PARTIAL update
        return {"field": new_value}

LangGraph merges the partial update into the full state automatically.
You never need to return the entire state — just the fields you changed.

The `Annotated[list, operator.add]` pattern is crucial:
- Normal fields: new value REPLACES old value
- Annotated list fields: new items are APPENDED to existing list
This lets multiple agents contribute to the same list without overwriting.
"""

from typing import TypedDict, Annotated, Optional
import operator

from shared.logging_config import get_logger

logger = get_logger(__name__)


class AgentState(TypedDict):
    # ─── INPUT ───────────────────────────────────────────────
    # The topic the user wants a blog post about.
    # Set once at the start, never modified by agents.
    topic: str

    # ─── RESEARCH LAYER ─────────────────────────────────────
    # Raw research notes produced by the Researcher agent.
    # Each call APPENDS to this list (operator.add = list concat).
    # This means if the Researcher runs twice (e.g., after feedback),
    # we keep ALL research — nothing is lost.
    research_notes: Annotated[list[str], operator.add]

    # ─── WRITING LAYER ──────────────────────────────────────
    # The current draft of the blog article.
    # REPLACES on each write — we only keep the latest draft.
    draft: str

    # ─── REVIEW LAYER ───────────────────────────────────────
    # Structured feedback from the Reviewer agent.
    # REPLACES each time — we only care about the latest review.
    review_feedback: Optional[str]

    # Quality score from 0-10. Used by the orchestrator to decide
    # whether to iterate or accept the draft.
    review_score: float

    # Did the draft pass the quality threshold?
    review_passed: bool

    # ─── ORCHESTRATION METADATA ─────────────────────────────
    # How many revision cycles have we completed?
    # Used to prevent infinite loops (max_iterations guard).
    iteration: int

    # Which agent is currently active? Used by supervisor pattern.
    current_agent: str

    # Ordered log of which agents ran and when.
    # Annotated list — each entry is appended, never overwritten.
    agent_history: Annotated[list[str], operator.add]

    # ─── SWARM-SPECIFIC FIELDS ──────────────────────────────
    # Priority queue of pending tasks for dynamic swarm.
    # Each task is a dict with "agent", "priority", "description".
    pending_tasks: list[dict]

    # Final output after all processing is complete.
    final_output: str

    # ─── FACT-CHECK FIELDS ─────────────────────────────────────
    # Claims extracted from the current draft
    extracted_claims: list[dict]
    # Verification result per claim
    claim_verifications: list[dict]
    # Deterministic accuracy score (0-10)
    accuracy_score: float
    # True if accuracy_score >= 9.5
    accuracy_passed: bool
    # Specific fix instructions for the writer
    fact_revision_notes: Optional[str]

    # ─── COST TRACKING FIELDS ─────────────────────────────────
    # Accumulated token usage and estimated cost across all LLM calls.
    # Each entry: {"agent": str, "prompt_tokens": int, "completion_tokens": int, "cost_usd": float}
    token_usage: Annotated[list[dict], operator.add]
    # Running total cost in USD for the entire pipeline run
    total_cost_usd: float


# ─── STATE PRUNING ─────────────────────────────────────────────
# Unbounded Annotated[list, operator.add] fields grow every iteration.
# Pruning keeps the latest entries and summarizes older ones.

# Limits per field (keep last N entries)
PRUNE_LIMITS = {
    "research_notes": 3,       # Keep last 3 research notes
    "agent_history": 20,       # Keep last 20 history entries
    "token_usage": 30,         # Keep last 30 usage records
    "extracted_claims": 50,    # Keep last 50 claims
    "claim_verifications": 50, # Keep last 50 verifications
}


def prune_state(state: dict) -> dict:
    """Prune unbounded list fields to prevent context bloat.

    Called between iterations by convergence/supervisor nodes.
    Returns a dict of pruned fields to merge back into state.

    Strategy:
    - research_notes: keep latest 3, compress older to summary
    - agent_history: keep latest 20 entries
    - token_usage: keep all (needed for cost summary) but cap at 30
    - extracted_claims/claim_verifications: keep latest iteration's
    """
    updates = {}
    pruned_any = False

    for field, limit in PRUNE_LIMITS.items():
        items = state.get(field, [])
        if isinstance(items, list) and len(items) > limit:
            old_len = len(items)
            # For research_notes, compress old ones instead of dropping
            if field == "research_notes" and len(items) > limit:
                from shared.context_compression import compress_research_notes
                updates[field] = compress_research_notes(items)
            else:
                # Keep the last N entries
                updates[field] = items[-limit:]

            pruned_any = True
            logger.debug("Pruned %s: %d -> %d entries", field, old_len,
                         len(updates.get(field, items)))

    if pruned_any:
        logger.info("State pruned: %s",
                     ", ".join(f"{k}={len(v)}" for k, v in updates.items()))

    return updates
