# Engine A/B Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A/B test Chrome extension vs Playwright engine on real job applications, with per-engine learning and head-to-head metrics dashboard.

**Architecture:** Driver swap pattern — shared ApplicationOrchestrator, interchangeable fill drivers (ExtensionBridge or PlaywrightDriver). TrackedDriver wraps either driver and logs per-field metrics to SQLite. Ralph Loop fixes and gotchas are engine-tagged for independent learning.

**Tech Stack:** Playwright (async, CDP connect), SQLite, existing Telegram bot infrastructure.

**Spec:** `docs/superpowers/specs/2026-04-08-engine-ab-testing-design.md`

---

## Execution Order

Tasks are mostly sequential (each builds on the previous), except where noted.

| # | Task | File | Depends On |
|---|---|---|---|
| 1 | [DriverProtocol](task-01-driver-protocol.md) | `jobpulse/driver_protocol.py` | — |
| 2 | [FillResult value_verified](task-02-fill-result-verified.md) | `jobpulse/form_engine/models.py` | — |
| 3 | [Form engine verification](task-03-form-engine-verify.md) | `jobpulse/form_engine/*.py` | 2 |
| 4 | [Validation scanner upgrade](task-04-validation-upgrade.md) | `jobpulse/form_engine/validation.py` | — |
| 5 | [PlaywrightDriver core](task-05-playwright-driver-core.md) | `jobpulse/playwright_driver.py` | 1 |
| 6 | [PlaywrightDriver fills](task-06-playwright-driver-fills.md) | `jobpulse/playwright_driver.py` | 5 |
| 7 | [PlaywrightDriver human-like](task-07-playwright-human-like.md) | `jobpulse/playwright_driver.py` | 6 |
| 8 | [ABTracker SQLite](task-08-ab-tracker.md) | `jobpulse/tracked_driver.py` | 1 |
| 9 | [TrackedDriver wrapper](task-09-tracked-driver.md) | `jobpulse/tracked_driver.py` | 8 |
| 10 | [Engine-tag PatternStore](task-10-engine-tag-patterns.md) | `jobpulse/ralph_loop/pattern_store.py` | — |
| 11 | [Engine-tag GotchasDB](task-11-engine-tag-gotchas.md) | `jobpulse/form_engine/gotchas.py` | — |
| 12 | [Orchestrator driver swap](task-12-orchestrator-driver-swap.md) | `jobpulse/application_orchestrator.py` | 1, 5 |
| 13 | [Pipeline engine routing](task-13-pipeline-routing.md) | `applicator.py`, `ralph_loop/loop.py`, `job_autopilot.py` | 12 |
| 14 | [Telegram toggle + dashboard](task-14-telegram-dashboard.md) | `jobpulse/ab_dashboard.py`, dispatchers | 8, 13 |
| 15 | [Runner chrome-pw command](task-15-runner-chrome-pw.md) | `jobpulse/runner.py` | — |

### Parallel groups:
- Tasks 1, 2, 4, 10, 11, 15 are independent — can run in parallel
- Tasks 5→6→7 are sequential (PlaywrightDriver builds up)
- Tasks 8→9 are sequential (tracker before wrapper)
- Task 12 needs 1+5, Task 13 needs 12, Task 14 needs 8+13

### Shared context for all tasks:
- Spec: `docs/superpowers/specs/2026-04-08-engine-ab-testing-design.md`
- Orchestrator: `jobpulse/application_orchestrator.py` (836 lines, class at line 78)
- Bridge: `jobpulse/ext_bridge.py` (ExtensionBridge class)
- Ralph Loop: `jobpulse/ralph_loop/loop.py` (ralph_apply_sync at line 150)
- Applicator: `jobpulse/applicator.py` (apply_job at line 103)
- Tests MUST use `tmp_path` for any SQLite — never touch `data/*.db`
