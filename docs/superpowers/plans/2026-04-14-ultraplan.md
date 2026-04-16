# JobPulse Ultraplan — Career-Ops Gap Close + Bug Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 47 failing tests, delete Ralph Loop, add 5 missing features inspired by career-ops (liveness detection, ATS API scanning, rejection pattern analysis, follow-up cadence, interview prep).

**Architecture:** 7 phases executed sequentially. Phase 1 (test fixes) and Phase 2 (Ralph Loop deletion) must complete first — they unblock everything. Phases 3-7 are independent features that can run in parallel via subagents. Each new feature is a self-contained module in `jobpulse/` with its own test file and Telegram command integration.

**Tech Stack:** Python 3.12, httpx (ATS APIs), sqlite3 (analytics/follow-ups), pytest, ReportLab, existing shared/ utilities.

---

## Phase 1: Fix Failing Tests (47 failures → 0)

### Task 1.1: Delete Stale Test Files (2 collection errors)

**Files:**
- Delete: `tests/test_cv_tailor.py`
- Delete: `tests/test_ralph_loop.py` (duplicate of `tests/jobpulse/test_ralph_loop.py`)

- [ ] **Step 1: Verify the stale files**

```bash
python -m pytest tests/test_cv_tailor.py --collect-only 2>&1 | head -5
# Expected: ModuleNotFoundError: No module named 'jobpulse.cv_tailor'

python -m pytest tests/test_ralph_loop.py --collect-only 2>&1 | head -5
# Expected: import file mismatch error
```

- [ ] **Step 2: Delete both files**

```bash
rm tests/test_cv_tailor.py
rm tests/test_ralph_loop.py
```

- [ ] **Step 3: Verify collection passes**

```bash
python -m pytest tests/ --collect-only 2>&1 | tail -5
# Expected: no collection errors
```

- [ ] **Step 4: Commit**

```bash
git add -u tests/test_cv_tailor.py tests/test_ralph_loop.py
git commit -m "fix(tests): delete stale test_cv_tailor.py and duplicate test_ralph_loop.py"
```

---

### Task 1.2: Fix NLP Classifier Semantic Tests (14 failures)

**Files:**
- Modify: `tests/test_nlp_classifier.py:8-81` (TestClassifySemantic)
- Modify: `tests/test_nlp_classifier.py:133-159` (TestThreeTierClassify)
- Modify: `jobpulse/nlp_classifier.py:274-291` (get_stats)

**Root cause:** `classify_semantic()` calls `_load_model()` which tries Ollama at localhost:11434. When Ollama is not running, `_model = None` and all calls return `("unknown", 0.0)`, failing all score assertions.

- [ ] **Step 1: Add skip guard to TestClassifySemantic**

In `tests/test_nlp_classifier.py`, replace the autouse fixture in `TestClassifySemantic` (lines 11-14):

```python
@pytest.fixture(autouse=True)
def _require_embedding_model(self):
    """Skip all semantic tests if no embedding model is available."""
    from jobpulse.nlp_classifier import _load_model
    model = _load_model()
    if model is None:
        pytest.skip("No embedding model available (Ollama not running / sentence-transformers not installed)")
```

- [ ] **Step 2: Add skip guard to TestThreeTierClassify NLP-dependent tests**

The `test_nlp_tier_catches_natural` and `test_nlp_tier_catches_slang` tests exercise the semantic tier. Add a fixture:

```python
@pytest.fixture(autouse=True)
def _require_embedding_model(self):
    from jobpulse.nlp_classifier import _load_model
    model = _load_model()
    if model is None:
        pytest.skip("No embedding model available")
```

The `test_unknown_falls_to_conversation` also needs this — it asserts `score < 0.85` from the semantic tier.

- [ ] **Step 3: Fix get_stats() hardcoded model name**

In `jobpulse/nlp_classifier.py`, replace line 286:

```python
# Before:
"model": "all-MiniLM-L6-v2" if _model else "not loaded",

# After:
"model": (
    getattr(_model, "model", "all-MiniLM-L6-v2")
    if _model else "not loaded"
),
```

