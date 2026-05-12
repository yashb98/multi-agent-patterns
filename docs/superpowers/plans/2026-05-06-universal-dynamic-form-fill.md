# Universal Dynamic Form-Fill — Gap Inventory + Plan

> Comprehensive gap audit and unified plan for handling every form
> dynamically — every widget type, every option-discovery path, every
> hardcoded heuristic the codebase still leans on.

## 0 — Audit corrections (read first)

Initial draft missed the following. Corrected here:

1. **Most per-type fillers already exist in `jobpulse/form_engine/`**:
   `text_filler.py`, `select_filler.py`, `radio_filler.py`,
   `checkbox_filler.py`, `date_filler.py`, `multi_select_filler.py`
   (with `fill_tag_input` for chips), `page_filler.py` routes by
   `InputType`. They are wired into the **unified** `FormFillEngine`
   (env `UNIFIED_FORM_ENGINE=true`), but NOT into the production
   legacy `NativeFormFiller` path. The real gap is **dual-engine
   duplication**, not "implement from scratch".

2. **`RICH_TEXT_EDITOR` is partially fake even in the unified path**:
   `page_filler.py:86-87` routes it to `text_filler.fill_text`,
   which is a `.fill()` call that doesn't work on contenteditable.
   Real handler still missing.

3. **Validation-error scanning EXISTS** in `form_engine/validation.py`
   and is consumed by the unified engine, but NOT by NativeFormFiller.

4. **Conditional-visibility partially handled**: NativeFormFiller has
   a "Post-fill rescan" loop (line 3798+) that catches newly-revealed
   fields. Whether it's adequate (e.g., re-runs combobox option scans
   for the new field) needs evaluation.

5. **Iframe routing is asymmetric**: `playwright_driver.py:381` hard-
   codes `("icims_content_iframe",)` only. `native_form_filler.py:365`
   reads iframe names from quirk hints — more flexible. The driver
   path needs to consume the same quirk source.

---

## 1 — Complete Gap Inventory

Five categories, every gap surfaced by the 2026-05-06 Revolut session
or by audit grep across the codebase. Each row carries a stable ID
referenced by the plan in §2.

### 1A — Widget-type coverage (what `_fill_resolved_widget` can drive)

The `InputType` enum in `form_engine/models.py` defines 17 distinct
widget types. Today's dispatcher coverage:

| ID | Widget | Today | Gap |
|---|---|---|---|
| **W-01** | `text`, `textarea`, `number`, `email`, `tel`, `url` | ✅ shipped (commit `c0a3796`) | None |
| **W-02** | `switch` | ✅ click + verify aria-checked | None |
| **W-03** | `checkbox` | ✅ is_checked + click | None |
| **W-04** | native `<select>` | ✅ `select_option(label=)` | Async-loaded `<option>` rows time out at 0.5 s |
| **W-05** | `combobox` / `custom_select` | ✅ open → `[role=option]` → `_best_option_match` → click | Search-filtered combobox: no type-to-filter; aria-controls / aria-owns linkage not followed; dialog-rendered option containers missed |
| **W-06** | `radio_group` | ✅ same dropdown path | Visual-grid radio (Reed survey style — radio buttons in a 5×2 layout, no `[role=option]`) not handled |
| **W-07** | `multiselect` (multi-pick) | ⚠️ picks one and exits | Need to keep menu open and pick N |
| **W-08** | `tag_input` (chips) | ❌ none | type → comma → wait → repeat per tag |
| **W-09** | `rich_text_editor` (TipTap, Lexical, Quill, contenteditable) | ❌ none — `.fill()` returns success silently but editor stays empty | Need `pressSequentially()` or DOM textContent insertion + dispatch input event |
| **W-10** | `date_native` (`<input type=date>`) | 🟡 `date_filler.fill_date()` shipped, only wired into unified engine path | Wire into legacy NativeFormFiller dispatcher |
| **W-11** | `date_custom` (React date picker) | ❌ `date_filler.py` has no calendar-grid handler | Open picker → identify `[role=grid]`/`[role=gridcell]` → click cell whose `aria-label` parses as target date. Pure feature detection. |
| **W-12** | `range` slider / split-numeric pair | ❌ none — Revolut salary regression | Detect sibling input pair with `type=number` under common ancestor with `range`/`slider` class hints; split value on `-` to fill min + max |
| **W-13** | `search_autocomplete` (typeahead) | ⚠️ treated as combobox; only sees pre-rendered list | Type to filter, poll option count for ~3 s, click result |
| **W-14** | `file_upload` standard `input[type=file]` | ✅ `file_uploader.py` | None |
| **W-15** | `file_upload` drag-drop zone (React Dropzone, custom) | 🟡 partial — `file_uploader.py` re-fires input/change events but doesn't simulate drop event | When `set_input_files` finds nothing, dispatch a synthetic `drop` event with a `DataTransfer` object |
| **W-16** | `phone` country selector | 🟡 hardcoded `iti__` selectors in `_fill_special_widget` | Generalize via combobox path with country-list option scan |
| **W-17** | `readonly` | ✅ skip | None |

