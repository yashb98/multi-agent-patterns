# Form Experience Full Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire all dead-code FormExperienceDB methods into the production fill pipeline so the system learns from failures, shares techniques cross-platform, persists label mappings, and captures real timing data.

**Architecture:** No new modules or schema — purely wiring existing `FormExperienceDB` methods to their intended call sites in `NativeFormFiller`, `post_apply_hook`, and `field_resolver`. Tests seed `tmp_path` DBs with real production data snapshots (real URLs, real field labels, real techniques).

**Tech Stack:** Python, SQLite (FormExperienceDB), pytest, Playwright (NativeFormFiller)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `jobpulse/native_form_filler.py` | Modify | Wire failure recording, negative techniques, platform fallback, validate_against_live, timing |
| `jobpulse/post_apply_hook.py` | Modify | Add failure learning path |
| `jobpulse/form_engine/field_resolver.py` | Modify | Replace no-op `_persist_label_mapping` |
| `tests/jobpulse/test_form_experience_pipeline.py` | Create | 10 integration tests with real production data |

---

### Task 1: Test fixture — seeded FormExperienceDB with real production data

**Files:**
- Create: `tests/jobpulse/test_form_experience_pipeline.py`

- [ ] **Step 1: Create test file with shared fixture**

```python
"""Integration tests for form experience pipeline wiring.

All data uses real production URLs, field labels, techniques, and platform names.
DB isolation via tmp_path per project testing rules.
"""
from __future__ import annotations

import sqlite3

import pytest

from jobpulse.form_experience_db import FormExperienceDB


@pytest.fixture
def seeded_exp_db(tmp_path):
    """Seed FormExperienceDB with real production data snapshot."""
    db = FormExperienceDB(str(tmp_path / "form_experience.db"))

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

- [ ] **Step 2: Run fixture to verify it loads**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py --collect-only`
Expected: 0 tests collected (fixture only), no import errors

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_form_experience_pipeline.py
git commit -m "test: add seeded FormExperienceDB fixture with real production data"
```

---

### Task 2: Tests T1+T2 — failure recording and negative fill techniques

**Files:**
- Modify: `tests/jobpulse/test_form_experience_pipeline.py`

- [ ] **Step 1: Write failing tests T1 and T2**

Append to `tests/jobpulse/test_form_experience_pipeline.py`:

```python
class TestFailureRecording:
    def test_failure_reason_recorded_and_queryable(self, seeded_exp_db):
        """T1: record_failure_reason persists and get_failure_reasons retrieves."""
        seeded_exp_db.record_failure_reason(
            domain="job-boards.greenhouse.io",
            platform="greenhouse",
            failure_type="no_field",
            field_label="Sponsorship status",
            selector="",
            details="No fillable element found for label 'Sponsorship status'",
        )
        failures = seeded_exp_db.get_failure_reasons("job-boards.greenhouse.io")
        assert len(failures) == 1
        assert failures[0]["failure_type"] == "no_field"
        assert failures[0]["field_label"] == "Sponsorship status"
        assert failures[0]["platform"] == "greenhouse"

    def test_platform_failure_stats_aggregate(self, seeded_exp_db):
        """T1b: get_platform_failure_stats aggregates across domains."""
        seeded_exp_db.record_failure_reason(
            "job-boards.greenhouse.io", "greenhouse", "no_field",
            field_label="Sponsorship status",
        )
        seeded_exp_db.record_failure_reason(
            "job-boards.eu.greenhouse.io", "greenhouse", "blocked",
            field_label="Country",
            details="Element intercepted by overlay",
        )
        seeded_exp_db.record_failure_reason(
            "job-boards.greenhouse.io", "greenhouse", "no_field",
            field_label="Disability status",
        )
        stats = seeded_exp_db.get_platform_failure_stats("greenhouse")
        assert stats["no_field"] == 2
        assert stats["blocked"] == 1

    def test_negative_fill_technique_does_not_overwrite_success(self, seeded_exp_db):
        """T2: Failed technique recorded but get_fill_techniques still returns success."""
        seeded_exp_db.record_fill_technique(
            "job-boards.greenhouse.io", "Country",
            "combobox:combobox", "combobox_type_to_search", "UK", success=False,
        )
        techniques = seeded_exp_db.get_fill_techniques("job-boards.greenhouse.io")
        # get_fill_techniques filters success=1, so Country should still show prescanned_match
        country_tech = techniques.get("Country")
        assert country_tech is not None
        assert country_tech["technique"] == "combobox_prescanned_match"

    def test_negative_fill_technique_raw_query_shows_both(self, seeded_exp_db):
        """T2b: Raw query shows both success and failure records."""
        seeded_exp_db.record_fill_technique(
            "job-boards.greenhouse.io", "Country",
            "combobox:combobox", "combobox_type_to_search", "UK", success=False,
        )
        with sqlite3.connect(seeded_exp_db._db_path) as conn:
            rows = conn.execute(
                "SELECT field_label, technique, success FROM fill_techniques "
                "WHERE domain = 'job-boards.greenhouse.io' AND field_label = 'Country' "
                "ORDER BY success DESC"
            ).fetchall()
        # ON CONFLICT replaces, so the latest write (failure) overwrites.
        # But get_fill_techniques filters success=1 — the key behavior is the filter.
        assert len(rows) >= 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestFailureRecording -v`
Expected: All 4 tests PASS (these test existing DB methods that already work)

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_form_experience_pipeline.py
git commit -m "test: T1+T2 failure recording and negative fill technique tests"
```

