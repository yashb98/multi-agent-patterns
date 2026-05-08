# Pipeline-Bugs Session Runner — Full Protocol

> Invoked by `/fix-pipeline-bugs` (see `.claude/commands/fix-pipeline-bugs.md`).
> **Session must be launched with `--dangerously-skip-permissions`.**

This file is the durable, copy-paste-ready prompt that drives one session of
the 18-session plan. It is designed so a fresh Claude session — with no prior
context — can read this file, detect what's already been done, pick up the
next session, and ship it with a regression test + live evidence.

---

## Step 0 — Prerequisites (one-time per machine)

Before the first invocation, confirm:

- [ ] Chrome is running with CDP at port 9222: `python -m jobpulse.runner chrome-pw`
- [ ] `.env` has `JOB_AUTOPILOT_AUTO_SUBMIT=false`, `JOBPULSE_FAST_FILL=true`,
      `OPENAI_API_KEY`, `NOTION_API_KEY`, optional `NEO4J_PASSWORD`.
- [ ] Tests pass on baseline: `python -m pytest tests/jobpulse/ tests/shared/ -q --ignore=tests/jobpulse/integration`
      (4 pre-existing failures from earlier audits are acceptable; document
      them in your end-of-session report. New failures = revert.)

---

## Step 1 — State detection (run every invocation)

```bash
# Last completed pipeline-bugs session (returns 0 if none yet)
LAST_DONE=$(git log --oneline | grep -oE 'fix\(pipeline-bugs-S[0-9]+\)' \
  | grep -oE '[0-9]+' | sort -n | tail -1)
NEXT_SESSION=$((${LAST_DONE:-0} + 1))
echo "Next session: S${NEXT_SESSION}"
```

If `NEXT_SESSION > 18`, all sessions are complete — run the **post-completion
verification** at the end of this file and exit.

Otherwise, look up `S${NEXT_SESSION}` in the table below and follow the
**per-session protocol** (Step 2 onward).

---

## The 18-session plan

