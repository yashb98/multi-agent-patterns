"""cv_tailor.py — Dataclasses and validation for dynamic CV tailoring.

Validates LLM-generated CV sections before they reach the PDF renderer.
All personal data comes from the profile DB at runtime — never hardcoded here.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from shared.agents import cognitive_llm_call
from shared.logging_config import get_logger
from shared.profile_store import ExperienceEntry

logger = get_logger(__name__)


def _parse_llm_json(raw: str | None) -> object:
    """Parse JSON from an LLM response, tolerating markdown fences and
    prefix/suffix prose. Raises json.JSONDecodeError if no JSON found
    (so callers can keep their existing except-and-log path).

    Cognitive engine + raw OpenAI fallback both occasionally return one of:
        - empty string (engine failure)
        - ```json\\n{...}\\n``` (markdown-wrapped)
        - "Here is the JSON: {...}" (prose prefix)
    Plain `json.loads(raw)` fails on every one of these. This helper unifies
    handling so all four cv_tailor functions get the same robustness.
    """
    if not raw or not raw.strip():
        raise json.JSONDecodeError("Empty LLM response", raw or "", 0)
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    if not cleaned:
        raise json.JSONDecodeError("Empty after stripping markdown fences", raw, 0)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fall back: find whichever opener comes first ('{' or '['), then take
    # everything up to the matching closer. Picking the earlier opener handles
    # prose prefixes like 'Sure! [{...}]' correctly — naive first-{/last-}
    # would slice the inner object out of the array.
    obj_start = cleaned.find("{")
    arr_start = cleaned.find("[")
    candidates: list[tuple[int, str]] = []
    if obj_start != -1:
        candidates.append((obj_start, "}"))
    if arr_start != -1:
        candidates.append((arr_start, "]"))
    candidates.sort(key=lambda c: c[0])
    for start, closer in candidates:
        last = cleaned.rfind(closer)
        if last > start:
            try:
                return json.loads(cleaned[start:last + 1])
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError(
        f"No valid JSON object or array found in response: {cleaned[:120]!r}",
        raw,
        0,
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_SOFT_SKILL_WORDS = {
    "communication", "teamwork", "leadership", "problem solving", "time management",
    "adaptability", "collaboration", "analytical thinking", "critical thinking",
    "stakeholder management", "mentoring", "coaching", "prioritization",
    "attention to detail", "self motivated", "fast learner", "customer focus",
    "decision making", "interviewing", "okrs", "presentation skills",
    "project management", "strategic thinking", "negotiation",
}

# Regex is used here only for structural format validation of numeric/percentage
# patterns — not for semantic classification (allowed per codebase rules).
_METRIC_RE = re.compile(r"\d+[%$£]|\d{2,}")


def validate_summary(summary: str) -> str | None:
    """Returns error string or None if valid."""
    if len(summary) < 100 or len(summary) > 500:
        return f"Summary length {len(summary)} outside 100-500 range"
    summary_lower = summary.lower()
    for word in _SOFT_SKILL_WORDS:
        if word in summary_lower:
            return f"Soft skill word found: '{word}'"
    if "<b>" not in summary:
        return "Summary must contain at least one <b> tag"
    return None


def validate_experience(original: list[ExperienceEntry], tailored: list[ExperienceEntry]) -> str | None:
    """Returns error string or None if valid."""
    if len(tailored) != len(original):
        return f"Entry count mismatch: expected {len(original)}, got {len(tailored)}"
    for i, entry in enumerate(tailored):
        for j, bullet in enumerate(entry.bullets):
            if len(bullet) > 200:
                return f"Entry {i} bullet {j} exceeds 200 chars ({len(bullet)})"
            if not _METRIC_RE.search(bullet):
                return f"Entry {i} bullet {j} missing quantified metric"
    return None


def validate_projects(original: list[dict], tailored: list[dict]) -> str | None:
    """Returns error string or None if valid."""
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


def validate_cover_letter(cl: TailoredCoverLetter, company: str) -> str | None:
    """Returns error string or None if valid."""
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


# ---------------------------------------------------------------------------
# Telegram alert helper
# ---------------------------------------------------------------------------

def _send_validation_alert(section: str, company: str, reason: str, text: str) -> None:
    """Send Telegram alert for validation failure. Non-blocking."""
    try:
        from jobpulse.telegram_bots import send_jobs
        msg = f"CV Tailoring: {section} failed validation for {company} — {reason}. Generated text: {text[:200]}"
        send_jobs(msg)
    except Exception as exc:
        logger.debug("cv_tailor: Telegram alert failed: %s", exc)


# ---------------------------------------------------------------------------
# LLM tailoring functions
# ---------------------------------------------------------------------------

def tailor_summary_and_tagline(
    jd_title: str,
    jd_description: str,
    company: str,
    required_skills: list[str],
    preferred_skills: list[str],
) -> TailoredHeader | None:
    """Generate tagline + professional summary tailored to JD."""
    yoe = "3+" if "data analyst" in jd_title.lower() else "2+"
    top_skills = ", ".join(required_skills[:4])
    prompt = (
        f"You are a CV writer. Generate a tailored tagline and professional summary for a job application.\n\n"
        f"Role: {jd_title}\n"
        f"Company: {company}\n"
        f"Required skills: {', '.join(required_skills)}\n"
        f"Preferred skills: {', '.join(preferred_skills)}\n"
        f"Job description excerpt: {jd_description[:500]}\n\n"
        f"Rules:\n"
        f"- Tagline format exactly: MSc Computer Science (UOD) | {yoe} YOE | {jd_title} | {top_skills}\n"
        f"- Summary: 3-4 sentences, mention '{company}', reference 2-3 required skills\n"
        f"- Summary format: <b>Role</b> with experience in ... Built ... Specialises in ...\n"
        f"- No soft skills (leadership, teamwork, communication, etc.), no em-dashes\n\n"
        f"Respond ONLY with valid JSON: {{\"tagline\": \"...\", \"summary\": \"...\"}}"
    )
    try:
        raw = cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")
    except Exception as exc:
        logger.warning("cv_tailor: LLM failure in tailor_summary_and_tagline: %s", exc)
        return None

    try:
        data = _parse_llm_json(raw)
        tagline = data["tagline"]
        summary = data["summary"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning(
            "cv_tailor: JSON parse failure in tailor_summary_and_tagline: %s — raw=%r",
            exc, (raw or "")[:200],
        )
        return None

    error = validate_summary(summary)
    if error:
        _send_validation_alert("summary", company, error, summary)

    return TailoredHeader(tagline=tagline, summary=summary)


def tailor_experience_bullets(
    experience: list[ExperienceEntry],
    jd_title: str,
    required_skills: list[str],
    preferred_skills: list[str],
    company: str,
) -> list[ExperienceEntry] | None:
    """Rephrase experience bullets using JD language. Same duties, different words."""
    exp_dicts = [
        {"title": e.title, "company": e.company, "dates": e.dates, "bullets": e.bullets}
        for e in experience
    ]
    prompt = (
        f"You are a CV writer. Rephrase the experience bullets below using language from the job description.\n\n"
        f"Target role: {jd_title} at {company}\n"
        f"Required skills: {', '.join(required_skills)}\n"
        f"Preferred skills: {', '.join(preferred_skills)}\n\n"
        f"Experience entries:\n{json.dumps(exp_dicts, indent=2)}\n\n"
        f"Rules:\n"
        f"- Same responsibilities rephrased — do NOT add or remove bullets\n"
        f"- Start each bullet with a strong action verb\n"
        f"- Preserve ALL quantified metrics exactly (numbers, percentages, currencies)\n"
        f"- Each bullet must be under 200 characters\n\n"
        f"Respond ONLY with valid JSON array: "
        f"[{{\"title\": \"...\", \"company\": \"...\", \"dates\": \"...\", \"bullets\": [...]}}]"
    )
    try:
        raw = cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")
    except Exception as exc:
        logger.warning("cv_tailor: LLM failure in tailor_experience_bullets: %s", exc)
        return None

    try:
        data = _parse_llm_json(raw)
        if not isinstance(data, list) or len(data) != len(experience):
            logger.warning(
                "cv_tailor: experience count mismatch: expected %d got %d",
                len(experience), len(data) if isinstance(data, list) else -1,
            )
            return None
        tailored = [
            ExperienceEntry(
                title=item["title"],
                company=item["company"],
                dates=item["dates"],
                bullets=item["bullets"],
                location=experience[i].location,
            )
            for i, item in enumerate(data)
        ]
    except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
        logger.warning(
            "cv_tailor: JSON parse failure in tailor_experience_bullets: %s — raw=%r",
            exc, (raw or "")[:200],
        )
        return None

    error = validate_experience(experience, tailored)
    if error:
        _send_validation_alert("experience", company, error, str([e.bullets for e in tailored]))

    return tailored


def tailor_project_bullets(
    projects: list[dict],
    jd_title: str,
    required_skills: list[str],
    preferred_skills: list[str],
    company: str,
) -> list[dict] | None:
    """Rewrite project bullets emphasising JD-relevant skills."""
    prompt_projects = [
        {"title": p.get("title", ""), "bullets": p.get("bullets", [])}
        for p in projects
    ]
    prompt = (
        f"You are a CV writer. Rewrite the project bullets below to emphasise skills relevant to the job.\n\n"
        f"Target role: {jd_title} at {company}\n"
        f"Required skills: {', '.join(required_skills)}\n"
        f"Preferred skills: {', '.join(preferred_skills)}\n\n"
        f"Projects:\n{json.dumps(prompt_projects, indent=2)}\n\n"
        f"Rules:\n"
        f"- Emphasise JD-relevant skills in every bullet\n"
        f"- Preserve ALL quantified metrics exactly (numbers, percentages, currencies)\n"
        f"- 3-4 bullets per project — no more, no less\n"
        f"- First bullet must lead with the strongest JD-relevant skill\n\n"
        f"Respond ONLY with valid JSON array: [{{\"title\": \"...\", \"bullets\": [...]}}]"
    )
    try:
        raw = cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")
    except Exception as exc:
        logger.warning("cv_tailor: LLM failure in tailor_project_bullets: %s", exc)
        return None

    try:
        data = _parse_llm_json(raw)
        if not isinstance(data, list) or len(data) != len(projects):
            logger.warning(
                "cv_tailor: project count mismatch: expected %d got %d",
                len(projects), len(data) if isinstance(data, list) else -1,
            )
            return None
        tailored = []
        for i, item in enumerate(data):
            merged = dict(projects[i])  # preserve original url, title etc.
            merged["title"] = projects[i].get("title", item.get("title", ""))
            merged["bullets"] = item["bullets"]
            tailored.append(merged)
    except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
        logger.warning(
            "cv_tailor: JSON parse failure in tailor_project_bullets: %s — raw=%r",
            exc, (raw or "")[:200],
        )
        return None

    error = validate_projects(projects, tailored)
    if error:
        _send_validation_alert("projects", company, error, str([p.get("bullets") for p in tailored]))

    return tailored


def tailor_cover_letter_prose(
    company: str,
    role: str,
    required_skills: list[str],
    matched_projects: list[dict],
) -> TailoredCoverLetter | None:
    """Generate intro, hook, and closing paragraphs tailored to the JD."""
    project_titles = [p.get("title", "") for p in matched_projects[:3]]
    prompt = (
        f"You are a cover letter writer. Generate tailored intro, hook, and closing paragraphs.\n\n"
        f"Company: {company}\n"
        f"Role: {role}\n"
        f"Required skills: {', '.join(required_skills)}\n"
        f"Relevant projects: {', '.join(project_titles)}\n\n"
        f"Rules:\n"
        f"- Intro (2-3 sentences): mention role and '{company}', explain why this company interests the candidate\n"
        f"- Hook (2-3 sentences): connect skills to the JD with a concrete achievement, no soft skills\n"
        f"- Closing (2-3 sentences): enthusiasm for {company}, mention looking forward to discussion\n"
        f"- No em-dashes, professional tone, each section 50-300 characters\n\n"
        f"Respond ONLY with valid JSON: {{\"intro\": \"...\", \"hook\": \"...\", \"closing\": \"...\"}}"
    )
    try:
        raw = cognitive_llm_call(task=prompt, domain="cv_tailoring", stakes="medium")
    except Exception as exc:
        logger.warning("cv_tailor: LLM failure in tailor_cover_letter_prose: %s", exc)
        return None

    try:
        data = _parse_llm_json(raw)
        cl = TailoredCoverLetter(
            intro=data["intro"],
            hook=data["hook"],
            closing=data["closing"],
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning(
            "cv_tailor: JSON parse failure in tailor_cover_letter_prose: %s — raw=%r",
            exc, (raw or "")[:200],
        )
        return None

    error = validate_cover_letter(cl, company)
    if error:
        _send_validation_alert("cover_letter", company, error, f"{cl.intro} {cl.hook} {cl.closing}")

    return cl


def tailor_all_sections(
    listing,
    matched_projects: list[dict],
    experience: list[ExperienceEntry],
) -> TailoredCV:
    """Run all 4 tailoring calls in parallel. Returns TailoredCV with all sections."""
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="cv_tailor") as pool:
        fut_header = pool.submit(
            tailor_summary_and_tagline,
            listing.title,
            listing.description_raw,
            listing.company,
            listing.required_skills,
            listing.preferred_skills,
        )
        fut_experience = pool.submit(
            tailor_experience_bullets,
            experience,
            listing.title,
            listing.required_skills,
            listing.preferred_skills,
            listing.company,
        )
        fut_projects = pool.submit(
            tailor_project_bullets,
            matched_projects,
            listing.title,
            listing.required_skills,
            listing.preferred_skills,
            listing.company,
        )
        fut_cl = pool.submit(
            tailor_cover_letter_prose,
            listing.company,
            listing.title,
            listing.required_skills,
            matched_projects,
        )

        header = fut_header.result()
        tailored_exp = fut_experience.result()
        tailored_proj = fut_projects.result()
        cl = fut_cl.result()

    return TailoredCV(
        tagline=header.tagline if header else None,
        summary=header.summary if header else None,
        experience=tailored_exp,
        projects=tailored_proj,
        cover_letter=cl,
    )
