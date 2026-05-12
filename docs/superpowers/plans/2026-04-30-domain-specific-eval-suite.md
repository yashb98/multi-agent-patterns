# Domain-Specific Eval Suite — Expanded Canonical Flows

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand `canonical_flows.json` from 5 to 50+ cases covering every failure class. Add new flow types: `screening_answer`, `page_classification`, `field_mapping`, `platform_bypass`, `fill_failure_class`. Auto-generate eval cases from OPRAL failures (mistakes.md entries, Telegram error alerts, CorrectionCapture diffs). Add trajectory evaluation — not just outcome (success/fail) but process (was the path optimal?).

**Architecture:** Extend `_agent_eval.py` with new flow runners. Add `failure_harvester.py` that reads `mistakes.md`, `CorrectionCapture` diffs, and `form_failure_reasons` to auto-generate eval cases. Add `trajectory_eval.py` for path-optimality scoring. All eval cases stay in `tests/fixtures/evals/canonical_flows.json` — one file, deterministic, no network.

**Tech Stack:** Python, `shared/evals/_agent_eval.py`, `tests/fixtures/evals/canonical_flows.json`, SQLite

---

## File Structure

| File | Responsibility |
|------|---------------|
| `shared/evals/_agent_eval.py` (MODIFY) | Add runners for new flow types |
| `shared/evals/failure_harvester.py` (CREATE) | Auto-generate eval cases from production failures |
| `shared/evals/trajectory_eval.py` (CREATE) | Trajectory-level evaluation (path optimality, strategy choice) |
| `tests/fixtures/evals/canonical_flows.json` (MODIFY) | Expand from 5 to 50+ cases |
| `tests/shared/evals/test_expanded_eval.py` (CREATE) | Tests for new flow types and failure harvester |

---

### Task 1: Add screening_answer Flow Runner

**Files:**
- Modify: `shared/evals/_agent_eval.py`
- Modify: `tests/fixtures/evals/canonical_flows.json`
- Test: `tests/shared/evals/test_expanded_eval.py`

- [ ] **Step 1: Write failing test for screening_answer flow**

```python
# tests/shared/evals/test_expanded_eval.py
"""Tests for expanded canonical flow eval suite."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from shared.evals._agent_eval import (
    CanonicalFlowCase,
    _run_case,
)


class TestScreeningAnswerFlow:
    def test_visa_question_classified_correctly(self):
        case = CanonicalFlowCase(
            case_id="screen-001",
            flow="screening_answer",
            input={
                "question": "Do you require visa sponsorship?",
                "options": ["Yes", "No"],
            },
            expected={
                "intent": "work_auth",
            },
        )
        result = _run_case(case)
        assert result["intent"] == "work_auth"

    def test_salary_question_classified(self):
        case = CanonicalFlowCase(
            case_id="screen-002",
            flow="screening_answer",
            input={
                "question": "What is your expected salary?",
                "options": [],
            },
            expected={
                "intent": "salary",
            },
        )
        result = _run_case(case)
        assert result["intent"] == "salary"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestScreeningAnswerFlow -v`
Expected: FAIL with `ValueError` or `KeyError` (no handler for `screening_answer` flow)

- [ ] **Step 3: Add screening_answer flow handler to _agent_eval.py**

In `shared/evals/_agent_eval.py`, add this block after the existing `validate_review` handler:

```python
    if case.flow == "screening_answer":
        from jobpulse.screening_intent import ScreeningIntentClassifier

        classifier = ScreeningIntentClassifier()
        intent = classifier.classify(case.input["question"])
        return {
            "intent": intent.value if intent else "unknown",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestScreeningAnswerFlow -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/evals/_agent_eval.py tests/shared/evals/test_expanded_eval.py
git commit -m "feat(eval): screening_answer flow runner in canonical eval harness"
```

---

### Task 2: Add field_mapping Flow Runner

**Files:**
- Modify: `shared/evals/_agent_eval.py`
- Test: `tests/shared/evals/test_expanded_eval.py`

- [ ] **Step 1: Write failing test for field_mapping flow**

