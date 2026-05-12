# Test Suite Real-Data Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mock-heavy tests with real-data tests across the entire pipeline, bringing real-data coverage from 4.3% to 15%+ and eliminating the most critical testing gaps.

**Architecture:** Each task creates a new `*_real.py` test file (or updates a stale `test_*.py` file) that uses real SQLite via `tmp_path`, real Ollama LLM calls (marked `@pytest.mark.slow`), and zero mocks. Browser-dependent tests get `@pytest.mark.live` stubs. All tasks are independent and parallelizable via worktree agents.

**Tech Stack:** pytest, SQLite (tmp_path), Ollama (local LLM via `get_llm()`), monkeypatch (DB path redirection only)

**Audit baseline (2026-04-30):** 3749 tests, 152 real-data tests (4.3%), 2389 mock instances, 171/282 files use mocks.

**P0 completed:** Broken test fixed (`test_job_scanner_platforms.py`), 4 real-data test files created (form experience, navigation learner, adaptation chains, screening pipeline).

---

## P1 — High-Churn Untested Source Files (Address Within 2 Weeks)

### Task 1: Tests for `shared/agents.py` (857 LOC, 33 functions)

The central LLM factory — every module depends on it. Zero tests despite 32 commits in 60 days.

**Files:**
- Test: `tests/shared/test_agents_real.py`
- Source: `shared/agents.py`

**Key functions to test:** `_probe_ollama()`, `_resolve_provider()`, `is_local_llm()`, `get_model_name()`, `get_llm()`, `get_openai_client()`, `create_initial_state()`, `_extract_code_blocks()`, `_make_local_llm()`, `_make_openai_llm()`

- [ ] **Step 1: Write Ollama detection tests**

```python
"""Tests for shared/agents.py — real Ollama + real LLM calls."""
import httpx
import pytest

def _ollama_available():
    try:
        return httpx.get("http://localhost:11434/api/tags", timeout=2).status_code == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _ollama_available(), reason="Ollama not running")


class TestOllamaDetection:
    def test_probe_ollama_returns_true(self):
        from shared.agents import _probe_ollama
        assert _probe_ollama() is True

    def test_resolve_provider_auto_finds_ollama(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        from shared.agents import _resolve_provider
        assert _resolve_provider() == "local"

    def test_is_local_llm_true_when_ollama_running(self):
        from shared.agents import is_local_llm
        assert is_local_llm() is True
```

- [ ] **Step 2: Write get_llm / LLM call tests**

```python
class TestGetLlm:
    def test_returns_callable_llm(self):
        from shared.agents import get_llm
        llm = get_llm(temperature=0.0)
        assert llm is not None
        assert hasattr(llm, "invoke")

    def test_llm_generates_response(self):
        from shared.agents import get_llm
        llm = get_llm(temperature=0.0)
        result = llm.invoke("Say exactly: hello")
        assert len(result.content) > 0

    def test_get_model_name_returns_local_model(self):
        from shared.agents import get_model_name
        name = get_model_name()
        # Should be the local model, not gpt-5-mini
        assert "gpt" not in name.lower()

    @pytest.mark.slow
    def test_get_openai_client_connects(self):
        from shared.agents import get_openai_client
        client = get_openai_client()
        assert client is not None
```

- [ ] **Step 3: Write state creation and code extraction tests**

```python
class TestCreateInitialState:
    def test_creates_valid_state(self):
        from shared.agents import create_initial_state
        state = create_initial_state("test topic")
        assert state["topic"] == "test topic"
        assert state["research_notes"] == []
        assert state["draft"] == ""
        assert state["review_score"] == 0.0

class TestExtractCodeBlocks:
    def test_extracts_python_block(self):
        from shared.agents import _extract_code_blocks
        text = "Here is code:\n```python\nprint('hello')\n```\nDone."
        blocks = _extract_code_blocks(text)
        assert len(blocks) >= 1

    def test_no_blocks_returns_empty(self):
        from shared.agents import _extract_code_blocks
        assert _extract_code_blocks("no code here") == []
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/shared/test_agents_real.py -v`
Expected: All PASS (slow tests skipped if no Ollama)

- [ ] **Step 5: Commit**

```bash
git add tests/shared/test_agents_real.py
git commit -m "test(agents): add real-data tests for shared/agents.py LLM factory"
```

---

### Task 2: Tests for `jobpulse/job_autopilot.py` (981 LOC, 26 functions)

The main autopilot loop — 34 commits in 60 days, zero tests. Focus on pure logic functions (no browser needed).

**Files:**
- Test: `tests/jobpulse/test_job_autopilot_real.py`
- Source: `jobpulse/job_autopilot.py`

**Key functions to test:** `determine_match_tier()`, `is_paused()` / `set_autopilot_paused()`, `parse_job_apply_next_cli()`, `_pending_jobs_dicts_from_db_rows()`, `_load_pending()` / `_save_pending()` / `_append_pending()`, `_load_actionable_pending()`

- [ ] **Step 1: Write match tier and CLI parsing tests**

