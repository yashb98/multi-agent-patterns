# Pipeline Introspection System Design

**Date**: 2026-04-30
**Inspired by**: "Introspection Adapters" (arXiv:2604.16812)
**Status**: Approved

## Overview

A post-hoc introspection layer that captures every granular action AI agents take during pipeline execution, then uses an LLM verbalizer to produce exhaustive agent-voice narrative reports. Reports are rendered as PDF and sent to Telegram after every application, with CLI drill-down for querying historical data.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Analysis timing | Post-hoc | No pipeline overhead, full context available |
| Output format | PDF to Telegram + CLI | Immediate visibility + queryable history |
| Scope | All 8 categories from day one | Complete coverage, no blind spots |
| DPO refinement | Light (10-20 manual corrections) | Bootstraps quality, then automated takes over |
| Storage | SQLite logs + PDF reports | Queryable + deliverable |
| Verbalization rate | Per-category breakdown | Pinpoints weak areas for DPO training |
| Report style | Agent-voice narrative | Natural debrief, not mechanical bullets |
| Completeness | Exhaustive (every event narrated) | Full reconstruction without raw logs |

---

## 1. Event Bus & Action Logging

### IntrospectionEvent

```python
@dataclass
class IntrospectionEvent:
    timestamp: float
    category: str          # FormFill | Navigation | Screening | Hooks | Learning | PreScreen | CVGen | Submission
    action: str            # e.g., "fill_field", "click_next", "gate_kill"
    target: str            # e.g., field name, URL, gate number
    outcome: str           # success | failure | skip | fallback
    detail: dict           # category-specific payload (value filled, error message, score, etc.)
    duration_ms: float     # wall-clock time for this action
```

### Instrumentation Points (52 total)

**PreScreen (6 points)**:
- `screening_pipeline.py:classify_action()` — gate routing decision
- `recruiter_screen.py:screen()` — Gate 0 title/keyword result
- `skill_graph_store.py:check_kill_signals()` — Gate 1 kill signal check
- `skill_graph_store.py:check_must_haves()` — Gate 2 must-have match
- `skill_graph_store.py:check_competitiveness()` — Gate 3 competitive score
- `pre_submit_gate.py:run_gate4()` — Gate 4 quality check (A1-A3, B1-B2)

**CVGen (5 points)**:
- `job_autopilot.py:_sync_profile()` — profile sync trigger + skills delta
- `cv_templates/__init__.py:generate_cv()` — role profile selected, sections rendered, PDF written
- `cv_templates/__init__.py:_build_extra_skills()` — dynamic skill matching against JD
- `cl_generator.py:generate_cover_letter()` — CL generation trigger, dynamic points built
- `cl_generator.py:polish_points_llm()` — LLM polish call

**Navigation (8 points)**:
- `_navigator.py:_navigate_to_form()` — initial page load
- `_navigator.py:_dismiss_overlays()` — each overlay/cookie dismissed
- `_navigator.py:_detect_page_type()` — DOM classifier result + confidence
- `_navigator.py:_bypass_verification_wall()` — each of 6 bypass stages attempted
- `_navigator.py:_click_apply_button()` — apply button found and clicked
- `_navigator.py:_handle_stuck()` — stuck detection fingerprint comparison
- `page_analysis/classifier.py:classify_page()` — page type + all signal weights
- `page_analysis/page_reasoner.py:reason_about_page()` — LLM page reasoning result

**FormFill (10 points)**:
- `native_form_filler.py:fill_form()` — form fill session start/end
- `native_form_filler.py:_fill_single_field()` — each field fill attempt + resolution method
- `native_form_filler.py:_resolve_field_value()` — value resolution chain (profile -> screening -> LLM)
- `native_form_filler.py:_upload_file()` — file upload attempt
- `field_scanner.py:scan_fields()` — field discovery method used (learned/auto-detect/strategy)
- `field_mapper.py:map_fields()` — field-to-value mapping decisions
- `semantic_matcher.py:match_option()` — each option matching attempt with tier used
- `form_experience.py:record_fill()` — experience DB write
- `vision_tier.py:analyze_field()` — vision fallback trigger
- `native_form_filler.py:_classify_fill_failure()` — failure classification

