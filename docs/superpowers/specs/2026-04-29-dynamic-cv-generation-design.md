# Dynamic CV Generation Pipeline

## Problem

CV sections (Professional Summary, Tagline, Experience bullets, Project bullets) use static templates selected by role type. Two "Data Scientist" applications at different companies produce identical resumes. The user wants every CV tailored to the specific JD and company.

## Approach

**Per-section LLM tailoring** — 3 independent `cognitive_llm_call()` invocations run in parallel, each generating a different CV section. Validation runs post-generation; failures send a Telegram alert with the reason (pipeline continues, human reviews before dry-run approval).

## What Changes Per JD

| Section | Before | After |
|---------|--------|-------|
| Tagline | 1 of 6 role templates | LLM-generated per JD |
| Professional Summary | 1 of 6 role templates | LLM-generated from scratch per JD |
| Experience bullets | Static from ProfileStore | LLM-rephrased to mirror JD language |
| Project bullets | Archetype variants (6 sets) | LLM-rewritten to emphasize JD-relevant skills |

## What Stays Static

- Education (facts)
- Base Technical Skills (5 categories from ProfileStore)
- "Also proficient in:" (already dynamic via `build_extra_skills()`)
- Certifications (facts)
- Community & Leadership
- References

## Architecture

### New Module: `jobpulse/cv_tailor.py`

Three public functions, each returning structured output:

```python
def tailor_summary_and_tagline(
    jd_title: str,
    jd_description: str,
    company: str,
    required_skills: list[str],
    preferred_skills: list[str],
) -> TailoredHeader | None:
    """Generate tagline + professional summary tailored to JD."""

def tailor_experience_bullets(
    experience: list[ExperienceEntry],
    jd_title: str,
    required_skills: list[str],
    preferred_skills: list[str],
    company: str,
) -> list[ExperienceEntry] | None:
    """Rephrase experience bullets using JD language. Same duties, different words."""

def tailor_project_bullets(
    projects: list[dict],
    jd_title: str,
    required_skills: list[str],
    preferred_skills: list[str],
    company: str,
) -> list[dict] | None:
    """Rewrite project bullets emphasizing JD-relevant skills."""
```

Orchestrator function runs all three in parallel:

```python
def tailor_cv_sections(
    listing: JobListing,
    matched_projects: list[dict],
    experience: list[ExperienceEntry],
) -> TailoredCV:
    """Run all 3 tailoring calls in parallel. Returns TailoredCV with all sections."""
```

### Typed Returns

```python
@dataclass
class TailoredHeader:
    tagline: str
    summary: str

@dataclass
class TailoredCV:
    tagline: str | None
    summary: str | None
    experience: list[ExperienceEntry] | None
    projects: list[dict] | None
```

### LLM Integration

- All calls use `cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")`
- Each prompt returns JSON, parsed with `json.loads()` + `try/except` fallback
- On LLM failure or JSON parse failure, function returns `None` (caller uses existing template)
- Parallel execution via `concurrent.futures.ThreadPoolExecutor`

### Prompt Constraints (embedded in each prompt)

**All sections:**
- Never invent metrics, responsibilities, or project names not in the input
- Preserve ALL quantified metrics exactly (numbers, percentages, currency)
- No soft skills (communication, teamwork, leadership, etc.)
- No em-dashes, en-dashes, or double dashes — use commas or periods
- Professional tone, no conversational language
- Output must be valid JSON

**Summary-specific:**
- 3-4 sentences maximum
- Must mention the company name naturally
- Must reference 2-3 of the JD's top required skills
- Must reference the user's strongest matching project/achievement
- Format: `<b>Role</b> with experience in ... Built ... Specialises in ...`

**Tagline-specific:**
- Format: `MSc Computer Science (UOD) | N+ YOE | {JD Role Title} | {top 4 JD skills}`
- YOE: Data Analyst = 3+, all others = 2+

**Experience-specific:**
- Same responsibilities, rephrased to mirror JD keywords
- Each bullet must start with an action verb
- Each bullet must contain a quantified impact
- Never add duties that weren't in the original
- Never remove bullets — rephrase all of them

**Project-specific:**
- Emphasize skills from the JD that appear in each project
- Preserve all metrics from original bullets
- Keep 3-4 bullets per project
- First bullet should lead with the strongest JD-relevant skill

### Validation Layer

After generation, each section is validated:

1. **Summary validation:**
   - No soft skill words (checked against `_SOFT_SKILL_WORDS` set)
   - No informal words (checked against existing Gate 4B1 list)
   - Length: 100-500 characters
   - Contains at least one `<b>` tag

2. **Experience validation:**
   - Same number of entries as input
   - Each bullet has a metric (regex: `\d+[%$£]|\d{2,}`)
   - No bullet exceeds 200 characters

3. **Project validation:**
   - Same number of projects as input
   - All original metrics preserved (extract numbers from original, verify they exist in output)
   - Each project has 3-4 bullets

**On validation failure:**
- Send Telegram alert to Jobs bot: `"CV Tailoring: {section} failed validation for {company} — {reason}. Generated text: {text}"`
- Continue pipeline with the generated (but failed-validation) text — human reviews during dry-run step

### Integration Point: `scan_pipeline.generate_materials()`

Current flow (lines 584-603):
```python
matched_projects = get_best_projects_for_jd(...)
role_profile = get_role_profile(listing.title)
tagline = role_profile.get("tagline")
summary = role_profile.get("summary")
```

New flow:
```python
matched_projects = get_best_projects_for_jd(...)
experience = _load_experience()  # from ProfileStore

tailored = tailor_cv_sections(listing, matched_projects, experience)

tagline = tailored.tagline or get_role_profile(listing.title).get("tagline")
summary = tailored.summary or get_role_profile(listing.title).get("summary")
projects = tailored.projects or matched_projects
# experience passed as new param to generate_cv_pdf
```

### Change to `generate_cv_pdf()`

Add optional `experience` parameter:
```python
def generate_cv_pdf(
    company: str,
    location: str = "London, UK",
    tagline: str | None = None,
    summary: str | None = None,
    projects: list[dict] | None = None,
    extra_skills: dict[str, str] | None = None,
    output_dir: str | None = None,
    experience: list[dict] | None = None,  # NEW
) -> Path:
```

When `experience` is provided, use it instead of `_load_experience()`.

## Cost

- 3 parallel `cognitive_llm_call()` per CV, GPT-5o-mini
- ~$0.008 per CV generation
- 5-15 CVs/day after Gate filtering = $0.04-0.12/day
- Parallel execution: ~2-3 seconds total

## Files Changed

| File | Change |
|------|--------|
| `jobpulse/cv_tailor.py` | **NEW** — 3 tailoring functions + orchestrator + validation |
| `jobpulse/scan_pipeline.py` | Call `tailor_cv_sections()` before PDF generation |
| `jobpulse/cv_templates/generate_cv.py` | Add `experience` parameter to `generate_cv_pdf()` |

## Files NOT Changed

- `generate_cover_letter.py` — already has dynamic point generation
- `project_portfolio.py` — project selection stays the same (MindGraph matching)
- `archetype_engine.py` — archetypes still used as fallback if tailoring fails
- `gate4_quality.py` — existing validation reused, not modified