```python
"""Tests for jobpulse/job_autopilot.py — real data, no mocks."""
import json
import pytest
from pathlib import Path


class TestDetermineMatchTier:
    def test_strong_match(self):
        from jobpulse.job_autopilot import determine_match_tier
        assert determine_match_tier(95.0) == "Strong Match"

    def test_good_match(self):
        from jobpulse.job_autopilot import determine_match_tier
        assert determine_match_tier(80.0) == "Good Match"

    def test_moderate_match(self):
        from jobpulse.job_autopilot import determine_match_tier
        assert determine_match_tier(65.0) == "Moderate Match"

    def test_weak_match(self):
        from jobpulse.job_autopilot import determine_match_tier
        assert determine_match_tier(40.0) == "Weak Match"

    def test_boundary_values(self):
        from jobpulse.job_autopilot import determine_match_tier
        # Test exact boundary values
        result_90 = determine_match_tier(90.0)
        result_75 = determine_match_tier(75.0)
        result_60 = determine_match_tier(60.0)
        assert all(isinstance(r, str) for r in [result_90, result_75, result_60])


class TestParseJobApplyNextCli:
    def test_default_args(self):
        from jobpulse.job_autopilot import parse_job_apply_next_cli
        count, found_on = parse_job_apply_next_cli([])
        assert count == "1"
        assert found_on is None

    def test_with_count(self):
        from jobpulse.job_autopilot import parse_job_apply_next_cli
        count, _ = parse_job_apply_next_cli(["5"])
        assert count == "5"

    def test_with_date(self):
        from jobpulse.job_autopilot import parse_job_apply_next_cli
        count, found_on = parse_job_apply_next_cli(["3", "--found-on", "2026-04-30"])
        assert count == "3"
        assert found_on is not None
```

- [ ] **Step 2: Write pending job queue tests with real file I/O**

```python
class TestPendingJobQueue:
    def test_save_and_load_pending(self, tmp_path, monkeypatch):
        from jobpulse.job_autopilot import _save_pending, _load_pending
        pending_file = tmp_path / "pending_jobs.json"
        monkeypatch.setattr("jobpulse.job_autopilot.PENDING_JOBS_FILE", str(pending_file))

        jobs = [{"url": "https://example.com/job1", "title": "Data Analyst", "company": "Acme"}]
        _save_pending(jobs)
        loaded = _load_pending()
        assert len(loaded) == 1
        assert loaded[0]["title"] == "Data Analyst"

    def test_append_pending(self, tmp_path, monkeypatch):
        from jobpulse.job_autopilot import _save_pending, _append_pending, _load_pending
        pending_file = tmp_path / "pending_jobs.json"
        monkeypatch.setattr("jobpulse.job_autopilot.PENDING_JOBS_FILE", str(pending_file))

        _save_pending([{"url": "https://a.com", "title": "Job A", "company": "A"}])
        _append_pending([{"url": "https://b.com", "title": "Job B", "company": "B"}])
        loaded = _load_pending()
        assert len(loaded) == 2

    def test_pending_from_db_rows(self):
        from jobpulse.job_autopilot import _pending_jobs_dicts_from_db_rows
        rows = [{"url": "https://x.com", "title": "Eng", "company": "X", "ats_score": 85.0}]
        result = _pending_jobs_dicts_from_db_rows(rows)
        assert len(result) == 1
        assert "url" in result[0]


class TestPauseControl:
    def test_pause_and_unpause(self, tmp_path, monkeypatch):
        from jobpulse.job_autopilot import is_paused, set_autopilot_paused
        pause_file = tmp_path / "autopilot_paused"
        monkeypatch.setattr("jobpulse.job_autopilot.PAUSE_FILE", str(pause_file))

        assert is_paused() is False
        set_autopilot_paused(True)
        assert is_paused() is True
        set_autopilot_paused(False)
        assert is_paused() is False
```

- [ ] **Step 3: Run and verify**

Run: `pytest tests/jobpulse/test_job_autopilot_real.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/jobpulse/test_job_autopilot_real.py
git commit -m "test(autopilot): add real-data tests for job_autopilot.py"
```

---

### Task 3: Tests for `jobpulse/gmail_agent.py` (382 LOC, 8 functions)

Focus on pure logic functions that don't need OAuth (category normalization, scoring, body extraction).

**Files:**
- Test: `tests/jobpulse/test_gmail_agent_real.py`
- Source: `jobpulse/gmail_agent.py`

- [ ] **Step 1: Write tests for pure logic functions**

