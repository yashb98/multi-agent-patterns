# Revolut Live-Validation Runbook (B/C/A plans, 2026-05-05)

The three plans (`vision-fallback-gate`, `learned-widget-patterns`,
`semantic-first-scanner`) were implemented in commits 805702e..1d39c42.
Each plan ends with a manual end-to-end Revolut run that this session
could not execute (needs a real browser, the live Revolut form, and
a human to make corrections). Run them in order — A and C bootstrap
the learning data B and the others rely on.

## Setup

```bash
cd ~/projects/multi_agent_patterns
# Make sure Chrome with CDP is running
python -m jobpulse.runner chrome-pw
```

## Step 1 — Reset Revolut to Pending Approval

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('data/applications.db')
c.execute(\"UPDATE applications SET status='Pending Approval', updated_at=datetime('now') WHERE job_id IN (SELECT job_id FROM job_listings WHERE company='Revolut')\")
c.commit()
"
rm -f data/live_review_active.json
```

## Step 2 — First run, observe vision-augment + semantic strategies

```bash
JOB_AUTOPILOT_AUTO_SUBMIT=false python -m jobpulse.runner job-apply-next 1
```

Watch the log for at least one of:
- `scan_semantic: matched N/M questions to widgets on welovealfa.com`
- `scan_fields: vision augment added N fields`
- `learned_patterns: N/M known widgets matched on welovealfa.com`
  (this only fires from run 2 onward — run 1 has nothing learned yet).

The agent should reach the dry-run approval gate. The form should now
contain the visa-sponsorship dropdown, notice-period dropdown, and
distributed-engines multiselect — fields that were missed previously.

## Step 3 — Approve / correct, observe DOM-signature capture

Make corrections in the browser as needed, then approve. Check that
`widget_patterns` rows landed for the corrected fields:

```bash
sqlite3 data/form_gotchas.db \
  "SELECT label, widget_type, fix_count FROM widget_patterns WHERE domain='welovealfa.com';"
```

Expect rows for visa-sponsorship, notice-period, distributed-engines,
country-of-residence — anything you corrected.

## Step 4 — Re-run, expect learned-pattern hits

```bash
sqlite3 data/applications.db \
  "UPDATE applications SET status='Pending Approval' WHERE job_id IN (SELECT job_id FROM job_listings WHERE company='Revolut')"
rm -f data/live_review_active.json
JOB_AUTOPILOT_AUTO_SUBMIT=false python -m jobpulse.runner job-apply-next 1
```

The log should now show:
```
learned_patterns: N/N known widgets matched on welovealfa.com
```
The agent should fill the previously-corrected fields autonomously.

## Step 5 — Verify form_interaction_log saw the fields

```bash
sqlite3 data/form_interaction_log.db \
  "SELECT field_labels FROM page_structures WHERE domain='welovealfa.com' ORDER BY ts DESC LIMIT 1;"
```

Should include the visa/notice/distributed-engines questions.

## What the three plans changed

- **B** (vision-fallback-gate, commits 805702e/57d17a6/248bebb):
  `should_force_vision()` triggers when scanner returns ≤10 fields on a
  confident application_form page. `vision_augment_scan()` calls
  `gpt-4.1-mini` via the existing `vision_tier.py` pattern, returns
  fields tagged `vision_only=True`. Wired into `scan_fields`; reasoner
  hints stamped on the page in `_phase_act`.
- **C** (learned-widget-patterns, commits 7b81c14/a0ed467/d28d1ab/14f26df):
  `widget_patterns` table in `form_gotchas.db`. `_scan_learned_patterns`
  is now Strategy 0 in `STRATEGIES`. `confirm_application` captures DOM
  signatures from `final_mapping["<label>__dom"]` keys; those keys are
  populated by `_snapshot_live_form_state` on each page.
- **A** (semantic-first-scanner, commits 808353a/3b135f8/ee725f7/22e9767/1d39c42):
  `extract_visible_questions` + `match_question_to_widget` +
  `classify_widget` + `scan_semantic`. Wired as the `semantic` strategy
  in `_run_strategy`. `_fill_by_label` short-circuits to
  `page.locator(selector)` when the field carries
  `semantic_match=True`.

## Plan deviations from the original specs

- `GotchasDB` lives in `jobpulse.form_engine.gotchas` (the plan said
  `jobpulse.gotchas_db`). All imports + tests were adjusted.
- Vision LLM uses `get_openai_client().responses.create` (the existing
  `vision_tier.py` pattern), not `smart_llm_call`. The plan's call
  signature didn't match `smart_llm_call(llm, messages, **kwargs)`.
- Strategy registration extends `STRATEGIES = (...)` tuple + adds a
  case to `_run_strategy` switch. The plan assumed a list of (name,
  callable) tuples that doesn't exist.
- The class is `AIAssistLogger` (plan said `AiAssistLogger`).
- `final_mapping` carries `"<label>__dom"` keys with dict values; the
  type annotation is now `dict[str, Any]` for accuracy.
