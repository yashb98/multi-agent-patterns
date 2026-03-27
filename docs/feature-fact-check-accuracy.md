# Feature: Fact-Check Accuracy 9.5+/10

**Goal:** Guarantee factual accuracy score of 9.5+/10 (target 9.7) on all blog generation output, with claim-level verification, web search verification from day 1, across all 4 orchestration patterns.

**Status:** APPROVED — ready for implementation

---

## Decisions (Finalized 2026-03-27)

- Apply to **all 4 patterns** (Hierarchical, Peer Debate, Dynamic Swarm, Enhanced Swarm) — test all, keep best
- **9.5 hard floor, 9.7 target** — system aims for 9.7, won't loop forever if it hits 9.5
- **Web search from day 1** — verify claims against live external sources (Papers With Code, HuggingFace, arXiv, web), not just research notes
- **Cost is not an issue** — prioritize accuracy over token spend
- **Unified fact-checker** — single `shared/fact_checker.py` used by both orchestration patterns and blog generator. Blog generator passes paper abstract as source; patterns pass research notes + web results
- **Overall quality gate raised to 8.0** (user changed from 7.0) + accuracy gate 9.5
- All techniques and methods must be latest as of March 2026

---

## Problem

Current system scores "technical accuracy" as 1 of 5 dimensions in the reviewer. A draft can pass (7.0+ overall) with mediocre accuracy if readability and structure carry it. There's no hard gate on factual correctness.

| Issue | Impact |
|-------|--------|
| Accuracy is averaged with 4 other scores | Low accuracy hidden by high style scores |
| Fact-checker is optional (spawned only for complex tasks) | Most runs skip fact-checking entirely |
| No claim-level verification | Unsupported claims slip through as "generally accurate" |
| Single-pass fact-check in blog generator | One chance to catch errors, no iterative fix |
| No external source verification | LLM scores its own accuracy (fox guarding henhouse) |

---

## Solution: Mandatory Fact-Check Gate + Claim Verification Loop

### Architecture

```
Researcher → Writer (GRPO, 3 candidates)
                │
                ▼
        ┌───────────────────┐
        │  CLAIM EXTRACTOR  │  ← NEW: extract every factual claim
        └───────┬───────────┘
                │
                ▼
        ┌───────────────────┐
        │  FACT VERIFIER    │  ← NEW: verify each claim against sources
        │  (per-claim loop) │
        └───────┬───────────┘
                │
          ┌─────┴─────┐
          │           │
     ALL PASS    FLAGS FOUND
          │           │
          ▼           ▼
      Reviewer    Writer REVISES (with specific flags)
          │           │
          │           └──→ back to CLAIM EXTRACTOR
          ▼
    Convergence Check:
      overall_score >= 8.0
      AND accuracy_score >= 9.5  ← NEW: hard gate
```

---

## Step-by-Step Plan

### Step 1: Claim Extraction

New function that pulls every verifiable claim from a draft:

```python
def extract_claims(draft: str) -> list[dict]:
    """
    Returns:
    [
        {"claim": "GPT-4 achieves 86.4% on MMLU", "type": "benchmark", "source_needed": True},
        {"claim": "Transformers were introduced in 2017", "type": "date", "source_needed": True},
        {"claim": "This approach is elegant", "type": "opinion", "source_needed": False},
    ]
    """
```

Claim types:
| Type | Needs Verification | Example |
|------|--------------------|---------|
| `benchmark` | Yes (exact numbers) | "Achieves 92.3% accuracy on X" |
| `date` | Yes | "Released in March 2024" |
| `attribution` | Yes | "Proposed by Smith et al." |
| `comparison` | Yes | "Faster than BERT by 3x" |
| `technical` | Yes | "Uses multi-head attention with 12 heads" |
| `opinion` | No (skip) | "This is a promising approach" |
| `definition` | Low priority | "RAG stands for Retrieval Augmented Generation" |

### Step 2: Per-Claim Verification

Each extracted claim gets verified against available sources:

```python
def verify_claim(claim: dict, sources: list[str]) -> dict:
    """
    Returns:
    {
        "claim": "GPT-4 achieves 86.4% on MMLU",
        "verdict": "VERIFIED" | "UNVERIFIED" | "INACCURATE" | "EXAGGERATED",
        "evidence": "Paper states 86.4% (Table 2)",
        "confidence": 0.95,
        "severity": "high" | "medium" | "low",
        "fix_suggestion": None | "Correct to 85.2% per source"
    }
    """
```

