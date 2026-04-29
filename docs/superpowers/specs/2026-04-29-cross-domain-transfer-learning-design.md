# Cross-Domain Transfer Learning for ATS Platforms

**Date:** 2026-04-29
**Status:** Approved
**Branch:** `feature/memory-3-engine`

---

## Problem

All 6 learning databases (FormExperienceDB, NavigationLearner, GotchasDB, CorrectionCapture, SemanticAnswerCache, FormExperienceDB timing) are keyed by domain. A successful Greenhouse application at `boards.greenhouse.io/companyA` teaches the system nothing about `boards.greenhouse.io/companyB` or the structurally similar `jobs.lever.co/companyX`. Each domain starts cold despite the platform being well-understood.

## Solution

A dedicated `PlatformTransferEngine` that:
1. Computes similarity between domains using 8 learned signals
2. Uses Thompson Sampling (Beta distributions) to select the best donor domain for each signal type
3. Records transfer outcomes to improve future donor selection
4. Integrates transparently into existing DB lookup methods as a fallback on domain-miss

## Approach

**Dedicated engine** (not inline fallback queries). A new `jobpulse/platform_transfer.py` module owns all transfer logic. Existing DBs call into it when their domain lookup returns nothing.

**Fully dynamic similarity** — no static platform groups. Similarity is computed from actual data across 8 signals, so the system discovers that `boards.greenhouse.io/stripe` is similar to `boards.greenhouse.io/airbnb` without being told "Greenhouse sites are alike."

**Hybrid architecture** — pre-computed similarity matrix (refreshed after each application) + query-time transfer from fresh donor data. Matrix computation is cheap (<100ms for 200 domains).

---

## Data Model

### Table: `platform_similarity`

Both tables stored in `data/form_experience.db` (co-located with the primary consumer). The engine reads from 3 DBs for similarity computation: `form_experience.db` (5 signals), `field_corrections.db` (correction_rates), and `navigation_learning.db` (navigation_flow). All reads are read-only — the engine never writes to external DBs.

| Column | Type | Description |
|--------|------|-------------|
| domain_a | TEXT | First domain |
| domain_b | TEXT | Second domain |
| signal_type | TEXT | One of the 8 signal types |
| similarity | REAL | 0.0–1.0 similarity score |
| sample_count | INTEGER | Number of data points used |
| updated_at | TEXT | ISO timestamp |
| **PK** | | (domain_a, domain_b, signal_type) |

### Table: `transfer_outcomes`

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment PK |
| target_domain | TEXT | Domain that received the transfer |
| donor_domain | TEXT | Domain that donated data |
| signal_type | TEXT | Which signal was transferred |
| alpha | REAL | Beta distribution α (successes + 1) |
| beta_param | REAL | Beta distribution β (failures + 1) |
| transfer_count | INTEGER | Total transfers attempted |
| success_count | INTEGER | Successful transfers |
| last_outcome | TEXT | "success" or "failure" |
| created_at | TEXT | ISO timestamp |
| updated_at | TEXT | ISO timestamp |
| **UNIQUE** | | (target_domain, donor_domain, signal_type) |

### 8 Similarity Signals

| Signal | Metric | Data Source |
|--------|--------|-------------|
| field_types | Cosine similarity of field-type frequency vectors | `form_experience.field_types` |
| page_count | 1 - abs(a-b)/max(a,b) | `form_experience.pages_filled` |
| timing_profile | Cosine similarity of [hydration, fill, transition] vectors | `page_timings` table |
| fill_techniques | Jaccard index of technique sets | `fill_techniques` table |
| failure_patterns | Cosine similarity of failure-reason frequency vectors | `form_failure_reasons` table |
| correction_rates | Cosine similarity of correction-type frequency vectors | `data/field_corrections.db` (CorrectionCapture — separate DB) |
| navigation_flow | Normalized Levenshtein distance of nav step sequences | `data/navigation_learning.db` (NavigationLearner — separate DB) |
| container_selectors | Token overlap (Jaccard of tokenized selectors) | `container_selectors` table |

---

## Architecture

### PlatformTransferEngine

Single class in `jobpulse/platform_transfer.py` with three responsibilities:

**1. Similarity Computation** (`recompute_similarity_matrix`)
- Called after each successful application (from `post_apply_hook`)
- Iterates all domain pairs, computes each of 8 signal metrics
- Stores results in `platform_similarity` table
- Skips pairs with <2 data points on either side
- Incremental: only recomputes pairs involving the newly-applied domain

**2. Thompson Sampling Transfer** (`get_transfer_data`)
```python
def get_transfer_data(
    self,
    target_domain: str,
    signal_type: str,
    min_similarity: float = 0.3,
) -> TransferResult | None:
```
- Queries `platform_similarity` for all domains similar to `target_domain` on `signal_type`
- Filters by `min_similarity` threshold
- For each candidate donor, samples from Beta(α, β) distribution
- Selects the donor with the highest sampled value
- Returns the donor domain + similarity score + confidence (sample_count)
- Returns `None` if no donors exceed threshold

**3. Outcome Recording** (`record_outcome`)
```python
def record_outcome(
    self,
    target_domain: str,
    donor_domain: str,
    signal_type: str,
    success: bool,
) -> None:
```
- Updates Beta distribution: success → α += 1, failure → β += 1
- Updates `transfer_count`, `success_count`, `last_outcome`
- Initializes with α=1, β=1 (uniform prior) on first encounter

### TransferResult (TypedDict)

