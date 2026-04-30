# Navigation Loop Redesign: 5-Phase Sequential Pipeline

**Date**: 2026-04-30
**Status**: Design
**Scope**: `jobpulse/application_orchestrator_pkg/_navigator.py` + `jobpulse/navigation_learner.py`

## Problem

The current navigation loop in `_navigator.py` has five structural gaps:

1. **Blind learned replay** (lines 146-199): Executes stored `{"page_type", "action"}` steps without verifying the current page matches. If the ATS changed its form, updated its DOM, or added a new step, the replay silently does the wrong thing.

2. **DOM classify short-circuits** (lines 220-226): At `dom_confidence >= 0.85`, returns `APPLICATION_FORM` or `CONFIRMATION` immediately without checking for overlays, verification walls, or errors. A page with a cookie banner on top of a form triggers the fast-path and skips dismissal.

3. **No proactive tab state checking**: `_handle_new_tabs()` only runs reactively after action execution (line 338). If a popup opens between steps, or a redirect happens without explicit action, it goes undetected until the next snapshot fails.

4. **No post-action verification**: After executing a click or fill, the code gets a fresh snapshot but never checks whether the action actually had an effect. Ghost clicks (overlay intercepts, element behind another element) proceed silently.

5. **Impoverished learned data**: Steps store only `{"page_type": "...", "action": "..."}` with no page fingerprint. There's nothing to score against when deciding whether a learned sequence still applies.

## Design: 5-Phase Sequential Pipeline

Every navigation step runs 5 phases in sequence, accumulating data in a `StepContext` dataclass:

```
OBSERVE → ANALYZE → MATCH → PLAN → ACT
```

Any page state change (new tab, popup, redirect, ghost click) results in the loop running a fresh full cycle. No phase is ever skipped.

### Architecture: Approach A (Sequential Pipeline)

Three approaches were evaluated:

- **A (chosen): Sequential Phase Pipeline** -- Each step runs all 5 phases as sequential method calls on `FormNavigator`, accumulating a `StepContext`. Simple, predictable, easy to debug and test. Post-ACT verification detects state changes; the main loop naturally re-runs a full cycle.

- **B (rejected): Reactive State Machine** -- Explicit states with Playwright event listeners triggering transitions. More responsive to async events but disproportionately complex for a loop that runs 5-10 steps max. Risk of event deduplication bugs and infinite restart loops.

- **C (rejected): Coroutine Pipeline with Event Interrupts** -- Sequential flow with background event monitor setting a restart flag. Subtle abort-and-restart bugs when events fire between PLAN and ACT.

## Data Model

### StepContext

Flows through all 5 phases. Each phase reads from and writes to it.

```python
@dataclass
class StepContext:
    # OBSERVE output
    snapshot: dict[str, Any]
    url: str
    tab_state: TabState  # enum: NORMAL, NEW_TAB, POPUP, CLOSED, REDIRECTED
    tab_recovered: bool = False

    # ANALYZE output
    dom_type: PageType = PageType.UNKNOWN
    dom_confidence: float = 0.0
    page_features: PageFeatures | None = None
    browser_signals: list[dict] | None = None
    overlays_detected: list[str] = field(default_factory=list)
    wall_detected: dict | None = None
    page_fingerprint: PageFingerprint | None = None

    # MATCH output
    learned_step: dict | None = None
    match_score: float = 0.0
    match_source: str = ""  # "domain", "platform", "content_hash", "none"

    # PLAN output
    planned_action: PageAction | None = None
    plan_source: str = ""  # "learned_verified", "reasoner", "fast_path"

    # ACT output (post-action)
    action_executed: bool = False
    post_snapshot: dict | None = None
    ghost_click: bool = False
```

### TabState

```python
class TabState(Enum):
    NORMAL = "normal"
    NEW_TAB = "new_tab"
    POPUP = "popup"
    CLOSED = "closed"
    REDIRECTED = "redirected"
```

### PageFingerprint

Enriched per-step data stored in NavigationLearner and used by MATCH for scoring.

```python
@dataclass
class PageFingerprint:
    field_count: int
    button_texts: tuple[str, ...]  # sorted, deduplicated, truncated to 20 chars each
    content_hash: str              # SHA256 of (url_path + page_text[:500] + field_labels + button_texts)
    has_dialog: bool
    has_file_inputs: bool
    page_type: str
    dom_confidence: float
    url_path_pattern: str          # path with numeric IDs replaced: /jobs/12345 -> /jobs/{id}
```

### Enriched Learned Step Schema

Stored in NavigationLearner's `steps` JSON blob. Backward compatible -- old steps without `fingerprint` key still load but cap at match score 0.4.

