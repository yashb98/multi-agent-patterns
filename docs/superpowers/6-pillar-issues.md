# 6 Pillar Issues — Known Bugs to Fix After All Pillars Built

Discovered during Pillar 2 (Cognitive Reasoning Engines) audit on 2026-04-21.
Re-audited on 2026-04-21 after Pillar 1 (Memory System Upgrade) was built.

**Result: 0 of 15 issues resolved by Pillar 1. All remained open.**

**All 15 issues resolved on 2026-04-21.**

---

## Blocking — Production Broken (3 issues)

### 1. Memory recall is dead (Pillar 1 <-> Pillar 2 gap)

**Status: RESOLVED.**

Added `get_procedural_entries(domain)` and `get_episodic_entries(domain)` delegation methods to `MemoryManager` in `shared/memory_layer/_manager.py`. They delegate to `self.procedural.recall(domain)` and `self.episodic.recall("", domain)` respectively.

### 2. L0 fallback returns prompt template as the answer

**Status: RESOLVED.**

L0 fallback now returns `score=0.0` with empty answer, which triggers auto-escalation to L1 via the existing `should_escalate()` logic in `think()`.

### 3. `flush()` is a documented no-op

**Status: RESOLVED.**

L1 success path (score >= threshold) now populates `_pending_writes` in `think()`. `flush()` writes these to memory via `learn_procedure()`. L2/L3 continue writing directly.

---

## High — Significant Behavior Bugs (5 issues)

### 4. `_resolve_stakes()` ignores explicit overrides

**Status: RESOLVED.**

Explicit stakes now take priority over registry. `_resolve_stakes()` returns `explicit_stakes` first when it's a valid value, falls back to registry only when stakes is not "high"/"medium"/"low".

### 5. Own-first template ranking never fires

**Status: RESOLVED.**

`source` parameter in `learn_procedure()` calls now set to `self._agent_name` instead of `"reflexion"` or `"tot"`. Both `ReflexionLoop._store_success()` and `TreeOfThought.explore()` pass the agent name. `StrategyComposer.compose()` matches `source == agent_name` for own-first ranking.

### 6. Screening answers has no scorer — accumulates false failures

**Status: RESOLVED.**

Added `_score_screening_answer()` scorer to `screening_answers.py`. Scores: <5 chars = 2.0, error keywords = 3.0, valid answer = 8.0. Wired into `think_sync()` call.

### 7. Budget cooldown is set but never enforced

**Status: RESOLVED.**

Added `if time.monotonic() < self._cooldown_until: return level <= ThinkLevel.L1_SINGLE` at the top of `allows()`. Budget-exhausted engines now restrict to L0/L1 during cooldown.

### 8. GRPO branches lack strategy differentiation

**Status: RESOLVED.**

Rewrote `_generate_branches_via_grpo()` to use `ThreadPoolExecutor` with per-strategy prompts. Each branch now gets a distinct `## Reasoning approach` section in its prompt, plus a different temperature.

---

## Medium — Spec Divergence / Fragile Code (7 issues)

### 9. ToT stores winning branch unconditionally

**Status: RESOLVED.**

Added `if winner.score >= 7.0:` gate before `learn_procedure()` call in `TreeOfThought.explore()`.

### 10. `load_persisted_stats()` fragile internal access

**Status: RESOLVED.**

Replaced chained `hasattr` checks with `getattr(..., None)` pattern and explicit `isinstance(facts, dict)` guard. Fails gracefully for both old JSON stores and new 3-engine system.

### 11. LLM message format — wrong token estimation in streaming

**Status: RESOLVED.**

All three `_llm_generate` functions (`_engine.py`, `_reflexion.py`, `_tree_of_thought.py`) now use `HumanMessage(content=prompt)` instead of raw dicts. Streaming token estimation works correctly.

### 12. Task injected twice into L1 prompt

**Status: RESOLVED.**

Removed redundant `\n\nTask: {task}` append in `_execute_l1`. `composed.text` already includes the task via `COMPOSED_SECTIONS["task"]`.

### 13. Wrong defensive check in ReflexionLoop

**Status: RESOLVED.**

Replaced `'attempt' in dir()` with proper initialization (`attempt = 0` before loop). Return uses `attempts=attempt` directly.

### 14. Ranking formula missing recency term

**Status: RESOLVED.**

Added `recency * 0.1` to `rank_key()` in `StrategyComposer.compose()`. Recency computed from `created_at` timestamp, normalized to 0-1 over 90-day window. Default 0.5 for entries without timestamps.

### 15. Neither agent calls `flush()` / `flush_sync()`

**Status: RESOLVED.**

Added `flush_sync()` at end of `check_emails()` in `gmail_agent.py` and after each `think_sync()` call in `screening_answers.py:_generate_answer()`.