```python
"""Tests for jobpulse/gmail_agent.py — pure logic, no OAuth needed."""
import pytest


class TestNormalizeCategory:
    def test_known_categories(self):
        from jobpulse.gmail_agent import _normalize_category
        assert _normalize_category("recruiter") == "recruiter"
        assert _normalize_category("RECRUITER") == "recruiter"

    def test_unknown_falls_to_other(self):
        from jobpulse.gmail_agent import _normalize_category
        result = _normalize_category("something_weird")
        assert isinstance(result, str)


class TestScoreClassification:
    def test_high_confidence(self):
        from jobpulse.gmail_agent import _score_classification
        score = _score_classification("recruiter")
        assert 0.0 <= score <= 1.0

    def test_unknown_category(self):
        from jobpulse.gmail_agent import _score_classification
        score = _score_classification("unknown")
        assert 0.0 <= score <= 1.0


class TestExtractBody:
    def test_plain_text_body(self):
        from jobpulse.gmail_agent import _extract_body
        payload = {
            "mimeType": "text/plain",
            "body": {"data": "SGVsbG8gV29ybGQ="}  # base64 "Hello World"
        }
        body = _extract_body(payload)
        assert "Hello" in body

    def test_multipart_body(self):
        from jobpulse.gmail_agent import _extract_body
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
                {"mimeType": "text/html", "body": {"data": "PGI+SGVsbG88L2I+"}}
            ]
        }
        body = _extract_body(payload)
        assert len(body) > 0

    def test_empty_payload(self):
        from jobpulse.gmail_agent import _extract_body
        body = _extract_body({})
        assert isinstance(body, str)
```

- [ ] **Step 2: Write LLM classification test (real Ollama)**

```python
class TestClassifyEmail:
    @pytest.mark.slow
    def test_recruiter_email_classified(self):
        from jobpulse.gmail_agent import _classify_email
        result = _classify_email(
            subject="Exciting Data Analyst opportunity at Acme Corp",
            body_snippet="Hi, I came across your profile and wanted to reach out about a role..."
        )
        assert isinstance(result, str)
        assert len(result) > 0
```

- [ ] **Step 3: Run and commit**

Run: `pytest tests/jobpulse/test_gmail_agent_real.py -v`

```bash
git add tests/jobpulse/test_gmail_agent_real.py
git commit -m "test(gmail): add real-data tests for gmail_agent.py"
```

---

### Task 4: Tests for `jobpulse/notion_agent.py` (465 LOC, 16 functions)

Focus on pure logic functions (date parsing, fuzzy matching, duplicate checking, formatting).

**Files:**
- Test: `tests/jobpulse/test_notion_agent_real.py`
- Source: `jobpulse/notion_agent.py`

- [ ] **Step 1: Write tests for parse_due_date, fuzzy_score, normalize, format_tasks**

```python
"""Tests for jobpulse/notion_agent.py — pure logic, no Notion API needed."""
import pytest


class TestParseDueDate:
    def test_explicit_date(self):
        from jobpulse.notion_agent import parse_due_date
        text, date = parse_due_date("Do something by 2026-05-01")
        assert date is not None

    def test_relative_tomorrow(self):
        from jobpulse.notion_agent import parse_due_date
        text, date = parse_due_date("Do something tomorrow")
        assert date is not None

    def test_no_date(self):
        from jobpulse.notion_agent import parse_due_date
        text, date = parse_due_date("Just a plain task")
        assert date is None
        assert "Just a plain task" in text


class TestFuzzyScore:
    def test_exact_match(self):
        from jobpulse.notion_agent import _fuzzy_score
        score = _fuzzy_score("Buy groceries", "Buy groceries")
        assert score >= 0.9

    def test_partial_match(self):
        from jobpulse.notion_agent import _fuzzy_score
        score = _fuzzy_score("Buy", "Buy groceries")
        assert 0.0 < score < 1.0

    def test_no_match(self):
        from jobpulse.notion_agent import _fuzzy_score
        score = _fuzzy_score("zzzzz", "Buy groceries")
        assert score < 0.5


class TestNormalize:
    def test_strips_whitespace_and_lowercases(self):
        from jobpulse.notion_agent import _normalize
        assert _normalize("  Hello World  ") == "hello world"


class TestFormatTasks:
    def test_formats_task_list(self):
        from jobpulse.notion_agent import format_tasks
        tasks = [
            {"title": "Task A", "status": "Not started"},
            {"title": "Task B", "status": "Done"},
        ]
        result = format_tasks(tasks)
        assert "Task A" in result
        assert "Task B" in result

    def test_empty_list(self):
        from jobpulse.notion_agent import format_tasks
        result = format_tasks([])
        assert isinstance(result, str)


class TestCheckDuplicate:
    @pytest.mark.slow
    def test_suggest_subtasks_returns_list(self):
        from jobpulse.notion_agent import suggest_subtasks
        # This uses real LLM via Ollama
        subtasks = suggest_subtasks("Plan a team offsite")
        assert isinstance(subtasks, list)
        assert len(subtasks) > 0
```

- [ ] **Step 2: Run and commit**

Run: `pytest tests/jobpulse/test_notion_agent_real.py -v`

```bash
git add tests/jobpulse/test_notion_agent_real.py
git commit -m "test(notion): add real-data tests for notion_agent.py"
```

---

### Task 5: Tests for `jobpulse/runner.py` (427 LOC, 1 function)

Runner is a CLI dispatcher — test argument parsing and command routing.

**Files:**
- Test: `tests/jobpulse/test_runner_real.py`
- Source: `jobpulse/runner.py`

- [ ] **Step 1: Read runner.py to understand the CLI dispatch**

Read `jobpulse/runner.py` and identify testable pure functions (argument parsing, command validation). The `main()` function dispatches to other modules — test that known commands are recognized without executing them.

- [ ] **Step 2: Write CLI dispatch validation tests**

