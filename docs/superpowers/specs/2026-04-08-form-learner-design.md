# FormLearner — Self-Learning Job Application System

## Overview

3-layer self-learning system that makes job application form filling progressively cheaper, faster, and more accurate over time. Each layer works independently and adds value immediately.

**Goal:** "The 2nd application to the same ATS form costs zero LLM calls. The 500th application trains a local model that replaces the cloud LLM entirely."

**Architecture:** Recipe-first deterministic fill → platform insights injection → LLM fallback → trajectory logging → nightly batch learning → eventual Blackwell fine-tuning.

**Hardware:** NVIDIA G10 Blackwell GPU for local inference and fine-tuning (deferred until ~500 trajectories collected).

---

## System Architecture

```
ApplicationOrchestrator
  └─ _fill_application()
       └─ NativeFormFiller.fill()              # existing execution engine
            ├─ RecipeStore.lookup()             # Layer 1 — try recipe first
            ├─ PlatformInsights.get()           # Layer 2 — inject platform knowledge
            ├─ _map_fields() via LLM            # existing — fallback when no recipe
            └─ TrajectoryStore.log()            # Layer 3 — record everything

Nightly Batch (3am cron):
  TrajectoryLearner
    ├─ Extract recipes from successful trajectories → RecipeStore
    ├─ Cluster failures → generate PlatformInsights via LLM
    ├─ Compute per-platform metrics
    └─ Prune old trajectories (>90 days)

Deferred:
  TrainingPipeline
    ├─ Export ShareGPT JSONL from TrajectoryStore
    ├─ Fine-tune 7B/70B on Blackwell (~15-30 min)
    └─ A/B test local model vs gpt-4.1-mini
```

## New Files

| Module | Purpose | ~LOC |
|--------|---------|------|
| `jobpulse/recipe_store.py` | Recipe CRUD + lookup by page signature | 250 |
| `jobpulse/platform_insights.py` | Structured per-platform knowledge notes | 150 |
| `jobpulse/trajectory_store.py` | Field-level action logging + JSONL export | 300 |
| `jobpulse/trajectory_learner.py` | Nightly batch: recipe extraction + failure analysis | 200 |
| `data/fill_trajectories.db` | SQLite database (auto-created) | — |

## Modified Files

| File | Change |
|------|--------|
| `jobpulse/native_form_filler.py` | Add 3 layer calls (recipe lookup, insights injection, trajectory logging) + constructor DI params |
| `jobpulse/runner.py` | Add `trajectory-learn` to 3am cron |

## Unchanged

- `PlaywrightDriver` — untouched
- `ApplicationOrchestrator` — untouched (already delegates to `NativeFormFiller.fill()`)
- Extension engine path — untouched
- All existing tests — untouched
- Ralph Loop — untouched

---

## Layer 1: Recipe Store

### Purpose

Store proven field mappings keyed by platform + page structure. When a form is seen again, fill deterministically with zero LLM cost.

### Page Signature

```python
def compute_page_signature(fields: list[dict]) -> str:
    """Hash of sorted (label, field_type) tuples — ignores values, order, selectors."""
    normalized = sorted((f["label"].lower().strip(), f["type"]) for f in fields)
    return hashlib.sha256(json.dumps(normalized).encode()).hexdigest()[:16]
```

Same Greenhouse form at different companies produces the same signature if fields are identical. Extra fields = different signature = separate recipe.

### Schema

```sql
CREATE TABLE recipes (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    page_signature TEXT NOT NULL,
    company TEXT,
    mappings TEXT NOT NULL,
    success_count INTEGER DEFAULT 1,
    fail_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, page_signature, company)
);
```

- `mappings`: JSON array of `{label, field_type, profile_key, format_hint, verified}`.
- `company`: NULL = generic recipe, set = company-specific override.

### Lookup Priority

1. Company-specific recipe (`platform + page_signature + company`)
2. Generic platform recipe (`platform + page_signature + company IS NULL`)
3. No recipe → fall through to LLM

### Confidence-Gated Promotion