```json
{
  "page_type": "job_description",
  "action": "click_apply",
  "fingerprint": {
    "field_count": 0,
    "button_texts": ["Apply Now", "Save"],
    "content_hash": "a1b2c3d4e5f6",
    "has_dialog": false,
    "has_file_inputs": false,
    "dom_confidence": 0.92,
    "url_path_pattern": "/jobs/{id}"
  }
}
```

## Phase Details

### Phase 1: OBSERVE

Checks browser environment and auto-recovers from unexpected tab state.

1. Check browser context: how many tabs open, which is active
2. Detect state:
   - `NEW_TAB`: new page appeared -- switch to it, wait for `domcontentloaded`
   - `POPUP`: dialog/popup window -- capture content for ANALYZE
   - `REDIRECTED`: URL changed since last step without explicit navigation -- accept new URL
   - `CLOSED`: active page closed -- abort navigation
   - `NORMAL`: same page, proceed
3. On state != NORMAL: auto-recover (switch tab, accept redirect)
4. Re-inject BrowserIntelligence listeners on new/changed pages (`intelligence.clear()` + `inject_on_new_page()`)
5. Get fresh snapshot into StepContext

**Replaces**: `_handle_new_tabs()` which only ran post-action. Now runs proactively at the start of every step.

### Phase 2: ANALYZE

Full page understanding. Always runs, never short-circuited.

1. DOM classify via `PageTypeClassifier.classify(snapshot)` -- returns `(PageType, confidence)`
2. Extract `PageFeatures` via `classifier._extract_features(snapshot)` -- 18 features
3. Build `PageFingerprint` from features + snapshot (field_count, button_texts, content_hash, url_path_pattern, etc.)
4. Read BrowserIntelligence buffer for console errors, network failures, DOM mutations
5. Detect overlays: cookie banners, site prompts, session timeouts
6. Detect verification walls: Cloudflare, Turnstile, reCAPTCHA, hCaptcha
7. Dismiss non-blocking overlays immediately (cookies, site prompts) using existing `dismiss_cookie_banner_playwright()` + `_dismiss_site_prompt_if_present()`
8. If overlay was dismissed: re-snapshot + re-extract features (page changed)
9. Store all results into StepContext

**Replaces**: The `dom_confidence >= 0.85` fast-path that skipped overlay/wall detection. Confidence still matters but flows into PLAN for terminal decisions, not ANALYZE for short-circuiting.

### Phase 3: MATCH

Scores current page against learned navigation sequences.

1. Get learned sequence for domain via `NavigationLearner.get_sequence(domain)`
2. If no domain sequence: try `get_platform_pattern(platform)` then `get_sequence_by_content_hash()`
3. If sequence found:
   a. Determine step index from `len(steps)` already taken
   b. If step index exceeds sequence length, match_source = "none"
   c. Get the learned step at that index
   d. Compare current `PageFingerprint` vs learned step's fingerprint:

   | Feature | Weight | Scoring |
   |---------|--------|---------|
   | page_type match | 0.30 | Exact match = 1.0, else 0.0 |
   | content_hash match | 0.25 | Exact match = 1.0, else 0.0 |
   | field_count similarity | 0.15 | `1.0 - min(abs(diff) / 10, 1.0)` |
   | button_overlap | 0.15 | Jaccard similarity of button_texts sets |
   | url_pattern match | 0.15 | Exact match = 1.0, else 0.0 |

   e. `match_score` = weighted sum
4. If `match_score >= 0.7`: set `learned_step` in StepContext, `match_source` = lookup source
5. If `match_score < 0.7`: `match_source = "none"`, falls through to reasoner in PLAN
6. Old steps without `fingerprint` key: match on page_type only, capped at score 0.4 (always falls through)

### Phase 4: PLAN

Decides what action to take. Three paths in priority order.

**Path 1 -- Fast-path terminals** (from ANALYZE output, checked before MATCH/reasoner):
- `wall_detected` is truthy: `action = wait_human` (enters bypass pipeline in ACT). PLAN passes `wall_bypass_attempts` through to ACT for escalation decisions.
- `dom_type == CONFIRMATION` with `dom_confidence >= 0.8`: return done. Safe because ANALYZE already dismissed overlays before PLAN runs.
- `page_type == expired_job` (from reasoner or classifier): return abort

**Path 2 -- Learned path** (from MATCH output, `match_score >= 0.7`):
1. Verify learned action is executable on current page:
   - `click_apply`: is there an apply button in snapshot buttons?
   - `sso_*`: does `sso.detect_sso()` find the expected provider?
   - `fill_login` / `fill_signup`: are there password + email fields?
   - `verify_email`: are there email verification signals in page text?
2. If verification passes: use learned step as planned_action, `plan_source = "learned_verified"`
3. If verification fails: discard learned step, fall to Path 3

**Path 3 -- Reasoner path** (LLM semantic analysis):
1. Call `PageReasoner.reason_sync(snapshot)` -- returns `PageAction`
2. PageAction includes action type, field fills, overlays, advance button
3. Already cached per domain + content_hash (1hr TTL)
4. `plan_source = "reasoner"`