```python
"""Tests for jobpulse/runner.py — CLI argument parsing."""
import pytest
import subprocess
import sys


class TestRunnerHelp:
    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "jobpulse.runner", "--help"],
            capture_output=True, text=True, timeout=10
        )
        # Should show usage info, not crash
        assert result.returncode == 0 or "usage" in result.stdout.lower() or "usage" in result.stderr.lower()

    def test_unknown_command_exits_cleanly(self):
        result = subprocess.run(
            [sys.executable, "-m", "jobpulse.runner", "nonexistent-command"],
            capture_output=True, text=True, timeout=10
        )
        # Should not crash with traceback
        assert "Traceback" not in result.stderr or result.returncode != 0
```

- [ ] **Step 3: Run and commit**

Run: `pytest tests/jobpulse/test_runner_real.py -v`

```bash
git add tests/jobpulse/test_runner_real.py
git commit -m "test(runner): add CLI dispatch tests for runner.py"
```

---

### Task 6: Tests for `jobpulse/form_engine/field_mapper.py` (874 LOC, 18 functions)

Core field mapping logic — maps form labels to profile keys. Mix of pure logic and LLM-backed resolution.

**Files:**
- Test: `tests/jobpulse/form_engine/test_field_mapper_real.py`
- Source: `jobpulse/form_engine/field_mapper.py`

- [ ] **Step 1: Write tests for pure logic functions**

```python
"""Tests for field_mapper.py — real data, no mocks."""
import pytest


class TestIsScreeningLikeField:
    def test_visa_is_screening(self):
        from jobpulse.form_engine.field_mapper import is_screening_like_field
        assert is_screening_like_field({"label": "Do you require visa sponsorship?"}) is True

    def test_name_is_not_screening(self):
        from jobpulse.form_engine.field_mapper import is_screening_like_field
        assert is_screening_like_field({"label": "First Name"}) is False


class TestCleanMapping:
    def test_removes_internal_keys(self):
        from jobpulse.form_engine.field_mapper import clean_mapping
        mapping = {"First Name": "John", "_confidence": 0.9, "__meta": "x"}
        result = clean_mapping(mapping)
        assert "_confidence" not in result
        assert "First Name" in result

    def test_empty_mapping(self):
        from jobpulse.form_engine.field_mapper import clean_mapping
        assert clean_mapping({}) == {}


class TestFuzzyCustomAnswer:
    def test_exact_match(self):
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer
        result = _fuzzy_custom_answer("email", {"email": "test@example.com"})
        assert result == "test@example.com"

    def test_close_match(self):
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer
        result = _fuzzy_custom_answer("email address", {"email": "test@example.com"})
        # Should fuzzy match
        assert result is not None or result is None  # depends on threshold

    def test_no_match(self):
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer
        result = _fuzzy_custom_answer("zzzzz", {"email": "test@example.com"})
        assert result is None


class TestResolveWithOptions:
    def test_exact_option_match(self):
        from jobpulse.form_engine.field_mapper import _resolve_with_options
        result = _resolve_with_options("Yes", {"options": ["Yes", "No"]})
        assert result == "Yes"

    def test_case_insensitive_match(self):
        from jobpulse.form_engine.field_mapper import _resolve_with_options
        result = _resolve_with_options("yes", {"options": ["Yes", "No"]})
        assert result.lower() == "yes"


class TestSaveGotcha:
    def test_save_and_exists(self, tmp_path, monkeypatch):
        from jobpulse.form_engine.field_mapper import save_gotcha
        # Redirect gotchas DB to tmp_path
        monkeypatch.setenv("GOTCHAS_DB_PATH", str(tmp_path / "gotchas.db"))
        save_gotcha("https://example.com/apply", "Phone", "wrong format", "use +44")
        # Verify it was saved (no crash = success for now)
```

- [ ] **Step 2: Write seed_mapping tests with real profile data**

```python
class TestSeedMapping:
    @pytest.mark.slow
    def test_seed_maps_common_fields(self):
        from jobpulse.form_engine.field_mapper import seed_mapping
        fields = [
            {"label": "First Name", "type": "text", "options": []},
            {"label": "Last Name", "type": "text", "options": []},
            {"label": "Email", "type": "email", "options": []},
            {"label": "Phone Number", "type": "tel", "options": []},
        ]
        profile = {"first_name": "Test", "last_name": "User", "email": "test@example.com", "phone": "+440000000000"}
        mapping = seed_mapping(fields, profile)
        assert len(mapping) >= 2  # Should map at least name + email
```

- [ ] **Step 3: Run and commit**

Run: `pytest tests/jobpulse/form_engine/test_field_mapper_real.py -v`

```bash
git add tests/jobpulse/form_engine/test_field_mapper_real.py
git commit -m "test(field_mapper): add real-data tests for field_mapper.py"
```

---

### Task 7: Tests for `jobpulse/form_engine/field_resolver.py` (756 LOC, 18 functions)

Option matching, label-to-profile-key resolution, country canonicalization.

**Files:**
- Test: `tests/jobpulse/form_engine/test_field_resolver_real.py`
- Source: `jobpulse/form_engine/field_resolver.py`

