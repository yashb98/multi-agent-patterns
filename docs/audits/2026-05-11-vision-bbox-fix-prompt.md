# S26-follow-up-M (P0) — Clean per-field crops via Playwright `locator.screenshot()`

**Date filed**: 2026-05-11
**Slot**: after L (vendor-fallback, shipped — `88bd5a1`), before L-3 (Phase A/B sweep)
**Status as of this prompt**: SCOPED — ready for next session to execute

---

## TL;DR

The vision verifier is sending JD body text to vision instead of form-field values. Live evidence from `data/audits/vision_verifier/1778510445_*_composite.webp` (Graphcore live run on 2026-05-11): every crop contains JD bullet points ("Build automation Go, PowerShell", "Monitor and im, Support audit", "process", "Experience app, Proficient prog") rather than the form's filled values. Root cause is the bbox-extraction path: `get_by_label(label).first` matches the wrong DOM element (often a label-like node in the JD body), and the JS bbox math then reports coordinates that point to those JD positions in the full-page screenshot.

The fix is to replace bbox-math + full-page-crop with **Playwright's native `locator.screenshot()`** per filled field — the same primitive every other production web-automation agent uses (browser-use, Skyvern, WebVoyager). It auto-waits, scrolls the element into view, handles iframes / shadow DOM internally, and is documented to work on the element actually filled.

Once crops are clean, the slice can be re-scored on G1 / G3 / G4 with the existing L vendor-fallback architecture — no further architectural work.

---

## Four binding requirements (user-stated, 2026-05-11)

These shape every gate, every step, and every code change in this slice:

### Requirement 1 — Adapter-agnostic, across the entire URL matrix

The screenshot pipeline must produce a clean per-field crop on **every active ATS adapter** in `docs/audits/url-coverage-matrix.md`: Greenhouse, Lever, Ashby, Workday, LinkedIn (Easy Apply), Indeed, Reed, SmartRecruiters, iCIMS, Generic, and Oracle Cloud HCM. **No `if platform == "X"` branches.** The primitive (`locator.screenshot()`) is adapter-agnostic by design; the slice's job is to verify that it actually works on each adapter's DOM patterns (free-text inputs, react-select widgets, shadow DOM, iframes, file inputs, demographic surveys) without per-adapter special-casing.

### Requirement 2 — Crop includes both QUESTION and VALUE

Vision must see the field **in context**: the question text (label / placeholder / help-text) **and** the filled value, in the same crop. Without the question, vision can only OCR the value — it can't judge whether the answer makes sense for the question.

For most form fields Playwright's `locator.screenshot()` on the input element only returns the input box, not its label. We must expand the screenshot region to include the label. Options:
- (a) Resolve the input's labelling element (`el.labels[0]`, `aria-labelledby`, parent `<label>`, or the nearest preceding text node — depending on DOM convention) and screenshot the **union** of input + label.
- (b) Walk up to a "form-row" ancestor (`<div class="field">`, `<fieldset>`, etc.) whose bounding box natively encloses both the label and the input, and screenshot that container.

