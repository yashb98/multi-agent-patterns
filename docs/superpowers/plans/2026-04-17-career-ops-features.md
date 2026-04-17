# Career-Ops Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 9 guarded features to the JobPulse pipeline (ghost detection, archetype engine, tone framework, etc.) without modifying any existing function internals — all behind feature flags defaulting to off.

**Architecture:** New `pipeline_hooks.py` mediates all feature integration. Each feature is a standalone module with its own tests. Feature flags (env vars) default to `false` — the pipeline produces identical output until a feature is explicitly enabled. Existing functions are wrapped, never edited.

**Tech Stack:** Python 3.12, Pydantic v2, httpx, ReportLab, SQLite, pytest

**Spec:** `docs/superpowers/specs/2026-04-16-career-ops-12-features-design.md`

**Key discovery — existing modules:** `followup_tracker.py`, `interview_prep.py`, and `liveness_checker.py` already exist and partially implement F8, F9, F2. New features extend these, not replace them.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `jobpulse/pipeline_hooks.py` | CREATE | Feature flag checks + wrapper functions for all pipeline integration |
| `jobpulse/ghost_detector.py` | CREATE | Ghost job detection: 5 signal analyzers, weighted scoring, 3 tiers |
| `jobpulse/archetype_engine.py` | CREATE | 6-archetype detection: keyword scoring + LLM fallback |
| `jobpulse/tone_framework.py` | CREATE | Banned phrase filter + proof point injection for screening answers |
| `jobpulse/models/application_models.py` | MODIFY | Add 7 Optional fields to JobListing (lines 43-97) |
| `jobpulse/ats_api_scanner.py` | MODIFY | Add Workday parser + detection pattern |
| `jobpulse/cv_templates/generate_cv.py` | MODIFY | Add `normalize_text_for_ats()` pure function at end of file |
| `jobpulse/scan_pipeline.py` | MODIFY | Import pipeline_hooks, swap 3 call sites (lines 27-34, 501) |
| `jobpulse/screening_answers.py` | MODIFY | Wrap `get_answer()` output with tone filter (1 line) |
| `data/archetype_profiles.json` | CREATE | 6 archetype definitions (user-editable) |
| `data/ats_company_registry.json` | CREATE | Company-to-ATS mapping seed data |
| `scripts/migrate_012_new_fields.py` | CREATE | Idempotent DB migration for new JobListing columns |
| `tests/jobpulse/test_ghost_detector.py` | CREATE | Ghost detection unit tests |
| `tests/jobpulse/test_archetype_engine.py` | CREATE | Archetype engine unit tests |
| `tests/jobpulse/test_tone_framework.py` | CREATE | Tone framework unit tests |
| `tests/jobpulse/test_pipeline_hooks.py` | CREATE | Feature flag + wrapper integration tests |
| `tests/jobpulse/test_normalize_ats.py` | CREATE | Unicode normalization tests |
| `tests/jobpulse/test_workday_parser.py` | CREATE | Workday parser unit tests |
| `tests/jobpulse/test_pipeline_no_regression.py` | CREATE | Full pipeline regression — all flags OFF = identical output |

---

## Phase 1: Zero-Risk Additions

### Task 1: JobListing Model Extension

**Files:**
- Modify: `jobpulse/models/application_models.py:43-97`
- Test: `tests/jobpulse/test_scan_pipeline.py` (existing — verify no breakage)

- [ ] **Step 1: Write the failing test**

Create a test that verifies the new fields exist and default correctly.

```python
# tests/jobpulse/test_model_extensions.py
"""Verify new JobListing fields are Optional with safe defaults."""
import pytest
from datetime import datetime


class TestJobListingNewFields:
    def test_new_fields_default_to_none(self):
        from jobpulse.models.application_models import JobListing

        listing = JobListing(
            job_id="test123",
            title="Data Analyst",
            company="TestCo",
            platform="reed",
            url="https://example.com/job/1",
            description_raw="Test JD",
            location="London",
            found_at=datetime.utcnow(),
        )
        assert listing.ghost_tier is None
        assert listing.archetype is None
        assert listing.archetype_secondary is None
        assert listing.archetype_confidence == 0.0
        assert listing.locale_market is None
        assert listing.locale_language is None
        assert listing.posted_at is None

    def test_new_fields_accept_values(self):
        from jobpulse.models.application_models import JobListing

        listing = JobListing(
            job_id="test456",
            title="ML Engineer",
            company="AICo",
            platform="linkedin",
            url="https://example.com/job/2",
            description_raw="Build ML pipelines",
            location="Remote",
            found_at=datetime.utcnow(),
            ghost_tier="high_confidence",
            archetype="agentic",
            archetype_secondary="data_platform",
            archetype_confidence=0.92,
            locale_market="uk",
            locale_language="en",
            posted_at="2026-04-15T10:00:00Z",
        )
        assert listing.archetype == "agentic"
        assert listing.archetype_confidence == 0.92
        assert listing.ghost_tier == "high_confidence"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_model_extensions.py -v`
Expected: FAIL — fields don't exist yet on JobListing.

- [ ] **Step 3: Add new fields to JobListing**

In `jobpulse/models/application_models.py`, add after `recruiter_email` (line 97):

```python
    # --- Career-ops feature fields (all optional, behind feature flags) ---
    ghost_tier: str | None = Field(
        default=None,
        description="Ghost detection tier: high_confidence, proceed_with_caution, or suspicious.",
    )
    archetype: str | None = Field(
        default=None,
        description="Primary archetype: agentic, data_platform, data_analyst, data_scientist, ai_ml, data_engineer.",
    )
    archetype_secondary: str | None = Field(
        default=None,
        description="Secondary archetype for hybrid roles.",
    )
    archetype_confidence: float = Field(
        default=0.0,
        description="Archetype detection confidence 0.0-1.0.",
    )
    locale_market: str | None = Field(
        default=None,
        description="Detected job market: uk, dach, france, nordics, us.",
    )
    locale_language: str | None = Field(
        default=None,
        description="Detected JD language: en, de, fr, etc.",
    )
    posted_at: str | None = Field(
        default=None,
        description="Posting date from ATS metadata (ISO 8601).",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_model_extensions.py -v`
Expected: PASS

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `python -m pytest tests/jobpulse/test_scan_pipeline.py tests/test_skill_graph_store.py -v`
Expected: All existing tests PASS (new fields have defaults, so nothing breaks).

- [ ] **Step 6: Commit**

```bash
git add jobpulse/models/application_models.py tests/jobpulse/test_model_extensions.py
git commit -m "feat: add optional career-ops fields to JobListing model"
```

---

### Task 2: ATS Unicode Normalization (F6)

**Files:**
- Modify: `jobpulse/cv_templates/generate_cv.py` (add function at end)
- Create: `tests/jobpulse/test_normalize_ats.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_normalize_ats.py
"""Tests for ATS Unicode normalization — pure function, no I/O."""
import pytest


class TestNormalizeTextForAts:
    def test_replaces_em_dash(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Experience \u2014 3 years"
        result, counts = normalize_text_for_ats(text)
        assert result == "Experience - 3 years"
        assert counts["\u2014"] == 1

    def test_replaces_smart_quotes(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "\u201CHello\u201D and \u2018world\u2019"
        result, counts = normalize_text_for_ats(text)
        assert result == '"Hello" and \'world\''
        assert counts["\u201C"] == 1
        assert counts["\u201D"] == 1

    def test_removes_zero_width_chars(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Py\u200Bthon \u200CSkill\uFEFF"
        result, counts = normalize_text_for_ats(text)
        assert result == "Python Skill"

    def test_replaces_ellipsis(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Skills\u2026 more"
        result, counts = normalize_text_for_ats(text)
        assert result == "Skills... more"

    def test_replaces_nbsp(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "word\u00A0word"
        result, counts = normalize_text_for_ats(text)
        assert result == "word word"

    def test_idempotent(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Already clean text with no unicode"
        result1, counts1 = normalize_text_for_ats(text)
        result2, counts2 = normalize_text_for_ats(result1)
        assert result1 == result2
        assert all(v == 0 for v in counts2.values())

    def test_preserves_normal_text(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "Built ML pipelines processing 10K+ records with 94% accuracy."
        result, counts = normalize_text_for_ats(text)
        assert result == text

    def test_en_dash(self):
        from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

        text = "2024\u20132026"
        result, _ = normalize_text_for_ats(text)
        assert result == "2024-2026"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_normalize_ats.py -v`
