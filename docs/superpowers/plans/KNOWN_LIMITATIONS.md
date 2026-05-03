# JobPulse Web Automation — Known Limitations (post 2026-05-01 fixes)

This file is the brutally honest companion to the verification hardening work. It documents what the system **can** handle reliably, what it **probably** handles, and what genuinely **cannot be guaranteed** without real-world data or a live ATS run.

## What's reliable now

After the `nav-verification-hardening` (14 tasks, 46 tests) + `pipeline-correctness-fixes` (5 tasks, 17 tests) + this final fix round (6 tasks, 16 tests) work — **79 tests passing across 16 files** — the following invariants hold:

- **Per-fill verification on the navigator path** (`action_executor.py`) — read-back + retry + structured `ExecutorResult` with length-gated three-way match.
- **Per-fill verification on the form-fill path** (`native_form_filler.py`) — existing `_fill_by_label` verification PLUS new `emit_form_fill_failures` so failures from this path now reach the same OptimizationEngine signal stream.
- **Auth handlers go through `_verify_action`** — same ghost-click detection as application pages.
- **Cache invalidation on three failure modes**: ghost click, expected-outcome violation, vision-DOM disagreement.
- **Reflexion (`reason_with_failure`) on three failure modes**: same three.
- **First-encounter safety**: never-seen domains force `dry_run=True` regardless of caller.
- **Mutex covers fill+submit** (separate `_fill_lock` from `_apply_lock`).
- **Pre-submit gate fires on every successful non-dry-run application** (synthesized `CompanyResearch` stub when missing).
- **`dry_run` propagates through `route_and_apply`** (was silently dropped before).
- **`job_analytics` reads from the right DB** — production funnel returns real data instead of zeros.
- **Failure learning enters MemoryManager** — failed strategies record episodes.

## What's probably reliable but untuned

- **Field-count guard threshold (80%)** — magic number. Will catch obvious LLM drops but might false-positive on optional-field-heavy forms or false-negative when the LLM picks the wrong 80%.
- **Vision-DOM gate threshold (`confidence < 0.7`)** — magic number. May fire too often (cost) or too rarely (miss).
- **Read-back retry count (1)** — covers React revert and autocomplete commit. May be insufficient for slow hydration.
- **Three-way match length guard (≥3 chars)** — prevents `'1' in '10 years'` false positives. Edge cases with 2-char codes (e.g. country codes "UK", "US") rely on exact match only — could miss valid normalizations.
- **First-encounter detection** — based on `FormExperienceDB.lookup` returning None. Domain canonicalization may differ between writer and reader; some "first encounters" are actually known domains in disguise.

These need production data over 1-2 weeks to tune. There is no way to know the right values from code review alone.

## What genuinely cannot be guaranteed

### Novel failure modes not in any verification primitive
- **Wrong field VALUES** that match the read-back exactly but are semantically wrong (e.g. wrong screening answer that the form happily accepts). Read-back verifies fill landed, not that fill was correct.
- **Conditional fields** that appear after answering another field. Field-count guard runs on the initial scan; new fields appearing post-fill aren't checked.
- **Multi-step forms** where step N requires reading step N-1's response. The reasoner cache is per-page-content-hash; if two steps look similar, the cache may serve a wrong plan.
- **Forms behind unusual auth** (SAML, OIDC, MFA, hardware keys). SSO handler covers Google/LinkedIn/Microsoft/Apple. New providers need a new branch.

### Novel platform patterns
- **Custom autocompletes** that need ArrowDown+Enter or other commit gestures — handled per-platform today; a new platform with this pattern fails until an adapter is written.
- **Canvas-rendered widgets** (date pickers, signature pads, custom dropdowns) — vision tier only fires at field level on stuck fills, not first-attempt.
- **Shadow DOM patterns we haven't seen** — `field_scanner` uses a11y tree; novel custom-element nesting can produce empty scans.
- **Reed-style modal-overlay flows on a new platform** — handled per-platform (`reed.py`, `smartrecruiters.py`); a new platform with similar pattern won't have an adapter.
- **iframe-nested forms beyond known patterns** — `_resolve_page_context` knows about `icims_content_iframe` and platform-specific frame names; novel frame names won't be auto-detected.

### Anti-bot detection
- **Novel fingerprinting patterns** — TLS fingerprinting, mouse movement entropy checks, timing fingerprints. Headed Chrome + `--disable-blink-features=AutomationControlled` + persistent profile is the entire defense.
- **New CAPTCHA variants** — 6-stage bypass pipeline is fixed. A novel CAPTCHA falls through to human fallback (Telegram).
- **Behavioral detection** — LinkedIn ML models could flag the agent on patterns we can't see in code.