| # | Name | Items it kills | Needs live URL? | Source DB | Acceptance test | Commit prefix |
|---|---|---|---|---|---|---|
| 1 | Architecture-doc batch | 20 contract lies (Section 6 of `pipeline-bugs.md`) | No | — | A new test under `tests/lint/` greps `CLAUDE.md` for the 20 known false claims; passes when none remain | `fix(pipeline-bugs-S1): doc batch` |
| 2 | Lint rules | Bug-class recurrence prevention | No | — | `ruff` config + 3 new tests under `tests/lint/` (no-bare-except-pass, no-regex-for-classification, no-write-only-flag) | `fix(pipeline-bugs-S2): lint rules` |
| 3 | Test-DB isolation conftest | S6 T-1, S10 T-10.1 (~50% test-leak rows in `cognitive_outcomes`) | No | — | `tests/conftest.py` autouse fixture monkeypatches `get_optimization_engine` to tmp DB; assertion that `data/optimization.db` has zero `agent_name='test_agent'` rows added during the test run | `fix(pipeline-bugs-S3): test isolation` |
| 4 | Dead-code deletion | All `💀` rows in `pipeline-bugs.md` Section 3 | No | — | `python -m pytest tests/ -q` green; `find_symbol` MCP confirms each deleted symbol has no remaining caller | `fix(pipeline-bugs-S4): dead code` |
| 5 | Wire-or-delete decisions | Section 4 wiring gaps with no consumer | No (presents to user) | — | Issue table in `pipeline-bugs.md`: each item marked WIRE / DELETE / KEEP with rationale; user signs off via AskUserQuestion | `fix(pipeline-bugs-S5): wire decisions` |
| 6 | AutonomousLinker wiring | S11 M-11.A (Neo4j zero edges) | Yes (synthetic) | `data/agent_memory/memories.db` | After `MemoryManager.store_memory(...)` × 5, Neo4j edge count > 0 (verify via `_neo4j_store.count_edges`) | `fix(pipeline-bugs-S6): linker` |
| 7 | SemanticMemory eviction + JSON↔SQLite read unification | S11 M-11.B, M-11.C | No | — | `SemanticMemory.learn` evicts to `max_facts=500`; `get_procedural_entries` returns SQLite count not JSON cap | `fix(pipeline-bugs-S7): memory reads` |
| 8 | Cognitive cost + flush + emit-adaptation | S6 M-D, M-E, W-1 | No | — | After L0→L1 escalation, `cognitive_outcomes.escalated=1` AND `optimization_engine.signals` has matching `signal_type='adaptation'` row | `fix(pipeline-bugs-S8): cognitive` |
| 9 | Scan-loop block-event wiring | S9 M-9.B (indeed), M-9.C (reed), M-9.D (handle_block) | **Yes (live)** | `data/applications.db` | Run `scan_indeed` + `scan_reed` against a known-good and a known-blocked URL each; assert `scan_learning.db` has matching success/block rows | `fix(pipeline-bugs-S9): scan_loop` |
| 10 | FormFillEngine wire-or-delete | S12 D-12.2 + S2 cascading | **Yes (live)** | `data/form_experience.db` | DECISION: ship with `UNIFIED_FORM_ENGINE=true` defaulted, OR delete `form_engine/engine.py` + B-tier methods. Whichever path: live dry-run on 1 Greenhouse + 1 Workday URL passes | `fix(pipeline-bugs-S10): form engine` |
| 11 | Verification readback sweep | S1 M-1.a, M-1.b, S12 W-12.1 | **Yes (live)** | `data/form_experience.db` | DOM readback added to `select_option` and `list_button_radio`; `_cv_pre_uploaded` flag now read by `file_uploader`; live dry-run on Workday + SmartRecruiters proves: zero ghost-success, single CV upload | `fix(pipeline-bugs-S11): readback` |
| 12 | Regex purge — screening | S4 B-6, B-8, S1 M-3, M-4 | **Yes (live)** | `data/screening_cache.db` | Replace each regex classification with embedding/semantic match; run on 50 real cached screening Q+A pairs from `screening_cache`; ≥ 95% intent agreement with old answers | `fix(pipeline-bugs-S12): regex screening` |
| 13 | Regex purge — form scanning | S2 M-B, M-C, M-D | **Yes (live)** | `data/form_experience.db` | `field_scanner` + `semantic_scanner` regex blocks replaced with learned-pattern + LLM tier; live dry-run on Greenhouse + Workday matches pre-fix field counts | `fix(pipeline-bugs-S13): regex scanner` |
| 14 | Regex purge — navigator | S3 M-C (cookie consent), gmail_verify, smaller spots | **Yes (live)** | `data/navigation_learner.db` | Replace navigator literal-tuple matchers with semantic detection; live dry-run on a fresh URL where cookie banner is non-English | `fix(pipeline-bugs-S14): regex navigator` |
| 15 | Materials N+1 cleanup | S8 M-F, M-G, M-H, M-I | No | — | `score_repo` + `score_ats` use precomputed reverse-lookup synonyms map; benchmark before/after shows ≥5× speedup; `tailor_summary_and_tagline` validates tagline | `fix(pipeline-bugs-S15): materials` |
| 16 | Navigator follow-ups | S3 M-A, M-B, M-E | **Yes (live)** | `data/applications.db` | `_scrape_direct_url` uses `asyncio.to_thread`; `verify_submission` either deleted or wired; reflection path runs `_apply_field_count_guard`; live dry-run on Indeed→ATS bypass URL | `fix(pipeline-bugs-S16): navigator` |
| 17 | Import-time + log-promotion sweep | S5 M-5.1, S10 M-10.A/B/C, residual `except: pass` | No | — | `process_logger` no longer runs `init_process_db()` at import; ~10 `logger.debug` swallows promoted to `warning`; lint rule from S2 catches new ones | `fix(pipeline-bugs-S17): log sweep` |
| 18 | Final reconciliation | All remaining; verification | **Yes (live)** | `data/applications.db` | Run full pipeline (`job-process-url`) on a fresh LinkedIn + Greenhouse URL; verify post_apply chain fires (CorrectionCapture, AgentRulesDB, strategy_reflector, OptimizationEngine, AgentPerformanceDB, Notion); `pipeline-bugs.md` has every row marked ✅ FIXED, ⏸ DEFERRED+reason, or 🚫 WONTFIX+reason | `fix(pipeline-bugs-S18): final` |

