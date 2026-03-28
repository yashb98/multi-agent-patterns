"""Cover Letter Generator — generates tailored cover letters using the user's template.

Pipeline:
    build_cover_letter_prompt → LLM generates text → save to data/applications/{job_id}/cover_letter.txt
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jobpulse.models.application_models import JobListing

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "Cover letter template.md"

_PROFILE = {
    "name": "Yash B",
    "education": [
        "MSc Computer Science, University of Dundee (Jan 2025 - Jan 2026)",
        "MBA Finance, JECRC University (2019-2021)",
    ],
    "experience": [
        "Team Leader, Co-op (Apr 2025 - Present)",
        "Market Research Analyst, Nidhi Herbal (Jul 2021 - Sep 2024)",
    ],
    "visa": "Student Visa; converting to Graduate Visa from 9 May 2026",
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_template() -> str:
    """Load cover letter template from jobpulse/templates/Cover letter template.md."""
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


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

    jd_snippet = jd_text[:2000]
    skills_str = ", ".join(matched_skills)
    projects_str = ", ".join(matched_projects)
    education_str = "\n".join(f"  - {e}" for e in _PROFILE["education"])
    experience_str = "\n".join(f"  - {x}" for x in _PROFILE["experience"])

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
- Name: {_PROFILE["name"]}
- Education:
{education_str}
- Work Experience:
{experience_str}
- Visa Status: {_PROFILE["visa"]}

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
    return prompt


def generate_cover_letter(
    job: JobListing,
    matched_skills: list[str],
    matched_projects: list[str],
) -> Path | None:
    """Generate a cover letter via gpt-4o-mini and save as a text file.

    Saves to data/applications/{job.job_id}/cover_letter.txt
    Returns the file path on success, None on failure.
    """
    # Lazy imports to avoid hard dependency when not calling LLM
    import openai  # type: ignore[import-untyped]
    from shared.logging_config import get_logger

    from jobpulse.config import DATA_DIR, OPENAI_API_KEY

    logger = get_logger(__name__)

    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    app_dir = DATA_DIR / "applications" / job.job_id
    app_dir.mkdir(parents=True, exist_ok=True)
    output_path = app_dir / "cover_letter.txt"

    prompt = build_cover_letter_prompt(
        company=job.company,
        role=job.title,
        jd_text=job.description_raw,
        matched_skills=matched_skills,
        matched_projects=matched_projects,
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.5,
            messages=[{"role": "user", "content": prompt}],
        )
        cover_letter_text: str = response.choices[0].message.content or ""

        if not cover_letter_text.strip():
            logger.warning("cover_letter_agent: LLM returned empty content for job %s", job.job_id)
            return None

        output_path.write_text(cover_letter_text, encoding="utf-8")
        logger.info(
            "cover_letter_agent: saved cover letter for %s @ %s → %s",
            job.title,
            job.company,
            output_path,
        )
        return output_path

    except Exception as exc:
        logger.error(
            "cover_letter_agent: failed for job %s — %s: %s",
            job.job_id,
            type(exc).__name__,
            exc,
        )
        return None