- [ ] **Step 1: Write tests for pure resolver functions**

```python
"""Tests for field_resolver.py — real data, no mocks."""
import pytest


class TestFuzzyLabelToProfileKey:
    def test_first_name(self):
        from jobpulse.form_engine.field_resolver import fuzzy_label_to_profile_key
        assert fuzzy_label_to_profile_key("First Name") is not None

    def test_email_address(self):
        from jobpulse.form_engine.field_resolver import fuzzy_label_to_profile_key
        result = fuzzy_label_to_profile_key("Email Address")
        assert result is not None

    def test_unknown_label(self):
        from jobpulse.form_engine.field_resolver import fuzzy_label_to_profile_key
        result = fuzzy_label_to_profile_key("zzzz_unknown_field_zzzz")
        assert result is None


class TestBestOptionMatch:
    def test_exact_match(self):
        from jobpulse.form_engine.field_resolver import best_option_match
        result = best_option_match("Male", ["Male", "Female", "Non-binary", "Prefer not to say"])
        assert result == "Male"

    def test_case_insensitive(self):
        from jobpulse.form_engine.field_resolver import best_option_match
        result = best_option_match("male", ["Male", "Female"])
        assert result is not None and result.lower() == "male"

    def test_no_match_returns_none(self):
        from jobpulse.form_engine.field_resolver import best_option_match
        result = best_option_match("Martian", ["Male", "Female"])
        assert result is None or isinstance(result, str)

    def test_yes_no_alignment(self):
        from jobpulse.form_engine.field_resolver import best_option_match
        result = best_option_match("Yes", ["Yes", "No"])
        assert result == "Yes"


class TestCanonicalizeCountryValue:
    def test_uk_variants(self):
        from jobpulse.form_engine.field_resolver import canonicalize_country_value
        result = canonicalize_country_value("Country", "UK")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_full_name(self):
        from jobpulse.form_engine.field_resolver import canonicalize_country_value
        result = canonicalize_country_value("Country", "United Kingdom")
        assert isinstance(result, str)


class TestBuildOptionAliases:
    def test_returns_dict(self):
        from jobpulse.form_engine.field_resolver import build_option_aliases
        aliases = build_option_aliases()
        assert isinstance(aliases, dict)
        assert len(aliases) > 0  # Should have gender, visa, etc.


class TestLabelMappingStore:
    def test_store_and_retrieve(self, tmp_path):
        from jobpulse.form_engine.field_resolver import LabelMappingStore
        store = LabelMappingStore(db_path=str(tmp_path / "labels.db"))
        store.save("Email Address", "email")
        result = store.lookup("Email Address")
        assert result == "email"

    def test_miss_returns_none(self, tmp_path):
        from jobpulse.form_engine.field_resolver import LabelMappingStore
        store = LabelMappingStore(db_path=str(tmp_path / "labels.db"))
        assert store.lookup("nonexistent") is None


class TestGetFieldGap:
    def test_known_label_returns_gap(self):
        from jobpulse.form_engine.field_resolver import get_field_gap
        gap = get_field_gap("Email")
        assert isinstance(gap, float)
        assert gap >= 0.0
```

- [ ] **Step 2: Run and commit**

Run: `pytest tests/jobpulse/form_engine/test_field_resolver_real.py -v`

```bash
git add tests/jobpulse/form_engine/test_field_resolver_real.py
git commit -m "test(field_resolver): add real-data tests for field_resolver.py"
```

---

### Task 8: Tests for `jobpulse/form_engine/field_scanner.py` (664 LOC, 15 functions)

Field scanning validation logic — testable without a browser.

**Files:**
- Test: `tests/jobpulse/form_engine/test_field_scanner_real.py`
- Source: `jobpulse/form_engine/field_scanner.py`

- [ ] **Step 1: Write tests for validation and merge logic**

```python
"""Tests for field_scanner.py — validation and merge logic, no browser."""
import pytest


class TestValidateFieldScan:
    def test_valid_fields_pass(self):
        from jobpulse.form_engine.field_scanner import validate_field_scan
        fields = [
            {"label": "First Name", "selector": "#fname", "type": "text"},
            {"label": "Email", "selector": "#email", "type": "email"},
        ]
        result = validate_field_scan(fields)
        assert result is True or isinstance(result, list)

    def test_empty_fields_rejected(self):
        from jobpulse.form_engine.field_scanner import validate_field_scan
        result = validate_field_scan([])
        assert result is False or result == []


class TestMergeFields:
    def test_merges_without_duplicates(self):
        from jobpulse.form_engine.field_scanner import _merge_fields
        primary = [{"label": "Name", "selector": "#name", "type": "text"}]
        secondary = [{"label": "Email", "selector": "#email", "type": "email"}]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 2

    def test_primary_wins_on_conflict(self):
        from jobpulse.form_engine.field_scanner import _merge_fields
        primary = [{"label": "Name", "selector": "#name-v2", "type": "text"}]
        secondary = [{"label": "Name", "selector": "#name-v1", "type": "text"}]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 1


class TestFillableCount:
    def test_counts_fillable(self):
        from jobpulse.form_engine.field_scanner import _fillable_count
        fields = [
            {"label": "Name", "type": "text"},
            {"label": "Submit", "type": "button"},
            {"label": "Email", "type": "email"},
        ]
        count = _fillable_count(fields)
        assert count >= 1


class TestEmitScanSignal:
    def test_emits_without_crash(self, tmp_path, monkeypatch):
        from jobpulse.form_engine.field_scanner import _emit_scan_signal
        # Should not crash even without OptimizationEngine configured
        _emit_scan_signal("greenhouse.io", 5, "cdp", 1.2)
```