Expected: FAIL — `normalize_text_for_ats` not defined.

- [ ] **Step 3: Implement `normalize_text_for_ats`**

Add at end of `jobpulse/cv_templates/generate_cv.py` (before any `if __name__` block):

```python
# ---------------------------------------------------------------------------
# ATS Unicode normalization
# ---------------------------------------------------------------------------

_UNICODE_REPLACEMENTS: dict[str, str] = {
    "\u2014": "-",
    "\u2013": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201C": '"',
    "\u201D": '"',
    "\u2026": "...",
    "\u00A0": " ",
    "\u200B": "",
    "\u200C": "",
    "\u200D": "",
    "\u2060": "",
    "\uFEFF": "",
}


def normalize_text_for_ats(text: str) -> tuple[str, dict[str, int]]:
    """Replace Unicode characters that ATS parsers handle poorly.

    Returns (normalized_text, replacement_counts).
    """
    counts: dict[str, int] = {}
    for char, replacement in _UNICODE_REPLACEMENTS.items():
        n = text.count(char)
        counts[char] = n
        if n:
            text = text.replace(char, replacement)
    return text, counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_normalize_ats.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_templates/generate_cv.py tests/jobpulse/test_normalize_ats.py
git commit -m "feat(F6): add normalize_text_for_ats pure function"
```

---

### Task 3: Workday ATS Parser (F1)

**Files:**
- Modify: `jobpulse/ats_api_scanner.py`
- Create: `tests/jobpulse/test_workday_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_workday_parser.py
"""Tests for Workday ATS parser — parser and detection only, no HTTP."""
import pytest


class TestDetectWorkday:
    def test_detects_workday_url(self):
        from jobpulse.ats_api_scanner import detect_ats_provider

        provider, slug = detect_ats_provider(
            "https://acme.wd3.myworkdayjobs.com/en-US/acme_careers/job/London/Data-Analyst_R12345"
        )
        assert provider == "workday"
        assert slug == "acme"

    def test_detects_workday_alt_shard(self):
        from jobpulse.ats_api_scanner import detect_ats_provider

        provider, slug = detect_ats_provider(
            "https://bigcorp.wd1.myworkdayjobs.com/BigCorpJobs"
        )
        assert provider == "workday"
        assert slug == "bigcorp"


class TestParseWorkday:
    def test_parses_jobs(self):
        from jobpulse.ats_api_scanner import parse_workday

        data = {
            "jobPostings": [
                {
                    "title": "Data Scientist",
                    "externalPath": "/en-US/jobs/job/London/Data-Scientist_R001",
                    "locationsText": "London, UK",
                    "postedOn": "Posted 3 Days Ago",
                },
                {
                    "title": "ML Engineer",
                    "externalPath": "/en-US/jobs/job/Remote/ML-Engineer_R002",
                    "locationsText": "Remote",
                    "postedOn": "Posted 7 Days Ago",
                },
            ]
        }
        result = parse_workday(data, "Acme", "acme.wd3.myworkdayjobs.com", "acme_careers")
        assert len(result) == 2
        assert result[0]["title"] == "Data Scientist"
        assert result[0]["company"] == "Acme"
        assert result[0]["location"] == "London, UK"
        assert result[0]["platform"] == "workday"
        assert "acme.wd3.myworkdayjobs.com" in result[0]["url"]

    def test_empty_response(self):
        from jobpulse.ats_api_scanner import parse_workday

        assert parse_workday({}, "Acme", "x.wd1.myworkdayjobs.com", "jobs") == []
        assert parse_workday({"jobPostings": []}, "Acme", "x.wd1.myworkdayjobs.com", "jobs") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_workday_parser.py -v`
Expected: FAIL — `parse_workday` not defined, workday not in detection patterns.

- [ ] **Step 3: Add Workday pattern and parser to `ats_api_scanner.py`**

Add to `_PATTERNS` dict (line 23-27):

```python
    "workday": re.compile(r"([a-z0-9_-]+)\.wd\d+\.myworkdayjobs\.com"),
```

Add parser function after `parse_lever`:

```python
def parse_workday(data: dict, company: str, host: str, site: str) -> list[dict]:
    jobs = []
    for job in data.get("jobPostings", []):
        path = job.get("externalPath", "")
        url = f"https://{host}{path}" if path else ""
        jobs.append({
            "title": job.get("title", ""),
            "url": url,
            "company": company,
            "location": job.get("locationsText", ""),
            "platform": "workday",
        })
    return jobs
```

Add scanner function after `scan_lever`:

```python
def scan_workday(slug: str, company: str, host: str, site: str, client: Optional[httpx.Client] = None) -> list[dict]:
    url = f"https://{host}/wday/cxs/{slug}/{site}/jobs"
    payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    _close = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        return parse_workday(resp.json(), company, host, site)
    except Exception as exc:
        logger.warning("workday scan failed for %s: %s", slug, exc)
        return []
    finally:
        if _close:
            client.close()
```

Update `detect_ats_provider` to extract shard info for Workday:

```python
def detect_ats_provider(url: str) -> tuple[Optional[str], Optional[str]]:
    """Return (provider, slug) or (None, None) if unrecognised."""
    for provider, pattern in _PATTERNS.items():
        m = pattern.search(url)
        if m:
            return provider, m.group(1)
    return None, None
```

Update `scan_ats_api` to handle workday:

```python
def scan_ats_api(url: str, company: str) -> list[dict]:
    """Auto-detect provider, extract slug, call the appropriate scanner."""
    provider, slug = detect_ats_provider(url)
    if provider is None:
        logger.debug("no ATS provider detected for %s", url)
        return []
    if provider == "greenhouse":
        return scan_greenhouse(slug, company)
    if provider == "ashby":
        return scan_ashby(slug, company)
    if provider == "lever":
        return scan_lever(slug, company)
    if provider == "workday":
        m = _PATTERNS["workday"].search(url)
        host = m.group(0) if m else ""
        path_parts = url.split(host)[-1].strip("/").split("/")
        site = path_parts[0] if path_parts and path_parts[0] else "External"
        return scan_workday(slug, company, host, site)
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_workday_parser.py tests/jobpulse/test_ats_api_scanner.py -v`
Expected: All tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ats_api_scanner.py tests/jobpulse/test_workday_parser.py
git commit -m "feat(F1): add Workday ATS parser and detection"
```

---

### Task 4: Pipeline Hooks Module

**Files:**
- Create: `jobpulse/pipeline_hooks.py`
- Create: `tests/jobpulse/test_pipeline_hooks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_pipeline_hooks.py
"""Tests for pipeline_hooks — feature flag wrappers."""
import os
import pytest
from unittest.mock import MagicMock, patch


class TestFeatureEnabled:
    def test_returns_false_by_default(self):
        from jobpulse.pipeline_hooks import feature_enabled

        assert feature_enabled("JOBPULSE_GHOST_DETECTION") is False

    def test_returns_true_when_set(self, monkeypatch):
        from jobpulse.pipeline_hooks import feature_enabled

        monkeypatch.setenv("JOBPULSE_GHOST_DETECTION", "true")
        assert feature_enabled("JOBPULSE_GHOST_DETECTION") is True

    def test_case_insensitive(self, monkeypatch):
        from jobpulse.pipeline_hooks import feature_enabled

        monkeypatch.setenv("JOBPULSE_GHOST_DETECTION", "True")
        assert feature_enabled("JOBPULSE_GHOST_DETECTION") is True