### Data-dependent
- **Threshold tuning** (vision gate, field count, retry count) needs production runs.
- **Vision tier trigger rate** (~5% per docstring) is unverified — no telemetry counts how often it fires vs is suppressed.
- **`gate_effectiveness` table** — writer is structurally correct, but it has 0 rows because the Gate 4 Phase A path that writes it hasn't run since the schema was created. Will populate on the next full scan-and-screen run.
- **AgentRulesDB consume metrics** — 7 orphan rules from a deprecated code path were marked inactive. The new write path (`auto_generate_from_correction` + Task 2's `_normalize_domain`) is correct but unproven until a correction is made and the rule is consumed.

### Architectural / requires user decision
- **`draft_applicator.py` (~900 lines)** — fully dead code. Telegram dispatcher returns "disabled". Either delete or wire — needs your call.
- **`GateThresholdAdapter`** — fully implemented, never instantiated. Same call.
- **Cron auto-submit dry-run-first refactor** — currently `_run_scan_window_inner` queues for review (draft mode); if cron ever auto-submits, the `dry_run=True` + `confirm_application()` cycle isn't there.
- **`scan_pipeline.py` side-effect changes** swept in by earlier `git add` (CV PDF generation moved to scan time, `cl_drive_link` hardcoded to None) — these are intentional user changes per the system, but their cost-model implications need an audit pass.

## What you actually need before claiming "100% confidence on new pages"

There is no path to 100% via code alone. The minimum you'd need is:

1. **A real-data dry-run on this branch.** Pick a job URL on a never-applied-to ATS. Run `JOB_AUTOPILOT_AUTO_SUBMIT=false python -m jobpulse.runner job-process-url <URL>`. Watch the logs for these markers:
   - `FIRST_ENCOUNTER` (FIX 2) — confirms safety override fires
   - `Filled X (verified)` / `(verified after retry)` / `Fill mismatch for 'X'` (verification primitives)
   - `ACT: ghost click detected` (existing) — should also produce `Reflection (trigger=ghost_click)` (existing)
   - `ACT: expected_outcome 'X' not met` → `Reflection (trigger=expected_outcome_violation)` (FIX 4)
   - `Vision-DOM disagreement: reasoner=X vision=Y` → `Reflection (trigger=vision_disagreement)` (FIX 4)
   - `FIRST_ENCOUNTER` markers should NOT cause silent failure
2. **Two weeks of data**, then re-tune the four magic-number thresholds.
3. **A mistake budget**. Some applications will fail in ways nothing in this codebase predicts. The system gets better as those failures feed `field_corrections.db` → `agent_rules.db` → consumed via `_normalize_domain`-fixed reader.

## Confidence per failure surface

| Surface | Confidence | Why |
|---|---|---|
| Login forms on known platforms | High | Auth handlers verified, SSO priority fixed, ghost-click detection works |
| Application forms on known platforms (Greenhouse, Lever, Workday, Ashby, iCIMS, LinkedIn, SmartRecruiters, Indeed, Reed) | High-ish | Per-platform adapters exist + 3-tier scoping + verification on both paths |
| Application forms on **novel** ATS platforms | **Medium-low** | Falls through to GenericStrategy + auto-detect. First-encounter mode forces dry-run; you'll review it |
| CAPTCHA / verification walls (known types) | High | 6-stage bypass + human fallback |
| CAPTCHA / verification walls (novel types) | Low | Falls to human fallback; success depends on you being available |
| SSO flows (Google/LinkedIn/MS/Apple) | High | Priority order verified |
| SSO flows (novel providers) | Low | No adapter — will likely fail and need human |
| Canvas/shadow-DOM custom widgets | Medium | a11y tree usually catches; vision is the fallback at ~5% trigger |
| Multi-step forms with conditional fields | Medium | Stuck-detection catches loops; conditional-field detection is missing |
| Forms with anti-bot ML detection | Unknowable | No defense beyond headed browser + persistent profile |

## The honest one-liner

The system is **measurably more reliable than two days ago** (79 new tests verify specific failure modes are caught). It is **not bulletproof**, and code alone cannot make it bulletproof. The next material gain comes from running it on real ATS forms in dry-run mode and feeding the resulting failures into the now-functional learning loops.