```python
# Append to tests/shared/evals/test_expanded_eval.py

class TestFieldMappingFlow:
    def test_semantic_match_exact(self):
        case = CanonicalFlowCase(
            case_id="field-001",
            flow="field_mapping",
            input={
                "desired_value": "Male",
                "available_options": ["Male", "Female", "Other"],
                "field_label": "Gender",
            },
            expected={
                "matched_option": "Male",
            },
        )
        result = _run_case(case)
        assert result["matched_option"] == "Male"

    def test_semantic_match_alias(self):
        case = CanonicalFlowCase(
            case_id="field-002",
            flow="field_mapping",
            input={
                "desired_value": "Male",
                "available_options": ["Man", "Woman", "Non-binary"],
                "field_label": "Gender",
            },
            expected={
                "matched_option": "Man",
            },
        )
        result = _run_case(case)
        assert result["matched_option"] == "Man"

    def test_semantic_match_numeric_range(self):
        case = CanonicalFlowCase(
            case_id="field-003",
            flow="field_mapping",
            input={
                "desired_value": "3",
                "available_options": ["0-1 years", "2-3 years", "4-5 years"],
                "field_label": "Experience",
                "numeric_value": 3.0,
            },
            expected={
                "matched_option": "2-3 years",
            },
        )
        result = _run_case(case)
        assert result["matched_option"] == "2-3 years"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestFieldMappingFlow -v`
Expected: FAIL (no handler for `field_mapping` flow)

- [ ] **Step 3: Add field_mapping flow handler**

In `shared/evals/_agent_eval.py`:

```python
    if case.flow == "field_mapping":
        from jobpulse.form_engine.semantic_matcher import semantic_option_match

        matched = semantic_option_match(
            case.input["desired_value"],
            case.input["available_options"],
            field_label=case.input.get("field_label", ""),
            numeric_value=case.input.get("numeric_value"),
        )
        return {
            "matched_option": matched,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestFieldMappingFlow -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/evals/_agent_eval.py tests/shared/evals/test_expanded_eval.py
git commit -m "feat(eval): field_mapping flow runner using semantic_option_match"
```

---

### Task 3: Add fill_failure_class Flow Runner

**Files:**
- Modify: `shared/evals/_agent_eval.py`
- Test: `tests/shared/evals/test_expanded_eval.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/shared/evals/test_expanded_eval.py

class TestFillFailureClassFlow:
    def test_classify_no_field(self):
        case = CanonicalFlowCase(
            case_id="fail-001",
            flow="fill_failure_class",
            input={
                "error_message": "Element not found: #salary-input",
                "field_label": "Salary",
                "field_type": "text",
            },
            expected={
                "failure_class": "no_field",
            },
        )
        result = _run_case(case)
        assert result["failure_class"] == "no_field"

    def test_classify_readonly(self):
        case = CanonicalFlowCase(
            case_id="fail-002",
            flow="fill_failure_class",
            input={
                "error_message": "Cannot fill readonly element",
                "field_label": "Email",
                "field_type": "text",
            },
            expected={
                "failure_class": "readonly",
            },
        )
        result = _run_case(case)
        assert result["failure_class"] == "readonly"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestFillFailureClassFlow -v`
Expected: FAIL

- [ ] **Step 3: Add fill_failure_class flow handler**

```python
    if case.flow == "fill_failure_class":
        error = case.input.get("error_message", "").lower()
        if "not found" in error or "no element" in error:
            failure_class = "no_field"
        elif "readonly" in error or "disabled" in error:
            failure_class = "readonly"
        elif "blocked" in error or "intercepted" in error:
            failure_class = "blocked"
        elif "wrong" in error or "invalid" in error or "validation" in error:
            failure_class = "wrong_value"
        else:
            failure_class = "unknown"
        return {"failure_class": failure_class}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestFillFailureClassFlow -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/evals/_agent_eval.py tests/shared/evals/test_expanded_eval.py
git commit -m "feat(eval): fill_failure_class flow runner for error classification"
```

---

### Task 4: Add platform_bypass Flow Runner