Only fields where `value_verified=True` and `success=True` are promoted. File upload fields are never included in recipes. Rules:

- 1 successful verified fill → create/update recipe
- `fail_count >= 3` → auto-demote (remove from lookup, fields fall back to LLM)
- `success_count >= 5` → mark "stable" (trajectory logging continues but recipe mappings are frozen — new fills do not update the stored mappings)

### Replay During Fill

```python
recipe = recipe_store.lookup(platform, page_signature, company)
if recipe:
    covered = apply_recipe(recipe, fields, profile)
    uncovered = [f for f in fields if f not in covered]
    if not uncovered:
        return covered  # full recipe hit — zero LLM cost
    # Partial hit — only send uncovered fields to LLM
    llm_mappings = await self._map_fields(uncovered, profile, insights=insights)
    return covered + llm_mappings
```

---

## Layer 2: Platform Insights

### Purpose

Learn platform-specific quirks that span across forms — formatting rules, field behavior, timing issues. Cross-form knowledge, not per-form mappings.

### Examples

- "Indeed phone field rejects +44 prefix, expects 07XXX format"
- "Workday salary field expects annual integer, no commas or currency symbol"
- "Lever autocomplete fields need 1500ms wait, not the default 800ms"
- "LinkedIn Easy Apply has max 2 pages, no multi-page navigation needed"

### Schema

