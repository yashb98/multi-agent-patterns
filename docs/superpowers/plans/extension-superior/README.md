# Extension Superior to Playwright — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Each task is in its own file — execute one at a time.

**Goal:** Close 10 critical gaps in `extension/content.js` so the Chrome extension is definitively superior to Playwright for form automation.

**Architecture:** All interaction logic lives in `content.js` (the extension is the primary engine). Python `form_engine/` remains for `APPLICATION_ENGINE=playwright` backward compat but gets no new investment. New protocol commands added to `protocol.js`. Python ext_adapter stays thin — decides *what* to fill, extension handles *how*.

**Tech Stack:** Chrome MV3 extension (vanilla JS), WebSocket bridge, Python (ext_adapter.py, ext_bridge.py)

---

## Execution Order

Execute sequentially — all tasks touch `extension/content.js`:

| # | File | What | Impact |
|---|------|------|--------|
| 1 | [task-01-react-setter-verify.md](task-01-react-setter-verify.md) | React `nativeInputValueSetter` + post-fill verification | Critical |
| 2 | [task-02-contenteditable.md](task-02-contenteditable.md) | Contenteditable / rich text support | Critical |
| 3 | [task-03-validation-scan.md](task-03-validation-scan.md) | Validation error scanning + protocol | High |
| 4 | [task-04-retry-wrapper.md](task-04-retry-wrapper.md) | Retry wrapper for all fill operations | High |
| 5 | [task-05-bezier-mouse.md](task-05-bezier-mouse.md) | Bezier mouse trajectories | Medium |
| 6 | [task-06-scroll-timing.md](task-06-scroll-timing.md) | Scroll-aware timing + smart delays | Medium |
| 7 | [task-07-verify-all-fills.md](task-07-verify-all-fills.md) | `value_verified` flag on ALL fill operations | High |
| 8 | [task-08-native-setter-everywhere.md](task-08-native-setter-everywhere.md) | `nativeInputValueSetter` in remaining typing functions | Critical |
| 9 | [task-09-python-thin-orchestrator.md](task-09-python-thin-orchestrator.md) | Python thin orchestrator methods | Medium |
| 10 | [task-10-presubmit-gate.md](task-10-presubmit-gate.md) | Pre-submit validation gate in orchestrator | High |

## Key Context for All Tasks

- `extension/content.js` — ~1940 lines, the content script injected into every page
- `extension/protocol.js` — message type constants shared between scripts
- `jobpulse/ext_bridge.py` — Python WebSocket bridge to extension
- `jobpulse/application_orchestrator.py` — full application lifecycle orchestrator
- `behaviorProfile` object at line ~22 of content.js — typing speed, variance, delays
- `resolveSelector()` at line ~308 — handles shadow DOM paths (`host>>inner`)
- Message handler at line ~1748 — `switch(action)` dispatches commands
