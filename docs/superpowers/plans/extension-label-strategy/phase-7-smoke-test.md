# Phase 7: Smoke test + cleanup (Task 23)

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Verify both strategies work end-to-end and remove the backup file.

**Depends on:** ALL previous phases (1-6) must be complete.

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

---

### Task 23: End-to-end smoke test and backup removal

**Files:**
- Remove: `extension/content.js.bak` (after verification)

- [ ] **Step 1: Test selector strategy (backward compat)**

Start the ext-bridge and send a `get_snapshot` command. Verify the response contains fields, buttons, and page_text_preview — same shape as before the refactor.

```bash
python -m jobpulse.runner ext-bridge
# In another terminal, or via Telegram:
python -m jobpulse.runner ralph-test https://boards.greenhouse.io/example
```

Expected: Same behavior as before. All existing Python code works without changes.

- [ ] **Step 2: Test label strategy**

Send a `scan_fields` command with `strategy: "label"`. Verify it returns `[{label, type, value, ...}]` instead of the full snapshot.

Send a `detect_page` command with `strategy: "label"`. Verify it returns `{is_confirmation, is_submit_page, ...}`.

- [ ] **Step 3: Remove backup**

```bash
rm extension/content.js.bak
git add -A extension/
git commit -m "chore(ext): remove content.js backup after successful smoke test"
```
