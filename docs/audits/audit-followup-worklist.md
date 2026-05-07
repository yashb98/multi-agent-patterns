# Pipeline Audit — Follow-up Worklist

Aggregated list of issues found during the 11-subsystem audit that were
**not fixed in the audit session** (deferred majors, minors, nits, wiring
gaps, dead code). Use this as the action list once all 11 subsystems are
audited.

**Sort order within each subsystem:** majors → minors → nits → dead code.

**Status legend:**
- 🔴 deferred-major — real bug or rule violation, not yet fixed
- 🟡 minor — quality issue, fix when adjacent code is touched
- ⚪ nit — cosmetic, no functional impact
- 💀 dead — orphan / unreachable code
- 🔌 wiring — producer/consumer mismatch or missing consumer

---

## Subsystem 1 — `form_fill_dispatch` (`jobpulse/native_form_filler.py`)

Audit doc: `docs/audits/audit-form_fill_dispatch.md`
Commit that fixed the in-scope items: `1c36f16`

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-1.a | `native_form_filler.py:1024-1025` | `select_option` branch returns `value_verified=True` without readback. Borderline because Playwright validates option existence, but a stricter readback (compare `await el.evaluate("el => el.options[el.selectedIndex].text")` to expected) would catch ghost selects. | Lower-impact than M-1.c/d/e; revisit when touching the dispatcher. |
| 🔴 M-1.b | `native_form_filler.py:1538-1547` | `list_button_radio` (Oracle HCM) reports `value_verified=True` from `match.click()` JS return — no DOM readback. Ghost click goes unnoticed. | Needs Oracle HCM live access to test the readback strategy. |
| 🔴 M-3   | `native_form_filler.py:2346` | Hardcoded keyword skip: `if any(kw in test_id.lower() for kw in ("privacy", "consent", "agree"))`. Principle 8 violation; duplicates `consent_policy.checkbox_intent`. | Behavior change risk — current skip interacts with `check_consent` path; needs a brainstorming pass. |
| 🔴 M-4   | `native_form_filler.py:161-199` | `_resolve_dropdown_from_profile`: hardcoded substring patterns (`"require sponsorship"`, `"not requiring sponsorship"`, `"without sponsorship"`, `"obtain visa"`, `"permanent"`/`"citizen"`/`"settled"`) for visa-option classification. Plus bare `try / except: pass` at L197. | Wider redesign — replace with learned mapping or `consent_policy`-style intent layer. |
| 🔴 M-5   | `native_form_filler.py:1389-1392` | Inline regex for select-placeholder filtering DUPLICATES `_SELECT_PLACEHOLDER_RE` (L86) and the helper `_is_select_placeholder` (L92). | Pure cleanup; safe to do, low priority. |
| 🔴 M-6   | `native_form_filler.py:3119` | Workday fallback in `_click_navigation`: `if dry_run: return "dry_run_stop"` fires on Next clicks (not just Submit). Multi-page Workday dry-run terminates at page 1. | Needs Workday dry-run reproducer; small Plan D regression vector. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `native_form_filler.py:138-158` | `emit_form_fill_failures` logs failure path at `log.debug`; should be `log.warning` since OPRAL learning loop relies on these signals firing. |
| 🟡 m-2 | `native_form_filler.py:267-278` | `_classify_fill_failure` uses substring keyword routing on `result["error"]`. Borderline — internal-message classification, not user-input. |
| 🟡 m-3 | `native_form_filler.py:767-768` | `_escalate_fill` silently swallows `page.evaluate` failure for visible_buttons; engine then has no candidates → silent abort. |
| 🟡 m-4 | `native_form_filler.py:933-935` | `_escalate_fill` swallows `record_fix` failure; ai_assist learning misses the win. |
| 🟡 m-5 | `native_form_filler.py:1436` | `f'input[name="{name_attr}"]'` — f-string in CSS selector; quotes in `name_attr` would break it. Low risk (DOM-sourced) but hygiene. |
| 🟡 m-6 | `native_form_filler.py:1789` | `await el.fill("")` clears the field before retyping; if subsequent type fails, original pre-filled value is lost. |
| 🟡 m-7 | `native_form_filler.py:2576` | `page.locator(f"#{button_id}")` raw CSS-id interpolation; CSS-special chars in id break the selector. Use `[id="…"]`. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `native_form_filler.py:506`  | `_save_gotcha` bare `try/except` with debug log only. |
| ⚪ n-2 | `native_form_filler.py:971`  | `_fill_resolved_widget` `try/except: pass` on `_smart_scroll` without log. |
| ⚪ n-3 | `native_form_filler.py:1382` | `fill_technique = "direct_fill"` initialized but unread in many paths. |
| ⚪ n-4 | `native_form_filler.py:1429`, `1466-1468` | Hardcoded boolean truthy/falsy tuples (`("yes","true","on","agreed","1","y")`). Bounded sets, arguably safe; consolidate via `semantic_matcher.checkbox_intent`. |

### Dead code

| ID | Location | Description |
|---|---|---|
| 💀 d-1 | `native_form_filler.py:3193-3211` | `scan_current_values` — public method, **0 callers in repo**. Also accesses `f["locator"]` which the field scanner doesn't always populate (would `KeyError` on some inputs). Candidate for deletion. |

### Wiring gaps

None found in subsystem 1 — all signal/DB producer-consumer pairs verified
in STEP 4 of the audit doc (OptimizationEngine, FormExperienceDB,
JobDB.cache_answer, ScreeningOutcomeRecorder, ai_assist_logger all
consume what `native_form_filler` emits).

---

## Subsystem 2 — `form_fill_widgets` (`jobpulse/form_engine/*`)

*pending audit*

---

## Subsystem 3 — `navigation`

*pending audit*

---

## Subsystem 4 — `screening_pipeline`

*pending audit*

---

## Subsystem 5 — `post_apply`

*pending audit*

---

## Subsystem 6 — `cognitive_engine`

*pending audit*

---

## Subsystem 7 — `pre_screen`

*pending audit*

---

## Subsystem 8 — `materials`

*pending audit*

---

## Subsystem 9 — `scan_loop`

*pending audit*

---

## Subsystem 10 — `optimization_engine + memory_layer`

*pending audit*

---

## Subsystem 11 — `ats_adapters`

*pending audit*