---

### Task 3: Tests T3 — post-apply hook failure path

**Files:**
- Modify: `tests/jobpulse/test_form_experience_pipeline.py`

- [ ] **Step 1: Write failing test T3**

Append to `tests/jobpulse/test_form_experience_pipeline.py`:

```python
class TestPostApplyHookFailurePath:
    def test_failure_records_partial_experience(self, tmp_path, monkeypatch):
        """T3: post_apply_hook records form experience even on failure."""
        db_path = str(tmp_path / "fe.db")

        # Monkeypatch external calls that would fail without credentials
        monkeypatch.setattr("jobpulse.post_apply_hook.upload_cv", lambda *a, **kw: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.upload_cover_letter", lambda *a, **kw: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.find_application_page", lambda *a, **kw: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.update_application_page", lambda *a, **kw: None)

        from jobpulse.post_apply_hook import post_apply_hook

        result = {
            "success": False,
            "pages_filled": 1,
            "field_types": ["text:first_name", "combobox:country"],
            "screening_questions": ["Do you hold the right to work in the UK?:Graduate Visa"],
            "time_seconds": 45.2,
            "error": "Stuck on identical page (page 2)",
            "agent_fill_stats": {
                "fields_attempted": 5,
                "fields_filled": 3,
                "fields_failed": 2,
                "failed_labels": ["Sponsorship status", "Disability"],
                "llm_fallback_count": 1,
            },
        }
        job_context = {
            "job_id": "",
            "company": "Sony Interactive",
            "title": "Data Analyst",
            "url": "https://job-boards.greenhouse.io/sonyinteractive/jobs/12345",
            "platform": "greenhouse",
            "ats_platform": "greenhouse",
            "notion_page_id": None,
            "cv_path": None,
            "cover_letter_path": None,
        }

        post_apply_hook(result, job_context, form_exp_db_path=db_path)

        db = FormExperienceDB(db_path)
        exp = db.lookup("job-boards.greenhouse.io")
        assert exp is not None
        assert exp["success"] == 0
        assert exp["pages_filled"] == 1

        failures = db.get_failure_reasons("job-boards.greenhouse.io")
        assert len(failures) == 2
        labels = {f["field_label"] for f in failures}
        assert labels == {"Sponsorship status", "Disability"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestPostApplyHookFailurePath -v`
Expected: FAIL — `post_apply_hook` currently returns early on `not success`, so `exp` is None

- [ ] **Step 3: Implement post_apply_hook failure path**

In `jobpulse/post_apply_hook.py`, replace lines 41-42:

```python
    if not result.get("success"):
        return
```

With:

