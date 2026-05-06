# Line-by-Line Apply-Pipeline Audit — Reusable Prompt

> Self-contained brief for a fresh-context agent (or human reviewer) to
> deliver a **per-function, per-line audit** of the URL-to-submit
> pipeline. Designed to be executed across multiple focused sessions —
> one subsystem per session — because the full audit cannot complete
> in a single session.

---

## The Prompt

> *(Paste this verbatim into a fresh-context Claude Code session, one
> subsystem at a time.)*

```
You are a senior AI engineer at Anthropic doing a brutal-scrutiny audit
of a job-application automation pipeline. The codebase is at
/Users/yashbishnoi/projects/multi_agent_patterns (branch
pipeline-correctness-fixes). Architecture overview:
docs/job-application-pipeline.md.

YOUR TARGET FOR THIS SESSION: <SUBSYSTEM>

(Pick exactly one of: scan_loop / pre_screen / materials / navigation /
form_fill_dispatch / form_fill_widgets / screening_pipeline /
post_apply / cognitive_engine / optimization_engine / memory_layer /
ats_adapters. Do not pick "everything" — that produces theatrical
output instead of fixes.)

DELIVERABLES (in this order):

1. CALL-GRAPH EXTRACTION
   For every public function and method in your target subsystem,
   write to /tmp/audit-<subsystem>.md a row containing:
     - file:line
     - function name + signature
     - direct callers (file:line of each call site)
     - direct callees (file:line of each call to another module)
     - "called by apply_job?" (yes / no / lazy-via-X / never)
   Find call sites with: rg "func_name\(" --type py
   Find callees with: ast.parse + walk Call nodes
   Don't trust grep alone — also walk the AST in case of dynamic
   dispatch (getattr, __import__).

2. WIRING STATUS PER FUNCTION
   Categorize each function as:
     A — runtime: definitely called during apply_job() in production
     B — runtime-conditional: called only if env flag X is set
     C — runtime-unreachable from apply_job (only called by tests,
         CLI, or other agents)
     D — orphan: imported nowhere; truly dead
     E — overridden / shadowed: defined here but a wrapper or
         monkey-patch replaces it at runtime
   Use evidence: import grep + AST + trace-on-live-run if feasible.
   Note explicitly when you cannot determine reachability without
   running the live pipeline (most legitimate "I don't know" cases).

3. INTERNAL LINE-BY-LINE READ
   For every function in category A or B, read the body line by line.
   Look for:
     - except: pass that swallows real errors silently
     - log.debug() in failure paths that should be log.warning
     - early returns that mask failures upstream
     - missing await on async calls (returns Coroutine, never runs)
     - unused parameters (the function lies about its contract)
     - branches that can never be reached given upstream invariants
     - resource leaks (file handles, browser pages, DB connections
       not in try/finally)
     - hardcoded strings/regexes for classification (forbidden by
       .claude/rules/seven-principles.md §8)
     - signal emissions to OptimizationEngine / MemoryManager that
       have no consumer (write-only telemetry channels)
     - DB writes whose corresponding read path doesn't exist
   For each finding, write:
     <file>:<line> [<severity>] <description>
     Severity: blocker | major | minor | nit
     One line per finding.

4. CROSS-MODULE WIRING
   For every signal/event/db-row your target subsystem emits or
   consumes, identify:
     - the producer (file:line)
     - the consumer (file:line)
     - the schema/contract (which fields, what types)
     - whether producer and consumer agree on schema (read both)
   Common bug pattern: signal_type='correction' emitted with payload
   {field, old_value, new_value} but consumer expects
   {label, agent, user}. Find these.

5. LIVE EVIDENCE
   Where possible, run the actual code path:
     - For form_fill_dispatch: run pytest on test_native_form_filler*
       with -vv, observe what's actually called.
     - For navigation: run a single Revolut application end-to-end
       with `JOB_AUTOPILOT_AUTO_SUBMIT=false python -m jobpulse.runner
       job-apply-next 1` (Chrome must be running with CDP at 9222),
       capture the log, grep for your target subsystem's function names,
       confirm each runtime-A function logged something.
     - For screening_pipeline: feed a known-good question through
       screening_pipeline.resolve() and verify each tier was consulted.
   Attach the captured evidence to your audit doc. NO claim is
   accepted without log evidence or a reproducible test.

6. FIXES
   For every blocker or major finding:
     - State the fix in one sentence
     - Implement the fix
     - Write a test that would have caught it
     - Run the test
     - Commit with message "fix(<subsystem>): <brief>"
   For minor/nit: list them but don't fix unless a blocker fix
   touches the same function.

7. UPDATE THE DOC
   After fixes ship, update docs/job-application-pipeline.md with
   anything you discovered that wasn't there. The doc should
   describe what the code DOES, not what someone intended.

STOP CONDITIONS — call advisor() and stop when:
   - You've spent more than 2 hours on this subsystem
   - You've found > 5 blockers (ship those, defer the rest to a
     follow-up session)
   - The live evidence step requires changes to the live pipeline
     you can't safely make in this session
   - You can't determine wiring status for > 30% of functions without
     a long-running tracing setup

DO NOT:
   - Audit more than the assigned subsystem in one session
   - Claim "verified" without log evidence or a passing test
   - Fix minor/nit findings that don't share a function with a major
     fix (they balloon scope)
   - Trust the existing doc — read it but cross-check every claim
   - Fabricate function names that "look like they should exist" —
     only audit functions that actually grep up

EVIDENCE OF QUALITY:
   - Each blocker has a reproducible test
   - Each fix has a passing CI run after
   - Your audit doc has line numbers for every claim
   - You called advisor at least once before declaring done
```

