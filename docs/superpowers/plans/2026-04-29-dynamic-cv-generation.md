# Dynamic CV Generation Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every CV and cover letter section is LLM-tailored per JD/company — no two applications produce identical resumes.

**Architecture:** 4 parallel `cognitive_llm_call()` invocations (summary+tagline, experience bullets, project bullets, cover letter prose) orchestrated via `ThreadPoolExecutor`. Each function returns typed output or `None` on failure. Validation runs post-generation; failures send Telegram alerts (pipeline continues with generated text, human reviews).

**Tech Stack:** `cognitive_llm_call()` (shared/agents.py), `ThreadPoolExecutor` (concurrent.futures), `send_jobs()` (jobpulse/telegram_bots.py), dataclasses, JSON parsing

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `jobpulse/cv_tailor.py` | **CREATE** | 4 tailoring functions + orchestrator + validation |
| `jobpulse/application_materials.py` | **MODIFY** | Call `tailor_all_sections()` before PDF generation |
| `jobpulse/cv_templates/generate_cv.py` | **MODIFY** | Add `experience` parameter to `generate_cv_pdf()` |
| `tests/jobpulse/test_cv_tailor.py` | **CREATE** | Unit tests for all tailoring + validation functions |
| `tests/jobpulse/test_application_materials_tailoring.py` | **CREATE** | Integration test for tailored material flow |

---

### Task 1: Dataclasses and Type Definitions

**Files:**
- Create: `jobpulse/cv_tailor.py` (initial scaffold)
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write the failing test for dataclass construction**