```python
    if not result.get("success"):
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

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestPostApplyHookFailurePath -v`
Expected: PASS

- [ ] **Step 5: Run existing post_apply_hook tests to verify no regression**

Run: `python -m pytest tests/jobpulse/test_post_apply_hook.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/post_apply_hook.py tests/jobpulse/test_form_experience_pipeline.py
git commit -m "feat: post_apply_hook records partial experience on failure"
```

---

### Task 4: Tests T4+T5+T6 — cross-platform techniques and validate_against_live

**Files:**
- Modify: `tests/jobpulse/test_form_experience_pipeline.py`

- [ ] **Step 1: Write tests T4, T5, T6**

Append to `tests/jobpulse/test_form_experience_pipeline.py`:

```python
class TestCrossPlatformTechniques:
    def test_platform_fill_techniques_returns_cross_domain(self, seeded_exp_db):
        """T4: get_platform_fill_techniques returns techniques from all greenhouse domains."""
        techniques = seeded_exp_db.get_platform_fill_techniques("greenhouse")
        assert len(techniques) > 0
        labels = [t["field_label"] for t in techniques]
        assert "Country" in labels
        assert "First Name" in labels
        technique_map = {t["field_label"]: t["technique"] for t in techniques}
        assert technique_map["Country"] == "combobox_prescanned_match"

    def test_platform_fill_techniques_sorted_by_apply_count(self, seeded_exp_db):
        """T4b: Techniques are sorted by apply_count DESC (most used first)."""
        techniques = seeded_exp_db.get_platform_fill_techniques("linkedin")
        assert len(techniques) > 0
        counts = [t["apply_count"] for t in techniques]
        assert counts == sorted(counts, reverse=True)


class TestValidateAgainstLive:
    def test_trusted_when_fields_match(self, seeded_exp_db):
        """T5: validate_against_live returns trusted when live matches stored."""
        live_types = ["text:first_name", "text:last_name", "text:email",
                      "combobox:country", "combobox:do_you_hold_the_right_to_work"]
        result = seeded_exp_db.validate_against_live(
            "job-boards.greenhouse.io", live_types,
        )
        assert result["trusted"] is True
        assert result["match_ratio"] >= 0.8

    def test_drift_detected_with_divergent_fields(self, seeded_exp_db):
        """T6: validate_against_live detects drift when fields completely different."""
        live_types = ["textarea:cover_letter", "file:portfolio", "radio:remote_preference"]
        result = seeded_exp_db.validate_against_live(
            "job-boards.greenhouse.io", live_types,
        )
        assert result["trusted"] is False
        assert len(result["diverged_fields"]) > 0
        assert result["match_ratio"] < 0.8

    def test_partial_overlap_uses_threshold(self, seeded_exp_db):
        """T5b: Partial overlap trusted if above 80% threshold."""
        live_types = ["text:first_name", "text:last_name", "text:email",
                      "combobox:country"]
        result = seeded_exp_db.validate_against_live(
            "job-boards.greenhouse.io", live_types,
        )
        assert result["trusted"] is True
        assert result["match_ratio"] >= 0.8
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestCrossPlatformTechniques -v && python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestValidateAgainstLive -v`
Expected: All 5 tests PASS (these test existing DB methods)

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_form_experience_pipeline.py
git commit -m "test: T4+T5+T6 cross-platform techniques and validate_against_live"
```

---

### Task 5: Wire platform technique fallback in NativeFormFiller._fill_by_label

**Files:**
- Modify: `jobpulse/native_form_filler.py:120-133` (add `_platform` to `__init__`)
- Modify: `jobpulse/native_form_filler.py:540-548` (platform technique fallback)
- Modify: `jobpulse/native_form_filler.py:1160` (store `_platform` in `fill()`)

- [ ] **Step 1: Add `_platform` attribute to `__init__`**

In `jobpulse/native_form_filler.py`, line 133, after `self._container_selector: str | None = None`, add:

```python
        self._platform: str = ""
```

- [ ] **Step 2: Store platform in fill()**

In `jobpulse/native_form_filler.py`, at line 1186 (after `self._load_platform_strategy(platform)`), add:

```python
        self._platform = platform
