"""CV Tailor — generates tailored LaTeX CVs per job using the Resume Prompt template.

Pipeline:
    build_cv_prompt → LLM generates .tex → compile to PDF → ATS score → refine if < 95
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from jobpulse.models.application_models import ATSScore, JobListing

# ---------------------------------------------------------------------------
# Template path
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "Resume Prompt.md"


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def build_cv_prompt(jd_data: dict, matched_projects: list[str]) -> str:
    """Build full LLM prompt by loading Resume Prompt template and injecting JD data.

    jd_data keys: location, role_title, years_exp, industry, sub_context,
                  skills_list, soft_skills, extended_skills
    matched_projects: list of repo names ordered by relevance

    Loads template from jobpulse/templates/Resume Prompt.md
    Appends an EXTRACTED block (Layer 3) and project priority instruction.
    """
    try:
        template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("cv_tailor: Resume Prompt template not found at %s", _TEMPLATE_PATH)
        return ""

    skills_str = ", ".join(jd_data.get("skills_list", []))
    soft_skills_str = ", ".join(jd_data.get("soft_skills", []))
    extended_skills_str = ", ".join(jd_data.get("extended_skills", []))
    projects_str = "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(matched_projects))

    extracted_block = f"""

% =====================================================
% LAYER 3 — EXTRACTED JD DATA (Auto-injected by CV Tailor)
% =====================================================

EXTRACTED_JD:
  ROLE_TITLE        : {jd_data.get("role_title", "")}
  LOCATION          : {jd_data.get("location", "")}
  YEARS_EXP         : {jd_data.get("years_exp", "")}
  INDUSTRY          : {jd_data.get("industry", "")}
  SUB_CONTEXT       : {jd_data.get("sub_context", "")}
  SKILLS_LIST       : {skills_str}
  SOFT_SKILLS       : {soft_skills_str}
  EXTENDED_SKILLS   : {extended_skills_str}

PROJECT_PRIORITY (ordered by relevance to this JD):
{projects_str}

