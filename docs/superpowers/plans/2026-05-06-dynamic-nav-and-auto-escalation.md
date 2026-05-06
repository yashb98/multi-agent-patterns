# Consume Reasoner's `advance_button` + Auto-Escalate Stuck Fills

> **Plans D + E.** Two paired fixes for `Dynamic Over Hardcoded`
> violations discovered during the 2026-05-06 Revolut welovealfa.com
> live validation.

## Context

Run on 2026-05-06: `scan_semantic` correctly matched 10/10 questions
to widgets on the screening page, but the agent **never reached** that
page. On `/upload-cv`, after CV upload, the fill loop exited with
`success=True, dry_run=True, pages_filled=1`. Screening page
(visa-sponsorship, notice-period, remote, distributed-engines) was
never scanned, so plans A/C never ran on the actual problem.

Root cause: `_is_submit_page` (line 2384) and `_click_navigation`
(line 2683) both check a hardcoded button-text list:

```python
["Submit Application", "Submit", "Apply"]   # submit
["Review", "Save and Continue", "Save & Continue", "Continue", "Next", "Proceed"]   # next
```

with `exact=False`. On any job-portal page, "Apply" matches header
buttons (e.g., "Apply now"). The submit-list is checked before the
next-list, so the loop short-circuits with `dry_run_stop` instead of
clicking the visible "Continue" button. The screening page is never
reached.

## The wiring gap (what's already there, what's not consumed)

`PageReasoner.PageAction` already carries:

```python
action: str          # fill_form | fill_and_advance | done | click_apply | ...
advance_button: str  # exact text of the Continue/Submit button
expected_outcome: str
```

The reasoner's prompt explicitly produces this (`page_reasoner.py:530`):
```
"advance_button": "text of Next/Submit/Continue button to click after filling"
```
And the JSON parser reads it (`page_reasoner.py:602`).

But `grep -rn advance_button` shows it is **set in 3 places** (the
reasoner) and **consumed in 0 places**. `NativeFormFiller._click_navigation`
runs its own string-matching loop instead of clicking the button the
reasoner already named.

That's the gap. The fix is to consume `advance_button` and the
`done` action signal, then delete the duplicate string lists.

Tech stack: existing `PageReasoner` · existing `CognitiveEngine` ·
existing `ai_assist_logger` · pytest. **No new files for D.**

---

## Plan D — Consume reasoner output for nav + submit detection

### D-1. Thread `PageAction` from navigator → form filler

**Files:** modify `jobpulse/application_orchestrator_pkg/_navigator.py`,
`jobpulse/application_orchestrator_pkg/_form_filler.py`,
`jobpulse/native_form_filler.py:fill`. Test:
`tests/jobpulse/test_planned_action_threaded_to_filler.py`.

Today the navigator's terminal-action branch (line 1256) hands off to
`NativeFormFiller` via `_make_result(ctx)` — it returns
`{"page_type": ..., "snapshot": ...}` only. The reasoner's
`PageAction` is dropped on the floor.

Change: include the planned action in the handoff result:

```python
def _make_result(ctx: "StepContext") -> dict[str, Any]:
    ...
    if act in ("fill_form", "fill_and_advance"):
        result = {"page_type": PageType.APPLICATION_FORM,
                  "snapshot": ctx.snapshot,
                  "planned_action": ctx.planned_action.to_dict()
                                    if ctx.planned_action else None}
    ...
```

`ApplicationOrchestrator` then forwards `planned_action` into
`NativeFormFiller.fill(... planned_action=None)` (new optional kwarg).
The filler stashes it on `self._planned_action` so `_click_navigation`
and `_is_submit_page` can read it.

### D-2. Replace `_is_submit_page` with the action signal

**Files:** modify `jobpulse/native_form_filler.py:_is_submit_page`.
Test: `tests/jobpulse/test_is_submit_page_uses_planned_action.py`.

```python
async def _is_submit_page(self) -> bool:
    """A page is the final submit page iff the reasoner said so.

    The reasoner emits action='done' for the success/confirmation
    page and 'fill_and_advance' for intermediate pages. If we have
    a planned_action, trust it. If not (e.g. tests, headless cron
    without orchestrator), fall back to the reasoner directly with
    the current snapshot.
    """
    pa = getattr(self, "_planned_action", None)
    if pa:
        return pa.get("action") == "done"
    # Fallback for callers without an orchestrator-supplied plan
    from jobpulse.page_analysis.page_reasoner import get_page_reasoner
    snap = await self._driver.get_snapshot()
    return get_page_reasoner().reason_sync(snap).action == "done"
```

The hardcoded `["Submit Application", "Submit", "Apply"]` list is
deleted.

### D-3. Replace `_click_navigation` button selection

**Files:** modify `jobpulse/native_form_filler.py:_click_navigation`.
Test: `tests/jobpulse/test_click_navigation_uses_advance_button.py`.

The function currently iterates two button lists and picks the first
visible match. Replace with:

```python
async def _click_navigation(self, dry_run: bool) -> str:
    page = self._page
    pa = getattr(self, "_planned_action", None) or {}
    target_text = pa.get("advance_button", "").strip()
    is_submit = pa.get("action") == "done"

    # If the reasoner didn't supply advance_button, ask it now with a
    # fresh snapshot. The reasoner is cached per-snapshot so this is
    # free on the second call within the same page state.
    if not target_text:
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        snap = await self._driver.get_snapshot()
        action = get_page_reasoner().reason_sync(snap)
        target_text = action.advance_button.strip()
        is_submit = action.action == "done"

    if not target_text:
        return ""

    # Resolve the button by exact name first, then by visible text.
    btn = page.get_by_role("button", name=target_text, exact=True)
    if not await btn.count():
        btn = page.get_by_role("button", name=target_text, exact=False)
    if not await btn.count() or not await btn.first.is_visible():
        # Reasoner-named button not on page — let the caller retry on
        # the next observe→plan→act tick (don't fall back to a string
        # list; that's exactly the violation we're fixing).
        return ""

    if is_submit and dry_run:
        return "dry_run_stop"
    if is_submit:
        try:
            await self._record_final_state_before_submit()
        except Exception as exc:
            logger.warning("record_final_state_before_submit failed: %s", exc)
    await self._move_mouse_to(btn.first)
    await btn.first.click()
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        await asyncio.sleep(2)
    logger.info("nav: clicked %s via reasoner-named %r",
                "submit" if is_submit else "next", target_text)
    return "submitted" if is_submit else "next"
```

Both string lists are deleted from this function. The Workday
`data-automation-id='bottom-navigation-next-button'` block (further
down the function) is **kept** — that's a structural selector the
reasoner won't emit, and it's allowed under the `Dynamic Over
Hardcoded` rule (structural format, not a classification heuristic).

### D-4. Reasoner prompt assertion

**Files:** modify `jobpulse/page_analysis/page_reasoner.py` —
strengthen the prompt to always emit `advance_button` for any
non-terminal action.

The current prompt asks for `advance_button` but doesn't mark it
required. Make it explicit + add a validation step in
`_validate_action`: if `action == "fill_and_advance"` and
`advance_button == ""`, downgrade confidence to 0.0 (forces re-plan
or human handoff). This guarantees the consumer always has a value
to click on, OR the reasoner has already self-flagged its
uncertainty.

Test: `tests/jobpulse/test_page_reasoner_advance_button_required.py`.

### D-5. End-to-end validation against Revolut

Real run, post-D, no E yet. Expect the agent to:
1. Upload CV on `/upload-cv`
2. Click reasoner-named Continue (e.g., "Continue") → advance to
   screening page
3. Reach `scan_semantic` + `_fill_resolved_widget` (already shipped
   in earlier commits) on the screening page

Failure of (3) = Plan E territory.

---

## Plan E — Auto-Escalate Stuck Fills (do AFTER D ships and validates)

### E-1. Failure-context envelope

**Files:** create
`jobpulse/form_engine/fill_escalation.py`.
Test: `tests/jobpulse/test_fill_escalation_context.py`.

Compact (≤4 KB) JSON of label, intended value, ordered failed-attempt
list, field metadata from `_fields_by_label[label]`, recent click
history.

### E-2. Escalation tier in `_fill_by_label`

**Files:** modify `jobpulse/native_form_filler.py:_fill_by_label`.
Test: `tests/jobpulse/test_fill_by_label_escalation.py`.

Insert a new tier after LLM-recovery, before final return:

```python
if not result.get("success"):
    esc = await self._escalate_fill(label, value, attempts, field_meta)
    if esc.get("success"):
        return esc
```

`_escalate_fill` calls `CognitiveEngine.think(domain="form_recovery",
stakes="high")`. Engine returns a structured plan
`{action, click_selector, option_text, verify}`. `_escalate_fill`
executes via the existing `_fill_resolved_widget` (so it reuses the
dispatcher we already added in commit 86790bd) and on success records
via `ai_assist_logger.record_fix(dom_signature=...)` so the fix lands
in `widget_patterns` for the next visit (Plan C-3 picks it up).

### E-3. Telegram bypass on persistent failure

After E-2, if the cognitive engine still has no plan or the plan
fails, fall through to existing Telegram bypass machinery
(`approval_request` with field name + page URL). Human floor —
never silently abandon a field.

### E-4. Session-bridge AI-assist logger

`NativeFormFiller.fill` already starts an AI-assist session for human
fixers. Extend the same session lifecycle to the auto-escalation
path so all fixes (cognitive + human) land in the same session log.
One session per `fill()` call.

### E-5. End-to-end validation

Real run against welovealfa.com **after Plan D ships**. Expect:

```
fill_escalation: stake=high, engine=L2, plan=click_then_select
  selector='div[data-q='visa'] button' option='Yes'
fill ✓ 'Do you require visa sponsorship?' = 'Yes' [tech=cognitive_escalation]
ai_assist: recorded fix with dom_signature → widget_patterns[welovealfa.com]
```

Run twice. Second run: `_scan_learned_patterns` returns the pattern
as Strategy 0; cognitive escalation does NOT re-fire.

---

## Out of scope

- `_navigator.py:1311` `("Apply now", "Apply for this job", ...)`
  list inside `click_apply_button`. That's a separate JD-page
  Apply-finder. Same fix applies (consume reasoner's
  `target_text`). Follow-up plan after D validates.
- Verification-wall string detection (Cloudflare/reCAPTCHA selectors)
  in `playwright_driver.py`. Those are structural format detectors
  for security widgets, not classification heuristics — the
  `Dynamic Over Hardcoded` policy explicitly allows structural
  format detection.
- `TERMINAL_ACTIONS` set in `_navigator.py:178`. That's the enum of
  reasoner output values, not a string-matched user-input. Fine.

## Why this plan is small

The codebase already had the primitive (`advance_button`). It just
wasn't being consumed. The previous draft of this plan invented a
parallel button-classifier with three tiers + a new file + a new
cache table — all of it duplicating `PageReasoner`. Advisor caught
that. This rewrite is the actual fix: thread the reasoner's output
through the pipeline, delete the duplicates.