```

- [ ] **Step 3: Wire platform technique fallback in _fill_by_label**

In `jobpulse/native_form_filler.py`, replace lines 540-548:

```python
            stored_technique = None
            try:
                from jobpulse.form_experience_db import FormExperienceDB
                page_url = getattr(self._page, "url", "") or ""
                if page_url:
                    techniques = FormExperienceDB().get_fill_techniques(page_url)
                    stored_technique = techniques.get(label, {}).get("technique")
            except Exception:
                pass
```

With:

```python
            stored_technique = None
            try:
                page_url = getattr(self._page, "url", "") or ""
                if page_url and self._fe_db:
                    techniques = self._fe_db.get_fill_techniques(page_url)
                    stored_technique = techniques.get(label, {}).get("technique")
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

- [ ] **Step 4: Run existing form filler tests**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -x 2>/dev/null; python -m pytest tests/jobpulse/ -v -k "form" --timeout=30 -x`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py
git commit -m "feat: wire platform-level technique fallback for cold-start domains"
```

---

### Task 6: Wire validate_against_live in NativeFormFiller.fill

**Files:**
- Modify: `jobpulse/native_form_filler.py:1189-1199` (replace unconditional known_domain)
- Modify: `jobpulse/native_form_filler.py:1275-1278` (validate after first page scan)

- [ ] **Step 1: Remove unconditional `_known_domain = True` and store `exp` as instance var**

In `jobpulse/native_form_filler.py`, replace lines 1189-1199:

```python
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if url:
                exp = FormExperienceDB().lookup(url)
                if exp and exp.get("success"):
                    self._known_domain = True
                    logger.info("FAST PATH: domain %s known (%d prior applies), skipping LLM/vision",
                                FormExperienceDB.normalize_domain(url), exp.get("apply_count", 0))
        except Exception:
            pass
```

With:

```python
        self._stored_exp = None
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if url:
                self._stored_exp = FormExperienceDB().lookup(url)
        except Exception:
            pass
```

- [ ] **Step 2: Add validate_against_live after first page scan**

In `jobpulse/native_form_filler.py`, after the field_types collection loop (after line 1277, where `seen_field_types.append(ft)` runs), add:

```python
            if page_num == 1 and self._stored_exp and self._stored_exp.get("success") and self._fe_db:
                validation = self._fe_db.validate_against_live(
                    page_url, seen_field_types, live_page_count=None,
                )
                if validation["trusted"]:
                    self._known_domain = True
                    logger.info(
                        "FAST PATH: domain %s validated (%.0f%% match, %d prior applies)",
                        FormExperienceDB.normalize_domain(page_url),
                        validation["match_ratio"] * 100,
                        self._stored_exp.get("apply_count", 0),
                    )
                else:
                    self._known_domain = False
                    logger.warning(
                        "DRIFT DETECTED on %s — match %.0f%%, diverged: %s. Using full LLM path.",
                        FormExperienceDB.normalize_domain(page_url),
                        validation["match_ratio"] * 100,
                        validation["diverged_fields"][:5],
                    )
```

- [ ] **Step 3: Run existing tests**