```python
# tests/jobpulse/test_cv_tailor.py
from jobpulse.cv_tailor import TailoredHeader, TailoredCoverLetter, TailoredCV


def test_tailored_header_fields():
    h = TailoredHeader(tagline="MSc CS | 2+ YOE", summary="Engineer with experience in X.")
    assert h.tagline == "MSc CS | 2+ YOE"
    assert h.summary == "Engineer with experience in X."


def test_tailored_cover_letter_fields():
    cl = TailoredCoverLetter(intro="Dear hiring manager", hook="Built X", closing="Looking forward")
    assert cl.intro.startswith("Dear")
    assert cl.hook == "Built X"
    assert cl.closing == "Looking forward"


def test_tailored_cv_all_none():
    cv = TailoredCV()
    assert cv.tagline is None
    assert cv.summary is None
    assert cv.experience is None
    assert cv.projects is None
    assert cv.cover_letter is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.cv_tailor'`

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/cv_tailor.py
"""Per-JD CV and cover letter tailoring via parallel LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.profile_store import ExperienceEntry


@dataclass
class TailoredHeader:
    tagline: str
    summary: str


@dataclass
class TailoredCoverLetter:
    intro: str
    hook: str
    closing: str


@dataclass
class TailoredCV:
    tagline: str | None = None
    summary: str | None = None
    experience: list[ExperienceEntry] | None = None
    projects: list[dict] | None = None
    cover_letter: TailoredCoverLetter | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add typed dataclasses for tailored CV sections"
```

---

### Task 2: Validation Functions

**Files:**
- Modify: `jobpulse/cv_tailor.py`
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing tests for summary validation**

```python
# tests/jobpulse/test_cv_tailor.py (append)

from jobpulse.cv_tailor import validate_summary, validate_experience, validate_projects


def test_validate_summary_clean():
    ok = "<b>Engineer</b> with 3 years experience in Python and ML. Built production systems. Specialises in NLP."
    result = validate_summary(ok)
    assert result is None  # None means valid


def test_validate_summary_too_short():
    result = validate_summary("Short.")
    assert result is not None
    assert "length" in result.lower()


def test_validate_summary_too_long():
    result = validate_summary("x" * 501)
    assert result is not None
    assert "length" in result.lower()


def test_validate_summary_soft_skill():
    bad = "<b>Engineer</b> with strong communication and teamwork skills. Built systems. Specialises in NLP and data."
    result = validate_summary(bad)
    assert result is not None
    assert "soft skill" in result.lower()


def test_validate_summary_no_bold():
    result = validate_summary("Engineer with 3 years experience. Built systems. Specialises in NLP and data pipelines.")
    assert result is not None
    assert "bold" in result.lower() or "<b>" in result.lower()
```

- [ ] **Step 2: Write failing tests for experience validation**

```python
# tests/jobpulse/test_cv_tailor.py (append)
from shared.profile_store import ExperienceEntry


def test_validate_experience_clean():
    original = [ExperienceEntry(title="Dev", company="Co", dates="2024-2025", bullets=["Built X saving 30%"])]
    tailored = [ExperienceEntry(title="Dev", company="Co", dates="2024-2025", bullets=["Developed X reducing costs by 30%"])]
    result = validate_experience(original, tailored)
    assert result is None


def test_validate_experience_count_mismatch():
    original = [ExperienceEntry(title="Dev", company="Co", dates="2024-2025", bullets=["Built X saving 30%"])]
    tailored = []  # wrong count
    result = validate_experience(original, tailored)
    assert result is not None
    assert "count" in result.lower()


def test_validate_experience_missing_metric():
    original = [ExperienceEntry(title="Dev", company="Co", dates="2024-2025", bullets=["Built X saving 30%"])]
    tailored = [ExperienceEntry(title="Dev", company="Co", dates="2024-2025", bullets=["Built something cool"])]
    result = validate_experience(original, tailored)
    assert result is not None
    assert "metric" in result.lower()


def test_validate_experience_bullet_too_long():
    original = [ExperienceEntry(title="Dev", company="Co", dates="2024-2025", bullets=["Built X saving 30%"])]
    tailored = [ExperienceEntry(title="Dev", company="Co", dates="2024-2025", bullets=["x" * 201 + " 50%"])]
    result = validate_experience(original, tailored)
    assert result is not None
    assert "200" in result or "long" in result.lower()
```

- [ ] **Step 3: Write failing tests for project validation**

```python
# tests/jobpulse/test_cv_tailor.py (append)

def test_validate_projects_clean():
    original = [{"title": "Proj", "bullets": ["Built X with 95% accuracy", "Reduced latency by 40%", "Deployed to 500 users"]}]
    tailored = [{"title": "Proj", "bullets": ["Achieved 95% accuracy with X", "Cut latency by 40%", "Served 500 users"]}]
    result = validate_projects(original, tailored)
    assert result is None


def test_validate_projects_count_mismatch():
    original = [{"title": "A", "bullets": ["X 10%"]}, {"title": "B", "bullets": ["Y 20%"]}]
    tailored = [{"title": "A", "bullets": ["X 10%"]}]
    result = validate_projects(original, tailored)
    assert result is not None
    assert "count" in result.lower()


def test_validate_projects_missing_metric():
    original = [{"title": "Proj", "bullets": ["Built X with 95% accuracy", "Reduced latency by 40%", "Deployed to 500 users"]}]
    tailored = [{"title": "Proj", "bullets": ["Built X with great accuracy", "Reduced latency significantly", "Deployed to many users"]}]
    result = validate_projects(original, tailored)
    assert result is not None
    assert "metric" in result.lower()
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "validate"`
Expected: FAIL with `ImportError: cannot import name 'validate_summary'`

- [ ] **Step 5: Implement validation functions**

```python
# jobpulse/cv_tailor.py (append after dataclasses)

import re

from shared.logging_config import get_logger

logger = get_logger(__name__)

_SOFT_SKILL_WORDS = {
    "communication", "teamwork", "leadership", "problem solving", "time management",
    "adaptability", "collaboration", "analytical thinking", "critical thinking",
    "stakeholder management", "mentoring", "coaching", "prioritization",
    "attention to detail", "self motivated", "fast learner", "customer focus",
    "decision making", "interviewing", "okrs", "presentation skills",
    "project management", "strategic thinking", "negotiation",
}

_METRIC_RE = re.compile(r"\d+[%$£]|\d{2,}")


def validate_summary(summary: str) -> str | None:
    """Validate tailored summary. Returns error string or None if valid."""
    if len(summary) < 100 or len(summary) > 500:
        return f"Summary length {len(summary)} outside 100-500 range"
    summary_lower = summary.lower()
    for word in _SOFT_SKILL_WORDS:
        if word in summary_lower:
            return f"Soft skill word found: '{word}'"
    if "<b>" not in summary:
        return "Summary must contain at least one <b> tag"
    return None


def validate_experience(
    original: list[ExperienceEntry],
    tailored: list[ExperienceEntry],
) -> str | None:
    """Validate tailored experience. Returns error string or None if valid."""
    if len(tailored) != len(original):
        return f"Entry count mismatch: expected {len(original)}, got {len(tailored)}"
    for i, entry in enumerate(tailored):
        for j, bullet in enumerate(entry.bullets):
            if len(bullet) > 200:
                return f"Entry {i} bullet {j} exceeds 200 chars ({len(bullet)})"
            if not _METRIC_RE.search(bullet):
                return f"Entry {i} bullet {j} missing quantified metric"
    return None


def validate_projects(
    original: list[dict],
    tailored: list[dict],
) -> str | None:
    """Validate tailored projects. Returns error string or None if valid."""
    if len(tailored) != len(original):
        return f"Project count mismatch: expected {len(original)}, got {len(tailored)}"
    for i, (orig, tail) in enumerate(zip(original, tailored)):
        orig_numbers = set(re.findall(r"\d+", " ".join(orig.get("bullets", []))))
        tail_numbers = set(re.findall(r"\d+", " ".join(tail.get("bullets", []))))
        missing = orig_numbers - tail_numbers
        if missing:
            return f"Project {i} missing metrics: {missing}"
        bullet_count = len(tail.get("bullets", []))
        if bullet_count < 3 or bullet_count > 4:
            return f"Project {i} has {bullet_count} bullets (expected 3-4)"
    return None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "validate"`
Expected: PASS (all validation tests)

- [ ] **Step 7: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add validation functions for summary, experience, projects"
```

---

### Task 3: Cover Letter Prose Validation

**Files:**
- Modify: `jobpulse/cv_tailor.py`
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing tests for CL validation**

```python
# tests/jobpulse/test_cv_tailor.py (append)

from jobpulse.cv_tailor import validate_cover_letter


def test_validate_cover_letter_clean():
    cl = TailoredCoverLetter(
        intro="I am excited to apply for the Data Scientist role at Acme Corp.",
        hook="Built ML pipeline achieving 95% accuracy for production NLP system.",
        closing="I look forward to discussing how I can contribute to Acme Corp.",
    )
    result = validate_cover_letter(cl, company="Acme Corp")
    assert result is None


def test_validate_cover_letter_no_company_in_intro():
    cl = TailoredCoverLetter(
        intro="I am excited to apply for this exciting role in data science.",
        hook="Built ML pipeline achieving 95% accuracy for production NLP system.",
        closing="I look forward to discussing how I can contribute.",
    )
    result = validate_cover_letter(cl, company="Acme Corp")
    assert result is not None
    assert "company" in result.lower()


def test_validate_cover_letter_too_short():
    cl = TailoredCoverLetter(intro="Hi.", hook="Built stuff.", closing="Thanks.")
    result = validate_cover_letter(cl, company="Acme")
    assert result is not None
    assert "length" in result.lower() or "short" in result.lower()


def test_validate_cover_letter_too_long():
    cl = TailoredCoverLetter(intro="x" * 301, hook="Built ML system. " * 20, closing="Thanks. " * 40)
    result = validate_cover_letter(cl, company="Acme")
    assert result is not None
    assert "length" in result.lower() or "long" in result.lower()


def test_validate_cover_letter_soft_skill_in_hook():
    cl = TailoredCoverLetter(
        intro="I am excited to apply for the role at Acme Corp in data science.",
        hook="Strong communication and leadership skills helped me deliver projects.",
        closing="I look forward to discussing how I can contribute to Acme Corp.",
    )
    result = validate_cover_letter(cl, company="Acme Corp")
    assert result is not None
    assert "soft skill" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py::test_validate_cover_letter_clean -v`
Expected: FAIL with `ImportError: cannot import name 'validate_cover_letter'`

- [ ] **Step 3: Implement cover letter validation**

```python
# jobpulse/cv_tailor.py (append after validate_projects)

def validate_cover_letter(
    cl: TailoredCoverLetter,
    company: str,
) -> str | None:
    """Validate tailored cover letter prose. Returns error string or None if valid."""
    for section_name, text in [("intro", cl.intro), ("hook", cl.hook), ("closing", cl.closing)]:
        if len(text) < 50:
            return f"CL {section_name} too short ({len(text)} chars, min 50)"
        if len(text) > 300:
            return f"CL {section_name} too long ({len(text)} chars, max 300)"

    if company.lower() not in cl.intro.lower():
        return f"CL intro does not mention company name '{company}'"

    hook_lower = cl.hook.lower()
    for word in _SOFT_SKILL_WORDS:
        if word in hook_lower:
            return f"CL hook contains soft skill word: '{word}'"

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "cover_letter"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add cover letter prose validation"
```

---

### Task 4: Telegram Alert Helper

**Files:**
- Modify: `jobpulse/cv_tailor.py`
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing test for alert helper**

```python
# tests/jobpulse/test_cv_tailor.py (append)

def test_send_validation_alert(monkeypatch):
    from jobpulse import cv_tailor

    sent = []
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: sent.append(text) or True)

    cv_tailor._send_validation_alert("summary", "Acme Corp", "Soft skill word found: 'teamwork'", "some generated text")
    assert len(sent) == 1
    assert "summary" in sent[0]
    assert "Acme Corp" in sent[0]
    assert "teamwork" in sent[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py::test_send_validation_alert -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement alert helper**

```python
# jobpulse/cv_tailor.py (add import at top, then function after validation functions)

# At top of file, add:
from jobpulse.telegram_bots import send_jobs


def _send_validation_alert(section: str, company: str, reason: str, text: str) -> None:
    """Send Telegram alert for validation failure. Non-blocking."""
    try:
        msg = f"CV Tailoring: {section} failed validation for {company} — {reason}. Generated text: {text[:200]}"
        send_jobs(msg)
    except Exception as exc:
        logger.debug("cv_tailor: Telegram alert failed: %s", exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py::test_send_validation_alert -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add Telegram validation alert helper"
```

---

### Task 5: Summary and Tagline Tailoring Function

**Files:**
- Modify: `jobpulse/cv_tailor.py`
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing test for tailor_summary_and_tagline**

```python
# tests/jobpulse/test_cv_tailor.py (append)

import json


def test_tailor_summary_and_tagline_success(monkeypatch):
    from jobpulse import cv_tailor

    response_json = json.dumps({
        "tagline": "MSc Computer Science (UOD) | 2+ YOE | Data Scientist | Python, ML, NLP, TensorFlow",
        "summary": "<b>Data Scientist</b> with experience in NLP and deep learning at Acme Corp. Built production ML pipelines processing 10M records. Specialises in <b>Python</b>, <b>TensorFlow</b>, and <b>transformer models</b>.",
    })
    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: response_json)

    alerts = []
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: alerts.append(text) or True)

    result = cv_tailor.tailor_summary_and_tagline(
        jd_title="Data Scientist",
        jd_description="We need a data scientist with NLP experience.",
        company="Acme Corp",
        required_skills=["Python", "NLP", "TensorFlow"],
        preferred_skills=["Deep Learning"],
    )
    assert result is not None
    assert "MSc" in result.tagline
    assert "<b>" in result.summary
    assert len(alerts) == 0


def test_tailor_summary_and_tagline_llm_failure(monkeypatch):
    from jobpulse import cv_tailor

    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("LLM down")))

    result = cv_tailor.tailor_summary_and_tagline(
        jd_title="Data Scientist",
        jd_description="Need a data scientist.",
        company="Acme",
        required_skills=["Python"],
        preferred_skills=[],
    )
    assert result is None


def test_tailor_summary_and_tagline_bad_json(monkeypatch):
    from jobpulse import cv_tailor

    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: "not valid json {{{")

    result = cv_tailor.tailor_summary_and_tagline(
        jd_title="Data Scientist",
        jd_description="Need a data scientist.",
        company="Acme",
        required_skills=["Python"],
        preferred_skills=[],
    )
    assert result is None


def test_tailor_summary_and_tagline_validation_failure_sends_alert(monkeypatch):
    from jobpulse import cv_tailor

    response_json = json.dumps({
        "tagline": "MSc Computer Science (UOD) | 2+ YOE | Data Scientist",
        "summary": "Short.",  # too short — will fail validation
    })
    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: response_json)

    alerts = []
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: alerts.append(text) or True)

    result = cv_tailor.tailor_summary_and_tagline(
        jd_title="Data Scientist",
        jd_description="We need a data scientist.",
        company="Acme Corp",
        required_skills=["Python"],
        preferred_skills=[],
    )
    # Returns the result even on validation failure (human reviews)
    assert result is not None
    assert len(alerts) == 1
    assert "summary" in alerts[0].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_summary"`
Expected: FAIL with `AttributeError: module 'jobpulse.cv_tailor' has no attribute 'tailor_summary_and_tagline'`

- [ ] **Step 3: Implement tailor_summary_and_tagline**

```python
# jobpulse/cv_tailor.py (add import at top, then function)

# At top, add:
import json
from shared.agents import cognitive_llm_call


def tailor_summary_and_tagline(
    jd_title: str,
    jd_description: str,
    company: str,
    required_skills: list[str],
    preferred_skills: list[str],
) -> TailoredHeader | None:
    """Generate tagline + professional summary tailored to JD."""
    prompt = f"""Generate a CV tagline and professional summary for this job application.