---

## Step 2 — Per-session protocol

For each session, follow this exact sequence. Skip live-reproducer steps if
the table says "No" for that session.

### 2.1 Read the catalog rows you're killing

```
Read docs/audits/pipeline-bugs.md
Read docs/audits/audit-followup-worklist.md (the relevant subsystem section)
Read each audit-<subsystem>.md doc that contains items the session targets
```

For each item ID (e.g. `S11 M-11.A`):
- Note the file:line.
- Identify the smallest reproducer (existing test that should fail post-fix /
  log line that should change / DB state that should differ).

### 2.2 Live ATS reproducer (when needed)

Use this protocol when the session table says "Yes (live)".

#### Pull a live URL from production DBs

For each platform you need, run the matching query:

```bash
# Indeed (S9, S16)
sqlite3 data/applications.db "
  SELECT j.url, j.company, j.title, a.status
  FROM job_listings j JOIN applications a ON a.job_id = j.job_id
  WHERE j.platform = 'indeed' AND a.status IN ('Applied', 'Found')
  ORDER BY j.found_at DESC LIMIT 3"

# Reed (S9)
sqlite3 data/applications.db "
  SELECT j.url, j.company, j.title FROM job_listings j
  WHERE j.platform = 'reed' ORDER BY j.found_at DESC LIMIT 3"

# LinkedIn (S18)
sqlite3 data/applications.db "
  SELECT j.url, j.company, j.title FROM job_listings j
  WHERE j.platform = 'linkedin' AND j.url LIKE '%/jobs/view/%'
  ORDER BY j.found_at DESC LIMIT 3"

# Greenhouse / Lever / Workday / Ashby / iCIMS / SmartRecruiters
# These flow into form_experience.db after first apply
sqlite3 data/form_experience.db "
  SELECT domain, apply_count, last_success_at
  FROM form_experience WHERE domain LIKE '%greenhouse%'
     OR domain LIKE '%lever%' OR domain LIKE '%workday%'
     OR domain LIKE '%ashbyhq%' OR domain LIKE '%icims%'
     OR domain LIKE '%smartrecruiters%'
  ORDER BY last_success_at DESC LIMIT 10"

# Then for a chosen domain, find a real URL from applications.db:
sqlite3 data/applications.db "
  SELECT j.url, j.company, j.title FROM job_listings j
  WHERE j.url LIKE '%<domain>%' ORDER BY j.found_at DESC LIMIT 1"

# Screening cache (S12) — real Q+A pairs to validate regex-purge outcomes
sqlite3 data/screening_cache.db "
  SELECT field_label, answer_text, generation_method, generated_at
  FROM screening_cache ORDER BY generated_at DESC LIMIT 50" \
  > /tmp/screening_baseline.tsv
```

#### Run the dry-run

```bash
# Pre-conditions: Chrome with CDP must be running.
# JOB_AUTOPILOT_AUTO_SUBMIT=false guarantees no real submission.
JOB_AUTOPILOT_AUTO_SUBMIT=false JOBPULSE_FAST_FILL=true \
  python -m jobpulse.runner job-process-url "<url>" 2>&1 | tee /tmp/pb-S<n>-live.log
```

If the URL is gated by SSO or captcha, the agents handle it via the
6-stage bypass + Telegram fallback (`.claude/rules/jobs.md`). **Do not write
ad-hoc Playwright scripts** — the agents must run the real path so
CorrectionCapture / strategy_reflector fire. If the bypass fails, stop the
session and ask the user.

#### Capture evidence

