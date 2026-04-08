# Task 10: Pre-Submit Validation Gate in Orchestrator

**Files:**
- Modify: `jobpulse/application_orchestrator.py` — add validation check before submit click

**Why:** Currently the orchestrator clicks submit without checking if the form has validation errors. This wastes applications on forms with missing required fields. The extension can now scan for errors (Task 3), and the bridge exposes it (Task 9).

**Dependencies:** Tasks 3 + 9 (validation scan must exist in extension + bridge)

---

- [ ] **Step 1: Find the submit logic in application_orchestrator.py**

Search for the submit button click — look for `find_next_button`, `submit`, or the form-fill loop that ends with a button click. The orchestrator has a multi-page loop where it fills fields then clicks Next/Continue/Submit.

- [ ] **Step 2: Add pre-submit validation check**

Before the submit/next button click, add this validation gate:

```python
# ── Pre-submit validation gate ──
try:
    validation = await self.bridge.scan_validation_errors()
    if validation.get("has_errors"):
        errors = validation.get("errors", [])
        logger.warning(
            "Pre-submit validation errors (%d): %s",
            len(errors),
            [e.get("error_message", "")[:60] for e in errors[:5]],
        )
        if not dry_run:
            return {
                "status": "validation_errors",
                "errorCategory": "validation",
                "errors": errors,
                "message": f"{len(errors)} validation error(s) before submit",
                "isRetryable": True,
                "agentName": "application_orchestrator",
                "attemptedAction": "pre_submit_validation",
            }
except Exception as exc:
    logger.warning("Validation scan failed (non-blocking): %s", exc)
```

**Note:** The error structure follows the project's error handling convention from `.claude/rules/error-handling.md`.

- [ ] **Step 3: Also add validation check in the multi-page Next button path**

If the orchestrator has separate logic for "Next" vs "Submit" buttons, add the same check before "Submit" only — not before "Next" (intermediate pages may have legitimately unfilled fields on other pages).

Find the distinction between intermediate navigation (Next/Continue) and final submission (Submit/Apply). Only gate the final submission.

- [ ] **Step 4: Commit**

```bash
git add jobpulse/application_orchestrator.py
git commit -m "feat(orchestrator): pre-submit validation gate via extension

Calls scan_validation_errors before clicking Submit. If errors exist,
aborts and returns structured validation error for re-fill. Only gates
final submit, not intermediate Next/Continue navigation."
```