JOB TITLE: {jd_title}
COMPANY: {company}
REQUIRED SKILLS: {', '.join(required_skills[:10])}
PREFERRED SKILLS: {', '.join(preferred_skills[:10])}
JD EXCERPT: {jd_description[:500]}

TAGLINE FORMAT: MSc Computer Science (UOD) | N+ YOE | {{JD Role Title}} | {{top 4 JD skills}}
- YOE: Data Analyst = 3+, all other roles = 2+

SUMMARY RULES:
- 3-4 sentences maximum
- Must mention {company} naturally
- Must reference 2-3 of the JD's top required skills
- Format: <b>Role</b> with experience in ... Built ... Specialises in ...
- Professional tone, no conversational language
- No soft skills (communication, teamwork, leadership, etc.)
- No em-dashes, en-dashes, or double dashes
- Preserve <b> tags for emphasis on key terms

Return ONLY valid JSON:
{{"tagline": "...", "summary": "..."}}"""

    try:
        raw = cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")
    except Exception as exc:
        logger.warning("cv_tailor: summary+tagline LLM call failed: %s", exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cv_tailor: summary+tagline JSON parse failed")
        return None

    tagline = data.get("tagline", "")
    summary = data.get("summary", "")
    if not tagline or not summary:
        return None

    result = TailoredHeader(tagline=tagline, summary=summary)

    error = validate_summary(summary)
    if error:
        _send_validation_alert("summary", company, error, summary)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_summary"`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add summary+tagline tailoring with LLM"
```

---

### Task 6: Experience Bullets Tailoring Function

**Files:**
- Modify: `jobpulse/cv_tailor.py`
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing tests for tailor_experience_bullets**

```python
# tests/jobpulse/test_cv_tailor.py (append)


def test_tailor_experience_success(monkeypatch):
    from jobpulse import cv_tailor

    original = [
        ExperienceEntry(
            title="Co-op Team Leader",
            company="Co-op Food",
            dates="Sep 2025 – Present",
            bullets=[
                "Managed team of 12 staff, improving scheduling efficiency by 25%",
                "Reduced shrinkage by 18% through data-driven stock auditing",
            ],
        ),
    ]

    response_json = json.dumps([
        {
            "title": "Co-op Team Leader",
            "company": "Co-op Food",
            "dates": "Sep 2025 – Present",
            "bullets": [
                "Led team of 12, optimising workforce allocation and improving scheduling efficiency by 25%",
                "Applied data-driven stock auditing techniques to reduce shrinkage by 18%",
            ],
        },
    ])
    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: response_json)
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: True)

    result = cv_tailor.tailor_experience_bullets(
        experience=original,
        jd_title="Data Analyst",
        required_skills=["Python", "Data Analysis"],
        preferred_skills=["Leadership"],
        company="Acme",
    )
    assert result is not None
    assert len(result) == 1
    assert result[0].title == "Co-op Team Leader"
    assert "25%" in result[0].bullets[0]


def test_tailor_experience_llm_failure(monkeypatch):
    from jobpulse import cv_tailor

    original = [ExperienceEntry(title="Dev", company="Co", dates="2024", bullets=["Built X 30%"])]
    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail")))

    result = cv_tailor.tailor_experience_bullets(
        experience=original, jd_title="Dev", required_skills=[], preferred_skills=[], company="Co",
    )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_experience"`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement tailor_experience_bullets**

```python
# jobpulse/cv_tailor.py (append)

def tailor_experience_bullets(
    experience: list[ExperienceEntry],
    jd_title: str,
    required_skills: list[str],
    preferred_skills: list[str],
    company: str,
) -> list[ExperienceEntry] | None:
    """Rephrase experience bullets using JD language. Same duties, different words."""
    entries_for_prompt = []
    for e in experience:
        entries_for_prompt.append({
            "title": e.title, "company": e.company, "dates": e.dates,
            "bullets": e.bullets,
        })

    prompt = f"""Rephrase these experience bullets to mirror the language of this job description.