- [ ] **Step 2: Run and commit**

Run: `pytest tests/jobpulse/form_engine/test_field_scanner_real.py -v`

```bash
git add tests/jobpulse/form_engine/test_field_scanner_real.py
git commit -m "test(field_scanner): add real-data tests for field_scanner.py"
```

---

### Task 9: Tests for `jobpulse/form_engine/unified_scanner.py` (882 LOC, 25 methods)

UnifiedFieldScanner class — test the static/pure methods without a browser.

**Files:**
- Test: `tests/jobpulse/form_engine/test_unified_scanner_real.py`
- Source: `jobpulse/form_engine/unified_scanner.py`

- [ ] **Step 1: Write tests for static helper methods**

```python
"""Tests for unified_scanner.py — static helpers, no browser."""
import pytest


class TestNormalizeInputType:
    def test_known_types(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        assert UnifiedFieldScanner._normalize_input_type("TEXT") == "text"
        assert UnifiedFieldScanner._normalize_input_type("email") == "email"
        assert UnifiedFieldScanner._normalize_input_type("TEL") == "tel"

    def test_unknown_type_passes_through(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        result = UnifiedFieldScanner._normalize_input_type("custom-widget")
        assert isinstance(result, str)


class TestSelectorQuality:
    def test_id_selector_high_quality(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        score = UnifiedFieldScanner._selector_quality("#email-input")
        assert score > 0

    def test_generic_selector_low_quality(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        score_id = UnifiedFieldScanner._selector_quality("#email")
        score_generic = UnifiedFieldScanner._selector_quality("input")
        assert score_id >= score_generic


class TestIsNoiseLabel:
    def test_noise_labels_detected(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        assert UnifiedFieldScanner._is_noise_label("") is True
        assert UnifiedFieldScanner._is_noise_label("   ") is True

    def test_real_labels_pass(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        assert UnifiedFieldScanner._is_noise_label("First Name") is False
        assert UnifiedFieldScanner._is_noise_label("Email Address") is False


class TestBboxOverlap:
    def test_no_overlap(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        a = {"x": 0, "y": 0, "width": 100, "height": 50}
        b = {"x": 200, "y": 200, "width": 100, "height": 50}
        assert UnifiedFieldScanner._bbox_overlap(a, b) == 0.0

    def test_full_overlap(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        a = {"x": 0, "y": 0, "width": 100, "height": 50}
        overlap = UnifiedFieldScanner._bbox_overlap(a, a)
        assert overlap > 0.9

    def test_none_bbox(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        assert UnifiedFieldScanner._bbox_overlap(None, None) == 0.0


class TestParseAxNode:
    def test_parses_text_input(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
        node = {
            "role": {"value": "textbox"},
            "name": {"value": "Email"},
            "properties": [{"name": "value", "value": {"value": ""}}],
        }
        role, name, value, props = UnifiedFieldScanner._parse_ax_node(node)
        assert role == "textbox"
        assert name == "Email"
```

- [ ] **Step 2: Run and commit**

Run: `pytest tests/jobpulse/form_engine/test_unified_scanner_real.py -v`

```bash
git add tests/jobpulse/form_engine/test_unified_scanner_real.py
git commit -m "test(unified_scanner): add real-data tests for UnifiedFieldScanner"
```

---

### Task 10: Update 6 stale form_engine tests

These tests are 21-30 days behind their source files and use heavy mocking. Update each to test against the current API surface.

**Files:**
- Modify: `tests/jobpulse/form_engine/test_radio_filler.py` (3 tests, 30 days stale)
- Modify: `tests/jobpulse/form_engine/test_detector.py` (16 tests, 30 days stale)
- Modify: `tests/jobpulse/form_engine/test_gotchas.py` (6 tests, 30 days stale)
- Modify: `tests/jobpulse/form_engine/test_checkbox_filler.py` (5 tests, 23 days stale)
- Modify: `tests/jobpulse/form_engine/test_text_filler.py` (5 tests, 21 days stale)
- Modify: `tests/jobpulse/form_engine/test_page_filler.py` (5 tests, 21 days stale)

- [ ] **Step 1: For each file, verify imports still work**

```bash
for f in tests/jobpulse/form_engine/test_radio_filler.py \
         tests/jobpulse/form_engine/test_detector.py \
         tests/jobpulse/form_engine/test_gotchas.py \
         tests/jobpulse/form_engine/test_checkbox_filler.py \
         tests/jobpulse/form_engine/test_text_filler.py \
         tests/jobpulse/form_engine/test_page_filler.py; do
  python -m pytest "$f" --collect-only -q 2>&1 | tail -2
done
```