---

## Subsystem definitions

When pasting the prompt, replace `<SUBSYSTEM>` with one of these
exact target lists:

### `scan_loop`
- Files: `jobpulse/job_scanner.py`, `jobpulse/job_scanners/{linkedin,
  indeed, reed}.py`, `jobpulse/liveness_checker.py`,
  `jobpulse/job_deduplicator.py`, `jobpulse/job_notion_sync.py`
- Entry: cron job running `python -m jobpulse.runner job-scan`
- Output: rows added to `applications.db` with `status='Found'` +
  Notion Job Tracker pages.

### `pre_screen`
- Files: `jobpulse/scan_pipeline.py:prescreen_listings`,
  `jobpulse/recruiter_screen.py`, `jobpulse/skill_graph_store.py`,
  `jobpulse/skill_extractor.py`, `jobpulse/gate4_quality.py`,
  `jobpulse/company_blocklist.py`, `jobpulse/jd_analyzer.py`,
  `jobpulse/skill_gap_tracker.py`,
  `jobpulse/cv_templates/scrutiny_calibrator.py`
- Entry: `apply_job(url, ...)` → first 5 gates.
- Output: pass/reject per gate, JDAnalysis dict, MatchTier.

### `materials`
- Files: `jobpulse/scan_pipeline.py:generate_materials`,
  `jobpulse/application_materials.py`, `jobpulse/cv_tailor.py`,
  `jobpulse/archetype_engine.py`, `jobpulse/portfolio_variants.py`,
  `jobpulse/project_portfolio.py`, `jobpulse/github_matcher.py`,
  `jobpulse/cv_templates/generate_cv.py`,
  `jobpulse/cv_templates/generate_cover_letter.py`,
  `jobpulse/ats_scorer.py`
- Entry: post-pre-screen.
- Output: cv_path, optional cover_letter_path, agent_mapping.

### `navigation`
- Files: `jobpulse/application_orchestrator_pkg/__init__.py`,
  `_navigator.py`, `_form_filler.py`, `_executor.py`, `_auth.py`,
  `jobpulse/cookie_dismisser.py`, `jobpulse/sso_handler.py`,
  `jobpulse/sso_auto_discovery.py`, `jobpulse/account_manager.py`,
  `jobpulse/gmail_verify.py`, `jobpulse/navigation_learner.py`,
  `jobpulse/navigation/{action_executor, overlay_dismisser,
  wait_conditions}.py`, `jobpulse/page_analysis/`,
  `jobpulse/page_analyzer.py`, `jobpulse/verification_detector.py`,
  `jobpulse/platform_bypass.py`, `jobpulse/playwright_driver.py`,
  `jobpulse/playwright_adapter.py`, `jobpulse/browser_intelligence.py`
- Entry: `ApplicationOrchestrator.execute_application` → 3-phase loop.
- Output: nav_result with snapshot + planned_action.

### `form_fill_dispatch`
- Files: `jobpulse/native_form_filler.py` (just this file, ~3500
  lines — enough for one session)
- Entry: `NativeFormFiller.fill(...)` from `_form_filler`.
- Output: fill_result with agent_mapping, screening_results,
  per_page_snapshots.

### `form_fill_widgets`
- Files: `jobpulse/form_engine/{field_scanner, semantic_scanner,
  vision_gate, gotchas, validation, intent_healing, semantic_matcher,
  detector, widget_detector, widget_strategies, confidence_scorer,
  consent_policy, models}.py` + every `*_filler.py`
- Entry: called by NativeFormFiller in form_fill_dispatch.
- Output: per-widget fill results.

### `screening_pipeline`
- Files: `jobpulse/screening_pipeline.py` + 7 sister modules
  (`screening_decomposer.py`, `screening_detector.py`,
  `screening_intent.py`, `screening_option_aligner.py`,
  `screening_pattern_extractor.py`, `screening_semantic_cache.py`,
  `screening_validator.py`, `screening_outcome_recorder.py`,
  `screening_feedback_loop.py`, `screening_answers.py`)
- Entry: `field_mapper.build_mapping` → screening pipeline for
  unknown fields.
- Output: answer string per question.