Verification sources (ALL used from day 1, in priority order):
1. **Research notes** from the Researcher agent (already gathered)
2. **Paper abstract/content** (for arXiv blog generation)
3. **Web search** — live queries against Papers With Code, HuggingFace leaderboards, arXiv, Google Scholar
4. **Cached verified facts** — previously verified claims stored in SQLite for instant reuse

### Step 3: Accuracy Score Calculation

```python
def compute_accuracy_score(verifications: list[dict]) -> float:
    """
    Scoring:
    - VERIFIED claims: +1.0
    - UNVERIFIED claims (low severity): -0.5
    - UNVERIFIED claims (high severity): -1.5
    - INACCURATE claims: -2.0
    - EXAGGERATED claims: -1.0

    Score = 10.0 * (total_points / max_possible_points)
    Floor at 0.0, cap at 10.0
    """
```

This gives a **deterministic, auditable accuracy score** — not an LLM's subjective judgment.

### Step 4: Hard Gate in Convergence

```python
def convergence_check(state: AgentState) -> str:
    overall = state["review_score"]
    accuracy = state["accuracy_score"]       # NEW field
    iteration = state["iteration"]

    # Both gates must pass (quality raised to 8.0, accuracy 9.5 floor)
    if overall >= 8.0 and accuracy >= 9.5:
        return "FINISH"

    # Accuracy failed — send back to writer with specific flags
    if accuracy < 9.5 and iteration < 3:
        return "REVISE_FACTS"   # NEW edge: targeted fact revision

    # Quality failed — send back to writer with reviewer feedback
    if overall < 8.0 and iteration < 3:
        return "REVISE_QUALITY"

    # Max iterations — accept best available
    return "FINISH_BEST"
```

### Step 5: Targeted Fact Revision

When accuracy < 9.5, the writer gets **specific fix instructions**, not vague "improve accuracy":

```
REVISION INSTRUCTIONS:
The following claims need correction:

1. INACCURATE: "GPT-4 achieves 92% on MMLU"
   → Evidence shows 86.4%. Fix the number.

2. UNVERIFIED: "This outperforms all existing methods"
   → No comparative benchmark found. Either cite a source or soften to "competitive with existing methods".

3. EXAGGERATED: "Revolutionary breakthrough in NLP"
   → Paper describes incremental improvement. Tone down.

Preserve all other content. Only fix the flagged claims.
```

This is far more effective than "improve technical accuracy" — the writer knows exactly what to fix.

---

## New AgentState Fields

```python
AgentState(TypedDict):
    # ... existing fields ...

    # NEW: Fact-check fields
    extracted_claims: list[dict]     # All claims from current draft
    claim_verifications: list[dict]  # Verification results per claim
    accuracy_score: float            # 0-10, computed from verifications
    accuracy_passed: bool            # True if accuracy_score >= 9.5
    fact_revision_notes: str         # Specific fix instructions for writer
```

---

## Integration with All 4 Patterns

All patterns get the fact-check gate. We test all 4 and keep whichever produces the best accuracy.

### Hierarchical Pattern
```
Supervisor now has 3 possible next steps after review:
  - FINISH (overall >= 8.0 AND accuracy >= 9.5)
  - REVISE_QUALITY (overall < 8.0)
  - REVISE_FACTS (accuracy < 9.5)     ← NEW edge
```

### Peer Debate Pattern
```
After each debate round, fact-check runs on the merged draft.
Debaters see claim flags in their next round context.
Convergence requires accuracy >= 9.5 in addition to quality gate.
```

### Dynamic Swarm Pattern
```
Fact-checker added to task queue after writer completes.
Re-analysis loop includes accuracy in its assessment.
```

### Enhanced Swarm Pattern
```
Fact-checker is now MANDATORY (not optional from task analyzer).
Spawned in every run, regardless of complexity score.
Experience memory stores accuracy patterns for future runs.
GRPO sampling can be applied to fact-check prompts too.
```

---

## Implementation Phases