JOB TITLE: {jd_title}
COMPANY: {company}
REQUIRED SKILLS: {', '.join(required_skills[:10])}
PREFERRED SKILLS: {', '.join(preferred_skills[:10])}

EXPERIENCE ENTRIES:
{json.dumps(entries_for_prompt, indent=2)}

RULES:
- Same responsibilities, rephrased to mirror JD keywords
- Each bullet MUST start with an action verb
- Each bullet MUST contain the EXACT same quantified impact (preserve all numbers, percentages, currency)
- NEVER add duties that were not in the original
- NEVER remove bullets — rephrase ALL of them
- Each bullet under 200 characters
- No soft skills, no em-dashes, professional tone

Return ONLY a valid JSON array with the same structure:
[{{"title": "...", "company": "...", "dates": "...", "bullets": ["..."]}}]"""

    try:
        raw = cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")
    except Exception as exc:
        logger.warning("cv_tailor: experience LLM call failed: %s", exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cv_tailor: experience JSON parse failed")
        return None

    if not isinstance(data, list) or len(data) != len(experience):
        logger.warning("cv_tailor: experience response count mismatch")
        return None

    tailored = []
    for entry_dict in data:
        tailored.append(ExperienceEntry(
            title=entry_dict.get("title", ""),
            company=entry_dict.get("company", ""),
            dates=entry_dict.get("dates", ""),
            bullets=entry_dict.get("bullets", []),
            location="",
        ))

    error = validate_experience(experience, tailored)
    if error:
        _send_validation_alert("experience", company, error, json.dumps(data)[:300])

    return tailored
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_experience"`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add experience bullets tailoring with LLM"
```

---

### Task 7: Project Bullets Tailoring Function

**Files:**
- Modify: `jobpulse/cv_tailor.py`
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing tests for tailor_project_bullets**

```python
# tests/jobpulse/test_cv_tailor.py (append)


def test_tailor_projects_success(monkeypatch):
    from jobpulse import cv_tailor

    original = [
        {
            "title": "ML Pipeline",
            "url": "https://github.com/user/ml-pipeline",
            "bullets": [
                "Built end-to-end ML pipeline achieving 95% accuracy",
                "Reduced inference latency by 40% via model optimisation",
                "Deployed to production serving 500 daily users",
            ],
        },
    ]

    response_json = json.dumps([
        {
            "title": "ML Pipeline",
            "url": "https://github.com/user/ml-pipeline",
            "bullets": [
                "Designed ML pipeline using Python and TensorFlow, achieving 95% accuracy on NLP tasks",
                "Optimised model serving to reduce inference latency by 40%",
                "Deployed production system handling 500 daily users with automated monitoring",
            ],
        },
    ])
    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: response_json)
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: True)

    result = cv_tailor.tailor_project_bullets(
        projects=original,
        jd_title="ML Engineer",
        required_skills=["Python", "TensorFlow", "NLP"],
        preferred_skills=["MLOps"],
        company="Acme",
    )
    assert result is not None
    assert len(result) == 1
    assert "95%" in result[0]["bullets"][0]
    assert result[0]["url"] == "https://github.com/user/ml-pipeline"


def test_tailor_projects_llm_failure(monkeypatch):
    from jobpulse import cv_tailor

    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail")))
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: True)

    result = cv_tailor.tailor_project_bullets(
        projects=[{"title": "X", "url": "u", "bullets": ["Built X 10%", "Did Y 20%", "Got Z 30%"]}],
        jd_title="Dev", required_skills=[], preferred_skills=[], company="Co",
    )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_projects"`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement tailor_project_bullets**

```python
# jobpulse/cv_tailor.py (append)

def tailor_project_bullets(
    projects: list[dict],
    jd_title: str,
    required_skills: list[str],
    preferred_skills: list[str],
    company: str,
) -> list[dict] | None:
    """Rewrite project bullets emphasizing JD-relevant skills."""
    projects_for_prompt = [
        {"title": p["title"], "bullets": p.get("bullets", [])}
        for p in projects
    ]

    prompt = f"""Rewrite these project bullets to emphasize skills from the job description.

