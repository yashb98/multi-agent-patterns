# Semantic Analysis Overhaul — Design Spec

**Date**: 2026-04-30
**Scope**: 12 files (1 new foundation, 10 component edits, 1 new test suite)
**Goal**: Restructure all 11 semantic analysis components to use embedding similarity as the primary decision mechanism, with string matching as a fast-exit optimization.

## Problem

The audit scored the semantic analysis system 6.5/10. Core issues:
1. `semantic_matcher.py` is named "semantic" but uses zero embeddings — pure string manipulation
2. `OptionAligner._fuzzy_score()` has a bug (`max/max` always = 1.0 for containment)
3. `PageReasoner` cache is hash-based, no semantic near-miss matching
4. `PageTypeClassifier` uses keyword counting, not embeddings
5. `ScreeningDetector` uses regex as primary signal (violates "no regex for semantic work" rule)
6. `_agent_rules()` in `ScreeningPipeline` duplicates intent classifier with keyword matching
7. 4+ independent `MemoryEmbedder()` instantiations — no sharing
8. Inconsistent cosine similarity implementations (pure Python vs numpy)
9. Hardcoded weights/thresholds everywhere — not adaptive
10. No evaluation framework to measure quality

## Design

### 1. Foundation: `shared/semantic_utils.py` (NEW)

Standalone functions with a lazy singleton `MemoryEmbedder`:

```python
def get_embedder() -> MemoryEmbedder:
    """Shared singleton. Replaces 4+ independent MemoryEmbedder() instantiations."""

def semantic_similarity(a: str, b: str) -> float:
    """Numpy cosine similarity between two texts. LRU-cached embeddings."""

def best_semantic_match(query: str, candidates: list[str], min_score: float = 0.75) -> tuple[str | None, float]:
    """Find best matching candidate by embedding similarity."""

def rank_semantic_matches(query: str, candidates: list[str], top_k: int = 5) -> list[tuple[str, float]]:
    """Rank candidates by descending similarity."""

def get_adaptive_weights(component: str, defaults: dict[str, float]) -> dict[str, float]:
    """Load adaptive signal weights from SQLite (data/adaptive_weights.db)."""

def record_weight_outcome(component: str, signal_contributions: dict[str, float], success: bool) -> None:
    """Record outcome for weight adjustment. Multiplicative update: +5% success, -5% failure, renormalize."""
```

All cosine similarity uses numpy vectorized ops. In-memory LRU cache (maxsize=2048) for embeddings.

Dependency: `shared/semantic_utils.py` imports from `shared/memory_layer/_embedder.py` only. Correct dependency direction.

### 2. SemanticMatcher (`form_engine/semantic_matcher.py`) — RESTRUCTURE

New 6-tier cascade (embedding is Tier 4, the primary semantic tier):

1. **Exact match** (free) — unchanged
2. **Canonical aliases** (free) — `CANONICAL_ALIASES` dict, fast-exit optimization
3. **Numeric range** (free) — unchanged
4. **Embedding similarity** (NEW, ~5ms) — `best_semantic_match(value, options)`. Catches "Male"→"Man", "United Kingdom"→"UK" that aliases miss
5. **Token overlap** (free) — demoted from Tier 4 to fallback
6. **Substring containment** (free) — unchanged

`checkbox_intent()` restructured: embedding similarity against consent/marketing anchor phrases instead of keyword sets.

### 3. OptionAligner (`screening_option_aligner.py`) — FIX + RESTRUCTURE

Bug fix: `_fuzzy_score()` line 173 — `min(len(a), len(b)) / max(len(a), len(b))` not `max/max`.

New alignment flow:
1. Learned corrections DB — unchanged
2. Exact match — unchanged
3. Normalized match — unchanged
4. **Embedding similarity** (NEW) — `best_semantic_match(answer, options, min_score=0.70)`
5. Fuzzy word overlap — demoted to last-resort fallback (with bug fix)

### 4. PageReasoner (`page_analysis/page_reasoner.py`) — ADD SEMANTIC CACHE

When hash-based fast path misses:
- Embed current `page_text[:200]` and compare against stored `page_understanding` strings from all cached entries for the same domain
- New SQLite column: `page_understanding_text` stored alongside `result_json`
- Threshold: 0.90 cosine similarity (high bar — page actions must be very similar)
- Hash-based fast path stays as free exact-match optimization

### 5. PageTypeClassifier (`page_analysis/classifier.py`) — ADD EMBEDDING SIGNAL

Reference descriptions per page type:
```python
_PAGE_TYPE_ANCHORS = {
    "application_form": "job application form with input fields for personal details and resume upload",
    "verification_wall": "security challenge captcha or verification blocking page access",
    "confirmation": "application submitted successfully thank you page",
    "login_form": "sign in login page with email and password fields",
    "signup_form": "create new account registration form",
    "email_verification": "check your email to verify your account",
    "session_expired": "session timed out please log in again",
    "consent_gate": "agree to terms and conditions privacy policy consent",
    "expired_job": "this job is no longer available position has been filled",
    "job_description": "job listing with role description requirements and apply button",
    "unknown": "unrecognized page content",
}
```

Embedding similarity score added to feature vector with weight on par with other strong signals.

### 6. ScreeningDetector (`screening_detector.py`) — REMOVE REGEX, RESTRUCTURE

**Remove** `_SCREENING_KEYWORDS` regex entirely.