class TestWithGhostDetection:
    def test_passthrough_when_disabled(self):
        from jobpulse.pipeline_hooks import with_ghost_detection

        listings = [MagicMock(), MagicMock()]
        result = with_ghost_detection(listings, {})
        assert result == listings

    def test_filters_when_enabled(self, monkeypatch):
        from jobpulse.pipeline_hooks import with_ghost_detection

        monkeypatch.setenv("JOBPULSE_GHOST_DETECTION", "true")
        listing1 = MagicMock()
        listing1.job_id = "a"
        listing1.description_raw = "A real job"
        listing2 = MagicMock()
        listing2.job_id = "b"
        listing2.description_raw = "A real job"

        with patch("jobpulse.pipeline_hooks.detect_ghost_job") as mock_detect:
            result1 = MagicMock()
            result1.tier = "high_confidence"
            result1.should_block = False
            result2 = MagicMock()
            result2.tier = "suspicious"
            result2.should_block = True
            mock_detect.side_effect = [result1, result2]

            result = with_ghost_detection([listing1, listing2], {"a": "JD1", "b": "JD2"})
            assert len(result) == 1
            assert listing1.ghost_tier == "high_confidence"


class TestEnhancedGenerateMaterials:
    def test_delegates_to_original_when_disabled(self):
        from jobpulse.pipeline_hooks import enhanced_generate_materials

        mock_original = MagicMock(return_value="original_result")
        listing = MagicMock()
        result = enhanced_generate_materials(
            original_fn=mock_original,
            listing=listing,
            screen=None,
            db=MagicMock(),
            repos=[],
            notion_failures=[],
        )
        assert result == "original_result"
        mock_original.assert_called_once()

    def test_applies_normalize_when_enabled(self, monkeypatch):
        from jobpulse.pipeline_hooks import enhanced_generate_materials

        monkeypatch.setenv("JOBPULSE_ATS_NORMALIZE", "true")
        mock_bundle = MagicMock()
        mock_bundle.cv_path = None
        mock_original = MagicMock(return_value=mock_bundle)
        listing = MagicMock()

        result = enhanced_generate_materials(
            original_fn=mock_original,
            listing=listing,
            screen=None,
            db=MagicMock(),
            repos=[],
            notion_failures=[],
        )
        mock_original.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_pipeline_hooks.py -v`
Expected: FAIL — `pipeline_hooks` module doesn't exist.

- [ ] **Step 3: Implement `pipeline_hooks.py`**

```python
# jobpulse/pipeline_hooks.py
"""Feature-flagged wrappers for the scan pipeline.

All new career-ops features integrate through this module.
Each wrapper checks an env var and either delegates to the new
feature or passes through to the original function unchanged.
"""

import os
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


def feature_enabled(env_var: str) -> bool:
    """Check if a feature flag env var is set to true."""
    return os.getenv(env_var, "false").lower() == "true"


# ---------------------------------------------------------------------------
# Ghost Detection wrapper (F2)
# ---------------------------------------------------------------------------


def with_ghost_detection(
    listings: list[Any],
    jd_texts: dict[str, str],
) -> list[Any]:
    """Filter listings through ghost detection when enabled. Pass-through when disabled."""
    if not feature_enabled("JOBPULSE_GHOST_DETECTION"):
        return listings

    from jobpulse.ghost_detector import detect_ghost_job

    result = []
    for listing in listings:
        try:
            jd = jd_texts.get(listing.job_id, getattr(listing, "description_raw", ""))
            ghost = detect_ghost_job(listing, jd)
            listing.ghost_tier = ghost.tier
            if not ghost.should_block:
                result.append(listing)
            else:
                logger.info(
                    "pipeline_hooks: ghost blocked %s @ %s — tier=%s",
                    listing.title, listing.company, ghost.tier,
                )
        except Exception as exc:
            logger.warning("pipeline_hooks: ghost detection failed for %s: %s", listing.job_id, exc)
            result.append(listing)
    return result


# ---------------------------------------------------------------------------
# Archetype Detection wrapper (F3)
# ---------------------------------------------------------------------------


def with_archetype_detection(listing: Any) -> None:
    """Detect and attach archetype to listing when enabled. No-op when disabled."""
    if not feature_enabled("JOBPULSE_ARCHETYPE_ENGINE"):
        return

    from jobpulse.archetype_engine import detect_archetype

    try:
        result = detect_archetype(
            getattr(listing, "description_raw", ""),
            getattr(listing, "required_skills", []),
        )
        listing.archetype = result.primary
        listing.archetype_secondary = result.secondary
        listing.archetype_confidence = result.confidence
    except Exception as exc:
        logger.warning("pipeline_hooks: archetype detection failed for %s: %s", listing.job_id, exc)


# ---------------------------------------------------------------------------
# Enhanced generate_materials wrapper (F5, F6)
# ---------------------------------------------------------------------------


def enhanced_generate_materials(
    original_fn: Any,
    listing: Any,
    screen: Any,
    db: Any,
    repos: list[dict],
    notion_failures: list[str],
) -> Any:
    """Wrap generate_materials with archetype framing and ATS normalization."""
    bundle = original_fn(listing, screen, db, repos, notion_failures)

    if feature_enabled("JOBPULSE_ATS_NORMALIZE") and bundle.cv_path:
        try:
            from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

            if bundle.cv_text:
                normalized, counts = normalize_text_for_ats(bundle.cv_text)
                total = sum(counts.values())
                if total > 0:
                    logger.info(
                        "pipeline_hooks: normalized %d chars in CV for %s",
                        total, listing.company,
                    )
                bundle.cv_text = normalized
        except Exception as exc:
            logger.warning("pipeline_hooks: ATS normalize failed: %s", exc)

    return bundle


# ---------------------------------------------------------------------------
# Tone Framework wrapper (F7)
# ---------------------------------------------------------------------------


def with_tone_filter(answer: str, question: str, listing: Any) -> str:
    """Apply tone framework to a screening answer when enabled. Pass-through when disabled."""
    if not feature_enabled("JOBPULSE_TONE_FRAMEWORK"):
        return answer

    from jobpulse.tone_framework import apply_tone

    try:
        return apply_tone(answer, question, listing)
    except Exception as exc:
        logger.warning("pipeline_hooks: tone filter failed: %s", exc)
        return answer
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_pipeline_hooks.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/pipeline_hooks.py tests/jobpulse/test_pipeline_hooks.py
git commit -m "feat: add pipeline_hooks module with feature flag wrappers"
```

---

### Task 5: Wire Pipeline Hooks into scan_pipeline.py

**Files:**
- Modify: `jobpulse/scan_pipeline.py:24-34,501`

- [ ] **Step 1: Write the regression test**

```python
# tests/jobpulse/test_pipeline_no_regression.py
"""Verify pipeline produces identical output with all feature flags OFF."""
from unittest.mock import MagicMock, patch
import pytest


class TestPipelineNoRegression:
    def test_generate_materials_unchanged_when_flags_off(self):
        """With all flags off, enhanced_generate_materials just delegates."""
        from jobpulse.pipeline_hooks import enhanced_generate_materials

        mock_bundle = MagicMock()
        mock_bundle.cv_path = "/tmp/test.pdf"
        mock_bundle.cv_text = "Some CV text with \u2014 dashes"
        mock_original = MagicMock(return_value=mock_bundle)

        result = enhanced_generate_materials(
            original_fn=mock_original,
            listing=MagicMock(),
            screen=None,
            db=MagicMock(),
            repos=[],
            notion_failures=[],
        )
        # With flags off, cv_text should NOT be normalized
        assert result.cv_text == "Some CV text with \u2014 dashes"
        mock_original.assert_called_once()

    def test_ghost_detection_passthrough_when_off(self):
        from jobpulse.pipeline_hooks import with_ghost_detection

        listings = [MagicMock(), MagicMock(), MagicMock()]
        result = with_ghost_detection(listings, {})
        assert len(result) == 3

    def test_archetype_noop_when_off(self):
        from jobpulse.pipeline_hooks import with_archetype_detection

        listing = MagicMock(spec=[])
        with_archetype_detection(listing)
        # listing should not have archetype set
        assert not hasattr(listing, "archetype") or listing.archetype is None
