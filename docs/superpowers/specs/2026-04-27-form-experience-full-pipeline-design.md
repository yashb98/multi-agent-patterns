# Form Experience Full Pipeline — Wire Dead Infrastructure

**Date:** 2026-04-27
**Score target:** 7.0/10 → 9.5+/10
**Scope:** Wire existing FormExperienceDB methods to production callers, enable failure learning, cross-platform technique sharing, label persistence, and timing instrumentation.

## Problem Statement

FormExperienceDB has 6 tables with 20+ methods — but most are dead code. Production data after 26 applications across 11 domains:

| Table | Records | Callers | Status |
|---|---|---|---|
| `form_experience` | 11 | 1 (post_apply_hook) | Success-only |
| `fill_techniques` | 15 | 1 (NativeFormFiller) | Success-only, 2 platforms |
| `field_label_mappings` | 7 | 1 (NativeFormFiller) | iCIMS only |
| `form_failure_reasons` | **0** | **0** | Dead code |
| `page_timings` | **0** | 1 (NativeFormFiller) | Called but zeros |
| `container_selectors` | **0** | 1 (_result helper) | Success-only, empty |

### Root Causes

1. **`record_failure_reason()`** — zero callers. `_classify_fill_failure()` — zero callers.
2. **`get_platform_fill_techniques()`** — zero callers. Cross-domain learning never consumed.
3. **`validate_against_live()`** — zero production callers. Form drift detection unwired.
4. **`save_field_mappings()`** — zero callers. `_persist_label_mapping()` is a no-op lambda.
5. **`post_apply_hook`** — `if not success: return` on line 41. Failures teach nothing.
6. **`store_timing()`** — called but always `hydration_ms=0, transition_ms=0`.

## Design

### 1. Failure Recording Pipeline

**Wire `_classify_fill_failure()` + `record_failure_reason()` at 3 sites in NativeFormFiller.fill():**

**Site A — After LLM recovery fails (line ~1456, `still_failing` loop):**
```python
for item in still_failing:
    label = item["field"]["label"]
    failure_type = _classify_fill_failure(item["result"])
    try:
        fe_db.record_failure_reason(
            domain=page_url, platform=platform,
            failure_type=failure_type, field_label=label,
            selector=item["field"].get("selector", ""),
            details=item["result"].get("error", ""),
        )
    except Exception:
        pass
```

**Site B — After vision recovery fails (line ~1480, `final_failed_labels` loop):**
Same pattern as Site A but for items that failed both LLM and vision recovery.

**Site C — On stuck-page abort (line 1267):**
```python
try:
    fe_db.record_failure_reason(
        domain=page_url, platform=platform,
        failure_type="stuck_page", field_label="",
        details=f"Identical page fingerprint on page {page_num}",
    )
except Exception:
    pass
```

**Negative fill technique recording — `_fill_by_label()` else-branch (after line 660):**
```python
else:
    # Record failed technique so system avoids it next time
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        page_url = getattr(self._page, "url", "") or ""
        if page_url and fill_technique:
            FormExperienceDB().record_fill_technique(
                domain_or_url=page_url, field_label=label,
                field_type=f"{tag}:{input_type or role}",
                technique=fill_technique, value_used=fill_value,
                success=False,
            )
    except Exception:
        pass
```

**Files:** `native_form_filler.py` (~25 lines added at 3 failure sites + 1 negative technique site)

### 2. Post-Apply Hook Failure Learning

**Replace hard `return` on line 41 with a partial recording path.**

Current:
```python
if not result.get("success"):
    return
```