Embedding similarity becomes primary signal:
```python
_SIGNAL_WEIGHTS = {
    "embedding_similarity": 0.40,      # primary (was boost-only at 0.35)
    "is_select_radio_checkbox": 0.20,  # structural
    "has_question_mark": 0.15,         # reduced from 0.30
    "options_contain_yes_no": 0.15,    # structural
    "is_required_and_unmapped": 0.10,  # reduced from 0.20
}
```

Weights stored via adaptive weights system. Anchors loaded lazily, used as primary not afterthought.

### 7. ScreeningPipeline (`screening_pipeline.py`) — REMOVE REDUNDANT RULES

**Remove** `_agent_rules()` method. Its keyword matching is a worse duplicate of:
- Step 3: Intent classification (embedding-based)
- Step 4: Intent resolution from profile

Fix `_finalise()` salary detection:
```python
# OLD: keyword matching
if any(kw in question.lower() for kw in ("salary", "compensation", "pay")):

# NEW: use classified intent
if result.get("intent") in ("salary_current", "salary_expected"):
```

Pipeline steps become: decompose → semantic cache → intent classify → profile resolve → LLM fallback → align → validate → learn.

### 8. ScreeningIntentClassifier (`screening_intent.py`) — USE SHARED UTILS

- Replace local `_cosine_similarity()` with numpy vectorized ops via shared utils
- Store prototypes as numpy arrays for batch comparison
- Use `get_embedder()` singleton instead of own `MemoryEmbedder()`

### 9. ScreeningSemanticCache (`screening_semantic_cache.py`) — USE SHARED UTILS

- Replace local `_cosine_similarity()` with shared utils
- Replace `_infer_boolean_from_text()` keyword sets with `semantic_similarity()` against affirmative/negative anchor phrases
- Use `get_embedder()` singleton

### 10. NLP Classifier (`nlp_classifier.py`) — USE SHARED EMBEDDER

- Use `get_embedder()` singleton instead of loading own model via `_load_model()`
- Keep numpy vectorized ops (already correct)
- Keep disk cache (`intent_embeddings.npz`) for startup performance

### 11. FieldMapper (`form_engine/field_mapper.py`) — ADD EMBEDDING FALLBACK

- `_fuzzy_custom_answer()`: add `best_semantic_match(label, custom_answer_keys)` after keyword matching
- `_DIVERSITY_KEYWORDS` dict supplemented with embedding similarity for labels that don't hit keywords
- Vision recovery stays as-is (already uses proper LLM)

### 12. Adaptive Weights (`shared/semantic_utils.py`)

SQLite-backed weight store (`data/adaptive_weights.db`):
- `get_adaptive_weights(component, defaults)` — returns current weights, initializes from defaults if new
- `record_weight_outcome(component, signal_contributions, success)` — multiplicative update (+5% success, -5% failure, renormalize)
- Used by: PageTypeClassifier, ScreeningDetector

### 13. Evaluation Framework (`tests/jobpulse/test_semantic_quality.py`) — NEW

Golden test sets per component:
- **SemanticMatcher**: 30+ known option matches (gender, boolean, country, experience, salary)
- **IntentClassifier**: 40+ question→intent pairs across all 31 intents
- **PageTypeClassifier**: 10+ page text→type pairs
- **ScreeningDetector**: 20+ field→screening/non-screening labels
- **OptionAligner**: 20+ answer→option alignment cases

Passing bar: >=90% accuracy per golden set. Below 90% = test failure.
Marker: `@pytest.mark.slow` (embedding model load).

## What Stays Unchanged

- `CANONICAL_ALIASES` in semantic_matcher — free fast-exit
- `_OPTION_NORMALISATION` in option_aligner — free fast-exit
- Vision fallback paths in field_mapper — already proper LLM
- Qdrant integration in semantic_cache — already good
- LLM fallback in screening_pipeline — already uses CognitiveEngine
- `_SEED_PROTOTYPES` in intent classifier — anchor set

## File Summary

| File | Action | ~Lines |
|---|---|---|
| `shared/semantic_utils.py` | NEW | 150 |
| `form_engine/semantic_matcher.py` | RESTRUCTURE | 80 |
| `screening_option_aligner.py` | FIX + RESTRUCTURE | 60 |
| `page_analysis/page_reasoner.py` | ADD semantic cache | 50 |
| `page_analysis/classifier.py` | ADD embedding signal | 40 |
| `screening_detector.py` | REMOVE regex, RESTRUCTURE | 60 |
| `screening_pipeline.py` | REMOVE redundant rules | 50 |
| `screening_intent.py` | USE shared utils | 30 |
| `screening_semantic_cache.py` | USE shared utils | 30 |
| `nlp_classifier.py` | USE shared embedder | 20 |
| `form_engine/field_mapper.py` | ADD embedding fallback | 30 |
| `tests/jobpulse/test_semantic_quality.py` | NEW eval suite | 250 |
| **Total** | | **~850** |

## Risks

1. **Embedding model availability** — All embedding calls go through `get_embedder()` which wraps `MemoryEmbedder`. If Ollama/model is unavailable, every component must degrade gracefully to its non-embedding tiers. Each component already handles `None` embedder — this pattern continues.
2. **Latency** — Embedding calls add ~5ms per comparison. Mitigated by LRU cache (same text isn't re-embedded) and lazy loading (embeddings only computed when fast tiers fail).
3. **Test isolation** — Golden test sets require embedding model. Marked `@pytest.mark.slow`. Tests use `tmp_path` for any DB access.
