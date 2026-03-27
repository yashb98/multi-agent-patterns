# Fact-Check Accuracy 9.5+/10 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee factual accuracy score of 9.5+/10 (target 9.7) on all blog generation output with claim-level verification, web search from day 1, across all 4 orchestration patterns.

**Architecture:** Extract claims from draft → verify each claim against research notes + web search → compute deterministic accuracy score → hard gate in convergence (8.0 quality + 9.5 accuracy) → targeted revision with specific fix instructions when accuracy fails. Unified `shared/fact_checker.py` replaces blog generator's inline fact_check().

**Tech Stack:** Python 3, OpenAI gpt-4o-mini, LangGraph, SQLite, web search (DuckDuckGo), pytest

**Spec:** `docs/feature-fact-check-accuracy.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `shared/fact_checker.py` | **NEW** — Claim extraction, per-claim verification, web search, accuracy scoring, revision notes, facts cache |
| `shared/state.py` | **MODIFY** — Add fact-check fields to AgentState |
| `shared/agents.py` | **MODIFY** — Add `fact_check_node()` graph node |
| `shared/prompts.py` | **MODIFY** — Add CLAIM_EXTRACTOR_PROMPT, CLAIM_VERIFIER_PROMPT |
| `patterns/hierarchical.py` | **MODIFY** — Add REVISE_FACTS edge, dual convergence gate |
| `patterns/peer_debate.py` | **MODIFY** — Add accuracy gate to convergence |
| `patterns/dynamic_swarm.py` | **MODIFY** — Add accuracy gate |
| `patterns/enhanced_swarm.py` | **MODIFY** — Make fact-checker mandatory, add accuracy gate |
| `shared/dynamic_agent_factory.py` | **MODIFY** — Update fact_checker template |
| `jobpulse/blog_generator.py` | **MODIFY** — Replace inline fact_check() with shared import |
| `tests/test_fact_checker.py` | **NEW** — Tests for claims, scoring, verification, cache |

---

## Phase 1: Core Fact-Checker Module

### Task 1.1: Add AgentState fields

**Files:** Modify `shared/state.py:27-75`

Add after `final_output: str` (line 75):

```python
    # ─── FACT-CHECK FIELDS ─────────────────────────────────────
    extracted_claims: list[dict]
    claim_verifications: list[dict]
    accuracy_score: float
    accuracy_passed: bool
    fact_revision_notes: Optional[str]
```

### Task 1.2: Create shared/fact_checker.py

Core module with: extract_claims, verify_claims, compute_accuracy_score, generate_revision_notes.

### Task 1.3: Write tests

Create `tests/test_fact_checker.py` with tests for claim extraction, scoring, revision notes.

---

## Phase 2: Web Search Verification + Cache

### Task 2.1: Add web_verify_claim()

Uses DuckDuckGo search to verify claims against external sources.

### Task 2.2: Add verified facts SQLite cache

Cache previously verified facts for instant reuse.

---

## Phase 3: Wire Into All 4 Patterns

### Task 3.1: Add fact_check_node to shared/agents.py

### Task 3.2: Update hierarchical.py convergence (8.0 quality + 9.5 accuracy)

### Task 3.3: Update peer_debate.py convergence

### Task 3.4: Update dynamic_swarm.py convergence

### Task 3.5: Update enhanced_swarm.py convergence

---

## Phase 4: Targeted Revision Edge + Blog Generator Unification

### Task 4.1: Add REVISE_FACTS edge to hierarchical pattern

### Task 4.2: Replace blog_generator.py fact_check() with shared import

---

## Phase 5: Prompts + Docs + Final Tests

### Task 5.1: Add prompts to shared/prompts.py

### Task 5.2: Update dynamic_agent_factory.py fact_checker template

### Task 5.3: Update docs/agents.md and CLAUDE.md
