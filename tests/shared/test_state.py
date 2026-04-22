"""Tests for shared.state reducer + pruning.

These tests pin down the Phase 0 fix: unbounded `Annotated[list, operator.add]`
fields couldn't actually be pruned — returning a trimmed list just got
appended again. The new `append_or_replace` reducer + `ReplaceList` marker
let `prune_state`/`prune_and_return` actually shrink state.
"""

from shared.state import (
    PRUNE_LIMITS,
    ReplaceList,
    append_or_replace,
    prune_and_return,
    prune_state,
)


# ─── Reducer semantics ────────────────────────────────────────

def test_reducer_appends_plain_lists():
    """Default path: plain list updates append — back-compat with every
    existing agent write site in shared/agents.py and patterns/."""
    assert append_or_replace([], ["a"]) == ["a"]
    assert append_or_replace(["a"], ["b"]) == ["a", "b"]
    assert append_or_replace(["a", "b"], ["c", "d"]) == ["a", "b", "c", "d"]


def test_reducer_replaces_on_replacelist_marker():
    """A `ReplaceList` update replaces the current list entirely."""
    assert append_or_replace(["a", "b", "c"], ReplaceList(["z"])) == ["z"]
    assert append_or_replace(list(range(100)), ReplaceList([1, 2, 3])) == [1, 2, 3]


def test_reducer_handles_none_and_empty():
    assert append_or_replace(None, None) == []
    assert append_or_replace(None, ["a"]) == ["a"]
    assert append_or_replace(["a"], None) == ["a"]
    assert append_or_replace([], ReplaceList([])) == []


def test_reducer_returns_fresh_list_not_alias():
    """Reducer must not alias caller-provided lists — state must be safe
    to mutate downstream without corrupting the pending update."""
    current = ["a"]
    update = ["b"]
    result = append_or_replace(current, update)
    result.append("c")
    assert current == ["a"]
    assert update == ["b"]


# ─── prune_state ───────────────────────────────────────────────

def test_prune_state_is_noop_under_limit():
    """Nothing should be emitted when all fields are within their limits."""
    state = {
        "agent_history": ["a"] * 5,          # under 20
        "token_usage": [{"c": 0.0}] * 5,     # under 30
        "research_notes": ["r"] * 2,         # under 3
    }
    assert prune_state(state) == {}


def test_prune_state_emits_replacelist_for_bloated_fields():
    """When a field exceeds its limit, prune_state emits a ReplaceList
    containing exactly the last N entries."""
    state = {
        "agent_history": [f"h{i}" for i in range(50)],
        "token_usage": [{"i": i} for i in range(100)],
    }
    updates = prune_state(state)

    assert "agent_history" in updates
    assert isinstance(updates["agent_history"], ReplaceList)
    assert len(updates["agent_history"]) == PRUNE_LIMITS["agent_history"]
    assert updates["agent_history"][-1] == "h49"  # keeps the tail

    assert isinstance(updates["token_usage"], ReplaceList)
    assert len(updates["token_usage"]) == PRUNE_LIMITS["token_usage"]


# ─── The critical end-to-end property: state actually shrinks ─

def test_pruned_update_applied_by_reducer_shrinks_state():
    """Simulate what LangGraph does: apply `append_or_replace` to the
    current state with prune_state's output. Verify the list shrinks."""
    state = {"agent_history": [f"h{i}" for i in range(50)]}
    pruned = prune_state(state)

    # Simulate LangGraph's reducer application
    new_history = append_or_replace(state["agent_history"], pruned["agent_history"])

    assert len(new_history) == PRUNE_LIMITS["agent_history"]
    assert new_history[-1] == "h49"


def test_old_broken_behavior_would_have_doubled_state():
    """Sanity-check what the pre-fix bug looked like: with operator.add
    semantics a 'pruned' plain list would have been APPENDED, growing
    state instead. This documents why ReplaceList is necessary."""
    import operator
    state = {"agent_history": [f"h{i}" for i in range(50)]}
    # What the old code effectively did: return items[-20:] as a plain list
    old_pruned_value = state["agent_history"][-20:]
    # Old reducer (operator.add) appends instead of replacing:
    new_history = operator.add(state["agent_history"], old_pruned_value)
    # The "prune" actually grew state by 20 entries — the documented bug.
    assert len(new_history) == 70


# ─── prune_and_return ──────────────────────────────────────────

def test_prune_and_return_passes_through_non_pruned_fields():
    state = {"agent_history": ["a"] * 3}
    out = prune_and_return(state, {
        "current_agent": "writer",
        "iteration": 5,
    })
    assert out == {"current_agent": "writer", "iteration": 5}


def test_prune_and_return_appends_new_entry_to_pruned_tail():
    """When pruning fires AND the caller wants to add a new entry,
    the new entry must land AFTER the pruned tail (not overwrite it,
    not be overwritten by it)."""
    state = {"agent_history": [f"h{i}" for i in range(50)]}
    out = prune_and_return(state, {
        "current_agent": "writer",
        "agent_history": ["NEW_ENTRY"],
    })

    assert out["current_agent"] == "writer"
    assert isinstance(out["agent_history"], ReplaceList)
    # pruned tail of 20 + 1 new entry
    assert len(out["agent_history"]) == PRUNE_LIMITS["agent_history"] + 1
    assert out["agent_history"][-1] == "NEW_ENTRY"
    assert out["agent_history"][-2] == "h49"


def test_prune_and_return_noop_when_under_limit():
    """When nothing needs pruning, the caller's update flows through
    unchanged — as a plain list (append semantics), not ReplaceList."""
    state = {"agent_history": ["a", "b"]}
    out = prune_and_return(state, {
        "agent_history": ["new"],
        "iteration": 1,
    })
    assert out == {"agent_history": ["new"], "iteration": 1}
    assert not isinstance(out["agent_history"], ReplaceList)


def test_iterated_prune_keeps_state_bounded():
    """The real invariant: over many iterations, history never exceeds
    limit + single-step appends. This is the regression test for the
    unbounded-growth bug."""
    state = {"agent_history": []}
    limit = PRUNE_LIMITS["agent_history"]

    for i in range(200):
        # Every node appends one entry, then the convergence/prune node
        # returns a prune_and_return that also appends one entry.
        state["agent_history"] = append_or_replace(
            state["agent_history"], [f"node_{i}"],
        )
        update = prune_and_return(state, {"agent_history": [f"conv_{i}"]})
        state["agent_history"] = append_or_replace(
            state["agent_history"], update["agent_history"],
        )

        # After each iteration, state must never exceed limit + 1 buffer
        # (the post-prune "conv_{i}" append). In the old broken code, len
        # grew monotonically past 200.
        assert len(state["agent_history"]) <= limit + 2, (
            f"iteration {i}: history grew to {len(state['agent_history'])}"
        )

    # Final check: the most recent entry is the latest convergence log
    assert state["agent_history"][-1] == "conv_199"
