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

### Cover Letter Tailoring

The cover letter's 4 numbered points are already LLM-tailored via `build_dynamic_points()` + `polish_points_llm()`. But the intro, hook, and closing paragraphs are still static templates. A 4th parallel tailoring function generates these:

```python
def tailor_cover_letter_prose(
    company: str,
    role: str,
    required_skills: list[str],
    matched_projects: list[dict],
) -> TailoredCoverLetter | None:
    """Generate intro, hook, and closing paragraphs tailored to the JD."""
```

```python
@dataclass
class TailoredCoverLetter:
    intro: str
    hook: str
    closing: str
```

**Prompt constraints:**
- Intro: 2-3 sentences, mention the specific role and company by name, reference why this company specifically interests the candidate
- Hook: 2-3 sentences, connect the candidate's strongest matching skills to the JD's requirements with a concrete achievement
- Closing: 2-3 sentences, express enthusiasm for the specific company, mention looking forward to discussion
- All three: professional tone, no em-dashes, no generic filler phrases ("I believe I would be a great fit"), must feel specific to this company
- Preserve `<b>` tag formatting for emphasis on key terms

**Validation:**
- No soft skill words in hook
- Intro mentions the company name
- Each section is 50-300 characters
- On failure: Telegram alert + continue with generated text

**Integration: `scan_pipeline.generate_materials()`**

The cover letter is generated lazily (only when ATS form has CL field), so tailoring runs at generation time inside the `cl_generator` callback:

```python
# In the cl_generator callback:
cl_prose = tailor_cover_letter_prose(company, role, required_skills, matched_projects)
generate_cover_letter_pdf(
    company=company, role=role,
    intro=cl_prose.intro if cl_prose else None,
    hook=cl_prose.hook if cl_prose else None,
    closing=cl_prose.closing if cl_prose else None,
    matched_projects=matched_projects,
    required_skills=required_skills,
)
```

The existing `generate_cover_letter_pdf()` signature already accepts `intro`, `hook`, `closing` as optional parameters, so no PDF generator changes needed.

### Orchestrator Update

`tailor_cv_sections()` becomes `tailor_all_sections()` and adds the CL prose call:

```python
@dataclass
class TailoredCV:
    tagline: str | None
    summary: str | None
    experience: list[ExperienceEntry] | None
    projects: list[dict] | None
    cover_letter: TailoredCoverLetter | None
```

All 4 calls run in parallel:
1. `tailor_summary_and_tagline()` — CV header
2. `tailor_experience_bullets()` — CV experience
3. `tailor_project_bullets()` — CV projects
4. `tailor_cover_letter_prose()` — CL intro/hook/closing

## Cost

- 4 parallel `cognitive_llm_call()` per application, GPT-5o-mini
- ~$0.010 per application (CV + CL)
- 5-15 applications/day after Gate filtering = $0.05-0.15/day
- Parallel execution: ~2-3 seconds total (all 4 concurrent)

## Files Changed

| File | Change |
|------|--------|
| `jobpulse/cv_tailor.py` | **NEW** — 4 tailoring functions + orchestrator + validation |
| `jobpulse/scan_pipeline.py` | Call `tailor_all_sections()`, pass CL prose to cl_generator callback |
| `jobpulse/cv_templates/generate_cv.py` | Add `experience` parameter to `generate_cv_pdf()` |

## Files NOT Changed

- `generate_cover_letter.py` — already accepts intro/hook/closing params, no changes needed
- `project_portfolio.py` — project selection stays the same (MindGraph matching)
- `archetype_engine.py` — archetypes still used as fallback if tailoring fails
- `gate4_quality.py` — existing validation reused, not modified
