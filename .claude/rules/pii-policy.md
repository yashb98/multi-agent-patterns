---
paths: ["**/*.py", "**/*.md"]
description: "Personal information must NEVER be hardcoded — always retrieved from databases at runtime"
---

# PII Policy: No Personal Data in Source Code (MANDATORY)

## Rule

Personal information MUST NEVER appear as literal values in source code, config templates, documentation, or test fixtures. All PII must be stored in the appropriate database and retrieved dynamically at runtime.

This satisfies both **Security & Safety** (no PII in source) and **Dynamic Over Hardcoded** (all values resolved at runtime).

---

## What Counts as PII

- Name, email, phone number, address
- Date of birth, age, nationality
- Visa/immigration status, work authorization details
- Salary (current/desired), notice period
- Diversity/DEI answers (gender, ethnicity, orientation, religion, disability)
- LinkedIn URL, personal website, GitHub profile
- Employment history (company names, roles, dates)
- Education details (university, degree, grades)
- Skills, certifications, projects
- Screening question answers (relocation, travel, sponsorship)
- References, emergency contacts
- Any value that identifies or describes the user personally

---

## Where PII Lives (Approved Sources)

| Data Type | Storage | Access Method |
|-----------|---------|---------------|
| Identity (name, email, phone) | `data/profile.db` or env vars via `config.py` | `get_profile()` / `config.USER_*` |
| Screening answers | `data/screening_cache.db` | `ScreeningPipeline` → LLM generation from JD+CV |
| Skills & projects | `data/skill_graph.db` + Notion Skill Tracker | `sync_verified_to_profile()` |
| Work history | `data/profile.db` | `get_experience()` |
| DEI/diversity answers | `data/screening_cache.db` | LLM-generated per form context |
| CV/CL content | Generated at runtime from profile DB | `generate_cv()` / `generate_cover_letter()` |
| Links (LinkedIn, GitHub, website) | `data/profile.db` or env vars | `get_profile_links()` |
| Address | `data/profile.db` | `get_address()` |

---

## Enforcement Rules

1. **No literal PII in Python files** — not in strings, dicts, lists, comments, or docstrings
2. **No PII in test files** — use `tmp_path` fixtures with synthetic/anonymized data, or pull from profile DB in `@pytest.mark.live` tests
3. **No PII in .md files checked into git** — CLAUDE.md, AGENTS.md, rules files must reference retrieval methods, not actual values
4. **No PII in .env.example** — use placeholder format: `USER_EMAIL=your-email@example.com`
5. **Memory files (`.claude/projects/*/memory/`) are exempt** — they exist outside the codebase and are user-private

---

## How to Add New Personal Data

1. Add a column/table to the appropriate `data/*.db` database
2. Add a retrieval function in the relevant module (profile, screening, etc.)
3. Call that function at runtime where the data is needed
4. NEVER create a "defaults" dict with personal values as fallback

---

## Common Violations to Avoid

```python
# BAD — hardcoded PII
IDENTITY = {"name": "Yash Bishnoi", "email": "user@example.com"}
DEFAULT_ANSWERS = {"salary": "35000", "visa": "Graduate Visa"}
LINKS = {"linkedin": "https://linkedin.com/in/username"}

# GOOD — retrieved from database
identity = get_profile()
answers = screening_pipeline.resolve(field_label, jd=jd, cv=cv)
links = get_profile_links()
```

---

## Rationale

- **Security**: PII in source code leaks via git history, forks, CI logs, error messages
- **Dynamic**: Profile data changes (new job, new address, new skills) — hardcoded values rot silently
- **Testability**: Tests with real PII break on different machines and violate data isolation
- **Compliance**: Source code is not an appropriate data controller for personal information