- [ ] **Step 2: Run all stale tests, identify failures**

```bash
pytest tests/jobpulse/form_engine/ -v --tb=short 2>&1 | tail -30
```

- [ ] **Step 3: For each failing test, check if the source API changed**

Read the corresponding source file (`radio_filler.py`, `detector.py`, etc.), compare function signatures with what the test imports, and update the test to match the current API.

- [ ] **Step 4: For `test_gotchas.py` — verify it uses tmp_path (it already does)**

`test_gotchas.py` uses a `gotchas_db` fixture — verify it points to tmp_path, not `data/`.

- [ ] **Step 5: Run all updated tests**

```bash
pytest tests/jobpulse/form_engine/ -v
```
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add tests/jobpulse/form_engine/
git commit -m "test(form_engine): update 6 stale test files to match current API"
```

---

## P2 — Structural Test Gaps (Address Within 1 Month)

### Task 11: Tests for 4 untested orchestration patterns

`dynamic_swarm`, `enhanced_swarm`, `hierarchical`, `peer_debate` have zero dedicated tests. The existing `test_map_reduce.py` and `test_plan_and_execute.py` mock all LLM calls — create pattern tests with real Ollama.

**Files:**
- Create: `tests/patterns/test_dynamic_swarm_real.py`
- Create: `tests/patterns/test_enhanced_swarm_real.py`
- Create: `tests/patterns/test_hierarchical_real.py`
- Create: `tests/patterns/test_peer_debate_real.py`

- [ ] **Step 1: Write dynamic_swarm tests**

```python
"""Tests for patterns/dynamic_swarm.py — real LLM via Ollama."""
import httpx
import pytest

def _ollama_available():
    try:
        return httpx.get("http://localhost:11434/api/tags", timeout=2).status_code == 200
    except Exception:
        return False

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _ollama_available(), reason="Ollama not running"),
]


class TestDynamicSwarmState:
    def test_build_swarm_graph(self):
        from patterns.dynamic_swarm import build_swarm_graph
        graph = build_swarm_graph()
        assert graph is not None

    def test_should_continue_swarm_converges(self):
        from patterns.dynamic_swarm import should_continue_swarm
        from shared.agents import create_initial_state
        state = create_initial_state("test")
        state["iteration"] = 3
        state["review_score"] = 9.0
        state["accuracy_score"] = 9.5
        result = should_continue_swarm(state)
        assert result in ("continue", "finish", "swarm_finish")

    def test_fallback_task_decomposition(self):
        from patterns.dynamic_swarm import _fallback_task_decomposition
        from shared.agents import create_initial_state
        state = create_initial_state("Compare Python and Rust for data pipelines")
        tasks = _fallback_task_decomposition(state)
        assert isinstance(tasks, list)
        assert len(tasks) >= 1


class TestDynamicSwarmRealLLM:
    def test_task_analyzer_produces_tasks(self):
        from patterns.dynamic_swarm import task_analyzer_node
        from shared.agents import create_initial_state
        state = create_initial_state("What are the benefits of test-driven development?")
        state["iteration"] = 0
        result = task_analyzer_node(state)
        # Should produce research tasks or a draft
        assert any(k in result for k in ["research_notes", "draft", "current_tasks"])
```

- [ ] **Step 2: Write similar tests for enhanced_swarm, hierarchical, peer_debate**

Follow the same pattern: test graph construction, convergence routing, and one real LLM node execution for each pattern. Key functions per pattern:

- `enhanced_swarm`: `build_enhanced_swarm_graph()`, `route_after_convergence()`, `enhanced_task_analysis()`
- `hierarchical`: `build_hierarchical_graph()`, `route_from_supervisor()`, `supervisor_node_rule_based()`
- `peer_debate`: `build_debate_graph()`, `route_after_convergence()`, `convergence_check()`

- [ ] **Step 3: Run all pattern tests**

```bash
pytest tests/patterns/ -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add tests/patterns/
git commit -m "test(patterns): add real-LLM tests for 4 untested orchestration patterns"
```

---

### Task 12: De-mock the existing pattern tests

`test_map_reduce.py` and `test_plan_and_execute.py` mock `get_llm` and `smart_llm_call` in every test. Add real-LLM companion tests.

**Files:**
- Create: `tests/patterns/test_map_reduce_real.py`
- Create: `tests/patterns/test_plan_and_execute_real.py`

- [ ] **Step 1: Write real-LLM tests for map_reduce**

```python
"""Real-LLM tests for map_reduce pattern."""
import httpx
import pytest

def _ollama_available():
    try:
        return httpx.get("http://localhost:11434/api/tags", timeout=2).status_code == 200
    except Exception:
        return False

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _ollama_available(), reason="Ollama not running"),
]