```python
class TransferResult(TypedDict):
    donor_domain: str
    signal_type: str
    similarity: float
    confidence: int   # sample_count from similarity computation
    _transfer: bool   # always True, filtered before JSON serialization
```

---

## Thompson Sampling Details

### Why Thompson Sampling

- **Cold start**: Uniform prior (α=1, β=1) means new donors get explored naturally
- **Exploration/exploitation**: Randomized sampling ensures the system doesn't lock onto the first donor that worked — it tries alternatives proportionally to their success probability
- **Per-signal granularity**: Separate Beta distributions per (target, donor, signal) triple means the system learns that domain X is a good timing donor but a bad field-type donor
- **Lightweight**: One random sample per candidate per query — no matrix inversions or gradient descent

### Success/Failure Criteria

| Signal | Success | Failure |
|--------|---------|---------|
| field_types | ≥70% of transferred field mappings match actual fields | <70% match |
| page_count | Actual pages within ±1 of transferred estimate | Off by >1 |
| timing_profile | No timeout or hydration failure on transferred timing | Timeout or failure occurred |
| fill_techniques | Transferred technique fills the field successfully | Field fill fails, fallback needed |
| failure_patterns | Predicted failure pattern matched actual failure | Unexpected failure type |
| correction_rates | No correction needed on transferred field value | User corrected the value |
| navigation_flow | Transferred nav sequence reaches target page | Sequence leads to wrong page or stuck |
| container_selectors | Transferred selector scopes form correctly (passes validate_field_scan) | Selector returns 0 fields or fails validation |

### Minimum Thresholds

- Similarity ≥ 0.3 to consider a donor (below this, noise dominates)
- Sample count ≥ 2 for the similarity score to be trusted
- Transfer confidence decays: if a donor hasn't been used in 30 days, halve α and β (prevents stale distributions from dominating)

---

## Integration Points

### 1. FormExperienceDB (form_experience_db.py)

Methods that gain transparent transfer fallback:
- `get_timing(domain)` → on miss, query transfer engine for `timing_profile` signal, return donor's timing
- `get_container(domain)` → on miss, query for `container_selectors` signal
- `get_field_mappings(domain)` → on miss, query for `field_types` signal
- `get_failure_reasons(domain)` → on miss, query for `failure_patterns` signal
- `get_scan_strategy(domain)` → on miss, query for `fill_techniques` signal

Pattern:
```python
def get_timing(self, domain: str) -> dict | None:
    result = self._query_timing(domain)  # existing direct lookup
    if result:
        return result
    transfer = self._transfer_engine.get_transfer_data(domain, "timing_profile")
    if transfer:
        donor_timing = self._query_timing(transfer["donor_domain"])
        if donor_timing:
            donor_timing["_transfer"] = True
            donor_timing["_donor"] = transfer["donor_domain"]
            return donor_timing
    return None
```

### 2. NavigationLearner (navigation_learner.py)

- `get_learned_sequence(domain)` → on miss, query for `navigation_flow` signal, return donor's sequence

### 3. GotchasDB (form_engine/gotchas.py)

- `get_gotchas(domain)` → on miss, query for `failure_patterns` signal, return donor's gotchas

### 4. post_apply_hook.py

After recording form experience, trigger:
```python
transfer_engine.recompute_similarity_matrix(domain)
```
And if the application used transferred data (`_transfer` key present in any result), record outcome:
```python
transfer_engine.record_outcome(domain, donor_domain, signal_type, success=fill_succeeded)
```

### 5. OptimizationEngine Integration

Emit `transfer` signal on every transfer event:
```python
optimization_engine.emit_signal("transfer", {
    "target_domain": domain,
    "donor_domain": donor,
    "signal_type": signal,
    "success": bool,
    "similarity": float,
})
```

---

## File Changes

| File | Change |
|------|--------|
| `jobpulse/platform_transfer.py` | **NEW** — PlatformTransferEngine class, similarity metrics, Thompson Sampling |
| `jobpulse/form_experience_db.py` | Add transfer fallback to 5 lookup methods, pass transfer engine in constructor |
| `jobpulse/navigation_learner.py` | Add transfer fallback to `get_learned_sequence()` |
| `jobpulse/form_engine/gotchas.py` | Add transfer fallback to `get_gotchas()` |
| `jobpulse/post_apply_hook.py` | Trigger similarity recomputation + outcome recording |
| `tests/jobpulse/test_platform_transfer.py` | **NEW** — unit tests + `@pytest.mark.live` integration tests |

---

## Testing Strategy

### Unit Tests
- Similarity metric computation for each of the 8 signals (synthetic data, `tmp_path` DB)
- Thompson Sampling: verify exploration with uniform prior, exploitation after strong signal
- Outcome recording: α/β updates, decay after 30 days
- Transfer fallback: domain miss → donor selected → data returned with `_transfer` metadata
- No-transfer case: no similar donors → returns None → original behavior preserved

### Live Integration Tests (`@pytest.mark.live`)
- Apply to a known Greenhouse domain → verify similarity matrix populated
- Query transfer for a new Greenhouse domain → verify donor selected from matrix
- Record outcome → verify Beta distribution updated
- Full pipeline: apply → learn → transfer → apply again → verify improved timing/fields

---

## Non-Goals

- No UI for viewing transfer decisions (Telegram stats may come later)
- No manual platform grouping or admin override
- No cross-DB transfers (e.g., transferring screening answers — SemanticAnswerCache already has its own cross-domain similarity via embeddings)
- No real-time similarity computation on every query (pre-computed matrix only)