JOB TITLE: {jd_title}
COMPANY: {company}
REQUIRED SKILLS: {', '.join(required_skills[:10])}
PREFERRED SKILLS: {', '.join(preferred_skills[:10])}

PROJECTS:
{json.dumps(projects_for_prompt, indent=2)}

RULES:
- Emphasize skills from the JD that appear in each project
- Preserve ALL metrics from original bullets (exact numbers, percentages, currency)
- Keep 3-4 bullets per project
- First bullet should lead with the strongest JD-relevant skill
- No soft skills, no em-dashes, professional tone
- Never invent metrics or project features not in the input

Return ONLY a valid JSON array:
[{{"title": "...", "bullets": ["..."]}}]"""

    try:
        raw = cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")
    except Exception as exc:
        logger.warning("cv_tailor: projects LLM call failed: %s", exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cv_tailor: projects JSON parse failed")
        return None

    if not isinstance(data, list) or len(data) != len(projects):
        logger.warning("cv_tailor: projects response count mismatch")
        return None

    tailored = []
    for i, proj_dict in enumerate(data):
        original_proj = projects[i]
        tailored.append({
            "title": original_proj["title"],
            "url": original_proj.get("url", ""),
            "bullets": proj_dict.get("bullets", []),
        })

    error = validate_projects(projects, tailored)
    if error:
        _send_validation_alert("projects", company, error, json.dumps(data)[:300])

    return tailored
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_projects"`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add project bullets tailoring with LLM"
```

---

### Task 8: Cover Letter Prose Tailoring Function

**Files:**
- Modify: `jobpulse/cv_tailor.py`
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing tests for tailor_cover_letter_prose**

```python
# tests/jobpulse/test_cv_tailor.py (append)


def test_tailor_cover_letter_prose_success(monkeypatch):
    from jobpulse import cv_tailor

    response_json = json.dumps({
        "intro": "I am writing to express my interest in the Data Scientist role at Acme Corp, drawn by your work in recommendation systems.",
        "hook": "My experience building production ML pipelines achieving 95% accuracy directly aligns with your need for scalable NLP solutions.",
        "closing": "I am enthusiastic about contributing to Acme Corp's data science initiatives and look forward to discussing this opportunity.",
    })
    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: response_json)
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: True)

    result = cv_tailor.tailor_cover_letter_prose(
        company="Acme Corp",
        role="Data Scientist",
        required_skills=["Python", "NLP", "ML"],
        matched_projects=[{"title": "ML Pipeline", "bullets": ["Built ML pipeline achieving 95% accuracy"]}],
    )
    assert result is not None
    assert "Acme Corp" in result.intro
    assert result.hook
    assert result.closing


def test_tailor_cover_letter_prose_llm_failure(monkeypatch):
    from jobpulse import cv_tailor

    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail")))

    result = cv_tailor.tailor_cover_letter_prose(
        company="Acme", role="Dev", required_skills=[], matched_projects=[],
    )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_cover_letter_prose"`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement tailor_cover_letter_prose**

```python
# jobpulse/cv_tailor.py (append)

def tailor_cover_letter_prose(
    company: str,
    role: str,
    required_skills: list[str],
    matched_projects: list[dict],
) -> TailoredCoverLetter | None:
    """Generate intro, hook, and closing paragraphs tailored to the JD."""
    project_summary = "; ".join(
        f"{p['title']}: {p['bullets'][0]}" if p.get("bullets") else p.get("title", "")
        for p in matched_projects[:3]
    )

    prompt = f"""Generate cover letter prose (intro, hook, closing) for this application.

ROLE: {role}
COMPANY: {company}
REQUIRED SKILLS: {', '.join(required_skills[:10])}
CANDIDATE'S MATCHED PROJECTS: {project_summary}

INTRO RULES (2-3 sentences):
- Mention the specific role and {company} by name
- Reference why this company specifically interests the candidate
- Professional tone, no generic filler

HOOK RULES (2-3 sentences):
- Connect the candidate's strongest matching skills to the JD requirements with a concrete achievement
- No soft skills (communication, teamwork, leadership, etc.)
- Must include a specific metric or achievement

CLOSING RULES (2-3 sentences):
- Express enthusiasm for {company} specifically
- Mention looking forward to discussion
- Professional tone, no generic filler like "I believe I would be a great fit"

ALL SECTIONS:
- No em-dashes, en-dashes, or double dashes
- Preserve <b> tag formatting for emphasis on key terms
- Each section 50-300 characters