New:
```python
if not result.get("success"):
    # Still record partial experience — learn from failures
    try:
        exp_db = FormExperienceDB(db_path=form_exp_db_path)
        exp_db.record(
            domain=url,
            platform=job_context.get("ats_platform") or job_context.get("platform", "generic"),
            adapter="extension",
            pages_filled=result.get("pages_filled", 0),
            field_types=result.get("field_types", []),
            screening_questions=result.get("screening_questions", []),
            time_seconds=result.get("time_seconds", 0.0),
            success=False,
        )
        # Record structured failure reasons from agent_fill_stats
        stats = result.get("agent_fill_stats", {})
        for label in stats.get("failed_labels", []):
            exp_db.record_failure_reason(
                domain=url,
                platform=job_context.get("ats_platform") or job_context.get("platform", "generic"),
                failure_type="fill_failure",
                field_label=label,
                details=result.get("error", ""),
            )
    except Exception as exc:
        logger.warning("post_apply_hook: failure recording failed: %s", exc)
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit(
            signal_type="failure", source_loop="form_experience",
            domain=url, agent_name="form_filler",
            payload={"error": result.get("error", ""), "pages_reached": result.get("pages_filled", 0)},
            session_id=f"fe_fail_{company}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        )
    except Exception:
        pass
    return
```

**Files:** `post_apply_hook.py` (~20 lines replacing 1 line)

### 3. Cross-Platform Technique Sharing (Cold-Start Resolver)

**Problem at scale:** At 100 applications/day, ~90% of domains are new. Each cold start requires full LLM-powered form analysis. Platform-level techniques exist in the DB but are never queried.

**Wire platform technique fallback in `_fill_by_label()` (after line 546):**

Reuse `self._fe_db` (already initialized in `fill()`) instead of creating new instances:

```python
stored_technique = None
try:
    page_url = getattr(self._page, "url", "") or ""
    if page_url and self._fe_db:
        techniques = self._fe_db.get_fill_techniques(page_url)
        stored_technique = techniques.get(label, {}).get("technique")
        # Cold-start fallback: query platform-level if no domain-level
        if not stored_technique and self._platform:
            platform_techniques = self._fe_db.get_platform_fill_techniques(self._platform)
            field_type_prefix = f"{tag}:{input_type or role}"
            for pt in platform_techniques:
                if pt["field_type"] == field_type_prefix and pt["success"]:
                    stored_technique = pt["technique"]
                    break
except Exception:
    pass
```

Note: `self._platform` must be stored during `fill()` (add `self._platform = platform` near line 1160) so `_fill_by_label()` can access it for platform-level queries.

**Wire `validate_against_live()` inside the fill loop (page 1 only), reusing the existing scan:**

The fill loop already calls `_scan_fields()` at line 1250. Instead of double-scanning, validate_against_live runs on the first page's scan results to decide whether to trust the fast path:

```python
# Inside the fill loop, after line 1277 (field_types collection), page 1 only:
if page_num == 1 and exp and exp.get("success"):
    validation = self._fe_db.validate_against_live(
        url, seen_field_types, live_page_count=None,
    )
    if validation["trusted"]:
        self._known_domain = True
        logger.info("FAST PATH: domain %s validated (%.0f%% match, %d prior applies)",
                     FormExperienceDB.normalize_domain(url),
                     validation["match_ratio"] * 100,
                     exp.get("apply_count", 0))
    else:
        self._known_domain = False
        logger.warning("DRIFT DETECTED on %s — match %.0f%%, diverged: %s. Using full LLM path.",
                        FormExperienceDB.normalize_domain(url),
                        validation["match_ratio"] * 100,
                        validation["diverged_fields"][:5])
```

This replaces the current unconditional `self._known_domain = True` assignment (line 1195). The initial `self._known_domain = True` is removed and instead set by the validation result on page 1.

**Files:** `native_form_filler.py` (~20 lines: platform fallback + validate_against_live)

### 4. Label Mapping Persistence

**Replace no-op lambda in `field_resolver.py` line 732:**

Current:
```python
_persist_label_mapping = lambda label, key: None  # No-op in old API
```

New:
```python
def _persist_label_mapping(label: str, profile_key: str) -> None:
    """Persist learned label→profile_key to FormExperienceDB for cross-session reuse."""
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        FormExperienceDB().save_field_mappings("_global", {label: profile_key})
    except Exception:
        pass
```

