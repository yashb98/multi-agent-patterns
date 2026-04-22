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

The `Annotated[list, append_or_replace]` pattern is crucial:
- Normal fields: new value REPLACES old value
- Annotated list fields: plain-list updates are APPENDED (multiple agents
  contribute to the same list without overwriting). `ReplaceList(...)`
  updates REPLACE the list in one shot — used by `prune_state` to bound
  context growth across iterations.
"""

from typing import TypedDict, Annotated, Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── REDUCER: APPEND-BY-DEFAULT, REPLACE-ON-DEMAND ─────────────
# Plain `operator.add` makes unbounded list fields impossible to prune:
# returning a shorter list from a node just appends those entries to the
# existing list, *growing* state instead of shrinking it.
#
# `append_or_replace` keeps append semantics for plain lists (so every
# existing agent write site works unchanged), but replaces the full list
# when a node returns a `ReplaceList(...)` marker. `prune_state` below
# uses this to actually shrink state between iterations.

class ReplaceList(list):
    """List subclass that signals "replace existing" to `append_or_replace`.

    Behaves identically to `list` for reads/iteration/indexing. Only the
    type identity matters — the reducer checks `isinstance(update, ReplaceList)`
    to decide between append and replace.
    """
    __slots__ = ()


def append_or_replace(current: list | None, update: list | None) -> list:
    """LangGraph reducer: replace if `update` is `ReplaceList`, else append.

    - `current=None, update=None`  → `[]`
    - `current=[a], update=[b]`    → `[a, b]`  (append, default)
    - `current=[a], update=ReplaceList([c])` → `[c]`  (prune/replace)
    """
    if update is None:
        return list(current) if current else []
    if isinstance(update, ReplaceList):
        return list(update)
    base = list(current) if current else []
    if isinstance(update, list):
        return base + update
    return base


class AgentState(TypedDict):
    # ─── INPUT ───────────────────────────────────────────────
    # The topic the user wants a blog post about.
    # Set once at the start, never modified by agents.
    topic: str

    # ─── RESEARCH LAYER ─────────────────────────────────────
    # Raw research notes produced by the Researcher agent.
    # Plain list updates are APPENDED (back-compat with every existing
    # agent write). `ReplaceList(...)` updates REPLACE the list — used by
    # `prune_state` to actually shrink unbounded fields.
    research_notes: Annotated[list[str], append_or_replace]

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
    # Appended by default; `prune_state` replaces with a trimmed tail.
    agent_history: Annotated[list[str], append_or_replace]

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
    # Appended by default; `prune_state` replaces with a trimmed tail.
    token_usage: Annotated[list[dict], append_or_replace]
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

    Returns a dict whose values are `ReplaceList` markers — these tell the
    `append_or_replace` reducer to REPLACE (not append), so the pruned list
    actually shrinks state. Call sites that also want to add a new entry
    in the same update must use `prune_and_return` (below), which merges
    the new entry AFTER the pruned tail; otherwise pruning would discard
    the caller's new entry.

    Strategy:
    - research_notes: keep latest N, compress older to a summary note
    - agent_history: keep latest 20 entries
    - token_usage: keep latest 30 entries
    - extracted_claims/claim_verifications: keep latest 50
    """
    updates: dict[str, ReplaceList] = {}

    for field, limit in PRUNE_LIMITS.items():
        items = state.get(field, [])
        if not isinstance(items, list) or len(items) <= limit:
            continue

        old_len = len(items)
        if field == "research_notes":
            from shared.context_compression import compress_research_notes
            updates[field] = ReplaceList(compress_research_notes(items))
        else:
            updates[field] = ReplaceList(items[-limit:])

        logger.debug("Pruned %s: %d -> %d entries", field, old_len, len(updates[field]))

    if updates:
        logger.info(
            "State pruned: %s",
            ", ".join(f"{k}={len(v)}" for k, v in updates.items()),
        )

    return updates


def prune_and_return(state: dict, update: dict) -> dict:
    """Merge an `update` from a node with pruned views of unbounded fields.

    For fields listed in `PRUNE_LIMITS`, the pruned tail from state is
    concatenated with the caller's new entries and returned as a
    `ReplaceList` — so the reducer replaces the full list in one shot
    (pruned_tail + new_entries), instead of appending the new entries
    to an ever-growing history.

    For other fields, `update` values pass through unchanged.

    Usage:
        return prune_and_return(state, {
            "current_agent": "researcher",
            "agent_history": [f"Supervisor → researcher"],
        })
    """
    pruned = prune_state(state)
    result: dict = {}

    for key, value in update.items():
        if key in PRUNE_LIMITS and isinstance(value, list) and key in pruned:
            # pruned tail + new entries, marked as replace
            result[key] = ReplaceList(list(pruned[key]) + list(value))
        else:
            result[key] = value

    # fields pruned but not mentioned by caller — still need to be replaced
    for key, value in pruned.items():
        if key not in result:
            result[key] = value

    return result