Quote the specific log lines / DB rows that prove the fix worked:

```bash
# Example for S11 (verification readback): the new log line should appear
grep -E "verified=true|ghost_click=false" /tmp/pb-S11-live.log

# Example for S6 (linker): verify Neo4j now has edges
python -c "from shared.memory_layer import get_shared_memory_manager; \
  m = get_shared_memory_manager(); \
  print('edges:', m._neo4j.count_edges() if m._neo4j else 'no neo4j')"

# Example for S9 (scan_loop): confirm scan_learning got a row
sqlite3 data/scan_learning.db "
  SELECT platform, event_type, COUNT(*) FROM scan_events
  WHERE recorded_at > datetime('now', '-1 hour')
  GROUP BY platform, event_type"
```

### 2.3 Implement the fix

- **Smallest fix that closes the bug pattern.** Don't refactor surrounding code.
- **For multi-instance bugs (e.g. all `except: pass` swallows in S17), use the
  lint rule from S2 as the regression check** — once the lint rule passes
  repo-wide, the class is closed.
- **For wired-but-unconsumed code (S5)**, the fix is one of:
  WIRE (build the missing consumer) / DELETE (remove the orphan)
  / KEEP+DOCUMENT (intentional reserved hook). Pick one per item.

### 2.4 Add a regression test that fails on the bug pattern, not the instance

The bug *pattern* test is what makes "won't break again" real. Examples:

| Bug class | Pattern-test shape |
|---|---|
| Silent `AttributeError` swallow | Lint rule in `tests/lint/test_no_silent_attribute_error.py`: greps for `except (AttributeError\|Exception)` followed by `logger\.(debug\|warning)` followed by `return (None\|\{\}\|\[\])` |
| Regex-for-classification (S2 lint) | `tests/lint/test_no_classification_regex.py`: greps for `re\.(match\|search\|compile)` in files outside the §8 allowlist (sanitization / format validation) |
| Write-only flag | `tests/lint/test_no_write_only_flag.py`: parses Python AST in `jobpulse/`, finds dict assignments to `_-prefixed` keys, asserts the same key has at least one read |
| FE DB silent swallow | `tests/lint/test_fe_db_failures_logged.py`: AST-walk `learned_strategy.py` + `_strategy_synthesis.py` confirming every `except` around `_get_fe_db()` body has a `logger.warning` call |
| CLAUDE.md drift | `tests/lint/test_claude_md_truth.py`: greps `CLAUDE.md` for the 20 known false claims; passes when none remain |
| Producer-without-consumer signal | `tests/wiring/test_signal_consumers.py`: for each `OptimizationEngine.emit(signal_type=X)` callsite, assert `_aggregator._handle_<X>` exists |

Place these under `tests/lint/` (string/AST checks, no fixtures needed) or
`tests/wiring/` (DB-state assertions, use `tmp_path`). Add to the test sweep
in S18.

### 2.5 Run tests + live verification

```bash
# Targeted: the new regression test
python -m pytest <new_test_file> -vv

# Wider: same scope + a sweep so we don't introduce regressions elsewhere
python -m pytest tests/jobpulse/ tests/shared/ -q --ignore=tests/jobpulse/integration

# If session needed live URL: run the live dry-run command from 2.2
```

If the wider sweep introduces > 1 new failure, **stop and revert**. If it
introduces exactly 1 new failure, verify it's pre-existing via
`git stash; pytest <file>; git stash pop`.

### 2.6 Mark progress in `pipeline-bugs.md`

For every catalog row this session closed, edit `docs/audits/pipeline-bugs.md`:

```markdown
| 🔴 M-11.A | `_manager.py:151` | … | Linker not invoked → Neo4j zero edges |
↓
| ✅ S6 a1b2c3d | `_manager.py:151` | … | Linker not invoked → Neo4j zero edges |
```

(`a1b2c3d` is the commit hash you'll get from step 2.7. You can either edit
this in pre-commit and amend after, or commit twice — the audit doc edit
can land in the same commit as the code.)