Uses `_global` as the domain key for universal mappings (e.g., "first name" → "first_name"). Domain-specific mappings are stored separately by `save_field_mappings(domain, {...})` when called from domain-aware contexts.

**Also wire `_load_domain_field_mappings()` in NativeFormFiller to load `_global` mappings:**

Currently `_load_domain_field_mappings()` (line 156) only queries the page URL domain. Add a second query for `_global` to pick up universal mappings:

```python
def _load_domain_field_mappings(self):
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        url = getattr(self._page, 'url', '') or ''
        if url:
            db = FormExperienceDB()
            # Domain-specific mappings (highest priority)
            self._domain_field_mappings = db.get_field_mappings(url)
            # Universal mappings (lower priority, don't overwrite domain-specific)
            global_mappings = db.get_field_mappings("_global")
            for label, key in global_mappings.items():
                self._domain_field_mappings.setdefault(label, key)
    except Exception:
        self._domain_field_mappings = {}
```

**Files:** `form_engine/field_resolver.py` (5 lines replacing 1 line) + `native_form_filler.py` (~3 lines in `_load_domain_field_mappings`)

### 5. Timing Instrumentation

**Capture real hydration and transition times in NativeFormFiller.fill().**

**Hydration (time from page load to fields scannable):**
```python
t_hydration_start = time.monotonic()
fields = await self._scan_fields()
hydration_ms = int((time.monotonic() - t_hydration_start) * 1000)
```

Measured on every page, stored as running average.

**Transition (time from Next click to next page ready):**
```python
t_transition_start = time.monotonic()
# ... existing page transition logic (click Next/Continue) ...
transition_ms = int((time.monotonic() - t_transition_start) * 1000)
```

**Store after fill loop completes (replace the current page-1-only store_timing call):**
```python
if page_timings_list:
    avg_hydration = sum(h for h, _, _ in page_timings_list) // len(page_timings_list)
    avg_fill = int((time.monotonic() - t0) * 1000) // max(page_num, 1)
    avg_transition = sum(t for _, _, t in page_timings_list) // max(len(page_timings_list) - 1, 1)
    try:
        FormExperienceDB().store_timing(page_url, avg_hydration, avg_fill, avg_transition)
    except Exception:
        pass
```

**Files:** `native_form_filler.py` (~15 lines: timing vars + measurement + final store)

## Testing Strategy

**All tests use real production URLs, real field labels, real techniques, and real platform data.** No synthetic data.

Tests use `tmp_path` for DB isolation per project testing rules. Fixtures seed DBs with exact production snapshots.

### Shared Fixture: `seeded_exp_db`