INSTRUCTION: Use the EXTRACTED_JD data above to tailor the CV keywords, summary,
and project descriptions. Prioritise the projects in the order listed above.
Output a complete, compilable XeLaTeX .tex file only — no prose, no markdown fences.
"""

    return template + extracted_block


def extract_text_from_tex(tex_content: str) -> str:
    r"""Strip LaTeX commands to get plain text for ATS scoring.

    Removes: \textbf{}, \textit{}, \href{}{}, \section*{}, \item, \noindent,
             \vspace{}, and other common LaTeX macros.
    Keeps the text content inside the commands.
    """
    text = tex_content

    # \href{url}{text} → text
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)

    # \textbf{text}, \textit{text}, \emph{text}, \text{text} → text
    text = re.sub(r"\\text(?:bf|it|rm|sf|tt|sc|up|normal)?\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\emph\{([^}]*)\}", r"\1", text)

    # \section*{heading}, \section{heading}, \subsection*{...}, etc. → heading
    text = re.sub(r"\\(?:sub)*section\*?\{([^}]*)\}", r"\1", text)

    # \item → nothing (keep the following text)
    text = re.sub(r"\\item\b", "", text)

    # \noindent, \vspace{...}, \hspace{...}, \medskip, \smallskip, \bigskip
    text = re.sub(r"\\(?:no)?indent\b", "", text)
    text = re.sub(r"\\[vh]space\*?\{[^}]*\}", "", text)
    text = re.sub(r"\\(?:med|small|big)skip\b", "", text)

    # \newline, \\, \linebreak
    text = re.sub(r"\\(?:newline|linebreak)\b", "\n", text)
    text = re.sub(r"\\\\", "\n", text)

    # Environments: \begin{...} \end{...}
    text = re.sub(r"\\(?:begin|end)\{[^}]*\}", "", text)

    # \documentclass, \usepackage, \geometry, \definecolor, etc. (preamble commands)
    text = re.sub(r"\\[a-zA-Z]+(?:\[[^\]]*\])?\{[^}]*\}", "", text)

    # Remaining backslash commands without braces (e.g. \centering, \raggedright)
    text = re.sub(r"\\[a-zA-Z]+\b\*?", "", text)

    # LaTeX comments
    text = re.sub(r"%[^\n]*", "", text)

    # Curly braces leftovers
    text = re.sub(r"[{}]", "", text)

    # Collapse whitespace but preserve newlines
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)

    return text


def determine_match_tier(ats_score: float) -> str:
    """Return 'auto' if >= 90, 'review' if >= 82, 'skip' otherwise."""
    if ats_score >= 90:
        return "auto"
    if ats_score >= 82:
        return "review"
    return "skip"


def compile_tex(tex_path: Path, output_dir: Path) -> Path | None:
    """Compile .tex to PDF using xelatex. Run twice for correct layout.

    Returns PDF path on success, None on failure.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for _ in range(2):
            result = subprocess.run(
                [
                    "xelatex",
                    "-interaction=nonstopmode",
                    f"-output-directory={output_dir}",
                    str(tex_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                return None

        pdf_path = output_dir / (tex_path.stem + ".pdf")
        return pdf_path if pdf_path.exists() else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def generate_tailored_cv(
    job: JobListing, matched_projects: list[str]
) -> tuple[Path | None, ATSScore]:
    """Full pipeline: build prompt → LLM generates .tex → compile → score → refine if < 95.

    Saves files to data/applications/{job.job_id}/cv.tex and cv.pdf
    Uses gpt-4.1-mini, temperature 0.3
    Max 3 refinement attempts.
    """
    # Lazy imports to avoid hard dependency when not calling LLM
    import openai  # type: ignore[import-untyped]

    from jobpulse.ats_scorer import score_ats
    from jobpulse.config import DATA_DIR, OPENAI_API_KEY
    from jobpulse.models.application_models import ATSScore
    from jobpulse.utils.safe_io import safe_openai_call

    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    # Build output directory
    app_dir = DATA_DIR / "applications" / job.job_id
    app_dir.mkdir(parents=True, exist_ok=True)
    tex_path = app_dir / "cv.tex"

    # Extract JD data from the job listing
    jd_data = {
        "location": job.location,
        "role_title": job.title,
        "years_exp": "",
        "industry": "",
        "sub_context": "",
        "skills_list": job.required_skills,
        "soft_skills": job.preferred_skills,
        "extended_skills": [],
    }

    all_jd_skills = job.required_skills + job.preferred_skills
    best_score: ATSScore | None = None
    best_tex: str = ""
    messages: list[dict] = []

    for attempt in range(3):
        prompt = build_cv_prompt(jd_data, matched_projects)

        if attempt == 0:
            messages = [{"role": "user", "content": prompt}]
        else:
            # Refinement: append feedback about missing keywords
            if best_score is None:
                # All prior attempts returned None — nothing to refine against, restart fresh
                messages = [{"role": "user", "content": prompt}]
            else:
                missing = ", ".join(best_score.missing_keywords[:10])
                feedback = (
                    f"The previous CV scored {best_score.total:.1f}% ATS (target 95%+). "
                    f"Missing keywords: {missing}. "
                    "Please revise the CV to naturally incorporate these keywords. "
                    "Output the complete updated .tex file only."
                )
                messages.append({"role": "assistant", "content": best_tex})
                messages.append({"role": "user", "content": feedback})

        tex_content = safe_openai_call(
            client,
            model="gpt-4.1-mini",
            temperature=0.3,
            messages=messages,
            caller=f"cv_tailor:attempt_{attempt}",
        )
        if tex_content is None:
            logger.warning("cv_tailor: LLM returned None for job %s attempt %d", job.job_id, attempt)
            continue

        # Strip markdown fences if present
        tex_content = re.sub(r"^```(?:latex|tex)?\s*\n?", "", tex_content, flags=re.IGNORECASE)
        tex_content = re.sub(r"\n?```\s*$", "", tex_content)

        # Score the generated CV
        plain_text = extract_text_from_tex(tex_content)
        score = score_ats(all_jd_skills, plain_text)

        if best_score is None or score.total > best_score.total:
            best_score = score
            best_tex = tex_content

        if best_score.passed:
            break

    # Write the best .tex
    tex_path.write_text(best_tex, encoding="utf-8")

    # Compile to PDF
    pdf_path = compile_tex(tex_path, app_dir)

    if best_score is None:
        logger.error("cv_tailor: all refinement attempts returned None for job %s", job.job_id)
        return None, ATSScore(total=0, keyword_score=0, section_score=0, format_score=0,
                              missing_keywords=[], matched_keywords=[], passed=False)
    return pdf_path, best_score
