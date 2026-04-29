# Screening Answers Feedback Overhaul

**Date**: 2026-04-28
**Status**: Approved
**Scope**: Fix broken feedback loop, consolidate caches, wire both manual + cron paths
**Target**: 6.0/10 → 9.5+/10

## Problem

The screening answers system scores 6.0/10 due to 6 root causes:

1. **V1 production schema broken** — `ats_answer_cache` missing `success_count`, `correction_count`, `last_verified_at` columns. `record_answer_verification()` has zero callers and would crash if called.
2. **`times_used` double-increments** — V2 `_touch_sqlite()` increments on every lookup, `cache()` increments on upsert conflict. "Right to work" shows 26 uses when reality is ~8-10.
3. **Success feedback path severed** — `record_outcome()` only called from `confirm_application()` → `DraftSession` (2 call sites). Fragile colon-format parsing silently drops entries.
4. **Zero real corrections in production** — 4 entries, all test data from `test.com`. `final_mapping` never captured in manual sessions.
5. **V1/V2 cache disconnect** — Two SQLite databases, no cross-feed. V1 gets no success signals.
6. **Notice period 0 successes** — 7 times_used, 0 success_count because `record_outcome()` never fires for it.

## Design

### 1. ScreeningOutcomeRecorder (New Module)

**File**: `jobpulse/screening_outcome_recorder.py`

Single class that owns all writes to V2 semantic cache counters. Two methods:

- `record_fill(question, answer, field_options, field_type, intent)` — Called by form filler after each screening field is filled. This is the "weak success" signal (answer was used). Increments `times_used` once per actual fill.

- `record_confirmation(screening_results, corrections)` — Called by `confirm_application()` from both cron and manual paths. Takes structured data (list of `{question, answer, field_options, field_type}` dicts) plus corrections dict from `CorrectionCapture`. For each screening result:
  - If NOT in corrections → `record_outcome(success=True)` + confidence boost
  - If in corrections → `record_outcome(success=False)` + teach V2 feedback loop the corrected answer

The recorder is the **only writer** to semantic cache success/correction counters.

### 2. Fix `times_used` Counter Semantics

Three changes to `ScreeningSemanticCache`:

- **Remove counter increment from `_touch_sqlite()`** — Only update `last_used_at` timestamp, not `times_used`.
- **Remove counter increment from `cache()` upsert** — Change `ON CONFLICT` to NOT touch `times_used`.
- **Add `increment_usage(question)` method** — Called only by `ScreeningOutcomeRecorder.record_fill()`.

Net effect: `times_used` accurately reflects real field fills, making `success_count / times_used` a reliable success rate.

### 3. Structured Screening Data in Dry-Run Results

Replace colon-separated strings with structured dicts:

```python
# Old format (fragile, breaks on questions with colons):
"screening_questions": ["What is your salary?: 35000", ...]

# New format:
"screening_results": [
    {
        "question": "What are your salary expectations?",
        "answer": "28000-33000",
        "field_type": "select",
        "field_options": ["20-25k", "25-30k", "30-35k"],
        "intent": "salary_expected",
        "strategy": "pattern_match",
    },
    ...
]
```

Updated call sites:
- `native_form_filler.py` — 5 append sites (lines ~1402, 1412, 1426, 1441, 1521) change from `f"{label}:{answer}"` strings to structured dicts
- `confirm_application()` in `applicator.py` — consume structured dicts, delete colon parsing

### 4. Retire V1 Cache, Migrate to V2

- **One-time migration script**: Read V1 entries from `ats_answer_cache`, embed question text, insert into V2 `screening_semantic_cache.db` with `confidence=0.7`. Skip generic entries ("Question", "Question 0", "Email").
- **Remove V1 write path from screening_answers.py**: Delete `JobDB.cache_answer()` / `get_cached_answer()` calls. V2 semantic cache subsumes exact-match (cosine 1.0 for identical questions).
- **Keep V1 table and methods**: Don't drop the table or delete `JobDB` methods — just stop using them from the screening path.
- **Simplified resolution order**:
  ```
  Tier 1: regex patterns (COMMON_ANSWERS) — unchanged
  Tier 2: agent rules (correction overrides) — unchanged
  Tier 3: V2 pipeline (semantic cache → intent → regex → rules → LLM)
  Tier 4: LLM generation → caches in V2 (not V1)
  ```

### 5. Wire Both Paths (Manual + Cron)

**Fill-time signals (both paths, new)**:
- `ScreeningOutcomeRecorder.record_fill()` called by the form filler itself, immediately after each screening field is filled. Works identically for cron and manual sessions since both use the same form-filling code.

**Cron path (fix existing)**:
- `DraftSession.run_submit_and_confirm()` → `confirm_application()` — replace colon-format parsing with structured `screening_results` consumption.
- Pass structured results to `ScreeningOutcomeRecorder.record_confirmation()`.

**Manual Claude Code path (new wiring)**:
- Add `capture_final_state()` helper that reads current form field values via Playwright (page is still open during review) and builds `final_mapping` from live DOM.
- `confirm_application()` receives the final_mapping alongside agent_mapping from dry-run result.
- Same `ScreeningOutcomeRecorder.record_confirmation()` call as cron path.

### 6. V1 Schema Migration

Add `ALTER TABLE` migration in `JobDB._init_db()`:

```python
existing = {r[1] for r in conn.execute("PRAGMA table_info(ats_answer_cache)").fetchall()}
for col, typ in [
    ("success_count", "INTEGER DEFAULT 0"),
    ("correction_count", "INTEGER DEFAULT 0"),
    ("last_verified_at", "TEXT"),
]:
    if col not in existing:
        conn.execute(f"ALTER TABLE ats_answer_cache ADD COLUMN {col} {typ}")
```

Defensive migration ensuring V1 schema matches code regardless of table creation date.

## Files Changed

| File | Change |
|------|--------|
| `jobpulse/screening_outcome_recorder.py` | **New** — single feedback writer |
| `jobpulse/screening_semantic_cache.py` | Fix counter semantics, add `increment_usage()` |
| `jobpulse/screening_answers.py` | Remove V1 calls, simplify resolution tiers |
| `jobpulse/applicator.py` | Structured screening_results, recorder wiring |
| `jobpulse/native_form_filler.py` | Emit structured screening dicts, call `record_fill()` |
| `jobpulse/job_db.py` | Schema migration for V1 table |
| `scripts/migrate_v1_screening_cache.py` | **New** — one-time V1→V2 migration |
| `tests/jobpulse/test_screening_outcome_recorder.py` | **New** — tests for recorder |

## Success Criteria

- `times_used` increments exactly once per actual field fill (not per lookup or cache store)
- `success_count` increments on every non-corrected screening field after confirm
- `correction_count` increments and teaches V2 on every corrected field
- Both manual Claude Code sessions and cron DraftSession paths feed the same recorder
- V1 `ats_answer_cache` is no longer written to from screening_answers.py
- Zero colon-format string parsing in confirm_application()
- Production `ats_answer_cache` has all tracking columns after first run

## Verification

```bash
# After implementation:
# 1. Run a dry-run fill — check screening_results has structured dicts
# 2. Confirm application — check semantic cache success_count incremented
# 3. Make a correction — check correction_count incremented + feedback loop taught
# 4. Check times_used matches actual fill count (not inflated)
# 5. Run migration script — check V1 entries appear in V2
python -m pytest tests/jobpulse/test_screening_outcome_recorder.py -v
python -m pytest tests/jobpulse/test_screening_answers.py -v
```
