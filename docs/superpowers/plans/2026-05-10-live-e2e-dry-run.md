# Goal: Perfect Live End-to-End Dry-Run on Anthropic Greenhouse

**Branch:** `pipeline-correctness-fixes`
**Recent commits to know about:**
- `e031f60` — Items 1-5 + 14 (form-fill correctness + DB observability)
- `3eedb67` — Items 6-13 + Kimi mandate (cache backlog)
- `82ef08a` — Item 8 measurement + Setup-2 acceptance

**Live URL of record:** `https://job-boards.greenhouse.io/anthropic/jobs/4017331008`

---

## The single goal

ONE full `apply_job(url, dry_run=True)` execution against the Anthropic
Greenhouse URL must complete such that **every** fix landed in this
plan is observed firing on real DOM, every learning chain writes real
rows to real DBs, and every error path is exercised — not synthetically.

If a single criterion below fails, you are not done. Re-run, fix the
root cause, re-run again. Confidence percent at end-of-task — anything
under 100% means the goal is not met.

---

## NON-NEGOTIABLE EXECUTION RULES

These apply to every item below. No exceptions.

1. **Live, no mocks.** Real Chrome with CDP. Real Anthropic Greenhouse
   URL. Real `data/*.db` files (don't redirect to `tmp_path` for the
   verification run). Real KimiAI key. `JOBPULSE_TEST_MODE` MUST NOT be
   set for the verification run.

2. **Read back the DOM, don't trust the log.** Every fill assertion
   uses `page.input_value(selector)` or
   `page.locator(...).text_content()`. Logs can lie (truncated values,
   intermediate states, retries). The fill is correct when the page
   state matches.

3. **OPRAL on every error** — Observe → Plan → Reason → Act → Learn.
   Don't suppress errors to make the run finish. Every error becomes
   a fix that improves the system, then a re-run that proves the fix.

4. **100% verified or not done.** State confidence percent in your
   final report. If you'd write anything below 100%, the goal is not
   met. Flaky paths are re-run until deterministic.