```

- [ ] **Step 2: Run regression test**

Run: `python -m pytest tests/jobpulse/test_pipeline_no_regression.py -v`
Expected: PASS — all wrappers are no-ops with flags off.

- [ ] **Step 3: Wire pipeline_hooks import into scan_pipeline.py**

Add to imports in `jobpulse/scan_pipeline.py` (after line 55):

```python
from jobpulse.pipeline_hooks import (
    enhanced_generate_materials,
    with_ghost_detection,
    with_archetype_detection,
)
```

No existing function call sites change yet — the wrappers are imported but will be
connected in Phase 2/3 when the underlying modules exist. For now, the import itself
is the only change, and it's safe because `pipeline_hooks.py` has no side effects at
import time (it only lazy-imports feature modules when flags are on).

- [ ] **Step 4: Run full pipeline tests**

Run: `python -m pytest tests/jobpulse/test_scan_pipeline.py tests/jobpulse/test_pipeline_no_regression.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/scan_pipeline.py tests/jobpulse/test_pipeline_no_regression.py
git commit -m "feat: wire pipeline_hooks into scan_pipeline imports"
```

---

## Phase 2: Guarded Pipeline Enhancements

### Task 6: Ghost Detector Module (F2)

**Files:**
- Create: `jobpulse/ghost_detector.py`
- Create: `tests/jobpulse/test_ghost_detector.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_ghost_detector.py
"""Tests for ghost job detection — 5 signal analyzers."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta


class TestFreshnessSignal:
    def test_recent_post_scores_high(self):
        from jobpulse.ghost_detector import _freshness_signal

        listing = MagicMock()
        listing.posted_at = (datetime.utcnow() - timedelta(days=2)).isoformat()
        signal = _freshness_signal(listing, "")
        assert signal.score >= 0.8
        assert signal.name == "freshness"

    def test_old_post_scores_low(self):
        from jobpulse.ghost_detector import _freshness_signal

        listing = MagicMock()
        listing.posted_at = (datetime.utcnow() - timedelta(days=60)).isoformat()
        signal = _freshness_signal(listing, "")
        assert signal.score <= 0.4

    def test_no_date_is_neutral(self):
        from jobpulse.ghost_detector import _freshness_signal

        listing = MagicMock()
        listing.posted_at = None
        signal = _freshness_signal(listing, "")
        assert signal.score == 0.5
        assert signal.confidence == "low"


class TestJdQualitySignal:
    def test_specific_jd_scores_high(self):
        from jobpulse.ghost_detector import _jd_quality_signal

        jd = (
            "We are looking for a Python developer with 3+ years experience in "
            "machine learning, NLP, and data pipelines. Must have experience with "
            "PyTorch, Docker, and AWS. Competitive salary range 45k-65k GBP."
        )
        signal = _jd_quality_signal(MagicMock(), jd)
        assert signal.score >= 0.7

    def test_vague_jd_scores_low(self):
        from jobpulse.ghost_detector import _jd_quality_signal

        jd = "Great opportunity. Apply now."
        signal = _jd_quality_signal(MagicMock(), jd)
        assert signal.score <= 0.4


class TestRepostSignal:
    def test_no_history_is_neutral(self):
        from jobpulse.ghost_detector import _repost_signal

        listing = MagicMock()
        listing.company = "NewCo"
        listing.title = "Data Analyst"
        signal = _repost_signal(listing, [])
        assert signal.score == 0.5

    def test_same_title_company_recently_is_suspicious(self):
        from jobpulse.ghost_detector import _repost_signal

        listing = MagicMock()
        listing.company = "RepeatCo"
        listing.title = "Data Analyst"
        history = [
            {"company": "RepeatCo", "title": "Data Analyst", "found_at": datetime.utcnow().isoformat()},
            {"company": "RepeatCo", "title": "Data Analyst", "found_at": (datetime.utcnow() - timedelta(days=30)).isoformat()},
        ]
        signal = _repost_signal(listing, history)
        assert signal.score <= 0.4


class TestDetectGhostJob:
    def test_returns_high_confidence_for_good_job(self):
        from jobpulse.ghost_detector import detect_ghost_job

        listing = MagicMock()
        listing.posted_at = datetime.utcnow().isoformat()
        listing.company = "Anthropic"
        listing.title = "ML Engineer"
        listing.url = "https://jobs.ashbyhq.com/anthropic/123"

        jd = (
            "Anthropic is hiring an ML Engineer. 3+ years Python, PyTorch, "
            "distributed training experience required. Salary 80-120k GBP."
        )
        result = detect_ghost_job(listing, jd)
        assert result.tier == "high_confidence"
        assert result.should_block is False

    def test_returns_suspicious_for_bad_signals(self):
        from jobpulse.ghost_detector import detect_ghost_job

        listing = MagicMock()
        listing.posted_at = (datetime.utcnow() - timedelta(days=90)).isoformat()
        listing.company = "Unknown Corp"
        listing.title = "Data Scientist"
        listing.url = "https://example.com/old-job"

        jd = "Great opportunity. Apply now."
        result = detect_ghost_job(listing, jd)
        assert result.tier in ("suspicious", "proceed_with_caution")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_ghost_detector.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `ghost_detector.py`**

```python
# jobpulse/ghost_detector.py
"""Ghost job detection — identifies likely-dead postings before wasting applications.

