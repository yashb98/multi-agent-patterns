"""Cover Letter Generator — generates tailored cover letters using the user's template.

Pipeline:
    build_cover_letter_prompt → LLM generates text → save to data/applications/{job_id}/cover_letter.txt
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from shared.pii import assert_prompt_has_wrapped_pii, wrap_pii_value

if TYPE_CHECKING:
    from jobpulse.models.application_models import JobListing

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "Cover letter template.md"

def _build_profile() -> dict:
    """Build profile dict from ProfileStore with applicator fallback."""
    try:
        from shared.profile_store import get_profile_store
        ps = get_profile_store()
        ident = ps.identity()
        name = ident.full_name or "Yash B"
        visa = ps.sensitive("visa_status") or ""
        edu_entries = ps.education()
        exp_entries = ps.experience()
        education = [f"{e.degree}, {e.institution} ({e.dates})" for e in edu_entries] or [ident.education]
        experience = [f"{e.title}, {e.company} ({e.dates})" for e in exp_entries]
    except Exception as _exc:
        from shared.logging_config import get_logger as _gl
        _gl(__name__).debug("ProfileStore unavailable, falling back to applicator: %s", _exc)
        from jobpulse.applicator import PROFILE as _AP
        name = f"{_AP.get('first_name', '')} {_AP.get('last_name', '')}".strip() or "Yash B"
        visa = ""
        education = []
        experience = []

    return {"name": name, "education": education, "experience": experience, "visa": visa}


_PROFILE: dict | None = None


def _get_profile() -> dict:
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = _build_profile()
    return _PROFILE


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_template() -> str:
    """Load cover letter template."""
    try:
        return _TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        from shared.logging_config import get_logger
        get_logger(__name__).error(
            "cover_letter_agent: template not found at %s. "
            "Ensure jobpulse/templates/ directory contains the template.",
            _TEMPLATE_PATH,
        )
        return ""


def _cover_letter_prompt_profile() -> dict:
    p = _get_profile()
    return {
        "name": p["name"],
        "education": p["education"],
        "experience": p["experience"],
        "visa": p["visa"],
    }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def build_cover_letter_prompt(
    company: str,
    role: str,
    jd_text: str,
    matched_skills: list[str],
    matched_projects: list[str],
) -> str:
    """Build LLM prompt for cover letter generation.

    Includes:
    - The template format loaded from disk
    - Company, role, JD text (truncated to 2000 chars)
    - Matched skills and projects
    - Profile data (name, education, experience, visa)
    - Instructions: follow template exactly, 4 numbered points, 250-350 words, professional tone
    """
    template = _load_template()
    prompt_profile = _cover_letter_prompt_profile()

    if len(jd_text) <= 2000:
        jd_snippet = jd_text
    else:
        cut = jd_text[:2000]
        last_period = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind(".\t"))
        jd_snippet = cut[:last_period + 1] if last_period > 1500 else cut
    skills_str = ", ".join(matched_skills)
    projects_str = ", ".join(matched_projects)
    education_str = "\n".join(
        f"  - {wrap_pii_value(f'cover_letter.education[{index}]', value)}"
        for index, value in enumerate(prompt_profile["education"])
    )
    experience_str = "\n".join(
        f"  - {wrap_pii_value(f'cover_letter.experience[{index}]', value)}"
        for index, value in enumerate(prompt_profile["experience"])
    )

    prompt = f"""You are writing a professional cover letter for a job application.

## COVER LETTER TEMPLATE FORMAT
Use the following template structure exactly:
{template}

## JOB DETAILS
- Company: {company}
- Role: {role}
- Job Description (truncated to 2000 chars):
{jd_snippet}

## APPLICANT PROFILE
- Name: {wrap_pii_value("cover_letter.name", prompt_profile["name"])}
- Education:
{education_str}
- Work Experience:
{experience_str}
- Visa Status: {wrap_pii_value("cover_letter.visa", prompt_profile["visa"])}

## MATCHED SKILLS (use these to write the 4 numbered points)
{skills_str}

## RELEVANT PROJECTS (reference where appropriate)
{projects_str}

## INSTRUCTIONS
1. Follow the template format exactly.
2. Write exactly 4 numbered points — each maps a key skill or duty from the JD to a specific
   experience or project with quantified metrics where possible.
3. Keep the total body (greeting through sign-off) between 250 and 350 words.
4. Use a professional tone throughout.
5. Tailor the hook (first 2 lines) specifically to {company} and the {role} role.
6. Output plain text only — no markdown fences, no LaTeX, no HTML.
"""
    assert_prompt_has_wrapped_pii(prompt, prompt_profile, "cover_letter")
    return prompt



# generate_cover_letter() removed — superseded by generate_cover_letter_pdf()
# in jobpulse/cv_templates/generate_cover_letter.py (ReportLab PDF output).