5. **Don't bypass agents.** Invoke `apply_job()` /
   `ApplicationOrchestrator` / `NativeFormFiller`. Do NOT write ad-hoc
   Playwright scripts that fill fields directly — that skips
   `CorrectionCapture`, `AgentRulesDB`, and the entire learning loop.
   Manual fills (only when an agent genuinely can't handle a field)
   MUST go through `ai_assist_logger.get_ai_assist_logger()` so the
   learning chain still fires.

6. **Real data + wiring verification = both required.** Fixing a fill
   bug doesn't count if the corresponding `db_observability` row, the
   `field_corrections` row, the `agent_rules` row, the optimization
   `correction` signal, and the `agent_performance` row aren't all
   observed after the run.

---

## Acceptance — every checkbox MUST be ticked at end of task

### Pipeline completion
- [ ] `apply_job("https://job-boards.greenhouse.io/anthropic/jobs/4017331008", dry_run=True)` runs to completion without raising
- [ ] `confirm_application()` is called on the dry-run result
- [ ] Final return dict has `status` ∈ {`completed`, `dry_run`} and `submitted=False`
- [ ] No `UnboundLocalError`, `FileUploadError`, or unhandled exceptions in the apply log
- [ ] `data/locks/apply.lock` correctly released on exit

### CV / CL generation
- [ ] CV PDF generated at `data/applications/Anthropic/Yash_Bishnoi_Anthropic.pdf` (NOT `Unknown_Company`)
- [ ] CV PDF title is human-readable in metadata (not a UUID)
- [ ] CL PDF generated for this URL (Greenhouse has 2-Attach pattern → CL slot exists)
- [ ] Second run of the same URL hits `tailored_cv_cache` (Item 6/7) — log shows "tailored_cv_cache: hit"
- [ ] Second run hits `cover_letter_cache` (Item 7) — log shows "cover_letter_cache: hit"
- [ ] Second run hits `cv_scrutiny_cache` (Item 10) — Gate 4 LLM call skipped on hit

### Form fill (Item 1, 2, 3 verification on real DOM)
- [ ] Visa-sponsorship Yes/No combobox fills with `"No"` on the Anthropic form. NEVER `"Norway"` (Item 1 listbox scoping verified live)
- [ ] Hispanic/Latino EEO question fills cleanly without an `ax_options` UnboundLocalError (Item 2 verified live)
- [ ] Resume upload lands in the FIRST Attach input (`files.length > 0` readback) (Item 3a)
- [ ] Cover Letter upload lands in the SECOND Attach input (Item 3b — Greenhouse 2-Attach pattern)
- [ ] If either upload fails on first try, retry-via-Attach-button fires; if still failing, `FileUploadError` raised + Telegram alert (Item 3a)
- [ ] Relocation question fills with `"Yes"` (not `"Yes, within the UK"`) (Item 5 cleanup + Item 4 embedding tier verified live)
- [ ] No `screening answer 'Yes, within the UK' did not align` warnings in the apply log

### Observability — `data/db_observability.db` after the run
- [ ] ≥100 rows total (synthetic baseline was 77; a real form fill should add 30+ more)
- [ ] At least one row per (db, table) for every wrapped accessor that's relevant to the apply path
- [ ] Every form-fill field has at least one row with `field_label` populated (mark_fill_outcome fired)
- [ ] `consumed` count > `dropped` count (most fills succeed cleanly)
- [ ] Any drops are tagged with a real reason (`option_misalignment`, `validation_failed`, `hit_returned_empty`) — never `unknown`
- [ ] Run `python -m scripts.db_observability_summary --window-days 1` — exit code 0 (no breach) OR if breach, the breach is investigated and fixed before declaring done

### Learning chains — every one of the 6 must write rows
After the dry-run completes (with `confirm_application`), confirm row
counts increased in each:

- [ ] `data/field_corrections.db` — `CorrectionCapture` recorded any agent↔user diffs
- [ ] `data/agent_rules.db` — `AgentRulesDB` row appended if any correction triggered a rule
- [ ] `data/trajectory.db` — `field_trajectories` rows for each filled field; `application_strategies` row for the run
- [ ] `data/experience_memory.db` — `strategy_reflector` wrote an entry
- [ ] `data/optimization.db.signals` — at least one signal emitted (`correction`, `failure`, or `success`) tied to this session
- [ ] `data/agent_performance.db` — one row for this application
- [ ] `data/form_experience.db` — `form_experience` row + `field_label_mappings` rows + `fill_techniques` rows for greenhouse
- [ ] `data/db_observability.db` — verified above

### Learning chain — second run improves
- [ ] Re-run apply_job on the same URL. Verify `form_experience.db.lookup` returns the prior session's data
- [ ] Verify `field_label_mappings` get_field_mappings() returns the learned mappings (no LLM cost on known fields)
- [ ] Verify cache-hit count incremented for `tailored_cv_cache`, `cover_letter_cache`, `cv_scrutiny_cache`

### Notion + Drive
- [ ] Notion application page created/updated for "Anthropic / Research Engineer, Knowledge Team"
- [ ] All Notion fields populated (no blanks): Status, Match Tier, Resume URL, CL URL, Applied Date, Platform, Location, ATS Score, Matched Projects
- [ ] Drive upload returns shareable URLs for both CV and CL PDFs
- [ ] Drive URLs are valid (HTTP 200 on HEAD, accessible without auth)

### Anti-detection / liveness
- [ ] Browser stays headed throughout the run (you can see the human-watching contract)
- [ ] No verification wall hit during the dry run; if one is hit, the 6-stage bypass fires through to human-Telegram fallback
- [ ] Rate limiter recorded the application BEFORE submit (mutex + record-first-then-submit)

### KimiAI mandate (verified live)
- [ ] Every LLM call in the apply log routes to `api.moonshot.ai`
- [ ] No call to `api.openai.com` (search apply log for the host)
- [ ] Cost tracker attribution is correct: `kimi-k2.6` for reasoning domains, `moonshot-v1-auto` for content domains
- [ ] Total LLM cost for one apply documented (probably <$0.10)

### Pre-existing tests no longer regress
- [ ] The 5 pre-existing test failures are diagnosed: each one is either fixed, marked `xfail` with a written reason, or root-caused to a pre-existing modification on the branch (then the modification's owner is responsible)
- [ ] After diagnosis, full test suite runs cleanly OR every remaining failure is documented with reason

---

## Pre-flight checklist (run before starting the live test)

```bash
# 1. Verify branch and uncommitted state
git status --short
git log --oneline -5

# 2. Verify Kimi key is set (mandatory)
[ -n "$KimiAI_API_KEY" ] || echo "FAIL: KimiAI_API_KEY not set"

# 3. Reset observability DB so the run starts clean
sqlite3 data/db_observability.db "DELETE FROM lookups"

# 4. Snapshot baseline row counts so we can diff after the run
for db in field_corrections agent_rules trajectory experience_memory optimization agent_performance form_experience db_observability; do
    rows=$(sqlite3 "data/${db}.db" "SELECT COUNT(*) FROM (SELECT * FROM sqlite_master WHERE type='table');" 2>/dev/null)
    echo "${db}: ${rows} tables"
done > /tmp/baseline_rows.txt

# 5. Launch Chrome with CDP for Playwright
python -m jobpulse.runner chrome-pw   # leaves Chrome running on port 9222

# 6. Confirm test mode is OFF for this run
unset JOBPULSE_TEST_MODE
```

---

## Execution sequence

### Phase 1 — Diagnose what we already have

The 2026-05-09 plan landed Items 1-14 with synthetic verification only.
This phase confirms each fix is real on the wire.

1. Read `e031f60`, `3eedb67`, `82ef08a` commit messages to absorb what's
   already done.
2. Run the comprehensive test sweep (the same 24 test files committed
   in this plan's commits) — must show 246+ pass.
3. Diagnose the 5 pre-existing test failures (`test_diversity_keyword_fallback`,
   `test_high_overlap`, `test_hero_project_has_all_archetypes`,
   `test_known_command_not_unknown[gmail]`,
   `test_scan_reed_records_success_on_200`):
   - For each: is the failure pre-session (verified yes earlier) or did
     this session's work introduce it? (`git stash` / restore to verify)
   - For genuine session-introduced failures: fix.
   - For pre-existing: either fix or write the rationale and mark `xfail`.

### Phase 2 — First live dry-run (no expectations of success)

4. Run `apply_job(URL, dry_run=True)` via the runner CLI. Either:
   ```bash
   python -m jobpulse.runner job-process-url \
     "https://job-boards.greenhouse.io/anthropic/jobs/4017331008" generic
   ```
   …or, if `process_single_url` ends at `queued_for_review` without
   reaching form fill (it does), use the `apply_live_with_review.py`
   script in `scripts/` which calls `apply_job` directly with `dry_run=True`.
5. Observe each step against `.claude/rules/jobs.md` "Live Visibility"
   section: Pre-Screen, CV/CL, Form Fill, Dry Run review, learning fire.
6. Capture every error. For each error, do OPRAL: Observe →
   Plan (trace via MCP `find_symbol` / `callers_of`) → Reason → Act →
   re-run → Learn (verify the fix landed in the right DB).

### Phase 3 — Drive every acceptance checkbox to ✅

7. For each unchecked box in the acceptance list above:
   - Read the corresponding code path
   - Diagnose why it's not firing
   - Fix the root cause (no symptoms-suppressing patches)
   - Re-run apply_job — confirm the box ticks
8. Don't aggregate fixes — fix one, re-run, observe one box tick, then
   move to the next. Anything that requires multiple boxes to tick
   simultaneously is a sign the fixes are coupled and should be
   redesigned.

### Phase 4 — Second-run verification (proves caches + learning persist)

9. Without changing anything, run apply_job a second time on the same
   URL. The cache-hit and learning-replay boxes get verified here:
   - `tailored_cv_cache`, `cover_letter_cache`, `cv_scrutiny_cache`,
     `portfolio_variant_cache`, `screening_decomposition_cache`,
     `vision_classification_cache`, `page_reasoning_cache` — all show
     hits in log
   - `form_experience_db.lookup` returns the prior session's data
   - `field_label_mappings.get` returns learned mappings
   - LLM cost on second run is at least 50% lower than first run

### Phase 5 — Daily summary + OPRAL signal

10. Run `python -m scripts.db_observability_summary --window-days 1`
    on the post-run observability DB.
11. If any (db, table) breaches the 50% drop threshold: that's a real
    bug. Diagnose, fix, re-run apply, verify the breach is gone.
12. Verify the OPRAL `failure` signal fired with the breach payload and
    the templated investigation entry was appended to `.claude/mistakes.md`.

### Phase 6 — Declare done

13. Final report covers:
    - All acceptance boxes ticked, with the specific log line / DB row /
      DOM readback that proves each
    - Final test sweep: 246+ passing, ANY failures explained
    - Confidence percent: 100% or restart Phase 3
    - Total LLM cost for the two-run sequence
    - Outstanding items pushed to a follow-up plan (must be NONE for
      the goal to be considered met)

---

## Things explicitly OUT OF scope

- **Item 15** (Firecrawl spike) — closed by user decision 2026-05-10
- **Item 8 cache decision** — measurement scaffold landed; calendar
  wait until 2026-05-17 for 7 days of cron data
- **30+ pre-existing modifications on the branch** — leave them
  uncommitted unless they actively block the goal. Their authorship is
  pre-session.
- **pipeline-bugs.md S6/S10/S12** — not blocking the dry-run goal
- **Real submit** — `dry_run=True` only. NEVER set
  `JOB_AUTOPILOT_AUTO_SUBMIT=true` for this verification.

---

## What "perfect" means

The system is **prepared for production autonomous job application**
when it can run dry through this URL three times in a row, each run
producing better outcomes than the last (caches hit, mappings learned,
fewer LLM calls), with zero unhandled exceptions, complete observability
coverage, and every learning chain firing without intervention. That is
the goal. Anything less is not done.

When you finish: produce a short report
(`docs/audits/live-e2e-2026-05-10.md`) with the three apply logs side
by side, the cache-hit deltas, the learning-chain row deltas, and the
final acceptance checklist with every box ticked.