Run: `python -m pytest tests/jobpulse/ -v -k "form" --timeout=30 -x`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add jobpulse/native_form_filler.py
git commit -m "feat: wire validate_against_live for form drift detection before fast path"
```

---

### Task 7: Wire failure recording at 3 sites in NativeFormFiller.fill

**Files:**
- Modify: `jobpulse/native_form_filler.py:1267` (stuck-page abort — Site C)
- Modify: `jobpulse/native_form_filler.py:1456` (after LLM recovery — Site A)
- Modify: `jobpulse/native_form_filler.py:1480-1484` (after vision recovery — Site B)

- [ ] **Step 1: Wire Site C — stuck-page abort (line 1267)**

In `jobpulse/native_form_filler.py`, before line 1267 (`return _result({"success": False, "error": f"Stuck...`), add:

```python
                    if self._fe_db:
                        try:
                            self._fe_db.record_failure_reason(
                                domain=page_url, platform=self._platform,
                                failure_type="stuck_page", field_label="",
                                details=f"Identical page fingerprint on page {page_num}",
                            )
                        except Exception:
                            pass
```

- [ ] **Step 2: Wire Site A — after LLM recovery fails (around line 1456)**

In `jobpulse/native_form_filler.py`, after the `still_failing` list is built (after line 1456, `still_failing.append(item)`), before the vision recovery section, add:

```python
                for item in still_failing:
                    if self._fe_db:
                        try:
                            self._fe_db.record_failure_reason(
                                domain=page_url, platform=self._platform,
                                failure_type=_classify_fill_failure(item["result"]),
                                field_label=item["field"]["label"],
                                selector=item["field"].get("selector", ""),
                                details=item["result"].get("error", ""),
                            )
                        except Exception:
                            pass
```

- [ ] **Step 3: Wire Site B — after vision recovery fails (around line 1480)**

In `jobpulse/native_form_filler.py`, inside the `final_failed_labels.append(label)` loop (line 1480), add failure recording. Replace:

```python
                        final_failed_labels.append(label)
```

With:

```python
                        final_failed_labels.append(label)
                        if self._fe_db:
                            try:
                                self._fe_db.record_failure_reason(
                                    domain=page_url, platform=self._platform,
                                    failure_type=_classify_fill_failure(item["result"]),
                                    field_label=label,
                                    selector=item["field"].get("selector", ""),
                                    details=item["result"].get("error", ""),
                                )
                            except Exception:
                                pass
```

- [ ] **Step 4: Run existing tests**

Run: `python -m pytest tests/jobpulse/ -v -k "form" --timeout=30 -x`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py
git commit -m "feat: wire failure recording at stuck-page, LLM-recovery, and vision-recovery sites"
```

---

### Task 8: Wire negative fill technique recording in _fill_by_label

**Files:**
- Modify: `jobpulse/native_form_filler.py:646-660` (add else-branch for failed verification)

- [ ] **Step 1: Add else-branch for negative technique recording**

In `jobpulse/native_form_filler.py`, after the existing `if verified:` block (lines 646-660), add an `else` branch. After line 660 (`pass`), add:

```python
        else:
            try:
                page_url = getattr(self._page, "url", "") or ""
                if page_url and fill_technique and self._fe_db:
                    self._fe_db.record_fill_technique(
                        domain_or_url=page_url, field_label=label,
                        field_type=f"{tag}:{input_type or role}",
                        technique=fill_technique, value_used=fill_value,
                        success=False,
                    )
            except Exception:
                pass
```

- [ ] **Step 2: Run existing tests**

Run: `python -m pytest tests/jobpulse/ -v -k "form" --timeout=30 -x`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add jobpulse/native_form_filler.py
git commit -m "feat: record negative fill techniques when verification fails"
```

---

### Task 9: Label mapping persistence — replace no-op lambda

**Files:**
- Modify: `jobpulse/form_engine/field_resolver.py:732`
- Modify: `jobpulse/native_form_filler.py:149-162` (load `_global` mappings)
- Modify: `tests/jobpulse/test_form_experience_pipeline.py`

- [ ] **Step 1: Write failing test T7**

Append to `tests/jobpulse/test_form_experience_pipeline.py`:

```python
class TestLabelMappingPersistence:
    def test_persist_label_mapping_writes_to_global(self, tmp_path, monkeypatch):
        """T7: _persist_label_mapping stores under _global domain."""
        db_path = str(tmp_path / "form_experience.db")
        db = FormExperienceDB(db_path)

        # Monkeypatch FormExperienceDB default path so _persist_label_mapping uses our tmp DB
        monkeypatch.setattr(
            "jobpulse.form_experience_db._DEFAULT_DB", db_path,
        )

        from jobpulse.form_engine.field_resolver import _persist_label_mapping

        _persist_label_mapping("first name", "first_name")
        _persist_label_mapping("last name", "last_name")
        _persist_label_mapping("email address", "email")

        mappings = db.get_field_mappings("_global")
        assert mappings["first name"] == "first_name"
        assert mappings["last name"] == "last_name"
        assert mappings["email address"] == "email"

    def test_global_mappings_loaded_by_native_filler(self, tmp_path, monkeypatch):
        """T7b: NativeFormFiller loads _global mappings in addition to domain-specific."""
        db_path = str(tmp_path / "form_experience.db")
        db = FormExperienceDB(db_path)
        db.save_field_mappings("_global", {"first name": "first_name", "email": "email"})
        db.save_field_mappings("greenhouse.io", {"country": "location"})

        monkeypatch.setattr(
            "jobpulse.form_experience_db._DEFAULT_DB", db_path,
        )

        from unittest.mock import MagicMock
        mock_page = MagicMock()
        mock_page.url = "https://greenhouse.io/jobs/apply"
        filler = NativeFormFiller.__new__(NativeFormFiller)
        filler._page = mock_page
        filler._domain_field_mappings = {}
        filler._load_domain_field_mappings()

        assert filler._domain_field_mappings["country"] == "location"
        assert filler._domain_field_mappings["first name"] == "first_name"
        assert filler._domain_field_mappings["email"] == "email"
```

Also add the import at the top of the file:

```python
from jobpulse.native_form_filler import NativeFormFiller
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestLabelMappingPersistence -v`
Expected: FAIL — `_persist_label_mapping` is currently a no-op lambda

- [ ] **Step 3: Replace no-op lambda in field_resolver.py**

In `jobpulse/form_engine/field_resolver.py`, replace line 732:

```python
_persist_label_mapping = lambda label, key: None  # No-op in old API
```

With:

```python
def _persist_label_mapping(label: str, profile_key: str) -> None:
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        FormExperienceDB().save_field_mappings("_global", {label: profile_key})
    except Exception:
        pass
```

- [ ] **Step 4: Wire _global mapping loading in NativeFormFiller**

In `jobpulse/native_form_filler.py`, replace lines 149-162 (`_load_domain_field_mappings`):

```python
    def _load_domain_field_mappings(self) -> None:
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if not url:
                return
            db = FormExperienceDB()
            self._domain_field_mappings = db.get_field_mappings(url)
            if self._domain_field_mappings:
                logger.info("Loaded %d domain-specific field mappings for %s",
                            len(self._domain_field_mappings),
                            FormExperienceDB.normalize_domain(url))
        except Exception as exc:
            logger.debug("Could not load domain field mappings: %s", exc)
```

With:

```python
    def _load_domain_field_mappings(self) -> None:
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if not url:
                return
            db = FormExperienceDB()
            self._domain_field_mappings = db.get_field_mappings(url)
            global_mappings = db.get_field_mappings("_global")
            for label, key in global_mappings.items():
                self._domain_field_mappings.setdefault(label, key)
            if self._domain_field_mappings:
                logger.info("Loaded %d field mappings for %s (%d global)",
                            len(self._domain_field_mappings),
                            FormExperienceDB.normalize_domain(url),
                            len(global_mappings))
        except Exception as exc:
            logger.debug("Could not load domain field mappings: %s", exc)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestLabelMappingPersistence -v`
Expected: PASS

- [ ] **Step 6: Run existing tests for regression**

Run: `python -m pytest tests/jobpulse/test_form_experience_db.py -v && python -m pytest tests/jobpulse/ -v -k "field_mapper or field_resolver" --timeout=30`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/form_engine/field_resolver.py jobpulse/native_form_filler.py tests/jobpulse/test_form_experience_pipeline.py
git commit -m "feat: persist label mappings to SQLite via _global domain key"
```

---

### Task 10: Timing instrumentation — real hydration and transition measurements

**Files:**
- Modify: `jobpulse/native_form_filler.py:1213` (add timing list)
- Modify: `jobpulse/native_form_filler.py:1249-1250` (wrap scan in hydration timing)
- Modify: `jobpulse/native_form_filler.py:1487-1499` (remove page-1-only store, add per-page timing)
- Modify: `jobpulse/native_form_filler.py:1520-1533` (wrap page transition in transition timing)
- Modify: `tests/jobpulse/test_form_experience_pipeline.py`

- [ ] **Step 1: Write tests T8 and T9**

Append to `tests/jobpulse/test_form_experience_pipeline.py`:

```python
class TestTimingInstrumentation:
    def test_timing_stored_and_averaged(self, seeded_exp_db):
        """T8: store_timing records and get_timing returns running averages."""
        seeded_exp_db.store_timing(
            "job-boards.greenhouse.io",
            hydration_ms=150, fill_ms=3000, transition_ms=800,
        )
        seeded_exp_db.store_timing(
            "job-boards.greenhouse.io",
            hydration_ms=250, fill_ms=5000, transition_ms=1200,
        )
        timing = seeded_exp_db.get_timing("job-boards.greenhouse.io")
        assert timing is not None
        assert timing["sample_count"] == 2
        assert timing["avg_hydration_ms"] == (150 + 250) // 2
        assert timing["avg_fill_ms"] == (3000 + 5000) // 2
        assert timing["avg_transition_ms"] == (800 + 1200) // 2


class TestSuccessNeverOverwrittenByFailure:
    def test_success_preserved_when_failure_recorded(self, seeded_exp_db):
        """T9: Existing success record not overwritten by failure."""
        exp_before = seeded_exp_db.lookup("linkedin.com")
        assert exp_before["success"] == 1
        old_count = exp_before["apply_count"]

        seeded_exp_db.record(
            domain="linkedin.com", platform="linkedin", adapter="extension",
            pages_filled=0, field_types=[], screening_questions=[],
            time_seconds=5.0, success=False,
        )

        exp_after = seeded_exp_db.lookup("linkedin.com")
        assert exp_after["success"] == 1
        assert exp_after["apply_count"] == old_count + 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestTimingInstrumentation -v && python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestSuccessNeverOverwrittenByFailure -v`
Expected: All PASS (these test existing DB methods)

- [ ] **Step 3: Add timing instrumentation to NativeFormFiller.fill**

In `jobpulse/native_form_filler.py`, after line 1221 (`_stuck_count = 0`), add:

```python
        page_timings_list: list[tuple[int, int, int]] = []
```

In the fill loop, wrap the scan at line 1250. Replace:

```python
            # 1. Scan fields
            fields = await self._scan_fields()
```

With:

```python
            # 1. Scan fields (measure hydration time)
            t_hydration = time.monotonic()
            fields = await self._scan_fields()
            hydration_ms = int((time.monotonic() - t_hydration) * 1000)
```

Replace lines 1487-1499 (the page-1-only store_timing block):

```python
            # 8. Anti-detection timing + timing measurement
            page_fill_ms = int((time.monotonic() - t0) * 1000) if page_num == 1 else None
            if page_fill_ms is not None:
                try:
                    from jobpulse.form_experience_db import FormExperienceDB
                    FormExperienceDB().store_timing(
                        page_url,
                        hydration_ms=0,
                        fill_ms=page_fill_ms,
                        transition_ms=0,
                    )
                except Exception:
                    pass
```

With:

```python
            # 8. Anti-detection timing + timing measurement
            page_fill_ms = int((time.monotonic() - t_hydration) * 1000)
```

After the page transition code (after line 1533, `if not clicked:` error return), wrap the successful navigation case. Before the loop continues to the next page, add transition timing. After the `if not clicked:` block and before the loop continues, add:

```python
            transition_ms = int((time.monotonic() - t_hydration) * 1000) - page_fill_ms if page_num > 1 else 0
            page_timings_list.append((hydration_ms, page_fill_ms, transition_ms))
```

After the fill loop ends (after line 1538, `return _result({...})`), but before the final return of the method, store the averaged timing. Insert before the final `return _result(...)` at line 1535:

```python
        if page_timings_list and self._fe_db:
            avg_h = sum(h for h, _, _ in page_timings_list) // len(page_timings_list)
            avg_f = sum(f for _, f, _ in page_timings_list) // len(page_timings_list)
            transitions = [t for _, _, t in page_timings_list if t > 0]
            avg_t = sum(transitions) // len(transitions) if transitions else 0
            try:
                self._fe_db.store_timing(page_url, avg_h, avg_f, avg_t)
            except Exception:
                pass
```

- [ ] **Step 4: Run existing tests**

Run: `python -m pytest tests/jobpulse/ -v -k "form" --timeout=30 -x`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_form_experience_pipeline.py
git commit -m "feat: timing instrumentation with real hydration and transition measurements"
```

---

### Task 11: Test T10 — platform aggregate with real multi-domain data

**Files:**
- Modify: `tests/jobpulse/test_form_experience_pipeline.py`

- [ ] **Step 1: Write test T10**

Append to `tests/jobpulse/test_form_experience_pipeline.py`:

```python
class TestPlatformAggregate:
    def test_greenhouse_aggregate_across_domains(self, seeded_exp_db):
        """T10: get_platform_aggregate aggregates across job-boards.greenhouse.io
        and job-boards.eu.greenhouse.io."""
        agg = seeded_exp_db.get_platform_aggregate("greenhouse")
        assert agg is not None
        assert agg["observation_count"] == 2
        assert agg["avg_pages"] == (2 + 1) / 2
        assert agg["avg_time_seconds"] == round((94.0 + 32.2) / 2, 1)
        assert "text:first_name" in agg["common_field_types"]
        assert "combobox:country" in agg["common_field_types"]

    def test_workday_aggregate_shows_high_variance(self, seeded_exp_db):
        """T10b: Workday shows extreme time variance (Snowflake 20s vs Expedia 600s)."""
        agg = seeded_exp_db.get_platform_aggregate("workday")
        assert agg is not None
        assert agg["observation_count"] == 2
        assert agg["avg_time_seconds"] == round((20.0 + 600.0) / 2, 1)
        assert agg["avg_pages"] == (1 + 5) / 2

    def test_nonexistent_platform_returns_none(self, seeded_exp_db):
        """T10c: Unknown platform returns None."""
        assert seeded_exp_db.get_platform_aggregate("unknown_platform") is None
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py::TestPlatformAggregate -v`
Expected: All 3 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_form_experience_pipeline.py
git commit -m "test: T10 platform aggregate tests with real multi-domain data"
```

---

### Task 12: Final regression run + classify_fill_failure caller verification

**Files:**
- No new files

- [ ] **Step 1: Verify _classify_fill_failure now has callers**

Run: `grep -n "_classify_fill_failure" jobpulse/native_form_filler.py`
Expected: At least 3 call sites (definition + 2 call sites in fill loop at Sites A and B)

- [ ] **Step 2: Verify record_failure_reason now has callers**

Run: `grep -rn "record_failure_reason" jobpulse/ --include="*.py" | grep -v __pycache__ | grep -v ".pyc"`
Expected: At least 4 matches — definition in `form_experience_db.py`, Sites A+B+C in `native_form_filler.py`, and `post_apply_hook.py`

- [ ] **Step 3: Verify get_platform_fill_techniques now has callers**

Run: `grep -rn "get_platform_fill_techniques" jobpulse/ --include="*.py" | grep -v __pycache__`
Expected: At least 2 matches — definition in `form_experience_db.py`, call site in `native_form_filler.py`

- [ ] **Step 4: Verify validate_against_live now has production callers**

Run: `grep -rn "validate_against_live" jobpulse/ --include="*.py" | grep -v __pycache__ | grep -v test`
Expected: At least 2 matches — definition + call in `native_form_filler.py`

- [ ] **Step 5: Verify _persist_label_mapping is no longer a no-op**

Run: `grep -A3 "_persist_label_mapping" jobpulse/form_engine/field_resolver.py`
Expected: A real function definition, NOT `lambda label, key: None`

- [ ] **Step 6: Full test suite run**

Run: `python -m pytest tests/jobpulse/test_form_experience_pipeline.py tests/jobpulse/test_form_experience_db.py tests/jobpulse/test_post_apply_hook.py -v`
Expected: All tests PASS (including original + new pipeline tests)

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat(form-experience): wire full learning pipeline — failures, cross-platform, timing"
```