### 1B — Option-discovery (knowing what choices each field offers)

| ID | Path | Today | Gap |
|---|---|---|---|
| **O-01** | Native `<select>` | ✅ `el.options` array | None |
| **O-02** | Radio group | ✅ label text per radio | None |
| **O-03** | A11y tree per-element `optionTexts` | ✅ when CDP path fires | Closed comboboxes return empty options — answer-generation has no option visibility |
| **O-04** | Custom React select / combobox | ❌ scanner only sees options after open click during fill — **not before** the screening pipeline generates the answer | Pre-fill option scanning: open every closed combobox briefly during scan, capture option list, close. Cache per (domain, selector) in `FormExperienceDB`. Pass options into the screening LLM prompt — guaranteed alignment. |
| **O-05** | Async-loaded options | ❌ 0.5 s wait only | Poll option-count change up to 3 s; stop when stable |
| **O-06** | Search-filtered combobox | ❌ shows top-N initial only | If desired value not in initial list, type-filter + re-scan |
| **O-07** | aria-controls / aria-owns linkage | ❌ scanner looks for `[role=option]` descendants of trigger | Follow `aria-controls`/`aria-owns` to external listbox |
| **O-08** | Dialog-rendered options (`role=dialog` modal) | ❌ scanner scope wrong | Same fix — follow linkage, not containment |
| **O-09** | Multi-select current-state | ❌ no track of "what's already selected" | Read `aria-selected="true"` set, compute diff vs target |

### 1C — Field labels (knowing what to ask)