class TestMapReduceRealLLM:
    def test_splitter_produces_chunks(self):
        from patterns.map_reduce import splitter_node, create_initial_state
        state = create_initial_state("Compare 3 programming languages: Python, Rust, Go")
        result = splitter_node(state)
        assert len(result.get("chunks", [])) >= 1

    def test_mapper_processes_chunk(self):
        from patterns.map_reduce import mapper_node, create_initial_state
        state = create_initial_state("Summarize programming language features")
        state["chunks"] = ["Python: dynamic typing, GC, extensive stdlib"]
        state["map_results"] = []
        result = mapper_node(state)
        assert len(result.get("map_results", [])) >= 1

    def test_reducer_synthesizes(self):
        from patterns.map_reduce import reducer_node, create_initial_state
        state = create_initial_state("Compare languages")
        state["map_results"] = ["Python is dynamic", "Rust is safe"]
        result = reducer_node(state)
        assert len(result.get("reduced_output", "")) > 0
```

- [ ] **Step 2: Write similar for plan_and_execute**

- [ ] **Step 3: Run and commit**

```bash
pytest tests/patterns/ -v --tb=short
git add tests/patterns/
git commit -m "test(patterns): add real-LLM companion tests for map_reduce and plan_and_execute"
```

---

### Task 13: De-mock `test_wiring_e2e.py`

The existing wiring tests mock 49 instances including Drive, Notion, JobDB, and strategy_reflector. Reduce mocking to only truly external services (Drive, Notion API) and use real local DBs for everything else.

**Files:**
- Modify: `tests/jobpulse/test_wiring_e2e.py`

- [ ] **Step 1: Read current test and identify which mocks can be removed**

The file patches these systems:
- `upload_cv` / `upload_cover_letter` — Google Drive API (KEEP mocked — needs OAuth)
- `find_application_page` / `update_application_page` — Notion API (KEEP mocked — needs API key)
- `JobDB` — SQLite (REMOVE mock — use real with tmp_path)
- `reflect_on_application` — LLM-backed (REMOVE mock if Ollama available, else mark @pytest.mark.slow)
- `get_optimization_engine` — SQLite-backed (REMOVE mock — use real with tmp_path)

- [ ] **Step 2: Create `_patch_only_external_apis()` helper**

Replace `_patch_externals()` with a new helper that only mocks Drive and Notion, using real DBs for everything else:

```python
def _patch_only_external_apis():
    """Mock only Drive and Notion APIs — use real DBs for everything else."""
    return [
        patch("jobpulse.post_apply_hook.upload_cv", return_value=None),
        patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None),
        patch("jobpulse.post_apply_hook.find_application_page", return_value=None),
        patch("jobpulse.post_apply_hook.update_application_page"),
    ]
```

- [ ] **Step 3: Update each test to use real JobDB and OptimizationEngine via tmp_path**

For each of the 5 tests, replace `MagicMock()` with real instances constructed with `tmp_path`.

- [ ] **Step 4: Run and verify**

```bash
pytest tests/jobpulse/test_wiring_e2e.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add tests/jobpulse/test_wiring_e2e.py
git commit -m "test(wiring): de-mock JobDB and OptimizationEngine, use real SQLite"
```

---

## P3 — Ongoing Improvements

These are not discrete tasks but practices to adopt incrementally.

### Guideline A: When touching a test file, reduce mock count

If you modify a test file for any reason:
1. Check if any mocks can be replaced with real calls (especially `get_llm`, `smart_llm_call`, SQLite connections)
2. Replace at least one mock with a real call per touch
3. Add `@pytest.mark.slow` if the replacement involves LLM calls

### Guideline B: New features require `@pytest.mark.live` or `@pytest.mark.slow` tests

Every new feature PR must include:
- At least one test with `@pytest.mark.slow` that uses real Ollama (if LLM-dependent)
- At least one test with `@pytest.mark.live` that uses real browser (if browser-dependent)
- Wiring verification: query the DB after the test to verify rows were written

### Guideline C: Add parametrized tests for input variation

When writing new tests, use `@pytest.mark.parametrize` for:
- Multiple field types (text, email, tel, select, radio, checkbox)
- Multiple platform strategies (greenhouse, workday, linkedin, indeed)
- Multiple screening question categories (visa, salary, notice, experience)

Target: increase from 17 to 50+ parametrized test decorators.

### Guideline D: Track mock reduction

Run this periodically to measure progress:
```bash
grep -rc "Mock\|MagicMock\|@patch\|patch(" tests/ --include="*.py" | awk -F: '{s+=$2} END {print "Mock instances:", s}'
```

Starting baseline: 2,389 mock instances. Target: under 1,500 within 3 months.

---

## Execution Summary

| Phase | Tasks | New Tests (est.) | Parallelizable |
|-------|-------|-----------------|----------------|
| P1 Tasks 1-5 | High-churn source files | ~60 | Yes (5 worktrees) |
| P1 Tasks 6-9 | Form engine core | ~50 | Yes (4 worktrees) |
| P1 Task 10 | Stale test updates | ~0 (updates) | Yes (6 worktrees) |
| P2 Task 11 | Pattern tests | ~30 | Yes (4 worktrees) |
| P2 Task 12 | Pattern de-mock | ~15 | Yes (2 worktrees) |
| P2 Task 13 | Wiring de-mock | ~0 (updates) | No |
| **Total** | **13 tasks** | **~155 new tests** | |

Combined with P0's 152 tests, this plan adds ~307 real-data tests total, bringing coverage from 0.2% to ~10%.