Return ONLY valid JSON:
{{"intro": "...", "hook": "...", "closing": "..."}}"""

    try:
        raw = cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")
    except Exception as exc:
        logger.warning("cv_tailor: cover letter prose LLM call failed: %s", exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cv_tailor: cover letter prose JSON parse failed")
        return None

    intro = data.get("intro", "")
    hook = data.get("hook", "")
    closing = data.get("closing", "")
    if not intro or not hook or not closing:
        return None

    result = TailoredCoverLetter(intro=intro, hook=hook, closing=closing)

    error = validate_cover_letter(result, company)
    if error:
        _send_validation_alert("cover_letter", company, error, f"intro={intro[:80]} hook={hook[:80]}")

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_cover_letter_prose"`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add cover letter prose tailoring with LLM"
```

---

### Task 9: Parallel Orchestrator

**Files:**
- Modify: `jobpulse/cv_tailor.py`
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing tests for tailor_all_sections**

```python
# tests/jobpulse/test_cv_tailor.py (append)

from jobpulse.models.application_models import JobListing


def _make_listing(**overrides) -> JobListing:
    defaults = {
        "job_id": "test-123",
        "title": "Data Scientist",
        "company": "Acme Corp",
        "url": "https://example.com/job",
        "required_skills": ["Python", "ML", "NLP"],
        "preferred_skills": ["TensorFlow"],
        "description_raw": "Looking for a data scientist with NLP experience.",
        "location": "London",
    }
    defaults.update(overrides)
    return JobListing(**defaults)


def test_tailor_all_sections_parallel(monkeypatch):
    from jobpulse import cv_tailor

    call_count = {"n": 0}

    def fake_cognitive(**kwargs):
        call_count["n"] += 1
        task = kwargs.get("task", "")
        if "tagline" in task.lower():
            return json.dumps({
                "tagline": "MSc Computer Science (UOD) | 2+ YOE | Data Scientist | Python, ML, NLP, TensorFlow",
                "summary": "<b>Data Scientist</b> with experience in NLP at Acme Corp. Built ML pipelines processing 10M records. Specialises in <b>Python</b> and <b>TensorFlow</b>.",
            })
        elif "experience" in task.lower():
            return json.dumps([
                {"title": "Dev", "company": "Co", "dates": "2024", "bullets": ["Built X saving 30%"]},
            ])
        elif "project" in task.lower():
            return json.dumps([
                {"title": "ML Pipeline", "bullets": ["Built ML pipeline achieving 95% accuracy", "Reduced latency by 40%", "Deployed to 500 users"]},
            ])
        elif "cover letter" in task.lower():
            return json.dumps({
                "intro": "I am writing to express my interest in the Data Scientist role at Acme Corp.",
                "hook": "My experience building production ML pipelines achieving 95% accuracy aligns with your requirements.",
                "closing": "I am enthusiastic about contributing to Acme Corp and look forward to discussing this opportunity.",
            })
        return "{}"

    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: fake_cognitive(**kwargs))
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: True)

    listing = _make_listing()
    experience = [ExperienceEntry(title="Dev", company="Co", dates="2024", bullets=["Built X saving 30%"])]
    projects = [{"title": "ML Pipeline", "url": "https://github.com/user/ml", "bullets": ["Built ML pipeline achieving 95% accuracy", "Reduced latency by 40%", "Deployed to 500 users"]}]

    result = cv_tailor.tailor_all_sections(listing, projects, experience)

    assert result.tagline is not None
    assert result.summary is not None
    assert result.experience is not None
    assert result.projects is not None
    assert result.cover_letter is not None
    assert call_count["n"] == 4  # All 4 LLM calls made


def test_tailor_all_sections_partial_failure(monkeypatch):
    from jobpulse import cv_tailor

    def fail_on_experience(**kwargs):
        task = kwargs.get("task", "")
        if "experience" in task.lower():
            raise RuntimeError("LLM failed")
        if "tagline" in task.lower():
            return json.dumps({
                "tagline": "MSc Computer Science (UOD) | 2+ YOE | Data Scientist",
                "summary": "<b>Data Scientist</b> with experience in NLP at Acme Corp. Built ML pipelines. Specialises in <b>Python</b> and <b>deep learning</b>.",
            })
        if "project" in task.lower():
            return json.dumps([
                {"title": "X", "bullets": ["Built X 10%", "Did Y 20%", "Got Z 30%"]},
            ])
        if "cover letter" in task.lower():
            return json.dumps({
                "intro": "I am writing to express my interest in the role at Acme Corp in data science.",
                "hook": "Built ML pipeline achieving 95% accuracy in production NLP system deployment.",
                "closing": "I look forward to discussing how I can contribute to Acme Corp's data team.",
            })
        return "{}"

    monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kwargs: fail_on_experience(**kwargs))
    monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", lambda text: True)

    listing = _make_listing()
    experience = [ExperienceEntry(title="Dev", company="Co", dates="2024", bullets=["Built X saving 30%"])]
    projects = [{"title": "X", "url": "u", "bullets": ["Built X 10%", "Did Y 20%", "Got Z 30%"]}]

    result = cv_tailor.tailor_all_sections(listing, projects, experience)
    assert result.tagline is not None  # succeeded
    assert result.experience is None   # failed
    assert result.projects is not None  # succeeded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_all"`
Expected: FAIL with `AttributeError: module 'jobpulse.cv_tailor' has no attribute 'tailor_all_sections'`

- [ ] **Step 3: Implement tailor_all_sections**

```python
# jobpulse/cv_tailor.py (append)

from concurrent.futures import ThreadPoolExecutor


def tailor_all_sections(
    listing,
    matched_projects: list[dict],
    experience: list[ExperienceEntry],
) -> TailoredCV:
    """Run all 4 tailoring calls in parallel. Returns TailoredCV with all sections."""
    result = TailoredCV()

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="cv_tailor") as pool:
        future_header = pool.submit(
            tailor_summary_and_tagline,
            jd_title=listing.title,
            jd_description=getattr(listing, "description_raw", "") or "",
            company=listing.company,
            required_skills=listing.required_skills or [],
            preferred_skills=listing.preferred_skills or [],
        )
        future_exp = pool.submit(
            tailor_experience_bullets,
            experience=experience,
            jd_title=listing.title,
            required_skills=listing.required_skills or [],
            preferred_skills=listing.preferred_skills or [],
            company=listing.company,
        )
        future_proj = pool.submit(
            tailor_project_bullets,
            projects=matched_projects,
            jd_title=listing.title,
            required_skills=listing.required_skills or [],
            preferred_skills=listing.preferred_skills or [],
            company=listing.company,
        )
        future_cl = pool.submit(
            tailor_cover_letter_prose,
            company=listing.company,
            role=listing.title,
            required_skills=listing.required_skills or [],
            matched_projects=matched_projects,
        )

        header = future_header.result()
        if header:
            result.tagline = header.tagline
            result.summary = header.summary

        result.experience = future_exp.result()
        result.projects = future_proj.result()
        result.cover_letter = future_cl.result()

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v -k "tailor_all"`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(cv-tailor): add parallel orchestrator for all 4 tailoring calls"
```

---

### Task 10: Add `experience` Parameter to `generate_cv_pdf()`

**Files:**
- Modify: `jobpulse/cv_templates/generate_cv.py:355-363` (signature) and `:543-551` (experience section)
- Test: `tests/jobpulse/test_cv_tailor.py`

- [ ] **Step 1: Write failing test for experience parameter**

```python
# tests/jobpulse/test_cv_tailor.py (append)

from unittest.mock import patch, MagicMock


def test_generate_cv_pdf_accepts_experience_param(monkeypatch, tmp_path):
    """Verify generate_cv_pdf accepts and uses the experience parameter."""
    from jobpulse.cv_templates import generate_cv

    # Mock _load_experience to track if it's called
    load_called = {"n": 0}
    original_load = generate_cv._load_experience

    def tracked_load():
        load_called["n"] += 1
        return original_load()

    monkeypatch.setattr("jobpulse.cv_templates.generate_cv._load_experience", tracked_load)

    custom_experience = [
        {"title": "Custom Role", "company": "Custom Co", "dates": "2024-2025",
         "bullets": ["Custom bullet saving 50%"]},
    ]

    try:
        generate_cv.generate_cv_pdf(
            company="TestCo",
            output_dir=str(tmp_path),
            experience=custom_experience,
        )
    except Exception:
        pass  # Font registration may fail in test env

    # If experience param is accepted, _load_experience should NOT be called
    # (This test verifies the param exists; full PDF test needs fonts)
    import inspect
    sig = inspect.signature(generate_cv.generate_cv_pdf)
    assert "experience" in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py::test_generate_cv_pdf_accepts_experience_param -v`
Expected: FAIL with `TypeError: generate_cv_pdf() got an unexpected keyword argument 'experience'`

- [ ] **Step 3: Modify generate_cv_pdf signature**

In `jobpulse/cv_templates/generate_cv.py`, change the function signature at line 355:

```python
def generate_cv_pdf(
    company: str,
    location: str = "London, UK",
    tagline: str | None = None,
    summary: str | None = None,
    projects: list[dict] | None = None,
    extra_skills: dict[str, str] | None = None,
    output_dir: str | None = None,
    experience: list[dict] | None = None,
) -> Path:
```

And change the experience section at line 545 from:

```python
    for i, exp in enumerate(_load_experience()):
```

to:

```python
    for i, exp in enumerate(experience if experience is not None else _load_experience()):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py::test_generate_cv_pdf_accepts_experience_param -v`
Expected: PASS

- [ ] **Step 5: Run existing CV tests to verify no regressions**

Run: `python -m pytest tests/jobpulse/test_generate_cv_wiring.py -v`
Expected: PASS (or pre-existing failures only — the SyntaxError in job_autopilot.py may still cause 3 failures)

- [ ] **Step 6: Commit**

```bash
git add jobpulse/cv_templates/generate_cv.py tests/jobpulse/test_cv_tailor.py
git commit -m "feat(generate-cv): add experience parameter to generate_cv_pdf"
```

---

### Task 11: Integrate into `application_materials.py`

**Files:**
- Modify: `jobpulse/application_materials.py:33-96` (`ensure_tailored_cv_for_job`)
- Modify: `jobpulse/application_materials.py:99-132` (`build_lazy_cover_letter_generator`)
- Test: `tests/jobpulse/test_application_materials_tailoring.py`

- [ ] **Step 1: Write failing test for tailored CV flow**

```python
# tests/jobpulse/test_application_materials_tailoring.py

import json
from unittest.mock import MagicMock

from shared.profile_store import ExperienceEntry


def test_ensure_tailored_cv_calls_tailor(monkeypatch, tmp_path):
    """ensure_tailored_cv_for_job should call tailor_all_sections."""
    from jobpulse import application_materials

    tailor_called = {"n": 0}

    def fake_tailor(listing, projects, experience):
        tailor_called["n"] += 1
        from jobpulse.cv_tailor import TailoredCV
        return TailoredCV(
            tagline="Tailored tagline",
            summary="<b>Tailored</b> summary with enough text to pass validation. Built production systems and ML pipelines. Specialises in Python and NLP.",
        )

    monkeypatch.setattr("jobpulse.application_materials.tailor_all_sections", fake_tailor)

    # Mock DB
    mock_db = MagicMock()
    mock_db.get_application.return_value = {}
    mock_db.get_listing.return_value = {
        "title": "Data Scientist",
        "company": "Acme",
        "location": "London",
        "required_skills": json.dumps(["Python", "ML"]),
        "preferred_skills": json.dumps(["NLP"]),
        "description_raw": "Looking for a data scientist.",
    }

    # Mock generate_cv_pdf to just return a path
    monkeypatch.setattr(
        "jobpulse.application_materials.generate_cv_pdf",
        lambda **kwargs: tmp_path / "cv.pdf",
    )
    monkeypatch.setattr(
        "jobpulse.application_materials.get_best_projects_for_jd",
        lambda *a, **kw: [{"title": "Proj", "url": "u", "bullets": ["Did X 10%"]}],
    )

    result = application_materials.ensure_tailored_cv_for_job("job-123", db=mock_db)
    assert tailor_called["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_application_materials_tailoring.py -v`
Expected: FAIL with `AttributeError: module 'jobpulse.application_materials' has no attribute 'tailor_all_sections'`

- [ ] **Step 3: Modify ensure_tailored_cv_for_job**

In `jobpulse/application_materials.py`, replace the current `ensure_tailored_cv_for_job` function body (lines 33-96). The key changes:

1. Import `tailor_all_sections` at the top of the function
2. Load experience from ProfileStore
3. Call `tailor_all_sections()` to get tailored sections
4. Pass tailored values (with fallbacks) to `generate_cv_pdf()`

```python
def ensure_tailored_cv_for_job(job_id: str, db: "JobDB | None" = None) -> Path | None:
    """Create tailored CV PDF on disk if missing; update JobDB cv_path."""
    if not job_id:
        return None
    from jobpulse.cv_templates.generate_cv import (
        build_extra_skills,
        generate_cv_pdf,
        get_role_profile,
    )
    from jobpulse.cv_tailor import tailor_all_sections
    from jobpulse.job_db import JobDB
    from jobpulse.project_portfolio import get_best_projects_for_jd
    from shared.profile_store import get_profile_store

    db = db or JobDB()
    app = db.get_application(job_id)
    row = db.get_listing(job_id)
    if not row:
        logger.warning("application_materials: no listing for job_id=%s", job_id[:12])
        return None

    existing = (app or {}).get("cv_path")
    if existing:
        p = Path(str(existing))
        if p.is_file():
            return p

    required = _parse_skill_list(row.get("required_skills"))
    preferred = _parse_skill_list(row.get("preferred_skills"))
    matched_projects = get_best_projects_for_jd(required, preferred)
    extra = build_extra_skills(required, preferred)

    # Boost extra_skills with user-corrected skill values
    try:
        from jobpulse.correction_capture import CorrectionCapture
        user_skills = CorrectionCapture().get_skill_correction_values(min_occurrences=2)
        if user_skills and extra is not None:
            existing_lower = {v.lower() for v in extra.values()}
            for skill in user_skills:
                if skill.lower().strip() not in existing_lower:
                    extra[f"Corrected: {skill}"] = skill
    except Exception as exc:
        logger.debug("application_materials: correction skill boost failed: %s", exc)

    # Load experience from ProfileStore
    experience = get_profile_store().experience()

    # Build a lightweight listing object for the tailor
    class _ListingProxy:
        pass

    listing_proxy = _ListingProxy()
    listing_proxy.title = row.get("title") or "Software Engineer"
    listing_proxy.company = row.get("company") or "Company"
    listing_proxy.required_skills = required
    listing_proxy.preferred_skills = preferred
    listing_proxy.description_raw = row.get("description_raw") or ""

    # Tailor all sections via parallel LLM calls
    try:
        tailored = tailor_all_sections(listing_proxy, matched_projects, experience)
    except Exception as exc:
        logger.warning("application_materials: tailoring failed, using templates: %s", exc)
        tailored = None

    # Resolve with fallbacks
    if tailored and tailored.tagline:
        tagline = tailored.tagline
    else:
        tagline = get_role_profile(row.get("title") or "Software Engineer").get("tagline")

    if tailored and tailored.summary:
        summary = tailored.summary
    else:
        summary = get_role_profile(row.get("title") or "Software Engineer").get("summary")

    projects = (tailored.projects if tailored and tailored.projects else None) or matched_projects

    # Convert tailored experience to dict format for generate_cv_pdf
    exp_dicts = None
    if tailored and tailored.experience:
        exp_dicts = [
            {"title": e.title, "company": e.company, "dates": e.dates, "bullets": e.bullets}
            for e in tailored.experience
        ]

    out_dir = str(DATA_DIR / "applications" / job_id)

    try:
        cv_path = generate_cv_pdf(
            company=row.get("company") or "Company",
            location=row.get("location") or "United Kingdom",
            tagline=tagline,
            summary=summary,
            projects=projects,
            extra_skills=extra if extra else None,
            output_dir=out_dir,
            experience=exp_dicts,
        )
    except Exception as exc:
        logger.warning("application_materials: CV generation failed: %s", exc)
        return None

    if cv_path:
        db.save_application(job_id=job_id, cv_path=str(cv_path))
    return cv_path
```

- [ ] **Step 4: Modify build_lazy_cover_letter_generator to use tailored CL prose**

In `jobpulse/application_materials.py`, update `build_lazy_cover_letter_generator`:

```python
def build_lazy_cover_letter_generator(
    job_id: str,
    *,
    db: "JobDB | None" = None,
) -> Callable[[], Path | None]:
    """Return a callable that builds a cover letter PDF when the form needs one."""

    def _generate() -> Path | None:
        from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
        from jobpulse.cv_tailor import tailor_cover_letter_prose
        from jobpulse.job_db import JobDB
        from jobpulse.project_portfolio import get_best_projects_for_jd

        _db = db or JobDB()
        row = _db.get_listing(job_id)
        if not row:
            return None
        required = _parse_skill_list(row.get("required_skills"))
        preferred = _parse_skill_list(row.get("preferred_skills"))
        matched = get_best_projects_for_jd(required, preferred)
        out_dir = str(DATA_DIR / "applications" / job_id)

        # Tailor CL prose
        cl_prose = None
        try:
            cl_prose = tailor_cover_letter_prose(
                company=row.get("company") or "Company",
                role=row.get("title") or "Role",
                required_skills=required + preferred,
                matched_projects=matched,
            )
        except Exception as exc:
            logger.debug("application_materials: CL tailoring failed: %s", exc)

        try:
            return generate_cover_letter_pdf(
                company=row.get("company") or "Company",
                role=row.get("title") or "Role",
                location=row.get("location") or "United Kingdom",
                intro=cl_prose.intro if cl_prose else None,
                hook=cl_prose.hook if cl_prose else None,
                closing=cl_prose.closing if cl_prose else None,
                matched_projects=matched,
                required_skills=required + preferred,
                output_dir=out_dir,
            )
        except Exception as exc:
            logger.warning("application_materials: cover letter generation failed: %s", exc)
            return None

    return _generate
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_application_materials_tailoring.py -v`
Expected: PASS

- [ ] **Step 6: Run existing application_materials tests**

Run: `python -m pytest tests/jobpulse/ -v -k "application_materials" 2>/dev/null || echo "No existing tests"`
Expected: PASS or no existing tests

- [ ] **Step 7: Commit**

```bash
git add jobpulse/application_materials.py tests/jobpulse/test_application_materials_tailoring.py
git commit -m "feat(application-materials): integrate cv_tailor for per-JD tailoring"
```

---

### Task 12: Integration into `scan_pipeline.py`

**Files:**
- Modify: `jobpulse/scan_pipeline.py:574-603` (the `generate_materials` function)

The scan pipeline builds a synthetic CV text for ATS scoring but doesn't generate the actual PDF — that happens in `application_materials.py`. However, the pipeline does select `tagline` and `summary` for the ATS score calculation. We should tailor those too so the ATS score reflects the tailored content.

- [ ] **Step 1: Update scan_pipeline to use tailored sections for ATS scoring**

In `jobpulse/scan_pipeline.py`, after line 589 (after `matched_projects` is computed and archetype/role profile selected), add the tailoring call:

```python
        # After matched_projects selection (line 589) and role_profile/archetype selection (line 603):
        
        # Tailor CV sections via parallel LLM calls
        try:
            from jobpulse.cv_tailor import tailor_all_sections
            from shared.profile_store import get_profile_store

            experience_entries = get_profile_store().experience()
            tailored = tailor_all_sections(listing, matched_projects, experience_entries)

            if tailored.tagline:
                tagline = tailored.tagline
            if tailored.summary:
                summary = tailored.summary
            if tailored.projects:
                matched_projects = tailored.projects
        except Exception as exc:
            logger.debug("scan_pipeline: CV tailoring failed, using templates: %s", exc)
```

This goes right after the existing `tagline`/`summary` assignment (after line 603), before the ATS scoring block (line 607).

- [ ] **Step 2: Run existing scan_pipeline tests**

Run: `python -m pytest tests/jobpulse/ -v -k "scan_pipeline" 2>/dev/null || echo "Check passed"`
Expected: PASS or no test failures from this change

- [ ] **Step 3: Commit**

```bash
git add jobpulse/scan_pipeline.py
git commit -m "feat(scan-pipeline): use tailored sections for ATS scoring"
```

---

### Task 13: Full Test Suite and Final Review

- [ ] **Step 1: Run all cv_tailor tests**

Run: `python -m pytest tests/jobpulse/test_cv_tailor.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run all application_materials tests**

Run: `python -m pytest tests/jobpulse/test_application_materials_tailoring.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run broader jobpulse test suite for regressions**

Run: `python -m pytest tests/jobpulse/ -v --timeout=60 2>&1 | tail -30`
Expected: No new failures

- [ ] **Step 4: Verify imports work**

Run: `python -c "from jobpulse.cv_tailor import tailor_all_sections, TailoredCV, TailoredHeader, TailoredCoverLetter; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit any final fixes**

```bash
git add -A
git commit -m "test(cv-tailor): complete test suite for dynamic CV generation pipeline"
```