### `post_apply`
- Files: `jobpulse/post_apply_hook.py`, `jobpulse/correction_capture.py`,
  `jobpulse/agent_rules.py`, `jobpulse/auto_rule_generator.py`,
  `jobpulse/strategy_reflector.py`, `jobpulse/trajectory_store.py`,
  `jobpulse/agent_performance.py`, `jobpulse/drive_uploader.py`,
  `jobpulse/form_experience_db.py`, `jobpulse/platform_transfer.py`,
  `jobpulse/cross_platform_field_transfer.py`,
  `jobpulse/rejection_analyzer.py`, `jobpulse/browser_cleanup.py`,
  `jobpulse/rate_limiter.py`, `jobpulse/pre_submit_gate.py`,
  `jobpulse/pipeline_hooks.py`, `jobpulse/process_logger.py`
- Entry: `confirm_application(...)` → `post_apply_hook(...)`.
- Output: applications.db status=Applied, learning chains fired.

### `cognitive_engine`
- Files: `shared/cognitive/{__init__, _engine, _classifier, _budget,
  _strategy, _reflexion, _tree_of_thought, _prompts}.py`
- Entry: `CognitiveEngine.think(task, domain, stakes)`.
- Callers in apply path: `screening_answers`, `field_mapper`,
  `native_form_filler._escalate_fill`, `native_form_filler._try_cognitive_unstuck`.
- Output: `CognitiveResult(answer, score, level, reasoning)`.

### `optimization_engine`
- Files: `shared/optimization/{__init__, _engine, _aggregator,
  _policy, _signals, _tracker, _trajectory, _replay}.py`
- Entry: `OptimizationEngine.emit(...)`,
  `before_learning_action()`, `after_learning_action()`.
- Output: `data/optimization.db` writes + adaptive signals.

### `memory_layer`
- Files: `shared/memory_layer/{__init__, _manager, _sqlite_store,
  _qdrant_store, _neo4j_store, _embedder, _entries, _linker,
  _forgetting, _query, _router, _stores, _sync, _pattern}.py`
- Entry: `MemoryManager` facade.
- Used in apply path by: screening_semantic_cache, learn chains in
  ⑦ post-apply.

### `ats_adapters`
- Files: `jobpulse/ats_adapters/*.py` (15 files)
- Entry: `ats_adapters.discovery.detect_ats_platform(url, dom)`.
- Output: per-platform `BasePlatformStrategy` instance.

---

## Suggested order

By risk × impact, I'd run them in this order:

1. **`form_fill_dispatch`** — highest user-visible blast radius;
   the bugs we found this session live here. Single-file (~3500 LOC)
   so one session can cover it.
2. **`form_fill_widgets`** — directly downstream of #1; covers the
   per-widget handlers and scanner strategies.
3. **`navigation`** — second-highest blast radius (any bug here
   means the agent never reaches the form).
4. **`screening_pipeline`** — answer correctness; bugs here produce
   wrong fills that pass form validation but get the application
   rejected later.
5. **`cognitive_engine`** — escalation correctness; rare-path bugs
   only surface when a normal fill fails.
6. **`post_apply`** — learning chains; bugs here decay over weeks
   not minutes, but eventually compound.
7. **`pre_screen`** — gate logic; bugs here cause false rejections
   (the pipeline never even tries to apply).
8. **`materials`** — CV/CL generation; bugs here are visible in the
   PDF output, easier to spot manually.
9. **`scan_loop`** — runs nightly, low blast radius if a single
   scan misses.
10. **`optimization_engine` / `memory_layer`** — supporting
    infrastructure; bugs here degrade learning quality but don't
    block applications.
11. **`ats_adapters`** — most platform adapters delegate to the
    universal `playwright_adapter` post-2026-04 unification; per-
    adapter audit only matters if a platform-specific issue surfaces.

---

## Output format

Each session produces:

- `/tmp/audit-<subsystem>.md` — the audit doc with:
  1. Function inventory + wiring categorization
  2. Findings list (severity-tagged)
  3. Live evidence (log excerpts, test output)
  4. Cross-module wiring map
  5. Fixes applied (commit hashes) + remaining work
- Commits with `fix(<subsystem>): ...` messages
- An updated `docs/job-application-pipeline.md` if anything in the
  audit contradicts the doc.

After all 11 subsystems audit, you have a true line-by-line picture
of the apply pipeline. Until then, claims of "100% wired" are
aspirational.

---

## Why this is the only honest path

A single session attempting all 11 subsystems produces theatrical
output. Each subsystem has 500-3500 LOC and 10-60 functions. Doing
function-level + line-level + cross-module + live-evidence
verification at the quality bar implied by "senior engineer at
Anthropic" requires the focus a single subsystem demands. Splitting
forces honesty: each session must complete its scope before moving
to the next.

The advisor warned about omnibus audits during this session. I
agree. If you push for "audit everything in one go" anyway, the
result will not be the audit you asked for — it will be a longer
list of unverified claims, and you'll find that out only by running
the live pipeline and watching it fail.