**Loop detection** (all paths):
- Track `visited_states[f"{page_type}:{action}"]` counter
- Same (page_type, action) pair seen 3 times: abort
- If reasoner returns confidence < 0.3 and visited_states shows 2+ repeated states: escalate to CognitiveEngine L1 (`domain="form_navigation"`)

### Phase 5: ACT

Executes the planned action and verifies it had an effect.

1. **Capture pre-action state**: url, content hash (`page_text[:300]` + `len(fields)` + `len(buttons)`), dialog state
2. **Execute action** (dispatch by action type -- terminal actions `fill_form`/`done`/`abort` are caught by the main loop before ACT runs and never reach this dispatch):
   - `click_apply` / `click_apply_guess`: `click_apply_button()` (existing)
   - `click_element`: `NavigationActionExecutor.execute()`
   - `fill_and_advance` / `login` / `signup`: `NavigationActionExecutor.execute()`
   - `dismiss_overlay` / `dismiss_dialog` / `accept_consent`: `NavigationActionExecutor.execute()`
   - `wait_human`: `_bypass_verification_wall()` 6-stage pipeline (existing). Uses `wall_bypass_attempts` counter: after 2 failed cycles on aggregator domains, escalates to `_try_platform_bypass()`.
   - `sso_*`: `sso.detect_sso()` + `sso.click_sso()` (existing)
   - `verify_email`: `auth.handle_email_verification()` (existing)
   - `go_back`: `page.go_back()` + wait for stable
3. **Wait for page settle**: 1s default, or adaptive timing from `FormExperienceDB` if available
4. **Post-action verification**:
   a. Get fresh snapshot
   b. Compute post-action content hash
   c. Compare pre vs post: URL changed? Content hash changed? Dialog appeared/disappeared?
   d. If nothing changed (ghost click):
      - Log warning with the failed action
      - If action was a click: retry once with `force=True`
      - If retry also ghosts: set `ghost_click = True`, emit `failure` signal to OptimizationEngine with `reason="ghost_click"`
   e. If page changed: clear BrowserIntelligence buffer, re-inject listeners
5. **Record step**: append enriched step (with `PageFingerprint`) to `steps` list
6. **Dismiss cookie banners** on new page state
7. **Store** `post_snapshot` in StepContext -- becomes next iteration's starting snapshot

## Main Loop Structure

```python
async def navigate_to_form(self, url, platform, steps, ...):
    # Initial navigation + snapshot (unchanged from current)
    snapshot = ...

    visited_states: dict[str, int] = {}
    wall_bypass_attempts = 0
    prev_url = snapshot.get("url", "")

    for step_idx in range(MAX_NAVIGATION_STEPS):
        ctx = StepContext(snapshot=snapshot, url=prev_url)

        # -- OBSERVE --
        ctx = await self._phase_observe(ctx)

        # -- ANALYZE --
        ctx = await self._phase_analyze(ctx)

        # -- MATCH --
        ctx = self._phase_match(ctx, domain, platform, len(steps))

        # -- PLAN --
        ctx = self._phase_plan(ctx, visited_states, wall_bypass_attempts)

        # Terminal states
        if ctx.planned_action and ctx.planned_action.action in TERMINAL_ACTIONS:
            return self._make_result(ctx)

        # -- ACT --
        ctx = await self._phase_act(ctx, platform, steps, wall_bypass_attempts)

        # Update wall bypass counter
        if ctx.planned_action and ctx.planned_action.action == "wait_human":
            wall_bypass_attempts += 1
        else:
            wall_bypass_attempts = 0

        # Next iteration uses post-action snapshot
        snapshot = ctx.post_snapshot or ctx.snapshot
        prev_url = snapshot.get("url", "")

    return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}
```

## Pipeline Component Wiring