**Screening (6 points)**:
- `screening_pipeline.py:resolve()` — pipeline entry, cache check
- `screening_pipeline.py:_classify_intent()` — question intent classification
- `screening_pipeline.py:_check_alignment()` — alignment verification
- `screening_pipeline.py:_generate_answer()` — LLM answer generation
- `screening_pipeline.py:_cache_answer()` — cache write
- `screening_decomposer.py:decompose()` — compound question split

**Submission (5 points)**:
- `job_autopilot.py:apply_job()` — dry_run flag, submission decision
- `native_form_filler.py:_find_submit_button()` — submit button discovery
- `job_autopilot.py:confirm_application()` — confirmation + quota update
- `job_db.py:record_application()` — application DB write
- Rate limiter check — platform + daily counts

**Hooks (5 points)**:
- `job_autopilot.py:post_apply_hook()` — hook entry
- `form_experience.py:record_experience()` — form experience DB write
- `job_notion_sync.py:update_application_page()` — Notion update
- `correction_capture.py:capture()` — correction captured
- `agent_rules_db.py:create_rule()` — agent rule created

**Learning (7 points)**:
- `strategy_reflector.py:reflect()` — reflection trigger + trajectory
- `optimization/engine.py:emit_signal()` — each optimization signal
- `optimization/engine.py:aggregate()` — aggregation result
- `experiential_learning.py:store_experience()` — experience memory write
- `agent_performance.py:record_snapshot()` — performance snapshot
- `cognitive_engine.py:think()` — cognitive escalation level
- `navigation_learner.py:record()` — navigation pattern stored

### Buffer Lifecycle

1. `ApplicationOrchestrator.__init__()` creates `IntrospectionBuffer(company, title)`
2. All `emit()` calls during the run append to the thread-local buffer
3. `confirm_application()` or `apply_job()` completion triggers `buffer.flush()` -> SQLite write -> verbalizer -> PDF -> Telegram
4. On unhandled crash, buffer is lost (acceptable — application failed, no report needed)

### Emit Call Pattern

Minimal intrusion — one line per instrumentation point:

```python
from jobpulse.introspection import emit

result = self._do_fill(field, value)
emit("FormFill", "fill_field", target=field.label,
     outcome="success" if result else "failure",
     detail={"value": value, "method": resolution_method,
             "container": container_selector, "duration_ms": elapsed})
```

---

## 2. Post-Hoc Verbalizer

### Verbalization Prompt

```
You are a pipeline agent writing a debrief of a job application you just completed.
Write in first person. Describe every single action you took, in the order you took it.

For each action, describe:
- What you did and why
- What the result was
- If something failed, what you tried as fallback
- If you learned something, what was stored and where

CRITICAL: You must mention EVERY action in the log. No summarizing, no grouping,
no "and N others." If 14 fields were filled, describe all 14 — what the field was,
what value was entered, how it was resolved (cache/LLM/semantic match/vision),
and whether it succeeded.

The reader should be able to reconstruct the EXACT sequence of everything that
happened without looking at the raw log.

Rules:
- Only report actions present in the log. Never invent actions.
- If a category has zero events, say "No actions recorded for [category]."
- Flag anomalies: unusually slow actions, repeated failures, missing downstream signals.
- Check the expected actions checklist and report anything that should have fired but didn't.
```

### Expected Action Checklist

| Run outcome | Expected signals |
|---|---|
| Successful submit | Hooks: post_apply_hook, correction_capture. Learning: strategy_reflect, optimization_signal, experience_store |
| Dry run complete | Hooks: none. Learning: none. Submission: dry_run_review logged |
| Gate kill (pre-screen) | PreScreen: gate_kill with reason. Learning: gate_effectiveness signal |
| Fill failure | FormFill: failure event. Hooks: correction_capture. Learning: gotcha_store if new pattern |
| Nav stuck | Navigation: stuck_detected. Learning: nav_learner update |