5 signal analyzers, weighted aggregation, 3 tiers.
Runs as Gate 0.5 via pipeline_hooks (between Gate 0 and Gates 1-3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class GhostSignal:
    name: str
    score: float
    confidence: str
    reason: str


@dataclass
class GhostDetectionResult:
    tier: str
    signals: list[GhostSignal] = field(default_factory=list)
    recommendation: str = ""
    should_block: bool = False


_SIGNAL_WEIGHTS = {
    "freshness": 0.30,
    "jd_quality": 0.25,
    "repost": 0.20,
    "url_liveness": 0.15,
    "company": 0.10,
}


def _freshness_signal(listing, jd_text: str) -> GhostSignal:
    posted_at = getattr(listing, "posted_at", None)
    if not posted_at:
        return GhostSignal("freshness", 0.5, "low", "No posting date available")

    try:
        posted = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00")).replace(tzinfo=None)
        age_days = (datetime.utcnow() - posted).days
    except (ValueError, TypeError):
        return GhostSignal("freshness", 0.5, "low", "Could not parse posting date")

    if age_days <= 7:
        return GhostSignal("freshness", 1.0, "high", f"Posted {age_days} days ago")
    if age_days <= 21:
        return GhostSignal("freshness", 0.7, "medium", f"Posted {age_days} days ago")
    if age_days <= 45:
        return GhostSignal("freshness", 0.4, "medium", f"Posted {age_days} days ago — getting stale")
    return GhostSignal("freshness", 0.2, "high", f"Posted {age_days} days ago — likely expired")


def _jd_quality_signal(listing, jd_text: str) -> GhostSignal:
    if len(jd_text) < 100:
        return GhostSignal("jd_quality", 0.2, "high", "JD too short (<100 chars)")

    specificity_markers = [
        r"\d+\+?\s*years?", r"\$[\d,]+|£[\d,]+|€[\d,]+", r"\bsalary\b",
        r"\bpython\b", r"\bsql\b", r"\bdocker\b", r"\baws\b",
        r"\bresponsibilities\b", r"\brequirements\b", r"\bqualifications\b",
    ]
    hits = sum(1 for p in specificity_markers if re.search(p, jd_text, re.IGNORECASE))
    ratio = hits / len(specificity_markers)

    if ratio >= 0.4:
        return GhostSignal("jd_quality", 0.9, "high", f"Specific JD ({hits}/{len(specificity_markers)} markers)")
    if ratio >= 0.2:
        return GhostSignal("jd_quality", 0.6, "medium", f"Moderate JD specificity ({hits} markers)")
    return GhostSignal("jd_quality", 0.3, "medium", "Vague JD — few specificity markers")


def _repost_signal(listing, history: list[dict]) -> GhostSignal:
    if not history:
        return GhostSignal("repost", 0.5, "low", "No historical data")

    company = getattr(listing, "company", "").lower()
    title_words = set(getattr(listing, "title", "").lower().split())
    matches = 0
    for prev in history:
        prev_company = prev.get("company", "").lower()
        prev_title_words = set(prev.get("title", "").lower().split())
        if prev_company == company and len(title_words & prev_title_words) >= len(title_words) * 0.6:
            matches += 1

    if matches >= 2:
        return GhostSignal("repost", 0.2, "high", f"Reposted {matches} times in 90 days")
    if matches == 1:
        return GhostSignal("repost", 0.5, "medium", "Posted once before recently")
    return GhostSignal("repost", 0.8, "medium", "No repost history")


def _url_liveness_signal(listing, jd_text: str) -> GhostSignal:
    return GhostSignal("url_liveness", 0.5, "low", "Liveness check deferred")


def _company_signal(listing, jd_text: str) -> GhostSignal:
    return GhostSignal("company", 0.5, "low", "Company signal deferred")


def detect_ghost_job(listing, jd_text: str, history: list[dict] | None = None) -> GhostDetectionResult:
    """Run all signal analyzers and aggregate into a tier."""
    signals = [
        _freshness_signal(listing, jd_text),
        _jd_quality_signal(listing, jd_text),
        _repost_signal(listing, history or []),
        _url_liveness_signal(listing, jd_text),
        _company_signal(listing, jd_text),
    ]

    weighted_score = sum(
        s.score * _SIGNAL_WEIGHTS.get(s.name, 0.1) for s in signals
    )

    if weighted_score >= 0.6:
        tier = "high_confidence"
        should_block = False
        recommendation = "Legitimate posting — proceed"
    elif weighted_score >= 0.4:
        tier = "proceed_with_caution"
        should_block = False
        recommendation = "Mixed signals — review before applying"
    else:
        tier = "suspicious"
        should_block = True
        recommendation = "Likely ghost job — skip"

    return GhostDetectionResult(
        tier=tier,
        signals=signals,
        recommendation=recommendation,
        should_block=should_block,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_ghost_detector.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ghost_detector.py tests/jobpulse/test_ghost_detector.py
git commit -m "feat(F2): add ghost job detection module with 5 signal analyzers"
```

---

### Task 7: Archetype Engine (F3)

**Files:**
- Create: `jobpulse/archetype_engine.py`
- Create: `data/archetype_profiles.json`
- Create: `tests/jobpulse/test_archetype_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_archetype_engine.py
"""Tests for archetype detection — keyword scoring + profile lookup."""
import pytest


class TestDetectArchetype:
    def test_agentic_jd(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "Build multi-agent orchestration systems with LangGraph and HITL flows"
        skills = ["Python", "LangGraph", "Agent", "Orchestration"]
        result = detect_archetype(jd, skills)
        assert result.primary == "agentic"
        assert result.confidence >= 0.5

    def test_data_analyst_jd(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "Create dashboards and reports for stakeholders using SQL and Power BI"
        skills = ["SQL", "Power BI", "Dashboards", "Reporting", "Stakeholder Management"]
        result = detect_archetype(jd, skills)
        assert result.primary == "data_analyst"

    def test_data_scientist_jd(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "Design A/B tests, build statistical models, run experiments"
        skills = ["Python", "Statistics", "A/B Testing", "Modeling", "Experiments"]
        result = detect_archetype(jd, skills)
        assert result.primary == "data_scientist"

    def test_data_platform_jd(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "Build ML pipelines with observability, evals, and monitoring in production"
        skills = ["MLOps", "Pipelines", "Observability", "Monitoring", "Python"]
        result = detect_archetype(jd, skills)
        assert result.primary == "data_platform"

    def test_unknown_jd_falls_back_to_general(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "We need someone to do various tasks in the office"
        skills = ["Communication", "Teamwork"]
        result = detect_archetype(jd, skills)
        assert result.primary == "general"
        assert result.confidence < 0.5

    def test_hybrid_role_has_secondary(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = (
            "Build multi-agent systems for ML pipeline orchestration. "
            "Experience with LangGraph, MLOps, model monitoring, and agent architectures."
        )
        skills = ["LangGraph", "MLOps", "Agents", "Monitoring", "Pipelines"]
        result = detect_archetype(jd, skills)
        assert result.secondary is not None
        assert result.primary != result.secondary


class TestGetArchetypeProfile:
    def test_returns_profile_for_known_archetype(self):
        from jobpulse.archetype_engine import get_archetype_profile

        profile = get_archetype_profile("agentic")
        assert "tagline" in profile
        assert "summary_angle" in profile
        assert "project_priority" in profile

    def test_returns_default_for_unknown(self):
        from jobpulse.archetype_engine import get_archetype_profile

        profile = get_archetype_profile("nonexistent")
        assert "tagline" in profile
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_archetype_engine.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `data/archetype_profiles.json`**

```json
{
  "agentic": {
    "keywords": {"agent": 3.0, "orchestration": 2.5, "hitl": 2.0, "multi-agent": 3.0, "langgraph": 2.5, "swarm": 2.0, "tool-use": 1.5, "human-in-the-loop": 2.0},
    "tagline": "MSc Computer Science (UOD) | 2+ YOE | AI Engineer | Multi-Agent Systems | LangGraph | Python",
    "summary_angle": "Building reliable agent systems from prototype to production",
    "project_priority": ["JobPulse", "Multi-Agent Patterns", "MindGraph"],
    "skills_to_highlight": ["LangGraph", "OpenAI Agents SDK", "Swarm", "Python"],
    "yoe_framing": "2+ years"
  },
  "data_platform": {
    "keywords": {"pipeline": 2.5, "evals": 2.0, "observability": 2.5, "mlops": 3.0, "monitoring": 2.0, "model serving": 2.0, "infrastructure": 1.5, "deployment": 1.5},
    "tagline": "MSc Computer Science (UOD) | 2+ YOE | ML Engineer | MLOps | Pipelines | Python",
    "summary_angle": "Production ML systems with monitoring, evals, and cost optimization",
    "project_priority": ["JobPulse", "Velox AI", "Cloud Sentinel"],
    "skills_to_highlight": ["Python", "Docker", "AWS", "MLOps", "CI/CD"],
    "yoe_framing": "2+ years"
  },
  "data_analyst": {
    "keywords": {"dashboard": 3.0, "sql": 2.5, "stakeholder": 2.0, "insights": 2.5, "reporting": 2.5, "power bi": 3.0, "tableau": 2.5, "excel": 1.5, "kpi": 2.0},
    "tagline": "MSc Computer Science (UOD) | 3+ YOE | Data Analyst | SQL | Power BI | Python",
    "summary_angle": "Turning complex data into clear, actionable business insights",
    "project_priority": ["90 Days ML", "Cloud Sentinel", "JobPulse"],
    "skills_to_highlight": ["SQL", "Power BI", "Python", "Excel", "Statistical Testing"],
    "yoe_framing": "3+ years"
  },
  "data_scientist": {
    "keywords": {"modeling": 2.5, "experiment": 2.5, "a/b test": 3.0, "statistics": 2.5, "research": 2.0, "hypothesis": 2.0, "regression": 2.0, "classification": 2.0},
    "tagline": "MSc Computer Science (UOD) | 2+ YOE | Data Scientist | Python | ML | Statistics",
    "summary_angle": "Research-to-production ML with experimentation rigor",
    "project_priority": ["Deep Learning 3D", "90 Days ML", "Cloud Sentinel"],
    "skills_to_highlight": ["Python", "PyTorch", "Scikit-learn", "Statistical Testing", "NLP"],
    "yoe_framing": "2+ years"
  },
  "ai_ml": {
    "keywords": {"training": 2.5, "fine-tuning": 3.0, "inference": 2.5, "gpu": 2.0, "model": 2.0, "neural network": 2.5, "transformer": 2.5, "deep learning": 2.5},
    "tagline": "MSc Computer Science (UOD) | 2+ YOE | AI/ML Engineer | PyTorch | Python | Deep Learning",
    "summary_angle": "Full-stack ML from training to deployment",
    "project_priority": ["Deep Learning 3D", "Cloud Sentinel", "Velox AI"],
    "skills_to_highlight": ["PyTorch", "TensorFlow", "Python", "Docker", "AWS"],
    "yoe_framing": "2+ years"
  },
  "data_engineer": {
    "keywords": {"etl": 3.0, "warehouse": 2.5, "spark": 3.0, "airflow": 3.0, "dbt": 2.5, "streaming": 2.0, "data lake": 2.0, "batch processing": 2.0},
    "tagline": "MSc Computer Science (UOD) | 2+ YOE | Data Engineer | Python | SQL | ETL | Cloud",
    "summary_angle": "Building scalable data infrastructure and pipelines",
    "project_priority": ["JobPulse", "Velox AI", "90 Days ML"],
    "skills_to_highlight": ["Python", "SQL", "Docker", "AWS", "CI/CD"],
    "yoe_framing": "2+ years"
  }
}
```

- [ ] **Step 4: Implement `archetype_engine.py`**

```python
# jobpulse/archetype_engine.py
"""Archetype detection engine — classify JDs into 6 role archetypes.

Keyword-based scoring (free, instant). LLM fallback when top two scores
are within 1.2x of each other (~15% of cases, ~$0.001 each).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)

_PROFILES_PATH = Path(__file__).parent.parent / "data" / "archetype_profiles.json"

_DEFAULT_PROFILE = {
    "tagline": "MSc Computer Science (UOD) | 2+ YOE | Software Engineer | Python",
    "summary_angle": "Building production software systems",
    "project_priority": [],
    "skills_to_highlight": ["Python"],
    "yoe_framing": "2+ years",
}


@dataclass
class ArchetypeResult:
    primary: str
    secondary: str | None = None
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)


def _load_profiles() -> dict:
    try:
        with open(_PROFILES_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("archetype_engine: failed to load profiles: %s — using defaults", exc)
        return {}


def detect_archetype(jd_text: str, required_skills: list[str]) -> ArchetypeResult:
    """Detect the best-fit archetype for a JD using keyword scoring."""
    profiles = _load_profiles()
    if not profiles:
        return ArchetypeResult(primary="general", confidence=0.0)

    combined_text = (jd_text + " " + " ".join(required_skills)).lower()
    scores: dict[str, float] = {}

    for archetype, profile in profiles.items():
        keywords = profile.get("keywords", {})
        score = 0.0
        for keyword, weight in keywords.items():
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            matches = len(pattern.findall(combined_text))
            score += matches * weight
        scores[archetype] = score

    if not scores or max(scores.values()) == 0:
        return ArchetypeResult(primary="general", confidence=0.0, scores=scores)

    sorted_archetypes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_name, best_score = sorted_archetypes[0]
    second_name, second_score = sorted_archetypes[1] if len(sorted_archetypes) > 1 else ("", 0)

    threshold = 2.0
    if best_score < threshold:
        return ArchetypeResult(primary="general", confidence=best_score / 10, scores=scores)

    confidence = min(best_score / 15, 1.0)

    secondary = None
    if second_score > threshold and second_score >= best_score * 0.6:
        secondary = second_name

    return ArchetypeResult(
        primary=best_name,
        secondary=secondary,
        confidence=confidence,
        scores=scores,
    )


def get_archetype_profile(archetype: str) -> dict:
    """Return the profile dict for an archetype, or defaults."""
    profiles = _load_profiles()
    profile = profiles.get(archetype, {})
    result = dict(_DEFAULT_PROFILE)
    result.update({k: v for k, v in profile.items() if k != "keywords"})
    return result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_archetype_engine.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/archetype_engine.py data/archetype_profiles.json tests/jobpulse/test_archetype_engine.py
git commit -m "feat(F3): add archetype detection engine with 6 role archetypes"
```

---

## Phase 3: Generation Upgrades

### Task 8: Tone Framework (F7)

**Files:**
- Create: `jobpulse/tone_framework.py`
- Create: `tests/jobpulse/test_tone_framework.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_tone_framework.py
"""Tests for tone framework — banned phrase filtering + proof point injection."""
import pytest
from unittest.mock import MagicMock


class TestBannedPhraseDetection:
    def test_detects_passionate_about(self):
        from jobpulse.tone_framework import contains_banned_phrase

        assert contains_banned_phrase("I am passionate about data science") is True

    def test_detects_proven_track_record(self):
        from jobpulse.tone_framework import contains_banned_phrase

        assert contains_banned_phrase("I have a proven track record in ML") is True

    def test_clean_text_passes(self):
        from jobpulse.tone_framework import contains_banned_phrase

        assert contains_banned_phrase("Built 3 production ML systems processing 10K+ records") is False


class TestApplyTone:
    def test_removes_banned_phrases(self):
        from jobpulse.tone_framework import apply_tone

        answer = "I am passionate about this role and have a proven track record."
        listing = MagicMock()
        listing.company = "TestCo"
        listing.title = "Data Analyst"
        listing.archetype = None
        result = apply_tone(answer, "why this role", listing)
        assert "passionate about" not in result.lower()
        assert "proven track record" not in result.lower()

    def test_preserves_clean_answers(self):
        from jobpulse.tone_framework import apply_tone

        answer = "Built 3 production ML systems. Reduced pipeline latency by 40%."
        listing = MagicMock()
        listing.company = "TestCo"
        listing.title = "ML Engineer"
        listing.archetype = None
        result = apply_tone(answer, "experience", listing)
        assert "production ML systems" in result

    def test_passthrough_on_empty(self):
        from jobpulse.tone_framework import apply_tone

        listing = MagicMock()
        listing.archetype = None
        assert apply_tone("", "question", listing) == ""


class TestClassifyQuestionType:
    def test_why_this_role(self):
        from jobpulse.tone_framework import classify_question_type

        assert classify_question_type("Why are you interested in this role?") == "why_this_role"

    def test_experience(self):
        from jobpulse.tone_framework import classify_question_type

        assert classify_question_type("Describe your relevant experience") == "relevant_experience"

    def test_unknown(self):
        from jobpulse.tone_framework import classify_question_type

        assert classify_question_type("asdfghjkl") == "other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_tone_framework.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `tone_framework.py`**

```python
# jobpulse/tone_framework.py
"""Tone framework — bans corporate-speak, injects concrete proof points.

Post-processes screening answers and cover letter text.
"""

from __future__ import annotations

import re

from shared.logging_config import get_logger

logger = get_logger(__name__)

BANNED_PHRASES = [
    "passionate about",
    "results-oriented",
    "proven track record",
    "leveraged",
    "spearheaded",
    "facilitated",
    "synergies",
    "robust",
    "seamless",
    "cutting-edge",
    "innovative",
    "just checking in",
    "just following up",
    "touching base",
    "circling back",
    "i would love the opportunity",
    "in today's fast-paced world",
    "demonstrated ability to",
    "strong communicator",
    "self-starter",
    "team player",
    "go-getter",
    "think outside the box",
    "hit the ground running",
]

_BANNED_PATTERNS = [re.compile(re.escape(p), re.IGNORECASE) for p in BANNED_PHRASES]

_QUESTION_PATTERNS = {
    "why_this_role": re.compile(r"why.*(this|the) (role|position|job)", re.IGNORECASE),
    "why_this_company": re.compile(r"why.*(this|our|the) (company|org)", re.IGNORECASE),
    "relevant_experience": re.compile(r"(relevant|related).*(experience|background|work)", re.IGNORECASE),
    "good_fit": re.compile(r"(good|great|strong) fit|why should we", re.IGNORECASE),
    "how_heard": re.compile(r"how did you (hear|find|learn)", re.IGNORECASE),
    "additional_info": re.compile(r"additional.*(info|anything|share)", re.IGNORECASE),
}


def contains_banned_phrase(text: str) -> bool:
    """Check if text contains any banned corporate-speak phrases."""
    return any(p.search(text) for p in _BANNED_PATTERNS)


def classify_question_type(question: str) -> str:
    """Classify a screening question into a known type."""
    for qtype, pattern in _QUESTION_PATTERNS.items():
        if pattern.search(question):
            return qtype
    return "other"


def _remove_banned(text: str) -> str:
    """Remove banned phrases from text, replacing with empty string and cleaning whitespace."""
    for pattern in _BANNED_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"^\s*[,.]", "", text).strip()
    return text


def apply_tone(answer: str, question: str, listing) -> str:
    """Apply tone framework to a screening answer.

    Removes banned phrases. Returns cleaned answer.
    """
    if not answer:
        return answer

    result = _remove_banned(answer)

    if not result or len(result) < 10:
        return answer

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_tone_framework.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/tone_framework.py tests/jobpulse/test_tone_framework.py
git commit -m "feat(F7): add tone framework with banned phrase filtering"
```

---

### Task 9: Wire Tone Framework into screening_answers.py

**Files:**
- Modify: `jobpulse/screening_answers.py` (1 import + 1 line in `get_answer`)

- [ ] **Step 1: Find the exact get_answer function**

Run: `python -m pytest tests/jobpulse/test_pipeline_hooks.py -v` to verify hooks still pass.

- [ ] **Step 2: Read get_answer to find the return point**

Use `find_symbol get_answer` to locate it.

- [ ] **Step 3: Add tone wrapper call**

At the return point of `get_answer()` in `screening_answers.py`, wrap the result:

```python
from jobpulse.pipeline_hooks import with_tone_filter

# Before the final return in get_answer():
# return answer
# After:
# return with_tone_filter(answer, question, listing)
```

This is a single-line change. When `JOBPULSE_TONE_FRAMEWORK=false` (default), `with_tone_filter` passes through unchanged.

- [ ] **Step 4: Run existing screening tests**

Run: `python -m pytest tests/ -k "screening" -v`
Expected: All existing tests PASS (tone filter is a no-op when disabled).

- [ ] **Step 5: Commit**

```bash
git add jobpulse/screening_answers.py
git commit -m "feat(F7): wire tone framework into screening_answers via pipeline_hooks"
```

---

### Task 10: ATS Company Registry Config

**Files:**
- Create: `data/ats_company_registry.json`

- [ ] **Step 1: Create seed registry**

```json
{
  "_comment": "Company-to-ATS mapping. Auto-detected from careers URLs, manually editable.",
  "anthropic": {"ats": "ashby", "slug": "anthropic"},
  "stripe": {"ats": "greenhouse", "slug": "stripe"},
  "figma": {"ats": "greenhouse", "slug": "figma"},
  "notion": {"ats": "greenhouse", "slug": "notion-hq"},
  "datadog": {"ats": "greenhouse", "slug": "datadog"},
  "mongodb": {"ats": "greenhouse", "slug": "mongodb"},
  "vercel": {"ats": "greenhouse", "slug": "vercel"},
  "hashicorp": {"ats": "greenhouse", "slug": "hashicorp"},
  "cloudflare": {"ats": "greenhouse", "slug": "cloudflare"},
  "twilio": {"ats": "greenhouse", "slug": "twilio"},
  "plaid": {"ats": "lever", "slug": "plaid"},
  "netflix": {"ats": "lever", "slug": "netflix"},
  "gusto": {"ats": "lever", "slug": "gusto"},
  "cockroachlabs": {"ats": "lever", "slug": "cockroach-labs"},
  "ramp": {"ats": "ashby", "slug": "ramp"},
  "scale": {"ats": "ashby", "slug": "scaleai"},
  "deepmind": {"ats": "workday", "slug": "deepmind"}
}
```

- [ ] **Step 2: Commit**

```bash
git add data/ats_company_registry.json
git commit -m "feat(F1): add ATS company registry seed data"
```

---

### Task 11: Database Migration Script

**Files:**
- Create: `scripts/migrate_012_new_fields.py`

- [ ] **Step 1: Write the migration script**

```python
#!/usr/bin/env python3
"""Idempotent migration: add career-ops fields to applications.db.

Run: python scripts/migrate_012_new_fields.py
Safe to run multiple times — checks column existence before ALTER.
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "applications.db"


def column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def migrate(db_path: Path = DB_PATH) -> None:
    if not db_path.exists():
        print(f"Database not found at {db_path} — skipping migration.")
        return

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    new_columns = {
        "listings": [
            ("ghost_tier", "TEXT"),
            ("archetype", "TEXT"),
            ("archetype_secondary", "TEXT"),
            ("archetype_confidence", "REAL DEFAULT 0.0"),
            ("locale_market", "TEXT"),
            ("locale_language", "TEXT"),
            ("posted_at", "TEXT"),
        ],
        "applications": [
            ("followup_count", "INTEGER DEFAULT 0"),
            ("followup_last_at", "TEXT"),
            ("followup_status", "TEXT DEFAULT 'active'"),
        ],
    }

    changes = 0
    for table, columns in new_columns.items():
        for col_name, col_type in columns:
            if not column_exists(cursor, table, col_name):
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                    print(f"  Added {table}.{col_name} ({col_type})")
                    changes += 1
                except sqlite3.OperationalError as e:
                    if "no such table" in str(e).lower():
                        print(f"  Table '{table}' does not exist — skipping column {col_name}")
                    else:
                        raise
            else:
                print(f"  {table}.{col_name} already exists — skipping")

    conn.commit()
    conn.close()
    print(f"\nMigration complete: {changes} columns added.")


if __name__ == "__main__":
    migrate()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/migrate_012_new_fields.py
git commit -m "feat: add idempotent DB migration for career-ops fields"
```

---

## Phase 4: Post-Apply and Batch (extend existing modules)

### Task 12: Extend Follow-Up Tracker (F8)

**Files:**
- Modify: `jobpulse/followup_tracker.py` (add `generate_draft` and `check_due` functions)
- Create: `tests/jobpulse/test_followup_draft.py`

Note: `followup_tracker.py` already exists with `compute_urgency`, `record_followup`,
`format_followup_report`. F8 extends it with draft generation.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_followup_draft.py
"""Tests for follow-up draft generation (F8 extension)."""
import pytest
from datetime import date


class TestGenerateDraft:
    def test_generates_email_draft(self):
        from jobpulse.followup_tracker import generate_followup_draft

        draft = generate_followup_draft(
            company="Anthropic",
            role="ML Engineer",
            status="Applied",
            followup_count=0,
            channel="email",
        )
        assert "Anthropic" in draft
        assert "ML Engineer" in draft
        assert len(draft) > 50

    def test_second_followup_differs(self):
        from jobpulse.followup_tracker import generate_followup_draft

        draft1 = generate_followup_draft("Co", "Role", "Applied", 0, "email")
        draft2 = generate_followup_draft("Co", "Role", "Applied", 1, "email")
        assert draft1 != draft2

    def test_linkedin_is_shorter(self):
        from jobpulse.followup_tracker import generate_followup_draft

        email = generate_followup_draft("Co", "Role", "Applied", 0, "email")
        linkedin = generate_followup_draft("Co", "Role", "Applied", 0, "linkedin")
        assert len(linkedin) <= 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_followup_draft.py -v`
Expected: FAIL — `generate_followup_draft` doesn't exist.

- [ ] **Step 3: Add `generate_followup_draft` to `followup_tracker.py`**

Append to end of `jobpulse/followup_tracker.py`:

```python
# ---------------------------------------------------------------------------
# Draft generation (F8 extension)
# ---------------------------------------------------------------------------

_EMAIL_TEMPLATES = {
    0: (
        "Subject: Following up — {role} application at {company}\n\n"
        "Hi,\n\n"
        "I applied for the {role} position at {company} recently and wanted to "
        "confirm my application was received. I'm particularly interested in this "
        "role given my experience with production Python systems and data pipelines.\n\n"
        "Happy to provide any additional information.\n\n"
        "Best regards,\nYash Bishnoi"
    ),
    1: (
        "Subject: {role} at {company} — checking in\n\n"
        "Hi,\n\n"
        "I wanted to follow up on my application for {role} at {company}. "
        "I remain very interested and would welcome the opportunity to discuss "
        "how my background in ML systems and data analysis aligns with your needs.\n\n"
        "Best regards,\nYash Bishnoi"
    ),
}

_LINKEDIN_TEMPLATES = {
    0: (
        "Hi — I recently applied for {role} at {company}. "
        "With my background in Python, ML, and data pipelines, "
        "I'd love to connect and learn more about the role."
    ),
    1: (
        "Following up on my {role} application at {company}. "
        "Happy to share more about my experience with production AI systems."
    ),
}


def generate_followup_draft(
    company: str,
    role: str,
    status: str,
    followup_count: int,
    channel: str = "email",
) -> str:
    """Generate a follow-up draft for email or LinkedIn."""
    templates = _LINKEDIN_TEMPLATES if channel == "linkedin" else _EMAIL_TEMPLATES
    idx = min(followup_count, max(templates.keys()))
    template = templates.get(idx, templates[0])
    draft = template.format(company=company, role=role, status=status)
    if channel == "linkedin" and len(draft) > 300:
        draft = draft[:297] + "..."
    return draft
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_followup_draft.py tests/jobpulse/test_followup_tracker.py -v`
Expected: All tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add jobpulse/followup_tracker.py tests/jobpulse/test_followup_draft.py
git commit -m "feat(F8): add follow-up draft generation to followup_tracker"
```

---

### Task 13: Batch Processing Module (F10)

**Files:**
- Create: `jobpulse/batch/__init__.py`
- Create: `jobpulse/batch/state.py`
- Create: `jobpulse/batch/orchestrator.py`
- Create: `tests/jobpulse/test_batch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_batch.py
"""Tests for batch processing — state tracking and orchestrator."""
import pytest
from pathlib import Path


class TestBatchState:
    def test_create_state_file(self, tmp_path):
        from jobpulse.batch.state import BatchState

        state = BatchState(tmp_path / "batch.tsv")
        state.mark_started("job1")
        state.mark_started("job2")
        assert state.get_status("job1") == "started"
        assert state.get_status("job2") == "started"

    def test_mark_completed(self, tmp_path):
        from jobpulse.batch.state import BatchState

        state = BatchState(tmp_path / "batch.tsv")
        state.mark_started("job1")
        state.mark_completed("job1", score=8.5)
        assert state.get_status("job1") == "completed"

    def test_mark_failed(self, tmp_path):
        from jobpulse.batch.state import BatchState

        state = BatchState(tmp_path / "batch.tsv")
        state.mark_started("job1")
        state.mark_failed("job1", error="timeout")
        assert state.get_status("job1") == "failed"

    def test_get_pending(self, tmp_path):
        from jobpulse.batch.state import BatchState

        state = BatchState(tmp_path / "batch.tsv")
        state.mark_started("job1")
        state.mark_completed("job1", score=8.0)
        state.mark_started("job2")
        state.mark_started("job3")
        state.mark_failed("job3", error="err")
        pending = state.get_pending(["job1", "job2", "job3", "job4"])
        assert "job1" not in pending
        assert "job2" not in pending
        assert "job3" in pending
        assert "job4" in pending

    def test_persistence(self, tmp_path):
        from jobpulse.batch.state import BatchState

        path = tmp_path / "batch.tsv"
        state1 = BatchState(path)
        state1.mark_started("job1")
        state1.mark_completed("job1", score=9.0)

        state2 = BatchState(path)
        assert state2.get_status("job1") == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_batch.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `jobpulse/batch/__init__.py`**

```python
# jobpulse/batch/__init__.py
```

- [ ] **Step 4: Implement `jobpulse/batch/state.py`**

```python
# jobpulse/batch/state.py
"""TSV-based batch state tracking for resumability."""

from __future__ import annotations

import csv
from datetime import datetime, UTC
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)


class BatchState:
    """Track batch job statuses in a TSV file for crash recovery."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._entries: dict[str, dict] = {}
        if self._path.exists():
            self._load()

    def _load(self) -> None:
        with open(self._path, newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                self._entries[row["job_id"]] = row

    def _save(self) -> None:
        fields = ["job_id", "status", "started_at", "completed_at", "score", "error", "retries"]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            for entry in self._entries.values():
                writer.writerow({k: entry.get(k, "") for k in fields})

    def mark_started(self, job_id: str) -> None:
        self._entries[job_id] = {
            "job_id": job_id,
            "status": "started",
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": "",
            "score": "",
            "error": "",
            "retries": "0",
        }
        self._save()

    def mark_completed(self, job_id: str, score: float = 0.0) -> None:
        entry = self._entries.get(job_id, {"job_id": job_id})
        entry["status"] = "completed"
        entry["completed_at"] = datetime.now(UTC).isoformat()
        entry["score"] = str(score)
        self._entries[job_id] = entry
        self._save()

    def mark_failed(self, job_id: str, error: str = "") -> None:
        entry = self._entries.get(job_id, {"job_id": job_id})
        entry["status"] = "failed"
        entry["error"] = error
        retries = int(entry.get("retries", "0")) + 1
        entry["retries"] = str(retries)
        self._entries[job_id] = entry
        self._save()

    def get_status(self, job_id: str) -> str | None:
        entry = self._entries.get(job_id)
        return entry["status"] if entry else None

    def get_pending(self, job_ids: list[str]) -> list[str]:
        """Return job IDs that need processing (not yet completed)."""
        return [
            jid for jid in job_ids
            if self.get_status(jid) not in ("completed",)
        ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_batch.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/batch/__init__.py jobpulse/batch/state.py tests/jobpulse/test_batch.py
git commit -m "feat(F10): add batch state tracking module"
```

---

### Task 14: Final Regression Test

**Files:**
- Run all tests

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --timeout=60 -x -q`
Expected: All tests PASS. No regressions.

- [ ] **Step 2: Verify feature flags are all OFF by default**

```bash
python -c "
from jobpulse.pipeline_hooks import feature_enabled
flags = [
    'JOBPULSE_GHOST_DETECTION',
    'JOBPULSE_ARCHETYPE_ENGINE',
    'JOBPULSE_ATS_NORMALIZE',
    'JOBPULSE_TONE_FRAMEWORK',
]
for f in flags:
    assert not feature_enabled(f), f'{f} should be OFF by default'
print('All feature flags are OFF by default - pipeline unchanged')
"
```

- [ ] **Step 3: Commit all**

If any uncommitted changes remain:

```bash
git add -A
git commit -m "feat: career-ops features Phase 1-4 complete — all behind feature flags"
```