**Files:**
- Modify: `shared/evals/_agent_eval.py`
- Test: `tests/shared/evals/test_expanded_eval.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/shared/evals/test_expanded_eval.py

class TestPlatformBypassFlow:
    def test_indeed_is_aggregator(self):
        case = CanonicalFlowCase(
            case_id="bypass-001",
            flow="platform_bypass",
            input={
                "url": "https://uk.indeed.com/viewjob?jk=abc123",
            },
            expected={
                "is_aggregator": True,
            },
        )
        result = _run_case(case)
        assert result["is_aggregator"] is True

    def test_greenhouse_is_not_aggregator(self):
        case = CanonicalFlowCase(
            case_id="bypass-002",
            flow="platform_bypass",
            input={
                "url": "https://boards.greenhouse.io/company/jobs/123",
            },
            expected={
                "is_aggregator": False,
            },
        )
        result = _run_case(case)
        assert result["is_aggregator"] is False

    def test_linkedin_is_aggregator(self):
        case = CanonicalFlowCase(
            case_id="bypass-003",
            flow="platform_bypass",
            input={
                "url": "https://www.linkedin.com/jobs/view/12345",
            },
            expected={
                "is_aggregator": True,
            },
        )
        result = _run_case(case)
        assert result["is_aggregator"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestPlatformBypassFlow -v`
Expected: FAIL

- [ ] **Step 3: Add platform_bypass flow handler**

```python
    if case.flow == "platform_bypass":
        from jobpulse.platform_bypass import is_aggregator_domain

        return {
            "is_aggregator": is_aggregator_domain(case.input["url"]),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestPlatformBypassFlow -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/evals/_agent_eval.py tests/shared/evals/test_expanded_eval.py
git commit -m "feat(eval): platform_bypass flow runner for aggregator detection"
```

---

### Task 5: Add page_classification Flow Runner

**Files:**
- Modify: `shared/evals/_agent_eval.py`
- Test: `tests/shared/evals/test_expanded_eval.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/shared/evals/test_expanded_eval.py

class TestPageClassificationFlow:
    def test_classify_form_page(self):
        case = CanonicalFlowCase(
            case_id="page-001",
            flow="page_classification",
            input={
                "text_content": "Apply for this position. First Name, Last Name, Email, Upload Resume, Submit Application",
                "has_form_elements": True,
                "has_submit_button": True,
            },
            expected={
                "page_type_contains": "form",
            },
        )
        result = _run_case(case)
        assert "form" in result["page_type"].lower()

    def test_classify_job_listing_page(self):
        case = CanonicalFlowCase(
            case_id="page-002",
            flow="page_classification",
            input={
                "text_content": "Job Description: We are looking for a Data Analyst. Requirements: SQL, Python. Apply Now",
                "has_form_elements": False,
                "has_submit_button": False,
            },
            expected={
                "page_type_contains": "job",
            },
        )
        result = _run_case(case)
        assert "job" in result["page_type"].lower() or "listing" in result["page_type"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestPageClassificationFlow -v`
Expected: FAIL

- [ ] **Step 3: Add page_classification flow handler**

```python
    if case.flow == "page_classification":
        text = case.input.get("text_content", "").lower()
        has_form = case.input.get("has_form_elements", False)
        has_submit = case.input.get("has_submit_button", False)

        if has_form and has_submit:
            page_type = "application_form"
        elif "apply" in text and has_submit:
            page_type = "application_form"
        elif "job description" in text or "requirements" in text:
            page_type = "job_listing"
        elif "sign in" in text or "log in" in text:
            page_type = "login"
        elif "verify" in text or "captcha" in text:
            page_type = "verification_wall"
        else:
            page_type = "unknown"
        return {"page_type": page_type}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestPageClassificationFlow -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/evals/_agent_eval.py tests/shared/evals/test_expanded_eval.py
git commit -m "feat(eval): page_classification flow runner"
```

---

### Task 6: Expand canonical_flows.json to 50+ Cases

**Files:**
- Modify: `tests/fixtures/evals/canonical_flows.json`

- [ ] **Step 1: Read the current 5 cases**

Run: `cat tests/fixtures/evals/canonical_flows.json`
Expected: 5 existing cases (flow-001 through flow-005)

- [ ] **Step 2: Append 45+ new cases**

Add cases covering all new flow types. Keep the existing 5 cases intact. Append after them:

```json
  {
    "case_id": "screen-001",
    "flow": "screening_answer",
    "input": {"question": "Do you require visa sponsorship?", "options": ["Yes", "No"]},
    "expected": {"intent": "work_auth"}
  },
  {
    "case_id": "screen-002",
    "flow": "screening_answer",
    "input": {"question": "What is your expected salary?", "options": []},
    "expected": {"intent": "salary"}
  },
  {
    "case_id": "screen-003",
    "flow": "screening_answer",
    "input": {"question": "What is your notice period?", "options": ["Immediately", "1 week", "2 weeks", "1 month"]},
    "expected": {"intent": "notice_period"}
  },
  {
    "case_id": "screen-004",
    "flow": "screening_answer",
    "input": {"question": "Are you legally authorized to work in the UK?", "options": ["Yes", "No"]},
    "expected": {"intent": "work_auth"}
  },
  {
    "case_id": "screen-005",
    "flow": "screening_answer",
    "input": {"question": "How many years of experience do you have with Python?", "options": []},
    "expected": {"intent": "experience"}
  },
  {
    "case_id": "screen-006",
    "flow": "screening_answer",
    "input": {"question": "Are you willing to relocate?", "options": ["Yes", "No"]},
    "expected": {"intent": "relocation"}
  },
  {
    "case_id": "screen-007",
    "flow": "screening_answer",
    "input": {"question": "What is your highest level of education?", "options": ["High School", "Bachelors", "Masters", "PhD"]},
    "expected": {"intent": "education"}
  },
  {
    "case_id": "screen-008",
    "flow": "screening_answer",
    "input": {"question": "Do you have a valid driving licence?", "options": ["Yes", "No"]},
    "expected": {"intent": "driving_license"}
  },
  {
    "case_id": "screen-009",
    "flow": "screening_answer",
    "input": {"question": "What gender do you identify as?", "options": ["Male", "Female", "Non-binary", "Prefer not to say"]},
    "expected": {"intent": "gender"}
  },
  {
    "case_id": "screen-010",
    "flow": "screening_answer",
    "input": {"question": "What is your ethnicity?", "options": ["White", "Asian", "Black", "Mixed", "Other"]},
    "expected": {"intent": "ethnicity"}
  },
  {
    "case_id": "field-001",
    "flow": "field_mapping",
    "input": {"desired_value": "Male", "available_options": ["Male", "Female", "Other"], "field_label": "Gender"},
    "expected": {"matched_option": "Male"}
  },
  {
    "case_id": "field-002",
    "flow": "field_mapping",
    "input": {"desired_value": "Male", "available_options": ["Man", "Woman", "Non-binary"], "field_label": "Gender"},
    "expected": {"matched_option": "Man"}
  },
  {
    "case_id": "field-003",
    "flow": "field_mapping",
    "input": {"desired_value": "3", "available_options": ["0-1 years", "2-3 years", "4-5 years"], "field_label": "Experience", "numeric_value": 3.0},
    "expected": {"matched_option": "2-3 years"}
  },
  {
    "case_id": "field-004",
    "flow": "field_mapping",
    "input": {"desired_value": "Yes", "available_options": ["I am authorized to work", "I require sponsorship"], "field_label": "Work authorization"},
    "expected": {"matched_option": "I am authorized to work"}
  },
  {
    "case_id": "field-005",
    "flow": "field_mapping",
    "input": {"desired_value": "1 month", "available_options": ["Immediately", "Less than 2 weeks", "Less than 1 month", "1-3 months"], "field_label": "Notice period"},
    "expected": {"matched_option": "Less than 1 month"}
  },
  {
    "case_id": "field-006",
    "flow": "field_mapping",
    "input": {"desired_value": "Graduate Visa", "available_options": ["Tier 4 Graduate Visa", "Work Permit", "Permanent Resident"], "field_label": "Visa type"},
    "expected": {"matched_option": "Tier 4 Graduate Visa"}
  },
  {
    "case_id": "field-007",
    "flow": "field_mapping",
    "input": {"desired_value": "Indian", "available_options": ["White", "Asian or Asian British - Indian", "Black", "Mixed"], "field_label": "Ethnicity"},
    "expected": {"matched_option": "Asian or Asian British - Indian"}
  },
  {
    "case_id": "field-008",
    "flow": "field_mapping",
    "input": {"desired_value": "No", "available_options": ["Yes, I have a disability", "No, I do not have a disability", "Prefer not to say"], "field_label": "Disability"},
    "expected": {"matched_option": "No, I do not have a disability"}
  },
  {
    "case_id": "field-009",
    "flow": "field_mapping",
    "input": {"desired_value": "immediately", "available_options": ["Available immediately", "2 weeks notice", "1 month notice"], "field_label": "Availability"},
    "expected": {"matched_option": "Available immediately"}
  },
  {
    "case_id": "field-010",
    "flow": "field_mapping",
    "input": {"desired_value": "United Kingdom", "available_options": ["UK", "United States", "Canada", "Australia"], "field_label": "Country"},
    "expected": {"matched_option": "UK"}
  },
  {
    "case_id": "bypass-001",
    "flow": "platform_bypass",
    "input": {"url": "https://uk.indeed.com/viewjob?jk=abc123"},
    "expected": {"is_aggregator": true}
  },
  {
    "case_id": "bypass-002",
    "flow": "platform_bypass",
    "input": {"url": "https://boards.greenhouse.io/company/jobs/123"},
    "expected": {"is_aggregator": false}
  },
  {
    "case_id": "bypass-003",
    "flow": "platform_bypass",
    "input": {"url": "https://www.linkedin.com/jobs/view/12345"},
    "expected": {"is_aggregator": true}
  },
  {
    "case_id": "bypass-004",
    "flow": "platform_bypass",
    "input": {"url": "https://www.totaljobs.com/job/12345"},
    "expected": {"is_aggregator": true}
  },
  {
    "case_id": "bypass-005",
    "flow": "platform_bypass",
    "input": {"url": "https://company.lever.co/apply/12345"},
    "expected": {"is_aggregator": false}
  },
  {
    "case_id": "bypass-006",
    "flow": "platform_bypass",
    "input": {"url": "https://www.reed.co.uk/jobs/data-analyst/12345"},
    "expected": {"is_aggregator": true}
  },
  {
    "case_id": "bypass-007",
    "flow": "platform_bypass",
    "input": {"url": "https://www.glassdoor.co.uk/job-listing/12345"},
    "expected": {"is_aggregator": true}
  },
  {
    "case_id": "bypass-008",
    "flow": "platform_bypass",
    "input": {"url": "https://company.workday.com/apply"},
    "expected": {"is_aggregator": false}
  },
  {
    "case_id": "fail-001",
    "flow": "fill_failure_class",
    "input": {"error_message": "Element not found: #salary-input", "field_label": "Salary", "field_type": "text"},
    "expected": {"failure_class": "no_field"}
  },
  {
    "case_id": "fail-002",
    "flow": "fill_failure_class",
    "input": {"error_message": "Cannot fill readonly element", "field_label": "Email", "field_type": "text"},
    "expected": {"failure_class": "readonly"}
  },
  {
    "case_id": "fail-003",
    "flow": "fill_failure_class",
    "input": {"error_message": "Click intercepted by overlay", "field_label": "Submit", "field_type": "button"},
    "expected": {"failure_class": "blocked"}
  },
  {
    "case_id": "fail-004",
    "flow": "fill_failure_class",
    "input": {"error_message": "Validation error: invalid phone format", "field_label": "Phone", "field_type": "text"},
    "expected": {"failure_class": "wrong_value"}
  },
  {
    "case_id": "fail-005",
    "flow": "fill_failure_class",
    "input": {"error_message": "Element is disabled and cannot be modified", "field_label": "ID", "field_type": "text"},
    "expected": {"failure_class": "readonly"}
  },
  {
    "case_id": "page-001",
    "flow": "page_classification",
    "input": {"text_content": "Apply for this position. First Name, Last Name, Email, Upload Resume, Submit Application", "has_form_elements": true, "has_submit_button": true},
    "expected": {"page_type_contains": "form"}
  },
  {
    "case_id": "page-002",
    "flow": "page_classification",
    "input": {"text_content": "Job Description: We are looking for a Data Analyst. Requirements: SQL, Python. Apply Now", "has_form_elements": false, "has_submit_button": false},
    "expected": {"page_type_contains": "job"}
  },
  {
    "case_id": "page-003",
    "flow": "page_classification",
    "input": {"text_content": "Sign in to your account. Email, Password, Forgot password?, Log in", "has_form_elements": true, "has_submit_button": true},
    "expected": {"page_type_contains": "login"}
  },
  {
    "case_id": "page-004",
    "flow": "page_classification",
    "input": {"text_content": "Please verify you are not a robot. Complete the CAPTCHA below.", "has_form_elements": false, "has_submit_button": false},
    "expected": {"page_type_contains": "verification"}
  },
  {
    "case_id": "page-005",
    "flow": "page_classification",
    "input": {"text_content": "Create your account to apply. First name, Last name, Email, Password, Create Account", "has_form_elements": true, "has_submit_button": true},
    "expected": {"page_type_contains": "form"}
  },
  {
    "case_id": "cmd-006",
    "flow": "classify_command",
    "input": {"text": "apply to next job"},
    "expected": {"intent": "apply_next"}
  },
  {
    "case_id": "cmd-007",
    "flow": "classify_command",
    "input": {"text": "scan for new jobs"},
    "expected": {"intent": "job_scan"}
  },
  {
    "case_id": "cmd-008",
    "flow": "classify_command",
    "input": {"text": "show my budget"},
    "expected": {"intent": "budget_summary"}
  },
  {
    "case_id": "cmd-009",
    "flow": "classify_command",
    "input": {"text": "job stats"},
    "expected": {"intent": "job_analytics"}
  },
  {
    "case_id": "cmd-010",
    "flow": "classify_command",
    "input": {"text": "what's on my calendar today"},
    "expected": {"intent": "calendar"}
  }
```