### Hallucination Guard

After the LLM generates the narrative, a deterministic validator cross-references every claim against the event log. Any action mentioned in the report that doesn't appear in the log gets flagged as `[UNVERIFIED]` and stripped from the final output.

### LLM Details

- One `smart_llm_call()` per report
- Input: ~3-5K tokens (full event log with detail payloads)
- Output: ~3-5K tokens (full narrative)
- Cost: ~$0.008-0.012 per report

---

## 3. DPO Refinement Loop

### Automated Preference Pairs (always-on)

After every report, the hallucination guard produces a diff:
- `chosen`: the cleaned report (after stripping `[UNVERIFIED]` claims)
- `rejected`: the raw LLM output (before cleaning)

If they differ, that's a preference pair stored in `dpo_pairs`. If identical, no pair generated.

### Manual Correction Pairs (first 10-20 reports)

User reviews report on Telegram and replies with correction:
```
/introspect correct <run_id> "FormFill section says field was skipped but it actually used vision fallback"
```

Manual pairs weighted 3x in prompt refinement.

### Prompt Refinement Cycle

```
Initial prompt (generic)
  -> 10-20 manual corrections -> Prompt v2 (calibrated)
  -> 50 automated pairs -> Prompt v3 (refined)
  -> ongoing automated pairs -> Prompt vN (mature)
```

Common hallucination patterns extracted from accumulated pairs and added as explicit negative examples in the verbalizer prompt.

---

## 4. Report Rendering & Delivery

### Agent-Voice Narrative Style

Reports read like one agent briefing another — natural language paragraphs, not bullets:

> "I ran PreScreen on the ASOS Data Analyst role and all five gates passed cleanly. The strongest signal was Gate 2 — 4 out of 5 must-have skills matched, with only 'Tableau' missing. Gate 4's recruiter simulation scored 8.2, noting strong Python and SQL alignment but flagging limited retail analytics experience.
>
> During form filling, 14 of 16 fields went through without issues, but 'Years of experience' was invisible to the a11y tree — the dropdown was rendered as a custom div. I fell back to vision tier, which identified it and selected '2-3 years.' This is the third time I've seen this pattern on Greenhouse forms — the correction has been stored in AgentRulesDB so I'll handle it directly next time.
>
> One concern: AgentPerformanceDB didn't record a snapshot for this run. post_apply_hook fired, CorrectionCapture and strategy_reflector both ran, but the performance snapshot step was skipped. This means the optimization engine won't have trajectory data for this application."

### PDF Layout

ReportLab PDF with:
- Header: company, role, date, outcome, duration, event count
- 8 category sections in pipeline order (PreScreen -> CVGen -> Navigation -> FormFill -> Screening -> Submission -> Hooks -> Learning)
- Each section: full agent-voice narrative + verbalization rate
- Footer: overall verbalization rate, anomaly count, DPO pairs generated

### Telegram Delivery

PDF sent via `shared/telegram_client.py:send_document()`.
Caption: one-line summary — `"Introspection: ASOS Data Analyst — Applied | 100% verbalized | 1 anomaly"`

### CLI Subcommands

```bash
python -m jobpulse.runner introspect last              # Most recent report (terminal)
python -m jobpulse.runner introspect list              # All runs with outcome + rate
python -m jobpulse.runner introspect show <run_id>     # Full report for a run
python -m jobpulse.runner introspect failures [--category FormFill] [--days 7]
python -m jobpulse.runner introspect correct <run_id> "correction text"
python -m jobpulse.runner introspect stats             # Rolling 7d/30d rates per category
python -m jobpulse.runner introspect ood-report        # Known vs OOD verbalization comparison
```

### File Storage

PDFs saved to `data/introspection/reports/YYYY-MM-DD_company_role.pdf`.
Retained for 90 days, then auto-cleaned on CLI access.

---

## 5. Verbalization Rate Metrics & OOD Tracking

### Exhaustive Verbalization (hard rule)

Every event in the log must be verbalized. No summarization, no grouping. 100% target.