### 2.7 Commit

```bash
git add <fix files> <test files> docs/audits/pipeline-bugs.md
git commit -m "$(cat <<EOF
fix(pipeline-bugs-S<n>): <session-name>

Closes from \`docs/audits/pipeline-bugs.md\`:
- <ID> <one-line summary>
- <ID> <one-line summary>

Pattern-test: <test path>
Live evidence: <url> dry-run produced <evidence>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

The `fix(pipeline-bugs-S<n>):` prefix is the **state marker** — Step 1 of the
next invocation parses it.

---

## Step 3 — Post-session checklist

Before declaring the session done:

- [ ] Every targeted catalog row in `pipeline-bugs.md` is marked ✅ or has a
      written reason for not closing (then promoted to ⏸ DEFERRED with a
      note in the row, not silently skipped).
- [ ] Pattern-test exists and passes.
- [ ] Wider regression sweep introduced 0 new failures.
- [ ] Live evidence (when applicable) is quoted in the commit message.
- [ ] **Call advisor()** — they see the full transcript and have caught
      silent-edit and scope-drift issues in earlier audits. Do this BEFORE
      the commit if possible; after the commit if you forgot.

---

## Step 4 — Stop conditions

Stop the session and ask the user when:

- Live reproducer requires a real account login that needs Telegram captcha
  resolution that's flaky → ask which alternative URL to try.
- Fix touches > 2 subsystems → split into multiple sessions.
- Pattern-test you write disagrees with > 5% of existing data (e.g. S12
  embedding agreement < 95%) → calibration issue, ask user.
- > 1 new test failure in the wider sweep → revert, advisor, ask.
- A "wired-but-unconsumed" item could be deleted OR built out — that's a
  product decision, ask via `AskUserQuestion`.

Do **not** auto-decide product questions. Do **not** ship a fix without a
regression test. Do **not** commit partial work.

---

## Step 5 — Post-completion verification (after S18)

When `LAST_DONE >= 18`, run this once:

```bash
# 1. Every row in pipeline-bugs.md is closed or explicitly deferred
python -c "
import re, sys
text = open('docs/audits/pipeline-bugs.md').read()
open_rows = re.findall(r'^\| 🔴', text, re.M)
if open_rows:
    print(f'Still open: {len(open_rows)} rows'); sys.exit(1)
print('✓ all rows closed or deferred')"

# 2. Lint rules from S2 pass repo-wide
python -m pytest tests/lint/ -v

# 3. Wider sweep clean
python -m pytest tests/jobpulse/ tests/shared/ -q --ignore=tests/jobpulse/integration

# 4. End-to-end pipeline run on a fresh URL
JOB_AUTOPILOT_AUTO_SUBMIT=false python -m jobpulse.runner job-apply-next 1
```

Then write `docs/audits/pipeline-bugs-completion-report.md` with:
- Total rows closed / deferred / wontfix
- Total regression tests added
- Live evidence summary
- Cross-subsystem theme status (the 7 themes from `pipeline-bugs.md`)
- Any new bug classes surfaced during the 18-session run

---

## Notes for safety

- **Never** run `JOB_AUTOPILOT_AUTO_SUBMIT=true` in a session.
- **Never** call `confirm_application()` without explicit user instruction.
- **Never** force-push or rebase commits across sessions — each
  `fix(pipeline-bugs-S<n>):` commit is the durable progress marker.
- **Never** modify `pipeline-bugs.md` to remove a row — only convert
  `🔴` → `✅`/`⏸`/`🚫` with the audit ID + commit hash + reason preserved.
- **Never** delete `data/*.db` rows during the audit. The audit-prompt rule
  "Tests NEVER touch data/*.db" applies here too — use `tmp_path` for any
  DB writes the regression tests perform.
- The `--dangerously-skip-permissions` flag means tool calls don't prompt;
  it does **not** mean "skip safety checks." The OPRAL loop and
  Live-Pipeline-Observation rules in `CLAUDE.md` still apply.