```sql
CREATE TABLE platform_insights (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    category TEXT NOT NULL,
    field_pattern TEXT,
    insight TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    times_applied INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- `category`: One of `'format'`, `'timing'`, `'field_behavior'`, `'navigation'`, `'gotcha'`.
- `field_pattern`: Regex or label substring this applies to. NULL = platform-wide.
- `source`: One of `'auto_failure_analysis'`, `'manual'`, `'trajectory_learner'`.
- `confidence`: 1.0 = verified, 0.5 = inferred from single failure.

### Insight Generation

**Automatic (from failures):** The nightly trajectory learner clusters failed `field_actions` by `(platform, field_label pattern)`. If 2+ failures share the same pattern, generates an insight via a single gpt-4.1-mini call (~$0.002).

**Manual:** Added via Telegram command or directly. `source='manual'`, `confidence=1.0`.

### Prompt Injection

```python
insights = platform_insights.get_for_prompt(platform)
# Returns formatted string:
# "Platform rules for Indeed:
#  - Phone fields: strip +44 prefix, use 07XXX format
#  - Salary: annual integer, no commas"
```

- Max 10 insights per prompt (sorted by `times_applied` descending)
- `times_applied` increments every time an insight is included
- Insights with `confidence < 0.3` are auto-pruned in nightly batch

---

## Layer 3: Trajectory Store + Training Pipeline

### Purpose

Record every field-level action for feeding Layers 1-2 with data and building the training dataset for Blackwell fine-tuning.

### Schema

```sql
CREATE TABLE trajectories (
    id INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    company TEXT,
    page_number INTEGER NOT NULL,
    page_signature TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE field_actions (
    id INTEGER PRIMARY KEY,
    trajectory_id INTEGER REFERENCES trajectories(id),
    field_label TEXT NOT NULL,
    field_type TEXT NOT NULL,
    field_selector TEXT,
    dom_context TEXT,
    profile_key TEXT,
    value_attempted TEXT,
    value_set TEXT,
    value_verified BOOLEAN,
    source TEXT NOT NULL,
    error TEXT,
    duration_ms INTEGER
);

CREATE TABLE application_outcomes (
    id INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    company TEXT,
    total_fields INTEGER,
    fields_verified INTEGER,
    fields_failed INTEGER,
    validation_errors INTEGER,
    outcome TEXT NOT NULL,
    total_duration_ms INTEGER,
    llm_calls INTEGER,
    recipe_hits INTEGER,
    timestamp TEXT NOT NULL
);
```

- `field_actions.source`: One of `'recipe'`, `'llm'`, `'screening'`, `'upload'`, `'consent'` — tracks where each value came from.
- `field_actions.dom_context`: Surrounding labels, placeholders, dropdown options — rich context for training data.
- `application_outcomes.recipe_hits`: Count of fields filled via recipe (Layer 1) — tracks learning effectiveness.

### Logging Integration

3 calls added to `NativeFormFiller.fill()`. The `application_id` is generated by the orchestrator (UUID) and passed to `fill()` as a new parameter:

```python
# Start of page:
traj_id = self._trajectory_store.start(app_id, platform, company, page_num, page_sig)

# After each fill:
self._trajectory_store.log_action(traj_id, label, field_type, selector, dom_ctx,
                                   profile_key, value, result, source, duration)

# End of application:
self._trajectory_store.record_outcome(app_id, platform, company, stats)
```

### JSONL Export (ShareGPT Format)

```python
def export_training_data(min_verified_pct=0.8) -> Path:
    """Export successful trajectories as ShareGPT JSONL for fine-tuning."""
    # Only applications where 80%+ fields were verified
    # Format: system prompt + fields input + mapping output
    # Output: data/training/field_mapper.jsonl
```

Each trajectory becomes one training example:
- **Input:** Field labels + types + dom_context + profile data
- **Output:** The mapping (profile_key + value + format applied)

### Nightly Batch (`trajectory_learner.py`, 3am cron)

1. **Recipe extraction:** Query today's successful trajectories → promote verified fields to RecipeStore (confidence-gated)
2. **Failure clustering:** Group failed field_actions by `(platform, field_label pattern)` → if 2+ failures match, generate PlatformInsight via single LLM call
3. **Metrics:** Per-platform success rate, recipe hit rate, LLM call count
4. **Pruning:** Remove trajectories older than 90 days (keep application_outcomes forever)

### Blackwell Fine-Tuning Path (Deferred)

Not built in initial implementation. Schema and export format are designed to support it.

```bash
# When ~500 trajectories collected:
python -m jobpulse.trajectory_store export --min-verified 0.8 --output data/training/field_mapper.jsonl
# Fine-tune on Blackwell (~15 min for 7B, ~30 min for 70B):
python -m jobpulse.training_pipeline train --base-model Qwen/Qwen2.5-7B --lora-rank 32 --lr 4e-5
# A/B test:
python -m jobpulse.training_pipeline eval --compare gpt-4.1-mini
```

---

## Integration Design

### Constructor Injection

```python
class NativeFormFiller:
    def __init__(self, page, driver, recipe_store=None,
                 platform_insights=None, trajectory_store=None):
        self._page = page
        self._driver = driver
        self._recipe_store = recipe_store or RecipeStore()
        self._platform_insights = platform_insights or PlatformInsights()
        self._trajectory_store = trajectory_store or TrajectoryStore()
```

Tests inject mocks. Production uses defaults connecting to `data/fill_trajectories.db`.

### Error Handling — Learning Never Blocks Filling

Every Layer 1/2/3 call is wrapped in try/except. Failures log a warning and fall through:

```python
try:
    recipe = self._recipe_store.lookup(platform, page_sig, company)
except Exception as exc:
    logger.warning("Recipe lookup failed, falling through to LLM: %s", exc)
    recipe = None
```

**Key principle:** The learning system is advisory, never blocking. If recipe lookup fails, LLM fills. If trajectory logging fails, the application proceeds. If the nightly batch crashes, no applications are affected. The fill engine works identically with all 3 layers disabled.

### Orchestrator Integration

```python
# In ApplicationOrchestrator._fill_application(), existing branch:
if self.engine == "playwright":
    filler = NativeFormFiller(
        page=self.driver.page,
        driver=self.driver,
        # Layers auto-initialize with default DB path
    )
    return await filler.fill(...)
```

No changes to ApplicationOrchestrator — the 3 layers are internal to NativeFormFiller.

---

## Staged Rollout

| Phase | Timeline | What Ships | Benefit |
|-------|----------|------------|---------|
| 1 | Week 1 | Recipe Store + NativeFormFiller integration | 2nd visit to same form = zero LLM cost |
| 2 | Week 2 | Platform Insights + prompt injection | Platform quirks learned from failures |
| 3 | Week 3 | Trajectory Store + nightly learner | Full action logging, automated recipe extraction |
| 4 | Month 3+ | Training pipeline + Blackwell fine-tuning | Replace cloud LLM with local model |

---

## Research Basis

### Patterns Adopted

| Pattern | Source | How Used |
|---------|--------|----------|
| Skill library with vector retrieval | Voyager (NVIDIA) | RecipeStore — store successful fills, retrieve by page signature |
| DOM element indexing | Agent-E, Browser-Use | NativeFormFiller._scan_fields() already does this |
| Plan/execute separation | WebAgent (DeepMind) | _map_fields() (plan) → _fill_by_label() (execute) — already exists |
| Prompt-injected memory | Hermes Agent (Nous Research) | PlatformInsights injected into LLM prompts |
| Frozen snapshot injection | Hermes Agent | Insights loaded once per fill, not updated mid-fill |
| Confidence-gated promotion | Original design | Only verified fields become recipes |
| Partial credit rewards | WebArena benchmarks | Per-field verification, not binary pass/fail |

### Patterns Rejected

| Pattern | Source | Why Rejected |
|---------|--------|-------------|
| Runtime code modification | AutoGPT | Dangerous, hard to debug, marginal benefit over recipes |
| Full online RL | WebArena | Reward signal too sparse, recipes give 80% of benefit immediately |
| Computer Use as primary engine | Anthropic/OpenAI | $0.10-0.30/app, 5x slower, no better accuracy |
| General self-improving agent | Hermes/AutoGPT | Overkill — form filling is a narrow problem |
| Flat text memory with § delimiters | Hermes Agent | Structured JSON is queryable, injectable, and prunable |
| Unbounded task decomposition | BabyAGI | Fails without grounding — our problem is already well-scoped |

### Hermes Agent Verified Claims

| Claim | Reality |
|-------|---------|
| "Self-improving" | Writes markdown notes + saves prompts as skill files. No code modification. |
| "Learning loop" | MEMORY.md is a flat 2200-char text file with § delimiters, frozen at session start |
| "InsightsEngine" | Pure dashboarding — token/cost analytics, zero learning |
| "RL training" | Offline pipeline only (Qwen3-8B, LoRA rank 32). Running agent never self-modifies |
| "40+ tools" | 37 core + MCP dynamic tools. Cannot create new tools at runtime |
| "User modeling" | Second flat text file (USER.md, 1375 chars). No embedding-based model |
| "Procedural memory" | Markdown skill files the LLM writes. Progressive disclosure is real (list → full view) |

### Existing Infrastructure Reused

| Module | What It Does | How FormLearner Uses It |
|--------|-------------|------------------------|
| `NavigationLearner` | Saves/replays nav sequences per domain | Pattern reference — recipe store follows same save/replay model |
| `ScanLearningEngine` | 17-signal statistical correlation for anti-detection | Pattern reference — failure clustering in trajectory learner |
| `ScreeningAnswers` | 3-tier Q&A cache (regex → SQLite → LLM) | Already handles screening questions — FormLearner handles the rest |
| `ExperienceMemory` | GRPO-scored experience injection | Could add 'form_fill' domain for general strategy learning |
| `Ralph TestStore` | Per-URL iteration diagnostics | Pattern reference — trajectory store is field-level version |

---

## Success Metrics

| Metric | Baseline (today) | Target (Month 1) | Target (Month 3) |
|--------|-------------------|-------------------|-------------------|
| LLM calls per application | 3-5 | 1-2 (recipe covers 60%+ fields) | 0 (local model) |
| Cost per application | ~$0.01-0.03 | ~$0.005 | ~$0.001 (local) |
| Fill accuracy (verified fields) | ~75% | ~85% (insights help) | ~90% (trained model) |
| Time per application | ~30s | ~15s (recipe = instant fill) | ~5s (local inference) |
| Recipe coverage | 0% | 60% of repeat forms | 90% of all forms |