```python
@pytest.fixture
def seeded_exp_db(tmp_path):
    """Seed FormExperienceDB with real production data snapshot."""
    db = FormExperienceDB(str(tmp_path / "form_experience.db"))

    # Real domains from production (all 11)
    db.record("job-boards.greenhouse.io", "greenhouse", "extension",
              pages_filled=2,
              field_types=["text:first_name", "text:last_name", "text:email",
                           "combobox:country", "combobox:do_you_hold_the_right_to_work"],
              screening_questions=["Do you hold the right to work in the UK?:Graduate Visa"],
              time_seconds=94.0, success=True)

    db.record("linkedin.com", "linkedin", "extension",
              pages_filled=3,
              field_types=["text:first_name", "text:last_name", "select:phone_country_code",
                           "select:email_address"],
              screening_questions=[], time_seconds=120.0, success=True)

    db.record("careers.snowflake.com", "workday", "extension",
              pages_filled=1,
              field_types=["text:first_name", "text:last_name", "text:email",
                           "combobox:country", "multiselect:skills"],
              screening_questions=[], time_seconds=20.0, success=True)

    db.record("jobs.smartrecruiters.com", "smartrecruiters", "extension",
              pages_filled=2,
              field_types=["text:first_name", "text:last_name", "combobox:city",
                           "combobox:gender", "radio:disability"],
              screening_questions=["Do you require a visa?:No"],
              time_seconds=35.0, success=True)

    db.record("jobs.ashbyhq.com", "ashby", "extension",
              pages_filled=1,
              field_types=["text:first_name", "text:email", "file:resume",
                           "radio:work_authorization"],
              screening_questions=[], time_seconds=45.0, success=True)

    db.record("experienced-arm.icims.com", "icims", "extension",
              pages_filled=1,
              field_types=["text:PersonProfileFields.FirstName",
                           "text:PersonProfileFields.LastName",
                           "text:PersonProfileFields.Email"],
              screening_questions=[], time_seconds=120.0, success=True)

    db.record("expedia.wd108.myworkdayjobs.com", "workday", "extension",
              pages_filled=5,
              field_types=["text:first_name", "text:last_name", "combobox:country",
                           "multiselect:skills", "textarea:cover_letter"],
              screening_questions=["Salary expectations:35000-42000"],
              time_seconds=600.0, success=True)

    db.record("jobs.asos.com", "icims", "extension",
              pages_filled=1,
              field_types=["text:first_name", "text:email"],
              screening_questions=[], time_seconds=25.0, success=True)

    db.record("uk.linkedin.com", "linkedin", "extension",
              pages_filled=0,
              field_types=[], screening_questions=[],
              time_seconds=0.0, success=True)

    db.record("job-boards.eu.greenhouse.io", "greenhouse", "extension",
              pages_filled=1,
              field_types=["text:first_name", "text:email", "combobox:country"],
              screening_questions=[], time_seconds=32.2, success=True)

    # Real fill techniques from production
    db.record_fill_technique("job-boards.greenhouse.io", "Country",
                             "combobox:combobox", "combobox_prescanned_match",
                             "United Kingdom", success=True)
    db.record_fill_technique("job-boards.greenhouse.io", "First Name",
                             "input:text", "direct_fill", "Yash", success=True)
    db.record_fill_technique("job-boards.greenhouse.io", "Email",
                             "input:text", "direct_fill",
                             "bishnoiyash274@gmail.com", success=True)
    db.record_fill_technique("job-boards.greenhouse.io",
                             "How did you hear about this job?",
                             "combobox:combobox", "combobox_type_to_search",
                             "LinkedIn", success=True)
    db.record_fill_technique("job-boards.greenhouse.io",
                             "What is your current notice period?",
                             "combobox:combobox", "combobox_prescanned_match",
                             "1 month", success=True)
    db.record_fill_technique("linkedin.com", "First name",
                             "input:text", "direct_fill", "Yash", success=True)
    db.record_fill_technique("linkedin.com", "Last name",
                             "input:text", "direct_fill", "Bishnoi", success=True)
    db.record_fill_technique("linkedin.com", "Email address",
                             "select:select", "select_option",
                             "bishnoiyash274@gmail.com", success=True)
    db.record_fill_technique("linkedin.com", "Phone country code",
                             "select:select", "select_option",
                             "+44", success=True)

    # Real label mappings from production
    db.save_field_mappings("experienced-arm.icims.com", {
        "PersonProfileFields.FirstName": "first_name",
        "PersonProfileFields.LastName": "last_name",
        "PersonProfileFields.Email": "email",
        "-1_PersonProfileFields.PhoneNumber": "phone",
        "-1_PersonProfileFields.AddressStreet1": "address",
        "-1_PersonProfileFields.AddressCity": "location",
        "-1_PersonProfileFields.AddressZip": "postcode",
    })

    return db
```

### Test Cases

**T1: Failure recording from fill pipeline**
- Seed DB with `job-boards.greenhouse.io` success data
- Simulate a fill failure: call `record_failure_reason("job-boards.greenhouse.io", "greenhouse", "no_field", field_label="Sponsorship status")`
- Verify `get_failure_reasons("job-boards.greenhouse.io")` returns the failure with correct type
- Verify `get_platform_failure_stats("greenhouse")` aggregates correctly