### Coverage Enforcement

After the verbalizer produces the narrative, a validator LLM checks which events are covered:
1. Verbalizer produces draft narrative
2. Validator checks coverage against event list
3. If 100% -> final report
4. If <100% -> re-prompt verbalizer with missed events highlighted
5. Maximum one retry. If second attempt still misses, append structured addendum

### Per-Category Tracking

```python
verbalization_rates = {
    "PreScreen": 1.0,
    "CVGen": 1.0,
    "Navigation": 0.83,
    "FormFill": 0.93,
    "Screening": 1.0,
    "Submission": 1.0,
    "Hooks": 1.0,
    "Learning": 0.75,
}
```

### Trend Monitoring

`introspect stats` shows rolling 7-day and 30-day averages per category.
If any category drops below 80%, the Telegram report footer warns: "Verbalization quality degrading for Learning (72% avg last 7 days)."

### OOD Generalization

Runs on unseen platforms (no `FormExperienceDB` entries) tagged `ood=True`.

```
introspect ood-report

Known platforms (>5 runs):  avg 94% verbalization
OOD platforms (first run):  avg 81% verbalization
Biggest OOD gap: FormFill (known=96%, OOD=71%)
```

Significant OOD drop = signal to add more DPO corrections for unfamiliar patterns.

---

## 6. Module Structure

### New Files

```
jobpulse/introspection/
    __init__.py          # Public API: emit(), flush(), get_buffer()
    events.py            # IntrospectionEvent dataclass, IntrospectionBuffer
    store.py             # SQLite read/write for events, reports, dpo_pairs tables
    verbalizer.py        # LLM verbalization + hallucination guard + retry
    validator.py         # Coverage checker, event-to-narrative cross-reference
    renderer.py          # ReportLab PDF generation, agent-voice layout
    cli.py               # CLI subcommands
    dpo.py               # DPO pair storage, prompt refinement, negative example library
```

### Database Schema

`data/introspection.db` — 3 tables:

```sql
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    outcome TEXT NOT NULL,
    detail TEXT,           -- JSON
    duration_ms REAL,
    ood INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX idx_events_run ON events(run_id);
CREATE INDEX idx_events_category ON events(run_id, category);

CREATE TABLE reports (
    run_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    outcome TEXT NOT NULL,
    event_count INTEGER,
    narrative TEXT NOT NULL,
    pdf_path TEXT,
    verbalization_rates TEXT,  -- JSON
    overall_rate REAL,
    anomaly_count INTEGER,
    retried INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE dpo_pairs (
    pair_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL,
    chosen TEXT NOT NULL,
    rejected TEXT NOT NULL,
    source TEXT NOT NULL,       -- "automated" | "manual"
    created_at REAL NOT NULL
);
```

### Pipeline Integration (3 touch points)

1. `ApplicationOrchestrator.__init__()` — create buffer
2. ~52 `emit()` calls across existing files (one line each)
3. `confirm_application()` / `apply_job()` completion — trigger flush + verbalize + render + send

### Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `INTROSPECTION_ENABLED` | `True` | Master switch for all emit calls |
| `INTROSPECTION_TELEGRAM` | `True` | Send PDF to Telegram |
| `INTROSPECTION_RETENTION_DAYS` | `90` | Auto-cleanup threshold |

### Dependencies

No new packages. Uses existing:
- ReportLab (CV generation)
- `smart_llm_call()` (LLM)
- `shared/telegram_client.py` (delivery)
- SQLite (storage)

---

## Paper Concept Mapping

| Paper concept | Our implementation |
|---|---|
| Joint training across behavior categories | All 8 categories verbalized in single LLM call with shared prompt |
| DPO refinement | Automated (log-vs-report diff) + manual corrections, prompt engineering not fine-tuning |
| Verbalization rate | Per-category metric, 100% target, enforced by validator with retry |
| OOD generalization | `ood` flag on unseen platforms, comparative rate tracking via CLI |
| Introspection adapters | `emit()` calls at 52 action points — thin instrumentation, no pipeline overhead |