| ID | Source | Today | Gap |
|---|---|---|---|
| **L-01** | Real `<label>` element with `for=` | ✅ | None |
| **L-02** | Wrapping `<label>` parent | ✅ | None |
| **L-03** | aria-label / aria-labelledby | ✅ | None |
| **L-04** | Placeholder text leaking as label | ❌ — live regression: `"Type your answer here (textarea)"`, `"Type your answer here (text)"` reach `_fill_by_label` | Filter: if string starts with placeholder verb ("Type your", "Enter your"), demote — search for nearest preceding heading-like sibling instead |
| **L-05** | Browser extension UI | ❌ — live regression: `"Open Grammarly."` from Grammarly toolbar | Filter: discard fields whose ancestor element has class containing `gr-`, `grammarly-`, `lastpass-`, `1password-`, or whose `tabindex=-1` (extensions inject into focus path) |
| **L-06** | Nav buttons leaking into field net | ❌ — `"Back"`, `"Apply now"`, `"_unlabeled_0"` reached `_fill_by_label` | Scanner needs to drop tag-name `button`/`a` from field list (they're already in the buttons array) |
| **L-07** | Compound labels with required marker | ✅ `_strip_required_marker` | None |
| **L-08** | Question text above input (no DOM linkage) | 🟡 — semantic_scanner solves this | Already shipped in Plan A |

### 1D — Dynamic-Over-Hardcoded violations (regex / button-name lists)

| ID | File:Line | What | Replacement |
|---|---|---|---|
| **R-01** | `native_form_filler.py:2696-2698` | submit/next button-name lists | ✅ deleted in Plan D |
| **R-02** | `native_form_filler.py:2384` | `_is_submit_page` button names | ✅ deleted in Plan D |
| **R-03** | `form_engine/engine.py:444-468` | **Same hardcoded lists in the unified engine path** (`UNIFIED_FORM_ENGINE=true`) | Mirror Plan D fix into `FormFillEngine` |
| **R-04** | `_navigator.py:1341` | `("Apply now", "Apply for this job", "Start application", "Apply")` JD-page Apply finder | Consume reasoner's `target_text` (same pattern as Plan D); apply when reasoner outputs `click_apply` |
| **R-05** | `screening_answers.py:299, 538, 636, 655, 672, 752` | Regex screening-question routing | Embedding tier in `screening_intent.py` (already partial) — extend |
| **R-06** | `screening_decomposer.py:24, 32, 95, 121, 113, 208` | Regex compound-question detection | LLM classify with cache (decomposer already has cache scaffold) |
| **R-07** | `form_engine/consent_policy.py:34, 68, 89` | Regex consent/marketing/required matchers | Use `semantic_matcher.checkbox_intent()` (exists, unused here) |
| **R-08** | `email_preclassifier.py:203, 211, 231, 239, 276, 284` | Regex email body/subject classification | Embedding tier in `nlp_classifier.py` |
| **R-09** | `screening_detector.py:23` | Regex screening keyword detection | Embedding similarity |
| **R-10** | `dispatcher.py:279-598` | Regex command parsing | NLP classifier |
| **R-11** | `native_form_filler.py:1486-1529` | React-Select vendor class hints (`.select__control`, `[class*="select__control"]`, `.select__menu`, `.select__option`) | Replace with feature detection: `aria-haspopup="listbox"` + clickable + opens-on-click. Already covered structurally by W-05 work |
| **R-12** | `native_form_filler.py:1799-1856` | `_fill_special_widget` `iti__` phone selectors | Generalize via W-16 (combobox-route the country picker) |
| **R-13** | `navigation/overlay_dismisser.py:106` | Hardcoded cookie button text matchers (`"Accept"`, `"Allow"`, `"Agree"`, `"Continue"`) | Reasoner emits `dismiss_overlay` with `target_text` — consume that |
| **R-14** | `sso_handler.py:32, 119` | Regex `continue` / `Continue as <Name>` | Reasoner emits `sso_<provider>` action — consume `target_text` |
| **R-15** | `_fill_resolved_widget` line 1007 | Regex `^select\s*(one|an?\s*option)?$/i` for placeholder filtering | Borderline — structural noise filter, acceptable. Convert to `semantic_matcher.is_placeholder_option(text)` for consistency |

### 1E — Verification, learning, escalation gaps

| ID | What | Today | Gap |
|---|---|---|---|
| **V-01** | Per-fill readback | ✅ for text/select | Combobox/radio_group readback uses option-text comparison; should also verify `aria-selected="true"` on the clicked node |
| **V-02** | Whole-page diff before/after submit | ✅ `_snapshot_live_form_state` per page | Doesn't compare to expected — only captures |
| **V-03** | Cognitive-engine escalation visibility | ✅ shipped in Plan E + `c0a3796` | Engine prompt doesn't include current option list when applicable — give it more context |
| **V-04** | Engine plan-execution loop | ❌ engine returns ONE plan; if plan fails, no second attempt | After failure, re-call engine with the failure context as new input ("the previous selector returned 0 — try again") |
| **V-05** | DOM-signature capture loop | ✅ shipped in Plan C-4 | Only fires on confirm_application. Should also fire when `_escalate_fill` succeeds — already does in `c0a3796`. Need to verify it actually lands rows in `widget_patterns` |
| **V-06** | Cross-domain transfer | 🟡 `cross_platform_field_transfer.py` exists, partly wired | `field_meta` from one domain doesn't transfer signature to another even when label matches semantically |
| **V-07** | Forgotten-pattern decay | 🟡 `_forgetting.py` exists | `widget_patterns` table doesn't have a decay column — stale selectors live forever |

### 1F-X — Conditional visibility, validation, honeypots, iframes

| ID | What | Today | Gap |
|---|---|---|---|
| **C-01** | Conditionally-revealed fields after a fill (e.g., visa→country) | 🟡 `Post-fill rescan` loop (`native_form_filler.py:3798+`) detects new fields | Doesn't re-run `scan_combobox_options` (F2) on the new field; doesn't re-prompt screening pipeline for newly-revealed labels |
| **C-02** | Form rejects value (red border, error text) | 🟡 `form_engine/validation.py` exists, consumed only by unified engine | Wire into legacy `NativeFormFiller` after each page fill: if `VALIDATION_ERROR` rows present, route stuck fields through `_escalate_fill` with the error text included |
| **C-03** | Honeypot detection beyond `"honeypot"` substring | 🟡 `_apply_field_count_guard` filters labels containing `"honeypot"` | Detect by attribute: `display:none`, `visibility:hidden`, `aria-hidden=true`, off-screen positioning (`left: -9999px`), `tabindex=-1` on a fillable input |
| **C-04** | Iframe-nested forms beyond iCIMS | 🟡 `native_form_filler.py:365-373` reads iframe names from quirks; `playwright_driver.py:381` hardcodes `("icims_content_iframe",)` | Driver should read the same quirk source. Reasoner should detect iframe forms semantically — emit `route_through_iframe: <name>` action |
| **C-05** | Cross-frame option discovery | ❌ when widget is in main frame but opens an option list in an iframe modal (Greenhouse pattern) | Follow `aria-controls` across frame boundaries |

### 1F — Page-flow / navigation gaps (separate from D)

| ID | What | Today | Gap |
|---|---|---|---|
| **N-01** | Reasoner consumed for advance | ✅ Plan D | None |
| **N-02** | Reasoner consumed for JD-page Apply | ❌ — see R-04 | Plan F-1 |
| **N-03** | Reasoner consumed for overlay dismiss | ❌ — see R-13 | Plan F-1 |
| **N-04** | Reasoner consumed for SSO routing | ❌ — see R-14 | Plan F-1 |
| **N-05** | Multi-tab transitions | ✅ via `_phase_observe` | None |
| **N-06** | Step counter / progress indicator | ❌ — agent doesn't know "you're on step 3 of 5" | Reasoner could extract; useful guard against premature submit |

---

## 2 — The Plan

Six phases, ordered so each builds on the previous. Every gap above
maps to exactly one phase. Some phases land as one PR, some are
multiple subtasks; the column **Maps To** shows which gap-IDs each
subtask resolves.

### Phase F1 — Reasoner consumption everywhere (R-03, R-04, R-13, R-14, N-02..N-04)

Same pattern as Plan D, applied to every other place where strings
substitute for the reasoner.

- **F1-1** Mirror Plan D fix into `form_engine/engine.py:444-468`
  (`FormFillEngine`). Tests:
  `tests/jobpulse/form_engine/test_engine_uses_advance_button.py`.
- **F1-2** `_navigator.py:1341` JD-page Apply: replace string list
  with `target_text` from `PageAction(action="click_apply")`.
- **F1-3** `navigation/overlay_dismisser.py`: replace cookie-button
  string list with `PageAction(action="dismiss_overlay")` →
  `target_text`.
- **F1-4** `sso_handler.py`: replace `continue` regex with
  `PageAction(action="sso_<provider>")` → `target_text`.

Acceptance: `git grep -E '("Apply now"|"Submit Application"|"Apply"|"Continue")' jobpulse/`
returns only docstring/comment matches in non-fill code paths.

### Phase F2 — Pre-fill option scanning (O-03..O-09, V-04 input)

The single biggest semantic-correctness gap. Without this, Plan A's
matching is choosing from the wrong menu.

- **F2-1** Add `scan_combobox_options(page, field) → list[str]` in
  `form_engine/field_scanner.py`. For every detected combobox /
  custom_select / multiselect:
  1. Click to open
  2. Follow `aria-controls`/`aria-owns` to the option container
  3. Poll option count for stability (3 s budget)
  4. Capture option list
  5. Press Escape
  6. Cache per `(domain, selector)` in `FormExperienceDB`
- **F2-2** Wire into `scan_fields()` so every combobox-class field
  gets `options` populated before fill.
- **F2-3** Pass options into `screening_pipeline.generate_answer`
  prompt so the LLM picks from the actual offered set.
- **F2-4** Async-loaded native `<option>` rows: same polling loop.
- **F2-5** Search-filtered combobox: when `_best_option_match` returns
  nothing on the initial scan, type-filter with the desired value as
  query and re-scan.

Acceptance: live run on Revolut shows
`scan_options: 'Do you require visa sponsorship?' → ['Yes - I require sponsorship', 'No - I do not']`
in log; screening answer comes back with the exact phrasing.

### Phase F3 — Widget-type coverage extension (W-07..W-13, W-15, W-16)

**The dual-engine question first.** `form_engine/page_filler.py`
already routes by `InputType` to per-type fillers
(`text_filler`, `select_filler`, `multi_select_filler`,
`radio_filler`, `checkbox_filler`, `date_filler`). Two architecture
options:

- **Option A (preferred)** — wire `_fill_resolved_widget` to call
  these existing fillers by `input_type`. NativeFormFiller becomes a
  thin wrapper around `form_engine/`. Single source of truth.
- **Option B** — duplicate the per-type logic into
  `_fill_resolved_widget`. Faster to ship, but doubles the
  maintenance surface and the unified-engine path drifts from the
  legacy path.

Plan assumes **Option A**. Each F3-N becomes "wire existing filler X
into _fill_resolved_widget" + "fill the gap if filler is missing or
fake".

Done in priority order:

- **F3-1** Range slider / split numeric pair (W-12). New filler:
  `range_filler.py`. Detect via feature: parent contains exactly
  two `<input type=number>` with `aria-valuemin`/`aria-valuemax`
  attributes, or sibling inputs with `min`+`max` and a common
  bounding box. Split value on any non-digit separator; fill
  each input. No vendor class hints.
- **F3-2** Multi-pick `MULTI_SELECT` (W-07). `multi_select_filler.py`
  exists — verify it does N picks, extend if it does only 1.
- **F3-3** Tag input / chips (W-08). `multi_select_filler.fill_tag_input`
  exists. Verify and wire into legacy dispatcher.
- **F3-4** Rich-text editor (W-09). `page_filler.py:86` currently
  routes to `text_filler.fill_text` (a `.fill()` call — broken).
  New `rich_text_filler.py`: detect `contenteditable=true` via
  attribute (no vendor class hint); use `pressSequentially()` or
  DOM-tree text node insertion + dispatch `input` event. Verify
  via `el.innerText`.
- **F3-5** Native `<input type=date>` (W-10). `date_filler.fill_date`
  exists — wire into NativeFormFiller dispatcher.
- **F3-6** React date picker (W-11). Extend `date_filler.py` with
  calendar-grid path: open picker → find `[role=grid]` →
  click `[role=gridcell]` whose accessible name parses as ISO date
  via `dateutil.parser`.
- **F3-7** Drag-drop file upload (W-15). `file_uploader.py` already
  re-fires input/change events (commit `c3910a8`). Add a fallback:
  if input/change events don't trigger React state, synthesize a
  `drop` event on the dropzone with a `DataTransfer` containing the
  file blob.
- **F3-8** Phone country picker (W-16). Delete `_fill_special_widget`
  for the country case; route through the generic combobox path
  now that O-04 + W-05 cover it.

### Phase F4 — Field-label noise filter (L-04..L-06)

Live regression: garbage labels (`"Type your answer here"`,
`"Open Grammarly."`, `"Back"`, `"_unlabeled_0"`, `"Apply now"`)
reaching `_fill_by_label`.

- **F4-1** `field_scanner.py`: drop fields whose tagName is
  `button` or `a` — they belong in the buttons array.
- **F4-2** Discard fields whose ancestor element is **detectably
  injected by browser extensions**. Pure feature detection (no
  hardcoded namespace list):
  1. Element is descendant of `<body>` but its closest positioned
     ancestor uses `position: fixed` AND `z-index >= 2147483647`
     (extension overlays use the max int32 z-index)
  2. Element's tag is namespaced with a hyphen (custom element)
     AND not registered via `customElements.get()`
  3. Element's nearest container has Shadow DOM whose host is
     outside the form's flow box
  Each criterion is a behavioral signal of "injected by something
  that isn't the page", not a name-based heuristic.
- **F4-3** When `label` exactly matches the field's `placeholder`
  attribute, demote — search for nearest preceding heading-like
  sibling (`<label>`, `<h*>`, text node ≥ 12 chars before the
  input's bounding box). The semantic_scanner already has the
  primitive — reuse.
- **F4-4** Drop fields with auto-generated labels matching
  `_unlabeled_*` / `field_*` synthetic patterns produced by the
  scanner when label resolution fails — these should be elevated
  to escalation, not pretended to be real labels.

### Phase F5 — Regex purge (R-05..R-10, R-15)

Each item is one focused PR. Migration rule: when touching a file
that uses regex for classification, migrate THOSE patterns in the
same change.

- **F5-1** `screening_answers.py` regex routing → embedding similarity
  via `screening_intent.py` (already partial).
- **F5-2** `screening_decomposer.py` compound-question detection →
  LLM classify (engine has cache scaffold).
- **F5-3** `consent_policy.py` → `semantic_matcher.checkbox_intent()`.
- **F5-4** `email_preclassifier.py` body/subject regex → embedding
  tier in `nlp_classifier.py`.
- **F5-5** `screening_detector.py` keyword regex → embedding
  similarity.
- **F5-6** `dispatcher.py` command-parsing regex → NLP classifier.
- **F5-7** `_fill_resolved_widget` placeholder filter → `semantic_matcher.is_placeholder_option`.

### Phase F6 — Verification + learning hardening (V-01, V-04..V-07, N-06)

- **F6-1** Combobox/radio readback verifies `aria-selected="true"`
  on the clicked node, not just option-text equality.
- **F6-2** Engine plan-retry loop. After the first
  `_escalate_fill` plan fails, re-call engine with the failure
  context appended ("previous selector X returned 0 elements; the
  page now shows these buttons: [...]"). Cap at 3 retries per
  field.
- **F6-3** Verify `widget_patterns` rows actually land. Add a
  wiring test (`tests/jobpulse/test_widget_pattern_wiring_e2e.py`)
  that runs `_escalate_fill` against a fake page, asserts a row in
  `form_gotchas.db` after.
- **F6-4** Cross-domain transfer: when no domain-specific pattern
  exists, also query `widget_patterns` for *any* domain by label
  similarity (cosine ≥ 0.85). Score-weight by fix_count.
- **F6-5** Add `last_used_at` + decay: stale (>90 days, or 3+
  consecutive failures since last success) widget_patterns rows
  get a confidence penalty.
- **F6-6** Step indicator: extend the reasoner prompt to extract
  `step_position: "N of M" | unknown`. Block submit if N < M.

---

## 3 — Execution order

Strict ordering — each phase exposes a problem the next solves:

1. **F1** first (~1 day). Cleans the regex/string-list debt so
   downstream phases don't fight the same pattern. Three small
   surgical changes mirroring Plan D.
2. **F2** second (~2 days). Without correct options the dispatcher
   in F3 has nothing valid to choose from.
3. **F3** third (~2-3 days). Per-widget handlers, prioritized by
   live-regression coverage (range slider first, then date pickers,
   then multi-select-N, then rich-text editor, then drag-drop, then
   phone).
4. **F4** fourth (~half day). Fixes the noise loop so the agent
   stops wasting LLM/escalation budget on garbage labels.
5. **F5** fifth (~1-2 days). Pure regex purge across separate
   files. Could parallelize.
6. **F6** sixth (~1 day). Hardening and learning loop.

Total: roughly 7-10 days of focused work, validated by **one
end-to-end Revolut run after each phase**.

---

## 4 — Out of scope

- Vision-based form filling for cases where DOM is genuinely opaque
  (Cloudflare-rendered, Canvas-based forms). Today's Plan B handles
  the sparse-field case; pixel-perfect vision filling is a much
  bigger machine.
- Captcha solving. Already covered by the 6-stage security wall
  bypass; not a form-fill concern.
- Verification-wall classification literals in `playwright_driver.py`
  (Cloudflare/reCAPTCHA selectors). These are security-feature
  literals, not form-fill heuristics; the policy explicitly allows
  structural format detection.
- The `TERMINAL_ACTIONS` enum in `_navigator.py:178`. Not a
  user-input string list — it's the reasoner-output enum.

---

## 5 — Acceptance per phase

Each phase ships when:
- All new tests pass.
- One live Revolut welovealfa.com run reaches the screening page,
  fills toggles + dropdowns + range slider + cover-letter textarea
  without falling back to human bypass.
- `git grep -E "<gap-pattern>"` returns zero matches in the targeted
  files.
- Widget-pattern rows appear in `form_gotchas.db` for the corrected
  fields → second run fills them autonomously via Strategy 0
  (`_scan_learned_patterns`).

The session ends when phase F6 acceptance passes and a fresh
welovealfa.com run completes the form without any human bypass.