### Phase 1: Unified Fact-Checker + Claim Extraction + Web Search
- New file: `shared/fact_checker.py` (unified — replaces blog generator's `fact_check()`)
  - `extract_claims(draft, topic) -> list[dict]`
  - `verify_claims(claims, sources, web_search=True) -> list[dict]`
  - `web_verify_claim(claim) -> dict` — live search against Papers With Code, HuggingFace, arXiv, Google Scholar
  - `compute_accuracy_score(verifications) -> float`
  - `generate_revision_notes(verifications) -> str`
  - `cache_verified_fact(claim, evidence) -> None` — SQLite cache for reuse
- Modify `shared/agents.py`:
  - New `fact_check_node(state) -> dict` (graph node)
  - Returns `extracted_claims`, `claim_verifications`, `accuracy_score`, `accuracy_passed`
- Modify convergence in all 4 patterns to add accuracy gate (8.0 quality + 9.5 accuracy)
- Migrate `jobpulse/blog_generator.py` to use `shared/fact_checker.py`
- **Commit + push after completion**

### Phase 2: Targeted Revision Edge + All Pattern Integration
- New graph edge: `REVISE_FACTS` → writer with `fact_revision_notes`
- Writer prompt includes specific claim fixes (not generic "improve accuracy")
- After revision, re-extract claims and re-verify (loop until pass or max iterations)
- Wire into all 4 patterns: Hierarchical, Peer Debate, Dynamic Swarm, Enhanced Swarm
- **Commit + push after completion**

### Phase 3: Verified Facts Cache + Pattern Comparison
- SQLite cache: previously verified claims available instantly on future runs
- Run same topic through all 4 patterns, compare accuracy scores
- Log which pattern achieves highest accuracy per topic type
- Add accuracy metrics to process trail dashboard
- **Commit + push after completion**

---

## Files Changed

| File | Change |
|------|--------|
| `shared/fact_checker.py` | **NEW** — unified claim extraction, web verification, scoring, caching |
| `shared/agents.py` | Add `fact_check_node`, update `AgentState` type |
| `shared/prompts.py` | Add `CLAIM_EXTRACTOR_PROMPT`, `CLAIM_VERIFIER_PROMPT`, `WEB_SEARCH_VERIFY_PROMPT` |
| `patterns/hierarchical.py` | Add `REVISE_FACTS` edge, dual gate (8.0 quality + 9.5 accuracy) |
| `patterns/peer_debate.py` | Add fact-check after each debate round, accuracy gate |
| `patterns/dynamic_swarm.py` | Add fact-checker to task queue, accuracy in re-analysis |
| `patterns/enhanced_swarm.py` | Make fact-checker mandatory, accuracy gate, GRPO on fact-check |
| `shared/dynamic_agent_factory.py` | Update fact_checker template with web search prompts |
| `jobpulse/blog_generator.py` | Replace inline `fact_check()` with `shared/fact_checker.py` import |
| `tests/test_fact_checker.py` | **NEW** — unit tests for claim extraction, scoring, web verification |

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Accuracy score | ~7-8/10 (mixed with quality) | 9.5-9.7/10 (hard gate) |
| Quality threshold | 7.0/10 | 8.0/10 |
| Unsupported claims per article | 2-5 (untracked) | 0-1 (each flagged and fixed) |
| LLM calls per run | 4-10 | 10-20 (+web search + per-claim verification) |
| Token cost per run | ~$0.02 | ~$0.05-0.10 (cost not a concern) |
| Revision quality | Vague ("improve accuracy") | Targeted (specific claim fixes with evidence) |
| Verification depth | LLM self-assessment only | Web search + cached facts + source cross-reference |

**No trade-off concern** — cost is explicitly not an issue. Every article ships with verifiable, evidence-backed accuracy.

---

## LLM Call Budget (cost not a constraint)

| Call | Purpose | Model | Tokens (est.) |
|------|---------|-------|---------------|
| Claim extraction | Parse draft into claim list | gpt-4o-mini | ~500 in, ~300 out |
| Web search per claim | Live verification against external sources | gpt-4o-mini | ~300 in, ~200 out |
| Claim verification (per claim) | Cross-reference claim against all sources | gpt-4o-mini | ~400 in, ~200 out |
| Revision notes | Generate fix instructions | None (template) | 0 (deterministic) |
| Re-verification (if revision) | Re-check fixed claims only | gpt-4o-mini | ~150 in, ~80 out |

Typical article: ~8-15 verifiable claims. Full pipeline with web search: ~$0.01-0.03 per article.