Option (b) is more robust across adapters because every ATS uses a row-style wrapper. Adapter-agnostic candidates to try (in order, per element): closest `[role="group"]`, closest `fieldset`, closest `<div>` whose computed style is `display: block` AND whose direct text content includes the field label, capped at a 250 px height (so we don't crop entire form sections). Element-level `locator.screenshot()` is the **last-resort fallback** when no enclosing form-row is detectable.

### Requirement 3 — Verify-loop with Telegram reconfirmation (DEV-PHASE SCAFFOLDING ONLY)

While building this slice, Claude must **not** edit the verifier's filling code path until it has 100 % confidence that the new screenshot primitive returns clean, in-context crops on every adapter in the URL matrix. The protocol during development:

1. Write a one-off harness (`/tmp/m_probe.py` or similar) that, given a URL + a filled-fields manifest, captures the new-style per-field screenshots and tiles them.
2. Run the harness against each ATS in the matrix.
3. Inspect every crop visually. For any crop that doesn't clearly show "question + filled value", iterate on the row-resolution heuristic.
4. Send a representative tiled composite per adapter to Telegram (`jobpulse.telegram_bots.send_jobs_photo` is the existing primitive — used for human-in-loop review in the live_review path). Wait for explicit user reconfirmation per adapter before treating that adapter as "passing".
5. Only after every adapter passes the visual inspection + Telegram reconfirmation, edit `vision_verifier.py` to use the new primitive in production.

**After the slice ships and runs cleanly on the live matrix, the Telegram-reconfirmation loop is REMOVED.** The dev-phase scaffolding exists only to build confidence; the production verifier runs autonomously with no human/Claude in the loop. The audit doc must record this removal explicitly so future readers don't re-introduce the manual gate.

### Requirement 4 — Greenhouse-style "demographic survey duplicates" → dedup by widget identity

Live evidence from the Graphcore artifact shows five panel-pairs with **identical bounding boxes** but two different label strings each:

| bbox (x, y, w, h) | ord pair | labels |
|---|---|---|
| (310, 3463, 568, 44) | 2 ↔ 6 | `"Do you have the legal right to work in the UK?*"` and `"Do you have the legal right to work in the UK?"` |
| (310, 3539, 568, 44) | 3 ↔ 7 | `"Please select your right to work status*"` and `"Please select your right to work status"` |
| (310, 4148, 568, 44) | 4 ↔ 10 | `"I identify my gender as*"` and `"I identify my gender as"` |
| (310, 4232, 568, 44) | 9 ↔ 11 | `"What is your ethnicity?*"` and `"What is your ethnicity?"` |
| (310, 4316, 568, 44) | 5 ↔ 8 | `"Do you consider yourself to have a disability?*"` and `"Do you consider yourself to have a disability?"` |

**What's happening**: Greenhouse demographic surveys render each EEOC question with TWO label strings on the SAME widget — a required copy (with the trailing `*`) and an unmarked copy. Both label strings are visible in the form scanner's a11y tree, both resolve to the same input element, and both have identical bounding boxes. So when the filler reports `{label: value}` for both, the verifier ends up asking vision to verify the same DOM widget twice under two ordinals.

**Fix**: dedupe before sending to vision. Two equivalent dedup keys:
- (a) `await locator.evaluate("el => el.outerHTML.slice(0,200)")` — same outerHTML prefix → same widget.
- (b) `await locator.bounding_box()` → tuple-of-floats key. Identical bbox → same rendered region → same widget.

Option (b) is preferred because it's cheap and matches the rendering reality. Collapse multiple `(label, value)` claims that share a bbox into a single panel; record both labels in the panel metadata so the verdict can map back to both claim rows. Vision verifies once; the verifier emits two `FieldVerdict` rows (one per original claim row) sharing the same observed value.

This is purely a dedup at the verifier layer — the form filler still fills both labels (correctly), the scanner still scans both (correctly). The verifier just stops asking vision to read the same pixels twice.

---

## How the industry does it (research summary)

**Set-of-Mark (SoM) prompting** (Microsoft, [arxiv 2310.11441](https://ar5iv.labs.arxiv.org/html/2310.11441)) — *the standard pattern*:
1. Take whole-page screenshot.
2. Use JS over the real DOM to extract bounding boxes of *actually-interactive* elements (`<input>`, `<select>`, `<textarea>`, `[role="textbox"]`, etc.) — never via label-text matching.
3. Overlay numbered rectangles on the screenshot post-hoc with PIL/canvas.
4. Send annotated whole-page screenshot + a short prompt: "Report the value visible in field [N]".

**WebVoyager / GPT-4V-ACT** ([arxiv 2401.13919](https://arxiv.org/html/2401.13919v4)) uses SoM with numerical labels in the top-left corner of each interactive element.

**browser-use** has a `highlight_elements` mode (default ON) that paints interactive elements with visible indices in the page chrome before screenshotting. Source: [browser-use AGENTS.md](https://github.com/browser-use/browser-use/blob/main/AGENTS.md).

**Skyvern** combines DOM parsing + screenshot. Each interactable element gets a unique ID that appears both in the DOM element list and as a visual label on the screenshot via bounding boxes. Source: [Skyvern blog](https://www.skyvern.com/blog/how-skyvern-reads-and-understands-the-web/).

**Playwright primitive used by all three**: `locator.screenshot()` — auto-waits for actionability, scrolls the element into view, returns the per-element bytes with no coordinate math needed. Documented at [Playwright Python Locator docs](https://playwright.dev/python/docs/api/class-locator#locator-screenshot).

**Why locator.screenshot() can't fail the way our current path does**:
- The locator IS the actual element. If filling it succeeded, screenshotting it will succeed.
- Scroll-into-view + actionability-wait is handled by Playwright internally.
- Frames and shadow DOM work transparently — no `window.scrollX/Y` math, no `frameElement.boundingBox()` offset translations.
- No risk of matching a wrong label in the JD body — the locator was resolved at fill time and stored.

---

## Why our current path fails — the OPRAL Observe step

`jobpulse/form_engine/vision_verifier.py`'s pipeline today:

1. `_full_page_screenshot(page)` → `page.screenshot(full_page=True)` — single tall PNG (correct).
2. `_resolve_label_locator(page, label, field_metadata)` → cascade of `get_by_label` / `get_by_placeholder` / `get_by_role`. **First failure surface here** — when `field_metadata` is missing the filler's resolved selector, the cascade can match the wrong element (a `<label>` in JD body, an `<h2>`-with-role, etc.).
3. `_FIELD_BBOX_JS` evaluated on that locator → reports `(x, y, width, height)` of whatever the cascade matched. **Second failure surface** — bbox now points to JD-body coordinates.
4. PIL crop from `(x, y)` of the full-page PNG → contains JD body pixels.
5. Tile crops into composite → composite shows JD bullet points instead of form field values.
6. Vision reads what's there (JD text) and reports it as `observed_value`.

Live confirmation in Graphcore artifact `1778510445_*`: every panel that should contain a form field shows JD bullet points instead. See "Requirement 4" above for the full duplicate-bbox table.

---

## The fix

### Core change: replace `_FIELD_BBOX_JS` + full-page-crop with `locator.screenshot()` on a form-row container

In `_extract_field_bboxes` (renamed to `_extract_field_crops`), per filled field:

```python
# 1. Resolve the input locator (prefer field_metadata, fall back to cascade).
input_locator = await _resolve_input_locator(page, label, field_metadata)

# 2. Walk up to the nearest form-row container that natively encloses
#    BOTH the label and the input (Requirement 2). Adapter-agnostic
#    heuristics — no platform branches.
row_locator = await _resolve_form_row(input_locator)

# 3. One-shot screenshot of the form-row. Playwright auto-waits +
#    scrolls into view + handles frames + handles shadow DOM.
crop_bytes = await row_locator.screenshot(
    type="png",
    animations="disabled",
    timeout=3000,
)
```

`_resolve_form_row(locator)` walks ancestors and picks the first one whose computed bounding box height ≤ 250 px AND whose `textContent` includes (a) the input's label/placeholder/aria-label AND (b) at minimum one form-control descendant. If no such ancestor exists, falls back to `locator.screenshot()` on the input itself (last-resort).

Then in `_build_composite`:
- Receive a list of `(ordinal, label, value, crop_bytes)` tuples — no more `bbox_entries`.
- For each crop: open with PIL, paste into the composite below a `[NN]` caption strip.
- Encode as WebP-lossless — same output shape as today, so `_call_vision`, `_safe_json`, and the verdict-parsing path are unchanged.

### Dedup duplicate field copies (Requirement 4)

Before sending to vision, dedupe by `(await locator.bounding_box())` tuple-of-floats key. Greenhouse-style required/optional copies collapse to one ordinal. Vision verifies once; the verifier emits two `FieldVerdict` rows (one per claim row) sharing the same observed value.

### Pass `field_metadata` reliably

Native form filler stores `_fields_by_label` with the actual resolved locator. Verify the wiring in `native_form_filler.py:4565` is firing — the filler-side metadata is the source of truth for "which locator filled this field." Audit log should show `field_metadata is not None` on every verifier invocation for forms the filler actually filled.

### What stays the same

- K's composite architecture: ONE vision call per page, tiled per-field crops, ordinal-keyed prompt.
- L's vendor fallback: primary Moonshot → fallback qwen3-vl (or future gpt-4o-mini once quota is back).
- L5 iframe support: Playwright's `locator.screenshot()` handles frames automatically — the explicit branch we added in L can simplify or stay (no harm; the fallback is just dead code for the common case).
- All env vars, `FieldVerdict` dataclass, semantic_decisions schema, ai_assist_logger routing.

---

## Plan

### Phase 0 — Visual confirmation BEFORE any code edit (Requirement 3)

The single most-important step: prove with a one-off harness that the new primitive actually produces clean crops on every adapter, BEFORE touching the verifier. No code edits to `vision_verifier.py` are allowed before this phase finishes.

0.1 **Write `/tmp/m_probe.py`** — takes a URL + an "expected filled fields" list, navigates to the form, fills the listed fields (via the existing pipeline, dry_run=True), then captures per-field screenshots using the proposed primitive (`_resolve_form_row` + `locator.screenshot()`). Tiles them into a composite and saves to `/tmp/m_probes/<adapter>_<ts>.webp`. (~45 min)

0.2 **Run the harness against the URL matrix** in the order specified in `docs/audits/url-coverage-matrix.md`: Greenhouse first (Graphcore, Drweng), then Lever, Ashby, SmartRecruiters, iCIMS, Reed, LinkedIn, Indeed, Oracle, Workday, Generic. One URL per adapter, picked from the matrix. (~2 hours; many of these are 5-min applies each)

0.3 **Inspect every composite visually** via the `Read` tool. For each adapter, verify:
- Every panel contains form-field pixels (input box + label, never JD body text)
- The label/question is visible in the crop (Requirement 2)
- Duplicate panels collapse to a single crop after dedup (Requirement 4)

0.4 **Send each adapter's composite to Telegram** via `jobpulse.telegram_bots.send_jobs_photo` with a caption like `"M-probe: <adapter> | <N> fields | crops include question+value? confirm before proceeding."`. Wait for user reply per adapter. (~20 min)

0.5 **Iterate on `_resolve_form_row` heuristics** for any adapter that doesn't pass visual inspection. Most likely failure modes: react-select widgets (need to target `.select__control` parent), shadow-DOM containers (need to use `get_by_role` deeper), iframe content (Playwright's primitive should handle but verify). Iterate until every adapter is visually clean. Iteration cap: 5 per adapter.

0.6 **User reconfirms each adapter on Telegram** by replying to the message with "OK" or naming the residual issue. Only when every adapter shows "OK" does Phase 0 conclude.

**HALT CONDITION**: if after the iteration cap any adapter still bleeds, file the residual issue as M-2 and proceed with the adapters that DO pass. SHIPPED-PARTIAL framing per K/L precedent.

Phase-0 deliverables: `/tmp/m_probes/*.webp` artifacts saved to a permanent location (`data/audits/vision_verifier/m_probe_<ts>/`), audit doc table of adapter × crop-quality outcomes, Telegram screenshots of the reconfirmation messages, no edits to `vision_verifier.py`.

### Phase 1 — Integration

Only after Phase 0 is OK across the matrix:

1. **Replace `_FIELD_BBOX_JS` + full-page-crop with the new primitive in `vision_verifier.py`**. The new code path is what Phase 0 validated; copy it in. Bbox-math and `_FIELD_BBOX_JS` come out. `_resolve_label_locator` simplifies to just resolve the input locator (no more frame iteration on the locator side — `locator.screenshot()` handles frames natively, so the L5 frame-iteration branch can either stay (harmless) or be removed). (~45 min)

2. **Add dedup pass** before sending to vision. Per-locator `bounding_box()` is the dedup key. Each unique bbox → one panel → one vision verdict. Map back to all `(label, value)` claim rows that share that bbox. (~20 min)

3. **Update unit tests** in `tests/jobpulse/form_engine/test_vision_verifier.py`:
   - `_fake_page_with_screenshot` returns a fake locator that responds to `.screenshot()` with a 50×30 white PNG.
   - `test_composite_built_when_field_bboxes_resolve` renamed and adjusted to use `screenshot` not `evaluate(_FIELD_BBOX_JS)`.
   - New test `test_duplicate_bbox_dedupes_to_single_panel`.
   - Aim 17/17 vision_verifier tests + 244/245 form_engine baseline preserved. (~30 min)

### Phase 2 — Live verification (the SHIPPED-PARTIAL bar from L re-scored)

1. **Re-run `apply_job(dry_run=True)`** on the URL matrix. One URL per adapter. The verifier fires from the live pipeline this time (not from the Phase-0 harness). (~2 hours)

2. **Inspect each artifact** in `data/audits/vision_verifier/`. The composite should be visually equivalent to the Phase-0 probe for that adapter — if the live run doesn't match the probe, something broke in the integration, revert and re-trace.

3. **Re-score L's gates** with the new crops. The relevant gates:
   - **G1** (≥ 90 % content verdicts per artifact): now plausible because vision is reading the right pixels.
   - **G3** (≤ 60 s/page): qwen response should be ~600 tokens for cleanly-cropped fields (~12 s gen + ~3 s image + ~5 s thinking ≈ 20 s). Total wallclock 25 s primary + 20 s fallback = ≈ 45 s. **L's G3 may flip from FAIL to PASS without any L code change.**
   - **G4** (Anthropic visa + AI Policy content): plausible once G1 passes.

### Phase 3 — Documentation + autonomy reset

1. **Remove the Telegram-reconfirmation loop** (Requirement 3's scaffolding). The dev-phase verification was one-time. Document its removal explicitly in the audit doc so future readers don't re-introduce a human gate that doesn't belong in the autonomous loop.

2. **Update the audit doc**: new "S26-follow-up-M ✅ SHIPPED" section with diff summary, before/after composite WebPs cited by filename, per-adapter evidence table, G1-G10 re-scoring of the L slice, SG distance delta. Match K + L doc styles.

3. **Commit** in two logical commits:
   - "feat(form_engine): S26-follow-up-M — Playwright locator.screenshot() for clean per-field crops"
   - "docs(audits): S26-follow-up-M SHIPPED — vision now sees form fields, not JD bleed"

Total budget: ~8 hours real-time (matches L's budget). Iteration cap: 5 per adapter in Phase 0, 2 in Phase 1.

---

## Goal-oriented engineering prompt for the next session

> **GOAL**: Make the vision verifier send clean per-field crops to vision — every panel in every composite WebP shows the actual form field (question/label **and** filled value, both visible in the same crop) on **every active ATS adapter** in the URL matrix. The fix is a 1:1 substitution of Playwright's `locator.screenshot()` on a dynamically-resolved form-row container for the current bbox-math + full-page-crop pipeline. Everything else (K composite, L vendor fallback, decision rows, ai_assist_logger routing) stays as-is. The work is done **adapter-agnostic**: no `if platform == "X"` branches.
>
> **WHY**: Live evidence (`data/audits/vision_verifier/1778510445_*_composite.webp`, Graphcore live run 2026-05-11) shows every crop today is JD body text. Vision dutifully reads it and reports e.g. `observed_value: "Build automation Go, PowerShell"` for the "Right to Work?" field. No vision endpoint, however fast, can produce correct verdicts on noise inputs. This is the actual P0 blocker for SG2 / SG3 / SG4 on every adapter — not vision-vendor latency.
>
> **INVOKE WITH**: `claude --dangerously-skip-permissions`. Stays on `pipeline-correctness-fixes` branch. Touches `jobpulse/form_engine/vision_verifier.py` (~80 LOC swap), `tests/jobpulse/form_engine/test_vision_verifier.py` (~50 LOC mock updates), the audit doc, and creates `/tmp/m_probe.py` + `/tmp/m_probes/*.webp` for dev-phase validation. Forbidden to introduce per-platform branches.
>
> **READ FIRST (binding, in order)**:
> 1. `docs/audits/2026-05-11-vision-bbox-fix-prompt.md` — this file. The four requirements, the plan, the dev-phase scaffolding protocol. Don't re-derive.
> 2. `data/audits/vision_verifier/1778510445_job-boards.greenhouse.io_p1_composite.webp` — the smoking-gun image. Use `Read` to view. Verify the bleed claim visually.
> 3. `data/audits/vision_verifier/1778510445_job-boards.greenhouse.io_p1.json` — sidecar that lists the 5 duplicate-bbox pairs (Requirement 4 evidence).
> 4. `jobpulse/form_engine/vision_verifier.py:309-450` — current `_resolve_label_locator` + `_extract_field_bboxes` + `_FIELD_BBOX_JS`. The exact functions being replaced.
> 5. `jobpulse/native_form_filler.py:4549-4577` — verifier invocation site. Confirm `field_metadata=getattr(self, "_fields_by_label", None)` is passed.
> 6. `docs/audits/url-coverage-matrix.md` — the canonical 10–15 URL matrix. Phase 0 runs the new primitive against every adapter listed here.
> 7. `.claude/rules/jobs.md` — Live Visibility rules, dry-run protocol, no-mocks rule.
> 8. `jobpulse/telegram_bots.py:177` (`sendPhoto` wrapper) and `jobpulse/live_review_applicator.py:699` (`send_jobs_photo` caller) — the Telegram primitive to use for dev-phase reconfirmation.
> 9. [Playwright `Locator.screenshot()` docs](https://playwright.dev/python/docs/api/class-locator#locator-screenshot) — auto-scroll + actionability-wait behavior.
> 10. [Microsoft Set-of-Mark paper](https://ar5iv.labs.arxiv.org/html/2310.11441) and [Skyvern blog](https://www.skyvern.com/blog/how-skyvern-reads-and-understands-the-web/) — industry standard for adapter-agnostic vision-based web automation.
>
> **PRECONDITIONS (halt if any fail)**:
> - Branch `pipeline-correctness-fixes`, clean tree after `88bd5a1` (L) and `f0adaac` (K-doc).
> - `KimiAI_API_KEY` set, Chrome CDP on `localhost:9222` up.
> - Daemon NOT running (`python -m jobpulse.runner stop` if needed).
> - Profile DB populated (real screening answers + identity).
> - `data/audits/vision_verifier/` and `data/audits/vision_verifier/m_probe_<ts>/` writable, ≥ 5 GB free.
> - Telegram bot token + chat ID set (so the dev-phase reconfirmation loop can fire).
>
> **PHASE 0 (mandatory — no edits to `vision_verifier.py` allowed before this phase concludes)**:
>
> Write `/tmp/m_probe.py` that, for a given URL, navigates to the form (re-using the apply_job pipeline up to but not including the verifier invocation), captures per-field screenshots via the proposed primitive (`_resolve_form_row` → `locator.screenshot()`), tiles them, and saves to `data/audits/vision_verifier/m_probe_<ts>/<adapter>.webp`.
>
> Run the harness against every adapter in the URL matrix. After each run:
> 1. Read the composite WebP via the `Read` tool and visually verify every panel shows the form field (label + value visible together) — NOT JD body text.
> 2. Send the composite to Telegram via `jobpulse.telegram_bots.send_jobs_photo` with a caption listing the URL, adapter name, and panel count.
> 3. Wait for user reply on Telegram before moving to the next adapter.
> 4. If a crop bleeds or misses the label, iterate on `_resolve_form_row` (try a different ancestor heuristic) up to 5 times per adapter.
>
> Phase 0 concludes only when every adapter has both Claude's visual verification AND user Telegram-confirmation as OK.
>
> **POST-SLICE AUTONOMY RESET**:
>
> After M ships and L's gates re-pass on the live matrix, the Telegram-reconfirmation loop is **removed** — it was dev-phase scaffolding. Production verifier runs autonomously. Document the removal explicitly in the audit doc so future readers don't re-introduce a human gate.
>
> **ACCEPTANCE GATES (every gate green on a single iteration of Phase 2)**:
>
> - **M-G1** — Every panel in every composite WebP across every adapter shows the form field (label + filled value visible together), zero panels show paragraph-style JD body text. Spot-check via `Read` on the composite WebPs for at least 6 adapters (3 well-defined + 3 exotic).
>
> - **M-G2** — Composite panel count == count of DISTINCT filled-field widgets (post-dedup by bbox). Greenhouse-style required + optional copies of the same widget collapse to one panel each; verdict rows are still emitted for both original claim rows.
>
> - **M-G3** — Tests 17/17 in `tests/jobpulse/form_engine/test_vision_verifier.py`, full form_engine suite preserves 244/245 baseline.
>
> - **M-G4** — `grep -nE "if (platform\|ats\|domain) ==" jobpulse/form_engine/vision_verifier.py` returns empty on the diff. Adapter-agnostic dynamic primitives only.
>
> - **M-G5** — One live `apply_job(dry_run=True)` per adapter in the URL matrix (Greenhouse, Lever, Ashby, Workday, LinkedIn, Indeed, Reed, SmartRecruiters, iCIMS, Generic, Oracle). Every artifact's composite shows clean form-field crops (M-G1 applied per adapter). Skip is allowed for URLs whose listings have expired (max 2 across the matrix).
>
> - **M-G6** — On the Anthropic Greenhouse URL specifically (K's bbox-bleed poster child), the new composite shows ≥ 70 % of panels with clean form-field content (not paragraph text). Anthropic's free-text textareas are the hardest case; document residual edge cases as M-2 follow-ups if needed.
>
> - **M-G7** — L's G3 (≤ 60 s/page) is re-scored against the new clean-crop artifacts. Best-effort: with clean crops qwen should return ~600 visible tokens in ~20–30 s total. If G3 flips to PASS on > 50 % of adapters' artifacts, the L slice gets a "re-scored after M" note. Not gating; informational.
>
> - **M-G8** — Telegram-reconfirmation scaffolding is removed in Phase 3. `grep -n "send_jobs_photo\|sendPhoto" jobpulse/form_engine/vision_verifier.py` returns empty in the final commit. The audit doc records the removal.
>
> - **M-G9** — Audit doc updated: new "S26-follow-up-M ✅ SHIPPED" section with diff summary, per-adapter before/after composite WebP filenames, G1–G10 re-scoring, SG distance delta, and explicit note that the dev-phase Telegram loop was scaffolding-only.
>
> **PROCESS DISCIPLINE**:
>
> - **No edits to `vision_verifier.py` before Phase 0 user reconfirmation is complete.** The harness runs in `/tmp/m_probe.py`, against the live pipeline, but the verifier itself stays on the L code path until Phase 1.
> - **No coordinate math.** If you find yourself computing x/y/width/height anywhere in the new code, you're doing it wrong. `locator.screenshot()` is the substitution.
> - **No per-adapter branches.** Every adapter goes through the same `_resolve_form_row` cascade. If an adapter needs special handling, the cascade gets a new universal-rule tier, never an `if platform == "X"`.
> - **Iterate on heuristics, not on slice scope.** If Anthropic's textareas don't crop cleanly with the first heuristic, try a second heuristic. Don't bundle "fix textareas" into "skip Anthropic this slice" — that's a slice-failure shortcut.
> - **Telegram reconfirmation is one-shot per adapter, not per crop.** Send one composite per adapter, get one OK, move on.
> - **Time cap: 8 hours.** Iteration cap: 5 per adapter in Phase 0, 2 in Phase 1.
>
> **CONSTRAINTS**:
> - `dry_run=True` only. Never `JOB_AUTOPILOT_AUTO_SUBMIT=true`.
> - Forbidden to change `FieldVerdict`, `_record_verdict_row` signature, `_learn_correction` routing, `_call_vision`, `_call_provider`, `_get_fallback_client`, or any L-era code. M is upstream of those.
> - Forbidden to introduce coordinate math. No `getBoundingClientRect`, `scrollX`, `scrollY`, or PIL.crop on a full-page screenshot in the new code.
> - Forbidden to add new env vars beyond what's needed for testability. The new path IS the only path after Phase 3.
> - Forbidden to swap Playwright `Locator.screenshot` for CDP-direct calls. The whole point is to use the same primitive every other framework uses.
> - Forbidden to leave the Telegram-reconfirmation loop in the production code path after Phase 3. It is dev-phase scaffolding; remove it explicitly and assert via `grep` that it's gone.
>
> **WHAT THIS SLICE EXPLICITLY DOES NOT DO**:
> - Doesn't change the vision endpoints. Moonshot stays primary, qwen3-vl stays fallback.
> - Doesn't re-run Phase A / Phase B of L. That's L-3 scope. Re-scoring L's gates is a side-effect of M, not its goal.
> - Doesn't fix screening-pipeline upstream issues (visa-No, ethnicity-cache, etc.) — those surface through the verifier once verdicts are accurate.
> - Doesn't add new vendor support — the L env-var hook already covers that.
>
> **ACCEPTANCE (the SHIPPED state)**:
>
> M ships when M-G1 through M-G6 + M-G9 hold on a single iteration of Phase 2 evidence. M-G7 is best-effort. M-G8 is mandatory but is a Phase-3 deliverable, not Phase-2. The audit doc carries the new section. The commit message scopes "M SHIPPED" and references the K and L commits.

---

## Why the SoM-on-whole-page alternative was NOT chosen for M

The alternative is to take a full-page screenshot, overlay numbered rectangles on it with PIL after the fact, and send the annotated whole-page screenshot to vision. This is what WebVoyager and browser-use do.

Reasons not to pivot to this for M:
1. **K's composite shape (one tiled image per page) is already wired through to the verdict-parsing path.** Switching to whole-page-with-overlays is a bigger rework — it changes the prompt, the verdict shape, the bbox→ordinal mapping, and the test mocks. M's value is in a clean *substitution* of the bbox primitive; SoM-on-whole-page is a separate slice.
2. **Vision endpoints today (Moonshot, qwen3-vl) handle small tiled crops better than they handle whole-page screenshots.** A 30 KB tiled composite is well within both vendors' image budgets; a 2 MB whole-page screenshot would tax Moonshot's queue further and exceed qwen's reasonable input size.
3. **Composite tiles are individually inspectable for debugging.** Whole-page-with-overlays makes debugging harder — you can't see which crop the vision call read wrong without zooming.

If after M the residual bleed is bad on react-select / shadow-DOM widgets, the right slice to follow with is one that adds **WebVoyager-style whole-page SoM as a fallback** for fields where `locator.screenshot()` returns a tiny / empty crop. That's a separate scoped follow-up; not M's job.

---

## Open questions to resolve in the M session

1. **What does `locator.screenshot()` return for a 1×1 hidden React-select input?** Playwright's actionability check should fail (element not visible) — verify, and decide whether the `_resolve_form_row` cascade should walk up to a visible ancestor (`.select__control`, parent with computed `display: block` + `offsetWidth > 0`) in those cases. Most likely yes — react-select is widespread.

2. **Does `field_metadata` in `native_form_filler` carry the resolved locator or just a CSS selector?** If selector-only, re-resolve at verifier time. If locator object, prefer that (it's already pinned to the correct frame / shadow context).

3. **iCIMS iframes** — `Locator.screenshot()` should handle iframe-embedded elements transparently. Verify on the iCIMS URL in the matrix. If not, the L5 frame-iteration branch we added in L stays; otherwise it can be removed.

4. **Anthropic free-text textareas** — `locator.screenshot()` on a `<textarea>` returns the textarea pixels. If the textarea has a separate-DOM label above it (common Greenhouse pattern), the form-row resolver should pick a parent `<div>` that includes both. Verify on the Anthropic URL; if the parent walk picks too large a container (re-introducing bleed), tighten the 250 px height cap or require a tighter text match between the form-row's `textContent` and the input's accessible-name.

5. **LinkedIn Easy Apply modal** — fields are inside `.jobs-easy-apply-modal`. The form-row resolver should produce per-field crops within the modal. Verify.

---

## Sources

- [Set-of-Mark Prompting paper (arxiv 2310.11441)](https://ar5iv.labs.arxiv.org/html/2310.11441)
- [Microsoft SoM GitHub](https://github.com/microsoft/SoM)
- [WebVoyager paper (arxiv 2401.13919)](https://arxiv.org/html/2401.13919v4)
- [Skyvern blog — how Skyvern reads the web](https://www.skyvern.com/blog/how-skyvern-reads-and-understands-the-web/)
- [Browser-use AGENTS.md](https://github.com/browser-use/browser-use/blob/main/AGENTS.md)
- [Playwright Python Locator docs](https://playwright.dev/python/docs/api/class-locator)
- [Playwright Screenshots docs](https://playwright.dev/docs/screenshots)
- [How AI agents see the screen — DOM vs Screenshots (Fazm)](https://fazm.ai/blog/how-ai-agents-see-your-screen-dom-vs-screenshots)