**T2: Negative fill technique recording**
- Record a failed technique: `record_fill_technique("job-boards.greenhouse.io", "Country", "combobox:combobox", "combobox_type_to_search", "UK", success=False)`
- Verify `get_fill_techniques("job-boards.greenhouse.io")` still returns the successful technique (success filter)
- Verify raw query shows both success and failure records

**T3: Post-apply hook failure path**
- Call `post_apply_hook(result={"success": False, "pages_filled": 1, "field_types": ["text:first_name"], "error": "stuck_page", "agent_fill_stats": {"failed_labels": ["Sponsorship"]}}, job_context={...}, form_exp_db_path=str(tmp_path / "fe.db"))`
- Verify form_experience recorded with `success=0`
- Verify failure_reasons recorded for "Sponsorship"

**T4: Cross-platform technique suggestion**
- Seed with Greenhouse techniques (Country → `combobox_prescanned_match`)
- Query `get_platform_fill_techniques("greenhouse")` for a new Greenhouse domain
- Verify it returns the Country technique from the known domain

**T5: validate_against_live — trusted**
- Seed `job-boards.greenhouse.io` with known field types
- Call `validate_against_live("job-boards.greenhouse.io", live_field_types=[matching fields])`
- Verify `trusted=True`, `match_ratio >= 0.8`

**T6: validate_against_live — drift detected**
- Same seed, but pass divergent live_field_types (completely different fields)
- Verify `trusted=False`, `diverged_fields` populated

**T7: Label mapping persistence**
- Call the new `_persist_label_mapping("first name", "first_name")` with DB path override
- Verify `get_field_mappings("_global")` returns `{"first name": "first_name"}`

**T8: Timing instrumentation**
- Call `store_timing("job-boards.greenhouse.io", hydration_ms=150, fill_ms=3000, transition_ms=800)`
- Call again with different values
- Verify `get_timing("job-boards.greenhouse.io")` returns running averages
- Verify `sample_count=2`

**T9: Success data never overwritten by failure (existing invariant)**
- Seed with successful `linkedin.com` record
- Call `record(domain="linkedin.com", ..., success=False)`
- Verify the stored record still has `success=1` (existing protection in `record()` method)

**T10: Platform aggregate with real multi-domain data**
- Seed with all 11 production domains
- Call `get_platform_aggregate("greenhouse")` — verify it aggregates across `job-boards.greenhouse.io` and `job-boards.eu.greenhouse.io`
- Verify `observation_count=2`, avg fields/pages/time calculated correctly

## Files Changed Summary

| File | Change | ~Lines |
|---|---|---|
| `jobpulse/native_form_filler.py` | Failure recording (3 sites), negative technique, platform fallback, validate_against_live, timing, `_platform` attr, `_global` mapping load | +65 |
| `jobpulse/post_apply_hook.py` | Failure learning path (replace hard return) | +20 |
| `jobpulse/form_engine/field_resolver.py` | Replace no-op lambda with real persistence | +5 |
| `tests/jobpulse/test_form_experience_pipeline.py` | 10 integration tests with real production data | +150 |
| **Total** | | **~240** |

## No Schema Changes

All 6 tables and all 20+ methods already exist in `form_experience_db.py`. Zero changes to the DB layer. This is purely a wiring exercise.

## Success Criteria

After implementation, the form experience system should:
1. Record failure reasons on every failed fill (verified by non-zero `form_failure_reasons` count after a failed apply)
2. Share techniques across domains on the same platform (verified by cold-start domain getting technique suggestion)
3. Detect form drift before trusting the fast path (verified by validate_against_live integration)
4. Persist label mappings across sessions (verified by `_global` mappings in `field_label_mappings`)
5. Capture real hydration/transition timing (verified by non-zero values in `page_timings`)
6. Learn partial experience from failed applications (verified by post_apply_hook recording on `success=False`)

**Target score: 9.5+/10** — all infrastructure wired, both positive and negative learning signals, cross-platform intelligence for cold starts, real timing data, and form drift detection.