Total: 5 (existing) + 10 (screening) + 10 (field_mapping) + 8 (bypass) + 5 (fail) + 5 (page) + 5 (command) = 48 cases, then add 2-3 more to reach 50+.

- [ ] **Step 3: Run the full eval suite**

Run: `python -m pytest tests/shared/evals/ -v`
Expected: All 50+ cases load and run correctly

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/evals/canonical_flows.json
git commit -m "feat(eval): expand canonical_flows.json from 5 to 50+ cases"
```

---

### Task 7: Failure Harvester — Auto-Generate from OPRAL Failures

**Files:**
- Create: `shared/evals/failure_harvester.py`
- Test: `tests/shared/evals/test_expanded_eval.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/shared/evals/test_expanded_eval.py

class TestFailureHarvester:
    def test_harvest_from_form_failures(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB
        from shared.evals.failure_harvester import FailureHarvester

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        # Insert a failure record
        import sqlite3
        from datetime import datetime, UTC
        with sqlite3.connect(str(tmp_path / "test.db")) as conn:
            conn.execute(
                """INSERT INTO form_failure_reasons
                   (domain, platform, failure_type, field_label, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("test.com", "greenhouse", "wrong_value", "Salary",
                 "Expected integer, got string", datetime.now(UTC).isoformat()),
            )

        harvester = FailureHarvester(form_experience_db=db)
        cases = harvester.harvest_form_failures()
        assert len(cases) >= 1
        assert cases[0]["flow"] == "fill_failure_class"

    def test_harvest_from_mistakes_md(self, tmp_path):
        from shared.evals.failure_harvester import FailureHarvester

        mistakes = tmp_path / "mistakes.md"
        mistakes.write_text(
            "## 2026-04-25\n"
            "- **field_mapping**: Gender field mapped to 'Male' but form had 'Man'\n"
            "- **screening**: Salary question answered with range instead of integer\n"
        )
        harvester = FailureHarvester(mistakes_path=str(mistakes))
        cases = harvester.harvest_mistakes()
        assert len(cases) >= 1
        for c in cases:
            assert "case_id" in c
            assert "flow" in c

    def test_empty_sources_return_empty(self, tmp_path):
        from shared.evals.failure_harvester import FailureHarvester
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "empty.db"))
        harvester = FailureHarvester(
            form_experience_db=db,
            mistakes_path=str(tmp_path / "nonexistent.md"),
        )
        assert harvester.harvest_form_failures() == []
        assert harvester.harvest_mistakes() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestFailureHarvester -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement failure_harvester.py**

```python
# shared/evals/failure_harvester.py
"""Auto-generate eval cases from production failures.

Reads from:
- FormExperienceDB.form_failure_reasons table
- .claude/mistakes.md entries
- CorrectionCapture diffs (future)

Outputs canonical flow cases that can be appended to canonical_flows.json.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


class FailureHarvester:
    def __init__(
        self,
        form_experience_db=None,
        mistakes_path: str | None = None,
    ):
        self._form_db = form_experience_db
        self._mistakes_path = mistakes_path

    def harvest_form_failures(self) -> list[dict[str, Any]]:
        if self._form_db is None:
            return []
        try:
            import sqlite3
            with sqlite3.connect(self._form_db._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM form_failure_reasons ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
        except Exception:
            return []

        cases = []
        for i, row in enumerate(rows):
            r = dict(row)
            cases.append({
                "case_id": f"harvest-fail-{i+1:03d}",
                "flow": "fill_failure_class",
                "input": {
                    "error_message": r.get("details", "unknown error"),
                    "field_label": r.get("field_label", ""),
                    "field_type": "text",
                },
                "expected": {
                    "failure_class": r.get("failure_type", "unknown"),
                },
            })
        return cases

    def harvest_mistakes(self) -> list[dict[str, Any]]:
        if not self._mistakes_path:
            return []
        path = Path(self._mistakes_path)
        if not path.exists():
            return []

        text = path.read_text(encoding="utf-8")
        entries = re.findall(
            r"-\s+\*\*(\w+)\*\*:\s+(.+)",
            text,
        )

        cases = []
        for i, (category, description) in enumerate(entries):
            flow = "fill_failure_class"
            if "screening" in category.lower():
                flow = "screening_answer"
            elif "field_mapping" in category.lower() or "mapping" in category.lower():
                flow = "field_mapping"
            elif "page" in category.lower() or "classification" in category.lower():
                flow = "page_classification"

            cases.append({
                "case_id": f"harvest-mistake-{i+1:03d}",
                "flow": flow,
                "input": {
                    "description": description.strip(),
                    "category": category,
                },
                "expected": {},
            })
        return cases

    def harvest_all(self) -> list[dict[str, Any]]:
        return self.harvest_form_failures() + self.harvest_mistakes()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestFailureHarvester -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/evals/failure_harvester.py tests/shared/evals/test_expanded_eval.py
git commit -m "feat(eval): FailureHarvester auto-generates eval cases from OPRAL failures"
```

---

### Task 8: Trajectory Evaluation

**Files:**
- Create: `shared/evals/trajectory_eval.py`
- Test: `tests/shared/evals/test_expanded_eval.py`

- [ ] **Step 1: Write failing test for trajectory eval**

```python
# Append to tests/shared/evals/test_expanded_eval.py

class TestTrajectoryEval:
    def test_optimal_trajectory_scores_high(self):
        from shared.evals.trajectory_eval import score_trajectory

        trajectory = [
            {"action": "navigate", "page_type": "job_listing", "time_ms": 500},
            {"action": "click_apply", "page_type": "application_form", "time_ms": 200},
            {"action": "fill_form", "page_type": "application_form", "time_ms": 3000},
            {"action": "submit", "page_type": "confirmation", "time_ms": 100},
        ]
        score = score_trajectory(trajectory, success=True)
        assert score >= 0.8

    def test_looping_trajectory_scores_low(self):
        from shared.evals.trajectory_eval import score_trajectory

        trajectory = [
            {"action": "navigate", "page_type": "job_listing", "time_ms": 500},
            {"action": "navigate", "page_type": "job_listing", "time_ms": 500},
            {"action": "navigate", "page_type": "job_listing", "time_ms": 500},
            {"action": "click_apply", "page_type": "application_form", "time_ms": 200},
            {"action": "fill_form", "page_type": "application_form", "time_ms": 3000},
            {"action": "submit", "page_type": "confirmation", "time_ms": 100},
        ]
        score = score_trajectory(trajectory, success=True)
        # Repeated navigations = suboptimal path
        assert score < 0.8

    def test_failed_trajectory_capped(self):
        from shared.evals.trajectory_eval import score_trajectory

        trajectory = [
            {"action": "navigate", "page_type": "job_listing", "time_ms": 500},
            {"action": "error", "page_type": "error", "time_ms": 0},
        ]
        score = score_trajectory(trajectory, success=False)
        assert score <= 0.3

    def test_strategy_optimality(self):
        from shared.evals.trajectory_eval import score_strategy_choice

        result = score_strategy_choice(
            chosen_strategy="cached",
            available_strategies=["cached", "llm", "vision"],
            outcome_success=True,
        )
        # Cached + success = optimal choice
        assert result >= 0.9

    def test_llm_when_cache_available_penalized(self):
        from shared.evals.trajectory_eval import score_strategy_choice

        result = score_strategy_choice(
            chosen_strategy="llm",
            available_strategies=["cached", "llm", "vision"],
            outcome_success=True,
        )
        # Used LLM when cache was available = suboptimal
        assert result < 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestTrajectoryEval -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement trajectory_eval.py**

```python
# shared/evals/trajectory_eval.py
"""Trajectory-level evaluation for application pipeline.

Scores not just outcome (success/fail) but process:
- Path optimality: were there unnecessary steps?
- Strategy choice: did the agent use the cheapest effective strategy?
- Time efficiency: was the fill time reasonable?
"""
from __future__ import annotations

from collections import Counter

from shared.logging_config import get_logger

logger = get_logger(__name__)

_STRATEGY_COST_ORDER = ["deterministic", "cached", "consensus", "llm", "vision"]


def score_trajectory(
    trajectory: list[dict],
    *,
    success: bool,
) -> float:
    if not trajectory:
        return 0.0

    if not success:
        return min(0.3, len(trajectory) * 0.05)

    action_counts = Counter(step.get("action", "") for step in trajectory)
    total_steps = len(trajectory)

    repeated_actions = sum(max(0, count - 1) for count in action_counts.values())
    repetition_penalty = min(0.4, repeated_actions * 0.1)

    step_penalty = max(0, (total_steps - 4) * 0.05)
    step_penalty = min(0.3, step_penalty)

    total_time_ms = sum(step.get("time_ms", 0) for step in trajectory)
    time_penalty = 0.0
    if total_time_ms > 30_000:
        time_penalty = min(0.2, (total_time_ms - 30_000) / 100_000)

    score = 1.0 - repetition_penalty - step_penalty - time_penalty
    return max(0.0, min(1.0, round(score, 3)))


def score_strategy_choice(
    chosen_strategy: str,
    available_strategies: list[str],
    outcome_success: bool,
) -> float:
    if not outcome_success:
        return 0.3

    cheapest_available = None
    for s in _STRATEGY_COST_ORDER:
        if s in available_strategies:
            cheapest_available = s
            break

    if cheapest_available is None:
        return 0.5

    chosen_rank = (
        _STRATEGY_COST_ORDER.index(chosen_strategy)
        if chosen_strategy in _STRATEGY_COST_ORDER
        else len(_STRATEGY_COST_ORDER)
    )
    cheapest_rank = _STRATEGY_COST_ORDER.index(cheapest_available)

    gap = chosen_rank - cheapest_rank
    penalty = gap * 0.15
    return max(0.0, min(1.0, round(1.0 - penalty, 3)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/evals/test_expanded_eval.py::TestTrajectoryEval -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/evals/trajectory_eval.py tests/shared/evals/test_expanded_eval.py
git commit -m "feat(eval): trajectory_eval for path optimality and strategy choice scoring"
```

---

### Task 9: Run Full Test Suite

- [ ] **Step 1: Run all eval tests**

Run: `python -m pytest tests/shared/evals/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run canonical flow eval end-to-end**

Run: `python -m pytest tests/shared/evals/test_agent_eval.py -v`
Expected: All 50+ cases PASS

- [ ] **Step 3: Run full shared/ regression**

Run: `python -m pytest tests/shared/ -v --timeout=30`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(eval): full expanded eval suite passing, no regressions"
```