| Component | Phase | Usage |
|---|---|---|
| BrowserIntelligence | OBSERVE, ANALYZE | OBSERVE re-injects listeners on new pages. ANALYZE reads signal buffer for console errors, network failures, DOM mutations. |
| PageTypeClassifier | ANALYZE | `classify(snapshot)` for (PageType, confidence) + `_extract_features()` for PageFeatures used in fingerprinting. |
| PageReasoner | PLAN | Called when MATCH score < 0.7 or learned step verification fails. Returns PageAction. Cached per domain+content_hash (1hr TTL). |
| NavigationLearner | MATCH, ACT | MATCH queries `get_sequence()` / `get_platform_pattern()` / `get_sequence_by_content_hash()`. ACT appends enriched steps with fingerprints. |
| NavigationActionExecutor | ACT | Executes PageAction: overlay dismissal, field fills, button clicks. Interface unchanged. |
| FormExperienceDB | OBSERVE, ACT | OBSERVE uses adaptive timing for page settle waits. ACT records navigation timing. |
| CognitiveEngine | PLAN | Escalation when reasoner confidence < 0.3 and 2+ repeated states. Domain: `form_navigation`. |
| OptimizationEngine | ACT | `save_sequence()` emits `adaptation` signal. `mark_failed()` emits `failure` signal. Ghost click detection emits `failure` with `reason="ghost_click"`. |
| OverlayDismisser | ANALYZE | Cookie banners and site prompts dismissed during ANALYZE before MATCH/PLAN see the page. |
| SSO Handler | ACT | `sso_*` actions delegate to `sso.detect_sso()` + `click_sso()`. |
| Auth Handler | ACT | Email verification delegates to `auth.handle_email_verification()`. |
| Verification Wall Bypass | ACT | `wait_human` action triggers existing `_bypass_verification_wall()` 6-stage pipeline. |
| Platform Bypass | ACT | After 2 failed wall bypass cycles on aggregator domains, tries `_try_platform_bypass()`. |

## Ghost Click Detection

```python
def _detect_ghost_click(pre_url, pre_content_hash, pre_dialog,
                        post_url, post_content_hash, post_dialog) -> bool:
    return (pre_url == post_url
            and pre_content_hash == post_content_hash
            and pre_dialog == post_dialog)
```

Content hash: SHA256 of `page_text_preview[:300]` + `str(len(fields))` + `str(len(buttons))`. Lightweight, sufficient to detect DOM changes.

On ghost click:
1. Log warning with the action that failed
2. If action was a click: retry once with `force=True`
3. If retry also ghosts: set `ghost_click = True`, emit failure signal, loop continues (PLAN will see the same page and try a different approach via fresh reasoner call)

## Changes vs Unchanged

### New code
- `StepContext`, `TabState`, `PageFingerprint` dataclasses (in `_navigator.py`)
- 5 phase methods on `FormNavigator`: `_phase_observe`, `_phase_analyze`, `_phase_match`, `_phase_plan`, `_phase_act`
- `build_page_fingerprint()` helper (extracts PageFingerprint from snapshot + PageFeatures)
- `score_fingerprint_match()` helper (compares two PageFingerprints, returns 0.0-1.0)
- `_detect_ghost_click()` static method
- `_make_result()` helper for terminal state returns
- Main `navigate_to_form()` loop rewritten to call phases sequentially

### Unchanged (same interface, called from new phases)
- `PageReasoner` -- called from PLAN
- `PageTypeClassifier` -- called from ANALYZE
- `NavigationActionExecutor` -- called from ACT
- `NavigationLearner` -- same DB schema, same methods, steps have richer JSON
- `_bypass_verification_wall()` -- called from ACT, same 6-stage pipeline
- `_try_platform_bypass()` -- called from ACT
- `click_apply_button()` -- called from ACT
- SSO, auth, cookie dismissal handlers -- same interfaces
- `BrowserIntelligence` -- OBSERVE calls `inject_on_new_page()` / `clear()`, ANALYZE reads buffer

### Removed
- Blind replay block (current lines 146-199) -- replaced by MATCH + verified PLAN
- `dom_confidence >= 0.85` fast-path short-circuit (lines 220-226) -- ANALYZE always runs full; PLAN uses confidence for terminal decisions
- `_reasoner_step()` helper (lines 701-714) -- logic absorbed into PLAN + ACT phases

## Backward Compatibility

- **NavigationLearner schema**: No table changes. Enrichment is in the steps JSON blob. Old steps without `fingerprint` key load fine; MATCH caps their score at 0.4 (below 0.7 threshold), so they fall through to the reasoner. On next successful run, the step gets re-saved with fingerprints.
- **External callers**: `navigate_to_form()` return type unchanged: `{"page_type": PageType, "snapshot": dict}` with optional `"expired"` and `"error"` keys.
- **Steps list**: Callers pass in a `steps: list[dict]` that gets enriched steps appended. Downstream consumers (`save_sequence()`) handle the extra `fingerprint` key transparently since they just `json.dumps(steps)`.

## Testing Strategy

- **Unit tests per phase**: Each `_phase_*` method tested independently with synthetic StepContext. Mock snapshot dicts, verify output fields.
- **MATCH scoring tests**: Known fingerprint pairs with expected scores. Verify threshold behavior (0.7 boundary), old-format fallback (capped at 0.4).
- **Ghost click detection tests**: Pre/post snapshots with various change combinations.
- **Integration test**: Full `navigate_to_form()` with mocked driver returning scripted snapshot sequences. Verify correct phase ordering, learned path vs reasoner fallback, terminal state returns.
- **Live test**: Real Playwright against a test form. Verify the full cycle fires, steps are enriched with fingerprints, NavigationLearner stores them.