This reads `_OllamaEmbedder.model` (which is `"nomic-embed-text:v1.5"`) when using Ollama, falls back to `"all-MiniLM-L6-v2"` for SentenceTransformers (which doesn't have a `.model` attr).

- [ ] **Step 4: Fix test_stats_loaded assertion**

In `tests/test_nlp_classifier.py`, find the `TestGetStats` test (around line 121-130). Replace:

```python
# Before:
assert "MiniLM" in stats["model"]

# After:
assert stats["model"] != "not loaded"
```

- [ ] **Step 5: Run the NLP tests**

```bash
python -m pytest tests/test_nlp_classifier.py -v 2>&1 | tail -20
# Expected: all tests either PASS or SKIP (no FAIL)
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_nlp_classifier.py jobpulse/nlp_classifier.py
git commit -m "fix(tests): skip NLP semantic tests when Ollama unavailable, fix get_stats model name"
```

---

### Task 1.3: Fix Arxiv Agent Test Patches (4 failures)

**Files:**
- Modify: `tests/test_arxiv_agent.py:206-344`

**Root cause:** Tests patch `openai.OpenAI` but `llm_rank_broad()` calls `get_openai_client()` from `shared.agents`, which creates the client internally. The mock never intercepts.

- [ ] **Step 1: Find the correct patch target**

```bash
python -c "from jobpulse.arxiv_agent import llm_rank_broad; import inspect; print(inspect.getfile(llm_rank_broad))"
```

Verify `llm_rank_broad` uses `get_openai_client()` (not `openai.OpenAI` directly).

- [ ] **Step 2: Fix all patches in TestLlmRankBroad**

In `tests/test_arxiv_agent.py`, in every test inside `TestLlmRankBroad`, replace:

```python
# Before:
with patch("openai.OpenAI", mock_cls):

# After:
with patch("jobpulse.arxiv_agent.get_openai_client", return_value=mock_instance):
```

Note: `mock_instance` is the second return value from `_mock_openai_class()`. The old code patches the class constructor; the new code patches the function that returns a client instance.

For each test, change the pattern from:

```python
mock_cls, mock_instance = _mock_openai_class(response_json)
with patch("jobpulse.arxiv_agent.OPENAI_API_KEY", "sk-test"), \
     patch("openai.OpenAI", mock_cls):
```

To:

```python
mock_cls, mock_instance = _mock_openai_class(response_json)
with patch("jobpulse.arxiv_agent.OPENAI_API_KEY", "sk-test"), \
     patch("jobpulse.arxiv_agent.get_openai_client", return_value=mock_instance):
```

- [ ] **Step 3: Fix the same pattern in test_json_parsing**

Same change — replace `patch("openai.OpenAI", mock_cls)` with `patch("jobpulse.arxiv_agent.get_openai_client", return_value=mock_instance)`.

- [ ] **Step 4: Verify arxiv_agent imports get_openai_client**

Check `jobpulse/arxiv_agent.py` — if `get_openai_client` is not imported at module level, add:

```python
from shared.agents import get_openai_client
```

If it uses a different name or calls it differently, adjust the patch target accordingly.

- [ ] **Step 5: Run arxiv tests**

```bash
python -m pytest tests/test_arxiv_agent.py -v -k "LlmRank or json_parsing" 2>&1 | tail -15
# Expected: all PASS
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_arxiv_agent.py jobpulse/arxiv_agent.py
git commit -m "fix(tests): patch get_openai_client instead of openai.OpenAI in arxiv tests"
```

---

### Task 1.4: Fix Pattern Memory Integration Test (1 failure)

**Files:**
- Modify: `tests/test_pattern_memory_integration.py:250-285`

**Root cause:** Test patches `shared.memory_layer.MemoryManager` (the class constructor) but the code uses `get_shared_memory_manager()` singleton, which may already be initialized.

- [ ] **Step 1: Fix the patch target**

Replace the `MemoryManager` patch with a patch of the singleton getter:

```python
# Before:
patch("shared.memory_layer.MemoryManager") as mock_mm_cls:
    mock_instance = mock_mm_cls.return_value

# After:
patch("shared.memory_layer.get_shared_memory_manager") as mock_get_mm:
    mock_instance = MagicMock()
    mock_get_mm.return_value = mock_instance
```

- [ ] **Step 2: Also reset the module-level singleton**

Add at the start of the test (inside the `with` block):

```python
import shared.memory_layer
shared.memory_layer._shared_manager = None  # Force re-fetch via our mock
```

Or patch the module-level variable directly:

```python
patch("shared.memory_layer._shared_manager", None),
```

- [ ] **Step 3: Run the test**

```bash
python -m pytest tests/test_pattern_memory_integration.py::TestLearnFact::test_fact_check_node_calls_learn_fact -v
# Expected: PASS
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_pattern_memory_integration.py
git commit -m "fix(tests): patch get_shared_memory_manager instead of MemoryManager class"
```

---

### Task 1.5: Fix Remaining LLM-Dependent Test Failures

**Files:**
- Modify: `tests/test_budget_agent.py` (TestClassifyTransaction::test_llm_fallback_on_unknown_description)
- Modify: `tests/test_command_router.py` (TestClassify::test_falls_back_to_llm)

**Root cause:** These tests exercise LLM fallback paths and likely make real API calls or fail when the model returns unexpected output.

- [ ] **Step 1: Read each failing test to identify the exact patch issue**

```bash
python -m pytest tests/test_budget_agent.py::TestClassifyTransaction::test_llm_fallback_on_unknown_description -v --tb=long 2>&1 | tail -30
python -m pytest tests/test_command_router.py::TestClassify::test_falls_back_to_llm -v --tb=long 2>&1 | tail -30
```

- [ ] **Step 2: Fix the patch targets**

Apply the same pattern as Task 1.3 — find what the code actually calls (likely `smart_llm_call` or `get_llm` from `shared.agents`) and patch that instead of the raw OpenAI/LangChain constructor.

- [ ] **Step 3: Run both tests**

```bash
python -m pytest tests/test_budget_agent.py::TestClassifyTransaction::test_llm_fallback_on_unknown_description tests/test_command_router.py::TestClassify::test_falls_back_to_llm -v
# Expected: PASS
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_budget_agent.py tests/test_command_router.py
git commit -m "fix(tests): correct LLM mock patch targets in budget and command router tests"
```

---

### Task 1.6: Fix job_autopilot.py Code Structure

**Files:**
- Modify: `jobpulse/job_autopilot.py:36-53`

- [ ] **Step 1: Move determine_match_tier after all imports**

Currently at line 39-45, sandwiched between imports. Move the function definition after the last import (after line 53). The import block should be continuous:

```python
# Line 36-37 stay as-is:
from jobpulse.applicator import classify_action
from jobpulse.ralph_loop import ralph_apply_sync  # Will change in Phase 2
from jobpulse.config import DATA_DIR, JOB_AUTOPILOT_ENABLED, JOB_AUTOPILOT_MAX_DAILY
from jobpulse.cv_templates.generate_cv import generate_cv_pdf, build_extra_skills, get_role_profile
from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
# ... rest of imports ...

# After all imports:
def determine_match_tier(ats_score: float) -> str:
    """Return 'auto' if >= 90, 'review' if >= 82, 'skip' otherwise."""
    if ats_score >= 90:
        return "auto"
    if ats_score >= 82:
        return "review"
    return "skip"
```

- [ ] **Step 2: Update docstring reference**

In `job_autopilot.py`, line 10 references `cv_tailor.determine_match_tier`. Change to:

```python
# L7: Score & tier (determine_match_tier — inline)
```

- [ ] **Step 3: Run autopilot tests**

```bash
python -m pytest tests/jobpulse/ -v -k "autopilot" 2>&1 | tail -10
# Expected: PASS
```

- [ ] **Step 4: Commit**

```bash
git add jobpulse/job_autopilot.py
git commit -m "fix: move determine_match_tier after imports, update stale docstring"
```

---

### Task 1.7: Fix applicator.py External Redirect Log Bug

**Files:**
- Modify: `jobpulse/applicator.py:321`

- [ ] **Step 1: Fix the adapter name in the log**

At line 321, after an external redirect, the log uses `adapter.name` but should use the external adapter name. Find the variable name for the external redirect adapter (likely `ext_adapter` or similar) and replace:

```python
# Before (line 321):
logger.info("Application submitted via %s (%d today)", adapter.name, total)

# After:
ext_name = result.get("external_platform", adapter.name)
logger.info("Application submitted via %s (%d today)", ext_name, total)
```

This uses the `external_platform` key that was set on line 316 (`result["external_platform"] = ext_platform or "generic"`).

- [ ] **Step 2: Run applicator tests**

```bash
python -m pytest tests/ -v -k "applicator" 2>&1 | tail -10
```

- [ ] **Step 3: Commit**

```bash
git add jobpulse/applicator.py
git commit -m "fix: use correct platform name in external redirect log"
```

---

## Phase 2: Delete Ralph Loop

### Task 2.1: Replace ralph_apply_sync Calls with Direct apply_job

**Files:**
- Modify: `jobpulse/job_autopilot.py:36,650,908`
- Modify: `jobpulse/job_api.py:444-581`

Ralph Loop wraps `apply_job()` with retry logic. Without it, callers should call `apply_job()` directly.

`ralph_apply_sync` signature:
```python
def ralph_apply_sync(url, ats_platform, cv_path, cover_letter_path=None,
                     cl_generator=None, custom_answers=None, db_path=None,
                     dry_run=False, iteration_callback=None, engine="extension") -> dict
```

`apply_job` signature:
```python
def apply_job(url, ats_platform, cv_path, cover_letter_path=None,
              cl_generator=None, custom_answers=None, overrides=None,
              dry_run=False, engine="extension") -> dict
```

The signatures are nearly identical — just drop `db_path`, `iteration_callback`, and `overrides` (set to `None`).

- [ ] **Step 1: Replace import in job_autopilot.py**

```python
# Before (line 36):
from jobpulse.ralph_loop import ralph_apply_sync

# After:
from jobpulse.applicator import apply_job
```

- [ ] **Step 2: Replace call at line 650 (auto-apply)**

```python
# Before:
result = ralph_apply_sync(
    url=listing.url,
    ats_platform=listing.ats_platform,
    cv_path=cv_path,
    cover_letter_path=cover_letter_path,
    cl_generator=cl_generator,
    custom_answers=None,
)

# After:
result = apply_job(
    url=listing.url,
    ats_platform=listing.ats_platform,
    cv_path=cv_path,
    cover_letter_path=cover_letter_path,
    cl_generator=cl_generator,
    custom_answers=None,
)
```

- [ ] **Step 3: Replace call at line 908 (approve_jobs)**

```python
# Before:
result = ralph_apply_sync(
    url=listing_url,
    ats_platform=ats_platform,
    cv_path=cv_path or Path("/dev/null"),
    cover_letter_path=cover_letter_path,
    custom_answers=None,
    engine=engine_override,
)

# After:
result = apply_job(
    url=listing_url,
    ats_platform=ats_platform,
    cv_path=cv_path or Path("/dev/null"),
    cover_letter_path=cover_letter_path,
    custom_answers=None,
    engine=engine_override,
)
```

- [ ] **Step 4: Replace ralph endpoints in job_api.py**

Remove the `/ralph-learn` endpoint (lines 444-480) and its models (`RalphLearnRequest`, `RalphLearnResponse` at lines 86-93).

In the `apply_job_endpoint` (line 515), replace:

```python
# Before (line 525):
from jobpulse.ralph_loop.loop import ralph_apply_sync

# After:
from jobpulse.applicator import apply_job
```

And replace the call at line 572 similarly.

- [ ] **Step 5: Run tests to verify nothing breaks**

```bash
python -m pytest tests/jobpulse/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add jobpulse/job_autopilot.py jobpulse/job_api.py
git commit -m "refactor: replace ralph_apply_sync with direct apply_job calls"
```

---

### Task 2.2: Delete Ralph Loop Module and Tests

**Files:**
- Delete: `jobpulse/ralph_loop/` (entire directory)
- Delete: `tests/jobpulse/test_ralph_loop.py`
- Delete: `tests/test_ralph_test_runner.py`
- Delete: `tests/test_ralph_test_store.py`
- Delete: `data/ralph_patterns.db`

- [ ] **Step 1: Delete the module**

```bash
rm -rf jobpulse/ralph_loop/
```

- [ ] **Step 2: Delete all ralph test files**

```bash
rm tests/jobpulse/test_ralph_loop.py
rm tests/test_ralph_test_runner.py
rm tests/test_ralph_test_store.py
```

- [ ] **Step 3: Delete the database**

```bash
rm data/ralph_patterns.db
```

- [ ] **Step 4: Run full test suite to verify nothing imports ralph_loop**

```bash
python -m pytest tests/jobpulse/ -v --tb=short 2>&1 | tail -20
# Expected: no import errors
```

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat: delete Ralph Loop module, tests, and database"
```

---

### Task 2.3: Clean Up Ralph References

**Files:**
- Modify: `jobpulse/runner.py:287-341` (remove ralph-test command)
- Modify: `jobpulse/ats_adapters/base.py:34,49` (update docstrings)
- Modify: `jobpulse/application_orchestrator_pkg/_form_filler.py:129` (update comment)
- Modify: `scripts/refresh_test_fixtures.py:9` (update docstring)
- Modify: `jobpulse/relay_bridge.py:3,125` (update comments)
- Modify: `jobpulse/ext_bridge.py:125` (update comment)

- [ ] **Step 1: Remove ralph-test command from runner.py**

Delete the `elif command == "ralph-test":` block (lines 287-341) and remove `ralph-test` from the help string (line 15).

- [ ] **Step 2: Update docstrings/comments in other files**

In `ats_adapters/base.py`, update the `fill_and_submit` and `resolve_selector` docstrings to say "overrides: learned fixes — selector overrides, wait adjustments..." (remove "Ralph Loop" mention).

In `_form_filler.py`, update line 129 comment to "Load known gotchas for this domain (learned from manual fixes)".

In `relay_bridge.py`, `ext_bridge.py`, `refresh_test_fixtures.py` — replace "ralph-test" with "ext-test" or remove the reference.

- [ ] **Step 3: Delete docs**

```bash
rm docs/superpowers/specs/2026-04-02-ralph-loop-linkedin-testing-design.md
rm docs/superpowers/plans/2026-04-02-ralph-loop-linkedin-testing.md
```

- [ ] **Step 4: Update CLAUDE.md references**

In root `CLAUDE.md`, remove the `ralph-test` lines from the Quick Reference section.

In `jobpulse/CLAUDE.md`, remove ralph-related text from the "Application Orchestrator" and "Commands" sections.

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ --ignore=tests/test_cv_tailor.py -v --tb=short -q 2>&1 | tail -10
# Expected: 0 failures (minus any pre-existing LLM-dependent ones)
```

- [ ] **Step 6: Commit**

```bash
git add -u
git commit -m "chore: clean up all Ralph Loop references from docs, comments, and runner"
```

---

## Phase 3: Ghost Job / Liveness Detection

### Task 3.1: Create Liveness Classifier Module

**Files:**
- Create: `jobpulse/liveness_checker.py`
- Create: `tests/jobpulse/test_liveness_checker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_liveness_checker.py
"""Tests for job posting liveness classification."""
import pytest
from jobpulse.liveness_checker import classify_liveness, LivenessResult


class TestClassifyLiveness:
    def test_active_with_apply_button(self):
        result = classify_liveness(
            status=200,
            final_url="https://jobs.lever.co/company/123",
            body_text="Software Engineer\nWe are looking for...\n" * 20,
            apply_controls=["Apply for this job"],
        )
        assert result.status == "active"

    def test_expired_404(self):
        result = classify_liveness(status=404, final_url="", body_text="", apply_controls=[])
        assert result.status == "expired"
        assert "404" in result.reason

    def test_expired_410(self):
        result = classify_liveness(status=410, final_url="", body_text="", apply_controls=[])
        assert result.status == "expired"

    def test_expired_greenhouse_error_redirect(self):
        result = classify_liveness(
            status=200,
            final_url="https://boards.greenhouse.io/company/jobs?error=true",
            body_text="Open positions",
            apply_controls=[],
        )
        assert result.status == "expired"
        assert "redirect" in result.reason.lower()

    def test_expired_no_longer_available(self):
        result = classify_liveness(
            status=200,
            final_url="https://jobs.lever.co/company/123",
            body_text="This job is no longer available. Browse other openings.",
            apply_controls=[],
        )
        assert result.status == "expired"

    def test_expired_position_filled(self):
        result = classify_liveness(
            status=200,
            final_url="https://example.com/jobs/456",
            body_text="Thank you for your interest. This position has been filled.",
            apply_controls=[],
        )
        assert result.status == "expired"

    def test_expired_short_body(self):
        result = classify_liveness(
            status=200,
            final_url="https://example.com/jobs/789",
            body_text="Page not found. Go back to careers.",
            apply_controls=[],
        )
        assert result.status == "expired"
        assert "short" in result.reason.lower()

    def test_uncertain_no_apply_button(self):
        result = classify_liveness(
            status=200,
            final_url="https://example.com/jobs/abc",
            body_text="Software Engineer\nGreat role\n" * 30,
            apply_controls=[],
        )
        assert result.status == "uncertain"

    def test_expired_listing_page_redirect(self):
        result = classify_liveness(
            status=200,
            final_url="https://example.com/jobs",
            body_text="42 jobs found matching your criteria",
            apply_controls=["Apply"],  # generic "Apply" in search page
        )
        assert result.status == "expired"

    def test_expired_german(self):
        result = classify_liveness(
            status=200,
            final_url="https://example.de/jobs/123",
            body_text="Diese Stelle ist nicht mehr besetzt.",
            apply_controls=[],
        )
        assert result.status == "expired"

    def test_expired_french(self):
        result = classify_liveness(
            status=200,
            final_url="https://example.fr/jobs/123",
            body_text="Cette offre n'est plus disponible.",
            apply_controls=[],
        )
        assert result.status == "expired"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_liveness_checker.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: Implement the classifier**

```python
# jobpulse/liveness_checker.py
"""Job posting liveness classifier.

Pure-function classifier — no browser dependency. Takes HTTP response data
and returns active/expired/uncertain. Inspired by career-ops liveness-core.mjs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HARD_EXPIRED_PATTERNS: list[re.Pattern] = [
    re.compile(r"job (?:is )?no longer available", re.I),
    re.compile(r"job.*no longer open", re.I),
    re.compile(r"position has been filled", re.I),
    re.compile(r"this job has expired", re.I),
    re.compile(r"job posting has expired", re.I),
    re.compile(r"no longer accepting applications", re.I),
    re.compile(r"this (?:position|role|job) (?:is )?no longer", re.I),
    re.compile(r"this job (?:listing )?is closed", re.I),
    re.compile(r"job (?:listing )?not found", re.I),
    re.compile(r"the page you are looking for doesn.t exist", re.I),
    # German
    re.compile(r"diese stelle (?:ist )?(?:nicht mehr|bereits) besetzt", re.I),
    # French
    re.compile(r"offre (?:expir[eé]e|n'est plus disponible)", re.I),
]

_APPLY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bapply\b", re.I),
    re.compile(r"\bsolicitar\b", re.I),
    re.compile(r"\bbewerben\b", re.I),
    re.compile(r"\bpostuler\b", re.I),
    re.compile(r"submit application", re.I),
    re.compile(r"easy apply", re.I),
    re.compile(r"start application", re.I),
    re.compile(r"ich bewerbe mich", re.I),
]

_LISTING_PAGE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\d+\s+jobs?\s+found", re.I),
    re.compile(r"search for jobs page is loaded", re.I),
]

MIN_BODY_LENGTH = 300


@dataclass
class LivenessResult:
    status: str  # "active" | "expired" | "uncertain"
    reason: str


def classify_liveness(
    status: int,
    final_url: str,
    body_text: str,
    apply_controls: list[str],
) -> LivenessResult:
    """Classify a job posting as active, expired, or uncertain.

    Decision tree (first match wins):
    1. HTTP 404/410 → expired
    2. ?error=true in URL → expired (Greenhouse redirect)
    3. Body matches hard expired pattern → expired
    4. Listing page pattern in body → expired (redirected to search)
    5. Body < 300 chars → expired (no JD content)
    6. Apply control matches apply pattern → active
    7. Default → uncertain
    """
    # 1. HTTP status
    if status in (404, 410):
        return LivenessResult("expired", f"HTTP {status}")

    # 2. Greenhouse error redirect
    if re.search(r"[?&]error=true", final_url):
        return LivenessResult("expired", "Error redirect (Greenhouse closed-job pattern)")

    # 3. Hard expired text patterns
    for pattern in _HARD_EXPIRED_PATTERNS:
        if pattern.search(body_text):
            return LivenessResult("expired", f"Expired text: {pattern.pattern}")

    # 4. Listing page redirect (check before apply controls — the page
    #    may have a generic "Apply" link in the search results header)
    for pattern in _LISTING_PAGE_PATTERNS:
        if pattern.search(body_text):
            return LivenessResult("expired", "Redirected to listing/search page")

    # 5. Too-short body
    if len(body_text) < MIN_BODY_LENGTH:
        return LivenessResult("expired", f"Short body ({len(body_text)} chars < {MIN_BODY_LENGTH})")

    # 6. Apply control present
    for control_text in apply_controls:
        for pattern in _APPLY_PATTERNS:
            if pattern.search(control_text):
                return LivenessResult("active", f"Apply control found: {control_text!r}")

    # 7. Default — content present but no apply button
    return LivenessResult("uncertain", "Content present but no apply control found")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_liveness_checker.py -v
# Expected: all PASS
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/liveness_checker.py tests/jobpulse/test_liveness_checker.py
git commit -m "feat: add liveness classifier for ghost job detection"
```

---

### Task 3.2: Integrate Liveness Check into Scan Pipeline

**Files:**
- Modify: `jobpulse/job_autopilot.py` (add liveness gate after JD analysis)
- Modify: `jobpulse/job_scanner.py` (add httpx-based liveness pre-check)

- [ ] **Step 1: Add httpx-based liveness pre-check to job_scanner.py**

After `scan_platforms()` returns raw listings, add a `check_liveness_batch()` function that does a quick HTTP HEAD/GET per URL and feeds results to `classify_liveness`:

```python
import httpx
from jobpulse.liveness_checker import classify_liveness

def check_liveness_batch(
    listings: list[dict],
    timeout: float = 15.0,
) -> tuple[list[dict], list[dict]]:
    """Check liveness of job URLs via HTTP. Returns (alive, expired)."""
    alive, expired = [], []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for listing in listings:
            url = listing.get("url", "")
            try:
                resp = client.get(url)
                result = classify_liveness(
                    status=resp.status_code,
                    final_url=str(resp.url),
                    body_text=resp.text[:5000],
                    apply_controls=[],  # No DOM parsing in HTTP mode
                )
                if result.status == "expired":
                    expired.append({**listing, "liveness": result.reason})
                else:
                    alive.append(listing)
            except httpx.HTTPError:
                alive.append(listing)  # Network error — don't discard, let pipeline handle
    return alive, expired
```

- [ ] **Step 2: Wire into _run_scan_window_inner in job_autopilot.py**

After `scan_platforms()` returns and before Gate 0, add the liveness check:

```python
# After: raw_listings = scan_platforms(platforms)
from jobpulse.job_scanner import check_liveness_batch
alive_listings, expired_listings = check_liveness_batch(raw_listings)
if expired_listings:
    logger.info("Liveness: filtered %d expired postings", len(expired_listings))
# Continue pipeline with alive_listings instead of raw_listings
```

- [ ] **Step 3: Write a test for check_liveness_batch**

```python
# In tests/jobpulse/test_liveness_checker.py, add:
class TestCheckLivenessBatch:
    def test_filters_expired(self, httpx_mock):
        # Mock an expired response
        httpx_mock.add_response(
            url="https://example.com/job/1",
            text="This job is no longer available",
            status_code=200,
        )
        httpx_mock.add_response(
            url="https://example.com/job/2",
            text="Software Engineer\n" * 50,
            status_code=200,
        )
        from jobpulse.job_scanner import check_liveness_batch
        alive, expired = check_liveness_batch([
            {"url": "https://example.com/job/1"},
            {"url": "https://example.com/job/2"},
        ])
        assert len(expired) == 1
        assert len(alive) == 1
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_liveness_checker.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/job_scanner.py jobpulse/job_autopilot.py tests/jobpulse/test_liveness_checker.py
git commit -m "feat: integrate liveness check into scan pipeline"
```

---

## Phase 4: ATS API Scanning

### Task 4.1: Create ATS API Scanner Module

**Files:**
- Create: `jobpulse/ats_api_scanner.py`
- Create: `tests/jobpulse/test_ats_api_scanner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_ats_api_scanner.py
"""Tests for ATS REST API scanner (Greenhouse, Ashby, Lever)."""
import json
import pytest
from jobpulse.ats_api_scanner import (
    scan_greenhouse,
    scan_ashby,
    scan_lever,
    parse_greenhouse,
    parse_ashby,
    parse_lever,
    detect_ats_provider,
)


class TestParseGreenhouse:
    def test_parses_jobs(self):
        data = {
            "jobs": [
                {
                    "title": "Software Engineer",
                    "absolute_url": "https://boards.greenhouse.io/co/jobs/123",
                    "location": {"name": "London"},
                },
                {
                    "title": "Product Manager",
                    "absolute_url": "https://boards.greenhouse.io/co/jobs/456",
                    "location": {"name": "Remote"},
                },
            ]
        }
        results = parse_greenhouse(data, "TestCo")
        assert len(results) == 2
        assert results[0]["title"] == "Software Engineer"
        assert results[0]["url"] == "https://boards.greenhouse.io/co/jobs/123"
        assert results[0]["company"] == "TestCo"
        assert results[0]["location"] == "London"
        assert results[0]["platform"] == "greenhouse"

    def test_empty_jobs(self):
        assert parse_greenhouse({"jobs": []}, "Co") == []


class TestParseAshby:
    def test_parses_jobs(self):
        data = {
            "jobs": [
                {
                    "title": "ML Engineer",
                    "jobUrl": "https://jobs.ashbyhq.com/co/abc-123",
                    "location": "Remote",
                },
            ]
        }
        results = parse_ashby(data, "AshbyCo")
        assert len(results) == 1
        assert results[0]["title"] == "ML Engineer"
        assert results[0]["platform"] == "ashby"


class TestParseLever:
    def test_parses_jobs(self):
        data = [
            {
                "text": "Data Scientist",
                "hostedUrl": "https://jobs.lever.co/co/def-456",
                "categories": {"location": "New York"},
            },
        ]
        results = parse_lever(data, "LeverCo")
        assert len(results) == 1
        assert results[0]["title"] == "Data Scientist"
        assert results[0]["url"] == "https://jobs.lever.co/co/def-456"

    def test_falls_back_to_apply_url(self):
        data = [
            {
                "text": "Engineer",
                "applyUrl": "https://jobs.lever.co/co/apply/xyz",
                "categories": {"location": "SF"},
            },
        ]
        results = parse_lever(data, "Co")
        assert results[0]["url"] == "https://jobs.lever.co/co/apply/xyz"


class TestDetectAtsProvider:
    def test_greenhouse(self):
        assert detect_ats_provider("https://boards.greenhouse.io/company/jobs") == "greenhouse"

    def test_greenhouse_eu(self):
        assert detect_ats_provider("https://job-boards.eu.greenhouse.io/company") == "greenhouse"

    def test_ashby(self):
        assert detect_ats_provider("https://jobs.ashbyhq.com/company") == "ashby"

    def test_lever(self):
        assert detect_ats_provider("https://jobs.lever.co/company") == "lever"

    def test_unknown(self):
        assert detect_ats_provider("https://careers.example.com") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_ats_api_scanner.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: Implement the scanner**

```python
# jobpulse/ats_api_scanner.py
"""ATS REST API scanner — zero-browser job discovery.

Hits public Greenhouse/Ashby/Lever APIs directly via httpx.
No authentication needed. No browser. No LLM tokens.
Inspired by career-ops scan.mjs.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from shared.logging_config import get_logger

logger = get_logger(__name__)

_FETCH_TIMEOUT = 10.0
_CONCURRENCY = 10

# ---------------------------------------------------------------------------
# ATS provider detection
# ---------------------------------------------------------------------------

_ATS_PATTERNS: dict[str, re.Pattern] = {
    "greenhouse": re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([^/?#]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)"),
    "lever": re.compile(r"jobs\.lever\.co/([^/?#]+)"),
}


def detect_ats_provider(url: str) -> str | None:
    """Detect ATS provider from a careers URL. Returns provider name or None."""
    for provider, pattern in _ATS_PATTERNS.items():
        if pattern.search(url):
            return provider
    return None


def extract_slug(url: str, provider: str) -> str | None:
    """Extract company slug from an ATS URL."""
    pattern = _ATS_PATTERNS.get(provider)
    if not pattern:
        return None
    match = pattern.search(url)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Parsers (pure functions — no HTTP)
# ---------------------------------------------------------------------------

def parse_greenhouse(data: dict, company: str) -> list[dict[str, str]]:
    """Parse Greenhouse boards API response."""
    return [
        {
            "title": job["title"],
            "url": job["absolute_url"],
            "company": company,
            "location": job.get("location", {}).get("name", ""),
            "platform": "greenhouse",
        }
        for job in data.get("jobs", [])
    ]


def parse_ashby(data: dict, company: str) -> list[dict[str, str]]:
    """Parse Ashby posting API response."""
    return [
        {
            "title": job["title"],
            "url": job["jobUrl"],
            "company": company,
            "location": job.get("location", ""),
            "platform": "ashby",
        }
        for job in data.get("jobs", [])
    ]


def parse_lever(data: list[dict], company: str) -> list[dict[str, str]]:
    """Parse Lever postings API response."""
    return [
        {
            "title": job["text"],
            "url": job.get("hostedUrl") or job.get("applyUrl", ""),
            "company": company,
            "location": job.get("categories", {}).get("location", ""),
            "platform": "lever",
        }
        for job in data
    ]


# ---------------------------------------------------------------------------
# API callers
# ---------------------------------------------------------------------------

def scan_greenhouse(slug: str, company: str, client: httpx.Client | None = None) -> list[dict]:
    """Fetch all jobs from Greenhouse boards API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=_FETCH_TIMEOUT)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return parse_greenhouse(resp.json(), company)
    except httpx.HTTPError as e:
        logger.warning("Greenhouse scan failed for %s: %s", slug, e)
        return []
    finally:
        if own_client:
            client.close()


def scan_ashby(slug: str, company: str, client: httpx.Client | None = None) -> list[dict]:
    """Fetch all jobs from Ashby posting API."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=_FETCH_TIMEOUT)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return parse_ashby(resp.json(), company)
    except httpx.HTTPError as e:
        logger.warning("Ashby scan failed for %s: %s", slug, e)
        return []
    finally:
        if own_client:
            client.close()


def scan_lever(slug: str, company: str, client: httpx.Client | None = None) -> list[dict]:
    """Fetch all jobs from Lever postings API."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=_FETCH_TIMEOUT)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return parse_lever(resp.json(), company)
    except httpx.HTTPError as e:
        logger.warning("Lever scan failed for %s: %s", slug, e)
        return []
    finally:
        if own_client:
            client.close()


# ---------------------------------------------------------------------------
# Unified scanner
# ---------------------------------------------------------------------------

_SCANNERS = {
    "greenhouse": scan_greenhouse,
    "ashby": scan_ashby,
    "lever": scan_lever,
}


def scan_ats_api(url: str, company: str) -> list[dict]:
    """Auto-detect ATS provider from URL and scan via API.

    Returns list of job dicts or empty list if provider unknown/scan fails.
    """
    provider = detect_ats_provider(url)
    if not provider:
        return []
    slug = extract_slug(url, provider)
    if not slug:
        return []
    scanner = _SCANNERS.get(provider)
    if not scanner:
        return []
    return scanner(slug, company)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_ats_api_scanner.py -v
# Expected: all PASS
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ats_api_scanner.py tests/jobpulse/test_ats_api_scanner.py
git commit -m "feat: add ATS API scanner for Greenhouse, Ashby, Lever"
```

---

### Task 4.2: Wire ATS API Scanner into scan_platforms

**Files:**
- Modify: `jobpulse/job_scanner.py`
- Modify: `jobpulse/job_autopilot.py`

- [ ] **Step 1: Add ats_api scan to scan_platforms**

In `jobpulse/job_scanner.py`, at the end of `scan_platforms()`, add an ATS API pass for any configured companies:

```python
from jobpulse.ats_api_scanner import scan_ats_api

# Inside scan_platforms(), after platform-specific scanners:
# ATS API pass — scan companies with known ATS URLs
config = load_search_config()
ats_companies = config.get("ats_companies", [])  # [{name, url}, ...]
for entry in ats_companies:
    api_results = scan_ats_api(entry["url"], entry["name"])
    all_results.extend(api_results)
```

- [ ] **Step 2: Add ats_companies config support**

In `data/job_search_config.json`, add the new field:

```json
{
  "ats_companies": [
    {"name": "Anthropic", "url": "https://boards.greenhouse.io/anthropic"},
    {"name": "Vercel", "url": "https://jobs.ashbyhq.com/vercel"}
  ]
}
```

- [ ] **Step 3: Test the integration**

```bash
python -m pytest tests/jobpulse/ -v -k "scanner" 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add jobpulse/job_scanner.py
git commit -m "feat: wire ATS API scanner into scan_platforms pipeline"
```

---

## Phase 5: Rejection Pattern Analysis

### Task 5.1: Create Pattern Analyzer Module

**Files:**
- Create: `jobpulse/rejection_analyzer.py`
- Create: `tests/jobpulse/test_rejection_analyzer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_rejection_analyzer.py
"""Tests for rejection pattern analysis."""
import pytest
from jobpulse.rejection_analyzer import (
    classify_outcome,
    classify_blocker,
    compute_funnel,
    compute_score_by_outcome,
    generate_recommendations,
)


class TestClassifyOutcome:
    def test_positive(self):
        assert classify_outcome("Interview") == "positive"
        assert classify_outcome("Offer") == "positive"
        assert classify_outcome("Responded") == "positive"

    def test_negative(self):
        assert classify_outcome("Rejected") == "negative"
        assert classify_outcome("Discarded") == "negative"

    def test_self_filtered(self):
        assert classify_outcome("Skipped") == "self_filtered"

    def test_pending(self):
        assert classify_outcome("Found") == "pending"
        assert classify_outcome("Applied") == "pending"


class TestClassifyBlocker:
    def test_geo_restriction(self):
        assert classify_blocker("Must have US work authorization") == "geo-restriction"
        assert classify_blocker("Canada residents only") == "geo-restriction"

    def test_stack_mismatch(self):
        assert classify_blocker("5+ years of Java experience required") == "stack-mismatch"
        assert classify_blocker("Expert in React Native") == "stack-mismatch"

    def test_seniority_mismatch(self):
        assert classify_blocker("Staff Engineer level required") == "seniority-mismatch"
        assert classify_blocker("Director of Engineering") == "seniority-mismatch"

    def test_onsite_requirement(self):
        assert classify_blocker("Must relocate to SF") == "onsite-requirement"
        assert classify_blocker("Hybrid, 3 days on-site") == "onsite-requirement"

    def test_other(self):
        assert classify_blocker("PhD preferred") == "other"


class TestComputeFunnel:
    def test_basic_funnel(self):
        applications = [
            {"status": "Found"},
            {"status": "Found"},
            {"status": "Applied"},
            {"status": "Interview"},
            {"status": "Rejected"},
        ]
        funnel = compute_funnel(applications)
        assert funnel["Found"] == 2
        assert funnel["Applied"] == 1
        assert funnel["Interview"] == 1
        assert funnel["Rejected"] == 1


class TestScoreByOutcome:
    def test_groups_scores(self):
        applications = [
            {"status": "Interview", "ats_score": 85.0},
            {"status": "Interview", "ats_score": 90.0},
            {"status": "Rejected", "ats_score": 60.0},
            {"status": "Skipped", "ats_score": 45.0},
        ]
        result = compute_score_by_outcome(applications)
        assert result["positive"]["avg"] == 87.5
        assert result["negative"]["avg"] == 60.0
        assert result["self_filtered"]["avg"] == 45.0


class TestRecommendations:
    def test_geo_blocker_recommendation(self):
        applications = [
            {"status": "Rejected", "ats_score": 80, "block_reason": "US work auth required"},
            {"status": "Rejected", "ats_score": 75, "block_reason": "Canada only"},
            {"status": "Rejected", "ats_score": 70, "block_reason": "visa sponsorship"},
            {"status": "Rejected", "ats_score": 82, "block_reason": "relocation needed"},
            {"status": "Applied", "ats_score": 90, "block_reason": None},
        ]
        recs = generate_recommendations(applications)
        geo_recs = [r for r in recs if "location" in r["action"].lower() or "geo" in r["action"].lower()]
        assert len(geo_recs) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_rejection_analyzer.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: Implement the analyzer**

```python
# jobpulse/rejection_analyzer.py
"""Rejection pattern analysis — learn from application outcomes.

Computes conversion funnels, score-vs-outcome correlations, blocker classification,
and generates actionable recommendations. Inspired by career-ops analyze-patterns.mjs.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

_POSITIVE = {"interview", "offer", "responded"}
_NEGATIVE = {"rejected", "discarded"}
_SELF_FILTERED = {"skipped", "blocked"}
# Everything else (found, applied, etc.) = pending


def classify_outcome(status: str) -> str:
    s = status.lower()
    if s in _POSITIVE:
        return "positive"
    if s in _NEGATIVE:
        return "negative"
    if s in _SELF_FILTERED:
        return "self_filtered"
    return "pending"


# ---------------------------------------------------------------------------
# Blocker classification
# ---------------------------------------------------------------------------

_BLOCKER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("geo-restriction", re.compile(
        r"us.only|u\.s\.\s*(?:work|citizen)|us work auth|"
        r"canada.only|uk.only|eu.only|"
        r"visa|residency|right.to.work|"
        r"must be located|geo.restrict|"
        r"work.authoriz",
        re.I,
    )),
    ("seniority-mismatch", re.compile(
        r"staff.engineer|principal|director|"
        r"vp.of|head.of|lead.architect|"
        r"senior.staff|distinguished",
        re.I,
    )),
    ("onsite-requirement", re.compile(
        r"on.?site|hybrid|relocat|in.office|"
        r"must.be.based|in.person",
        re.I,
    )),
    ("stack-mismatch", re.compile(
        r"(?:java|c\+\+|ruby|swift|kotlin|scala|"
        r"react.native|flutter|objective.c|"
        r"\.net|c#|php|perl|erlang|elixir|"
        r"rust|haskell)\b",
        re.I,
    )),
]


def classify_blocker(reason: str) -> str:
    """Classify a block/reject reason into a blocker category."""
    for category, pattern in _BLOCKER_PATTERNS:
        if pattern.search(reason):
            return category
    return "other"


# ---------------------------------------------------------------------------
# Funnel computation
# ---------------------------------------------------------------------------

def compute_funnel(applications: list[dict]) -> dict[str, int]:
    """Count applications by status."""
    return dict(Counter(app["status"] for app in applications))


# ---------------------------------------------------------------------------
# Score-by-outcome analysis
# ---------------------------------------------------------------------------

def compute_score_by_outcome(applications: list[dict]) -> dict[str, dict[str, float]]:
    """Group ATS scores by outcome category. Returns avg/min/max per group."""
    groups: dict[str, list[float]] = {}
    for app in applications:
        score = app.get("ats_score")
        if score is None:
            continue
        outcome = classify_outcome(app["status"])
        groups.setdefault(outcome, []).append(float(score))

    result = {}
    for outcome, scores in groups.items():
        result[outcome] = {
            "avg": sum(scores) / len(scores),
            "min": min(scores),
            "max": max(scores),
            "count": len(scores),
        }
    return result


# ---------------------------------------------------------------------------
# Blocker frequency analysis
# ---------------------------------------------------------------------------

def compute_blocker_frequency(applications: list[dict]) -> dict[str, dict[str, Any]]:
    """Classify block reasons and compute frequency per category."""
    total = len(applications)
    if total == 0:
        return {}

    blockers: dict[str, int] = Counter()
    for app in applications:
        reason = app.get("block_reason")
        if not reason:
            continue
        category = classify_blocker(reason)
        blockers[category] += 1

    return {
        cat: {"count": count, "pct": count / total * 100}
        for cat, count in blockers.most_common()
    }


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

def generate_recommendations(
    applications: list[dict],
    max_recs: int = 5,
) -> list[dict[str, str]]:
    """Generate actionable recommendations from application patterns."""
    recs: list[dict[str, str]] = []
    total = len(applications)
    if total < 5:
        return recs

    # Blocker analysis
    blockers = compute_blocker_frequency(applications)

    if blockers.get("geo-restriction", {}).get("pct", 0) >= 20:
        recs.append({
            "action": "Tighten location filters — geo-restriction blocks "
                      f"{blockers['geo-restriction']['pct']:.0f}% of applications",
            "impact": "high",
        })

    if blockers.get("stack-mismatch", {}).get("pct", 0) >= 15:
        recs.append({
            "action": "Filter out roles requiring mismatched tech stacks — "
                      f"stack mismatch blocks {blockers['stack-mismatch']['pct']:.0f}%",
            "impact": "high",
        })

    if blockers.get("seniority-mismatch", {}).get("pct", 0) >= 10:
        recs.append({
            "action": "Exclude senior/staff/director roles from scan — "
                      f"seniority mismatch blocks {blockers['seniority-mismatch']['pct']:.0f}%",
            "impact": "medium",
        })

    if blockers.get("onsite-requirement", {}).get("pct", 0) >= 15:
        recs.append({
            "action": "Filter for remote-only roles — onsite requirement blocks "
                      f"{blockers['onsite-requirement']['pct']:.0f}%",
            "impact": "medium",
        })

    # Score threshold recommendation
    scores = compute_score_by_outcome(applications)
    positive = scores.get("positive", {})
    if positive and positive.get("min", 0) > 60:
        recs.append({
            "action": f"Set minimum ATS score threshold at {positive['min']:.0f} — "
                      "no positive outcomes below this score",
            "impact": "high",
        })

    return recs[:max_recs]


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------

def generate_full_report(applications: list[dict]) -> dict[str, Any]:
    """Generate complete rejection pattern analysis."""
    return {
        "funnel": compute_funnel(applications),
        "score_by_outcome": compute_score_by_outcome(applications),
        "blocker_frequency": compute_blocker_frequency(applications),
        "recommendations": generate_recommendations(applications),
        "total_applications": len(applications),
    }
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_rejection_analyzer.py -v
# Expected: all PASS
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/rejection_analyzer.py tests/jobpulse/test_rejection_analyzer.py
git commit -m "feat: add rejection pattern analyzer with blocker classification and recommendations"
```

---

### Task 5.2: Add Telegram Command + Analytics Integration

**Files:**
- Modify: `jobpulse/job_analytics.py` (add rejection analysis)
- Modify: `jobpulse/dispatcher.py` (add `job patterns` intent)
- Modify: `jobpulse/swarm_dispatcher.py` (add same intent)
- Modify: `shared/nlp_classifier.py` (add NLP examples)

- [ ] **Step 1: Add rejection analysis to job_analytics.py**

```python
# Add to job_analytics.py:
from jobpulse.rejection_analyzer import generate_full_report, generate_recommendations


def get_rejection_patterns(days: int = 30, db_path: str | None = None) -> str:
    """Return Telegram-formatted rejection pattern analysis."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT a.status, a.ats_score, a.block_reason "
            "FROM applications a "
            "WHERE a.created_at >= ?",
            (_cutoff_iso(days),),
        ).fetchall()
    finally:
        con.close()

    applications = [dict(r) for r in rows]
    report = generate_full_report(applications)

    lines = ["\U0001f4ca Rejection Pattern Analysis (last 30 days)", ""]

    # Funnel
    funnel = report["funnel"]
    lines.append("\U0001f4c8 Funnel:")
    for status, count in sorted(funnel.items(), key=lambda x: -x[1]):
        lines.append(f"  {status}: {count}")

    # Blockers
    blockers = report["blocker_frequency"]
    if blockers:
        lines.append("")
        lines.append("\U0001f6ab Top Blockers:")
        for cat, info in blockers.items():
            lines.append(f"  {cat}: {info['count']} ({info['pct']:.0f}%)")

    # Recommendations
    recs = report["recommendations"]
    if recs:
        lines.append("")
        lines.append("\U0001f4a1 Recommendations:")
        for i, rec in enumerate(recs, 1):
            lines.append(f"  {i}. [{rec['impact'].upper()}] {rec['action']}")

    return "\n".join(lines)
```

- [ ] **Step 2: Add intent to BOTH dispatchers**

In `jobpulse/dispatcher.py` and `jobpulse/swarm_dispatcher.py`, add `"job_patterns"` to the appropriate intent set and handler map pointing to `get_rejection_patterns`.

- [ ] **Step 3: Add NLP examples**

In `shared/nlp_classifier.py`, add examples for the `job_patterns` intent:

```python
"job_patterns": [
    "show rejection patterns",
    "why am I getting rejected",
    "application failure analysis",
    "what's blocking my applications",
],
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -v -k "rejection or pattern_analy" 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/job_analytics.py jobpulse/dispatcher.py jobpulse/swarm_dispatcher.py shared/nlp_classifier.py
git commit -m "feat: add rejection pattern analysis Telegram command"
```

---

## Phase 6: Follow-Up Cadence System

### Task 6.1: Create Follow-Up Tracker Module

**Files:**
- Create: `jobpulse/followup_tracker.py`
- Create: `tests/jobpulse/test_followup_tracker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_followup_tracker.py
"""Tests for follow-up cadence tracker."""
import sqlite3
from datetime import date, timedelta
import pytest
from jobpulse.followup_tracker import (
    compute_urgency,
    get_pending_followups,
    record_followup,
    FollowUpEntry,
    init_db,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "followups.db")
    init_db(path)
    return path


class TestComputeUrgency:
    def test_applied_overdue(self):
        app_date = date.today() - timedelta(days=10)
        result = compute_urgency("Applied", app_date, followup_count=0)
        assert result == "overdue"

    def test_applied_waiting(self):
        app_date = date.today() - timedelta(days=3)
        result = compute_urgency("Applied", app_date, followup_count=0)
        assert result == "waiting"

    def test_applied_cold_after_max(self):
        app_date = date.today() - timedelta(days=30)
        result = compute_urgency("Applied", app_date, followup_count=2)
        assert result == "cold"

    def test_responded_urgent(self):
        app_date = date.today()
        result = compute_urgency("Responded", app_date, followup_count=0)
        assert result == "urgent"

    def test_responded_overdue(self):
        app_date = date.today() - timedelta(days=5)
        result = compute_urgency("Responded", app_date, followup_count=0)
        assert result == "overdue"

    def test_interview_overdue_thankyou(self):
        app_date = date.today() - timedelta(days=2)
        result = compute_urgency("Interview", app_date, followup_count=0)
        assert result == "overdue"

    def test_interview_waiting(self):
        app_date = date.today()
        result = compute_urgency("Interview", app_date, followup_count=0)
        assert result == "waiting"


class TestRecordFollowup:
    def test_records_and_retrieves(self, db_path):
        record_followup(
            db_path=db_path,
            job_id="abc123",
            channel="email",
            contact="hr@example.com",
            notes="Initial follow-up",
        )
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT * FROM followups WHERE job_id = 'abc123'").fetchall()
        con.close()
        assert len(rows) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_followup_tracker.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: Implement the follow-up tracker**

```python
# jobpulse/followup_tracker.py
"""Follow-up cadence tracker — urgency-tiered application follow-ups.

Tracks follow-up history per application, computes urgency tiers,
and generates next follow-up dates. Inspired by career-ops followup-cadence.mjs.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)
_DB_PATH = str(DATA_DIR / "followups.db")

# Cadence configuration
APPLIED_FIRST_DAYS = 7
APPLIED_SUBSEQUENT_DAYS = 7
APPLIED_MAX_FOLLOWUPS = 2
RESPONDED_INITIAL_DAYS = 1
RESPONDED_SUBSEQUENT_DAYS = 3
INTERVIEW_THANKYOU_DAYS = 1


@dataclass
class FollowUpEntry:
    job_id: str
    company: str
    role: str
    status: str
    urgency: str  # "urgent" | "overdue" | "waiting" | "cold"
    next_followup_date: date
    days_until_next: int
    followup_count: int
    contacts: list[str]


def init_db(db_path: str | None = None) -> None:
    """Create follow-ups table if it doesn't exist."""
    con = sqlite3.connect(db_path or _DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            date TEXT NOT NULL,
            channel TEXT,
            contact TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()
    con.close()


def compute_urgency(
    status: str,
    last_action_date: date,
    followup_count: int,
) -> str:
    """Compute follow-up urgency tier."""
    days_since = (date.today() - last_action_date).days
    s = status.lower()

    if s == "applied":
        if followup_count >= APPLIED_MAX_FOLLOWUPS:
            return "cold"
        threshold = APPLIED_FIRST_DAYS if followup_count == 0 else APPLIED_SUBSEQUENT_DAYS
        return "overdue" if days_since >= threshold else "waiting"

    if s == "responded":
        if days_since <= RESPONDED_INITIAL_DAYS:
            return "urgent"
        return "overdue" if days_since >= RESPONDED_SUBSEQUENT_DAYS else "waiting"

    if s == "interview":
        return "overdue" if days_since >= INTERVIEW_THANKYOU_DAYS else "waiting"

    return "waiting"


def record_followup(
    job_id: str,
    channel: str,
    contact: str = "",
    notes: str = "",
    db_path: str | None = None,
) -> None:
    """Record a follow-up action."""
    con = sqlite3.connect(db_path or _DB_PATH)
    con.execute(
        "INSERT INTO followups (job_id, date, channel, contact, notes) VALUES (?, ?, ?, ?, ?)",
        (job_id, date.today().isoformat(), channel, contact, notes),
    )
    con.commit()
    con.close()


def get_followup_count(job_id: str, db_path: str | None = None) -> int:
    """Get number of follow-ups sent for a job."""
    con = sqlite3.connect(db_path or _DB_PATH)
    count = con.execute(
        "SELECT COUNT(*) FROM followups WHERE job_id = ?", (job_id,)
    ).fetchone()[0]
    con.close()
    return count


def get_pending_followups(db_path: str | None = None, jobs_db_path: str | None = None) -> list[FollowUpEntry]:
    """Get all applications needing follow-up, sorted by urgency."""
    from jobpulse.job_db import JobDB

    db = JobDB(db_path=jobs_db_path)
    followup_db = db_path or _DB_PATH
    init_db(followup_db)

    actionable_statuses = {"Applied", "Responded", "Interview"}
    entries: list[FollowUpEntry] = []

    for app in db.get_applications(status_filter=list(actionable_statuses)):
        job_id = app["job_id"]
        app_date = date.fromisoformat(app["created_at"][:10])
        count = get_followup_count(job_id, followup_db)
        urgency = compute_urgency(app["status"], app_date, count)

        # Compute next follow-up date
        if urgency == "cold":
            next_date = app_date  # No more follow-ups
            days_until = -1
        elif app["status"].lower() == "applied":
            threshold = APPLIED_FIRST_DAYS if count == 0 else APPLIED_SUBSEQUENT_DAYS
            next_date = app_date + timedelta(days=threshold * (count + 1))
            days_until = (next_date - date.today()).days
        elif app["status"].lower() == "responded":
            next_date = app_date + timedelta(days=RESPONDED_INITIAL_DAYS)
            days_until = (next_date - date.today()).days
        else:  # interview
            next_date = app_date + timedelta(days=INTERVIEW_THANKYOU_DAYS)
            days_until = (next_date - date.today()).days

        entries.append(FollowUpEntry(
            job_id=job_id,
            company=app.get("company", ""),
            role=app.get("role", ""),
            status=app["status"],
            urgency=urgency,
            next_followup_date=next_date,
            days_until_next=days_until,
            followup_count=count,
            contacts=[],
        ))

    # Sort: urgent → overdue → waiting → cold
    priority = {"urgent": 0, "overdue": 1, "waiting": 2, "cold": 3}
    entries.sort(key=lambda e: (priority.get(e.urgency, 4), e.days_until_next))
    return entries


def format_followup_report(entries: list[FollowUpEntry]) -> str:
    """Format follow-up entries for Telegram."""
    if not entries:
        return "\U0001f4ec No pending follow-ups"

    counts = {"urgent": 0, "overdue": 0, "waiting": 0, "cold": 0}
    for e in entries:
        counts[e.urgency] = counts.get(e.urgency, 0) + 1

    lines = [
        "\U0001f4ec Follow-Up Cadence",
        f"\U0001f534 Urgent: {counts['urgent']} | \U0001f7e0 Overdue: {counts['overdue']} "
        f"| \u23f3 Waiting: {counts['waiting']} | \u2744\ufe0f Cold: {counts['cold']}",
        "",
    ]

    for e in entries:
        if e.urgency == "cold":
            continue  # Don't show cold entries
        icon = {
            "urgent": "\U0001f534",
            "overdue": "\U0001f7e0",
            "waiting": "\u23f3",
        }.get(e.urgency, "")
        days_str = (
            f"({e.days_until_next}d overdue)" if e.days_until_next < 0
            else f"(in {e.days_until_next}d)" if e.days_until_next > 0
            else "(today!)"
        )
        lines.append(f"{icon} {e.company} — {e.role} [{e.status}] {days_str}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_followup_tracker.py -v
# Expected: all PASS
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/followup_tracker.py tests/jobpulse/test_followup_tracker.py
git commit -m "feat: add follow-up cadence tracker with urgency tiers"
```

---

### Task 6.2: Wire Follow-Ups into Dispatcher + Cron

**Files:**
- Modify: `jobpulse/dispatcher.py`
- Modify: `jobpulse/swarm_dispatcher.py`
- Modify: `shared/nlp_classifier.py`
- Modify: `jobpulse/job_autopilot.py` (existing `check_follow_ups` at line 1150)

- [ ] **Step 1: Check existing check_follow_ups function**

`job_autopilot.py:1150` already has a `check_follow_ups()` function. Read it and wire in the new `followup_tracker.get_pending_followups()` + `format_followup_report()` instead of whatever placeholder exists.

- [ ] **Step 2: Add intent to BOTH dispatchers**

Add `"follow_ups"` intent to both dispatchers, pointing to `check_follow_ups`.

- [ ] **Step 3: Add NLP examples**

```python
"follow_ups": [
    "check follow ups",
    "any follow ups needed",
    "who should I follow up with",
    "follow up cadence",
    "pending follow ups",
],
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/ -v -k "followup" 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/dispatcher.py jobpulse/swarm_dispatcher.py shared/nlp_classifier.py jobpulse/job_autopilot.py
git commit -m "feat: wire follow-up cadence into Telegram commands and dispatcher"
```

---

## Phase 7: Interview Prep System

### Task 7.1: Create Interview Prep Module

**Files:**
- Create: `jobpulse/interview_prep.py`
- Create: `tests/jobpulse/test_interview_prep.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_interview_prep.py
"""Tests for interview prep system."""
import pytest
from jobpulse.interview_prep import (
    map_skills_to_stories,
    build_star_story,
    generate_prep_report,
)


class TestMapSkillsToStories:
    def test_maps_matching_skills(self):
        required_skills = ["Python", "ML", "Docker"]
        projects = [
            {"name": "JobPulse", "skills": ["Python", "ML", "LangGraph"], "description": "AI agent system"},
            {"name": "Dashboard", "skills": ["React", "Docker"], "description": "Analytics dashboard"},
        ]
        mapping = map_skills_to_stories(required_skills, projects)
        assert "Python" in mapping
        assert mapping["Python"]["project"] == "JobPulse"
        assert "Docker" in mapping
        assert mapping["Docker"]["project"] == "Dashboard"

    def test_unmapped_skills(self):
        mapping = map_skills_to_stories(["Rust", "Haskell"], [])
        assert mapping == {}


class TestBuildStarStory:
    def test_builds_story_structure(self):
        story = build_star_story(
            skill="Python",
            project="JobPulse",
            description="Built multi-agent AI system with LangGraph",
        )
        assert "situation" in story
        assert "task" in story
        assert "action" in story
        assert "result" in story
        assert "reflection" in story
        assert story["skill"] == "Python"
        assert story["project"] == "JobPulse"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_interview_prep.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: Implement the interview prep module**

```python
# jobpulse/interview_prep.py
"""Interview prep — skill-to-story mapping and STAR+Reflection generation.

Maps JD required skills to candidate's projects, generates STAR+R story
templates, and creates company-specific prep reports.
Inspired by career-ops interview-prep.md.
"""
from __future__ import annotations

from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


def map_skills_to_stories(
    required_skills: list[str],
    projects: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Map each required skill to the best matching project.

    Returns {skill: {project, description}} for skills with matches.
    """
    mapping: dict[str, dict[str, str]] = {}
    skill_lower = {s.lower(): s for s in required_skills}

    for skill_l, skill_orig in skill_lower.items():
        best_project = None
        best_overlap = 0

        for project in projects:
            project_skills = {s.lower() for s in project.get("skills", [])}
            if skill_l in project_skills:
                # Count total overlap with required skills for ranking
                overlap = len(project_skills & set(skill_lower.keys()))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_project = project

        if best_project:
            mapping[skill_orig] = {
                "project": best_project["name"],
                "description": best_project.get("description", ""),
            }

    return mapping


def build_star_story(
    skill: str,
    project: str,
    description: str,
) -> dict[str, str]:
    """Build a STAR+Reflection story template for a skill-project pair.

    Returns a dict with situation, task, action, result, reflection keys.
    These are templates — LLM fills them with specifics from cv.md.
    """
    return {
        "skill": skill,
        "project": project,
        "situation": f"Working on {project}: {description}",
        "task": f"Needed to apply {skill} to solve a specific challenge",
        "action": f"Applied {skill} — describe the specific approach, decisions, trade-offs",
        "result": "Quantified outcome — metrics, time saved, performance improvement",
        "reflection": "What was learned, what would be done differently next time",
    }


def generate_prep_report(
    company: str,
    role: str,
    required_skills: list[str],
    projects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate a full interview prep report.

    Returns skill mapping, STAR stories, and gap analysis.
    """
    mapping = map_skills_to_stories(required_skills, projects)

    stories = []
    for skill, info in mapping.items():
        story = build_star_story(skill, info["project"], info["description"])
        stories.append(story)

    unmapped = [s for s in required_skills if s not in mapping]

    return {
        "company": company,
        "role": role,
        "skill_coverage": f"{len(mapping)}/{len(required_skills)}",
        "mapped_skills": mapping,
        "star_stories": stories,
        "unmapped_skills": unmapped,
        "gap_mitigation": [
            f"{skill}: frame as learning goal with transferable experience"
            for skill in unmapped
        ],
    }


def format_prep_telegram(report: dict[str, Any]) -> str:
    """Format interview prep report for Telegram."""
    lines = [
        f"\U0001f3af Interview Prep: {report['company']} — {report['role']}",
        f"Skill coverage: {report['skill_coverage']}",
        "",
    ]

    if report["star_stories"]:
        lines.append("\U0001f4d6 STAR+R Stories:")
        for story in report["star_stories"]:
            lines.append(f"  {story['skill']} → {story['project']}")

    if report["unmapped_skills"]:
        lines.append("")
        lines.append("\u26a0\ufe0f Gaps to prepare for:")
        for gap in report["unmapped_skills"]:
            lines.append(f"  - {gap}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_interview_prep.py -v
# Expected: all PASS
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/interview_prep.py tests/jobpulse/test_interview_prep.py
git commit -m "feat: add interview prep module with STAR+Reflection story mapping"
```

---

### Task 7.2: Wire Interview Prep into Dispatcher

**Files:**
- Modify: `jobpulse/dispatcher.py`
- Modify: `jobpulse/swarm_dispatcher.py`
- Modify: `shared/nlp_classifier.py`

- [ ] **Step 1: Create handler function**

Add a handler in the appropriate agent module that takes a job_id argument, loads the listing from the DB, extracts required skills, fetches GitHub projects, and calls `generate_prep_report()` + `format_prep_telegram()`.

- [ ] **Step 2: Add intent to BOTH dispatchers**

Add `"interview_prep"` intent to both dispatchers.

- [ ] **Step 3: Add NLP examples**

```python
"interview_prep": [
    "prepare for interview",
    "interview prep for",
    "help me prepare for interview",
    "STAR stories for",
    "interview questions for",
],
```

- [ ] **Step 4: Run dispatch consistency tests**

```bash
python -m pytest tests/ -v -k "dispatch" 2>&1 | tail -10
# Expected: all PASS, both dispatchers in sync
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/dispatcher.py jobpulse/swarm_dispatcher.py shared/nlp_classifier.py
git commit -m "feat: wire interview prep into Telegram commands"
```

---

## Phase 8: Final Verification

### Task 8.1: Full Test Suite + Stats Update

- [ ] **Step 1: Run the complete test suite**

```bash
python -m pytest tests/ -v --tb=short -q 2>&1 | tail -20
# Expected: 0 failures (only skips for Ollama-dependent tests)
```

- [ ] **Step 2: Update codebase stats**

```bash
python scripts/update_stats.py
```

- [ ] **Step 3: Update CLAUDE.md**

Add new modules to the relevant CLAUDE.md sections:
- `liveness_checker.py` — Ghost job / posting liveness detection
- `ats_api_scanner.py` — Zero-browser ATS API scanning (Greenhouse/Ashby/Lever)
- `rejection_analyzer.py` — Statistical rejection pattern analysis
- `followup_tracker.py` — Follow-up cadence with urgency tiers
- `interview_prep.py` — STAR+Reflection interview prep

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: update CLAUDE.md and stats for new modules"
```

---

## Summary

| Phase | Tasks | New Files | Tests |
|-------|-------|-----------|-------|
| 1. Fix Tests | 7 tasks | 0 | Fix 47→0 failures |
| 2. Delete Ralph | 3 tasks | 0 (delete 7+) | Remove ~800 test lines |
| 3. Liveness | 2 tasks | 2 | ~12 new tests |
| 4. ATS API Scanner | 2 tasks | 2 | ~10 new tests |
| 5. Rejection Analysis | 2 tasks | 2 | ~10 new tests |
| 6. Follow-Up Cadence | 2 tasks | 2 | ~8 new tests |
| 7. Interview Prep | 2 tasks | 2 | ~4 new tests |
| 8. Verification | 1 task | 0 | Full suite run |
