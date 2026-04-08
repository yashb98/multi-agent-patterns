# Extension Label Strategy — Implementation Plan Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the 2500-line content.js monolith into 25 focused files using a strategy pattern, then add a label-based form filling strategy alongside the existing selector-based approach for A/B testing.

**Architecture:** Namespace-based module system (`window.JobPulse`) since MV3 content scripts cannot use ES modules. Files loaded in dependency order via manifest.json. Strategy selection via `payload.strategy` parameter in messages from Python — defaults to `"selector"` for backward compatibility.

**Tech Stack:** Vanilla JS (Chrome MV3 content scripts), no build step, no dependencies.

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

**Full plan (single file):** `docs/superpowers/plans/2026-04-08-extension-label-strategy.md`

---

## Phases

| Phase | File | Tasks | Description |
|---|---|---|---|
| 1 | [phase-1-core-utilities.md](phase-1-core-utilities.md) | 1-4 | Extract `core/` utilities (dom, form, timing, cursor) |
| 2 | [phase-2-scanners.md](phase-2-scanners.md) | 5-7 | Extract `scanners/` + new label scanner |
| 3 | [phase-3-fillers.md](phase-3-fillers.md) | 8-15 | Extract `fillers/` + new label fillers |
| 4 | [phase-4-detectors.md](phase-4-detectors.md) | 16-19 | Extract `detectors/` + new native detector |
| 5 | [phase-5-ai-persistence-protocol.md](phase-5-ai-persistence-protocol.md) | 20-21 | Extract AI, persistence, protocol updates |
| 6 | [phase-6-dispatcher.md](phase-6-dispatcher.md) | 22 | Rewrite content.js as dispatcher + update manifest |
| 7 | [phase-7-smoke-test.md](phase-7-smoke-test.md) | 23 | Smoke test + cleanup |

**Total: 23 tasks, ~25 commits.** Extension works identically after each commit (no big-bang switchover). Label strategy becomes available after Task 22.
