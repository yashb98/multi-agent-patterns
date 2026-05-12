"""cv_tailor.py — Dataclasses and validation for dynamic CV tailoring.

Validates LLM-generated CV sections before they reach the PDF renderer.
All personal data comes from the profile DB at runtime — never hardcoded here.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta

from shared.agents import cognitive_llm_call
from shared.db_observability import observe_lookup
from shared.logging_config import get_logger
from shared.profile_store import ExperienceEntry

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tailored-CV cache (cache-llm-S4)
# ---------------------------------------------------------------------------

_TAILORED_CV_CACHE_TTL_DAYS = 14
_TAILORED_CV_CACHE_LOCK = threading.Lock()


def _classify_role_archetype(role_title: str) -> str:
    """Coarse archetype for cache keying. Mirrors
    `screening_answers._classify_role_archetype` so cache rows stay
    consistent across the pipeline."""
    if not role_title:
        return "generic"
    t = role_title.lower().strip()
    if "data analyst" in t or ("analytics" in t and "engineer" not in t):
        return "data_analyst"
    if "data engineer" in t:
        return "data_engineer"
    if "data scientist" in t:
        return "data_scientist"
    if "machine learning" in t or "ml engineer" in t or "ai engineer" in t:
        return "ml_engineer"
    if "research engineer" in t or "research scientist" in t:
        return "research_engineer"
    if "backend" in t or "back-end" in t:
        return "backend_engineer"
    if "frontend" in t or "front-end" in t:
        return "frontend_engineer"
    if "full stack" in t or "fullstack" in t or "full-stack" in t:
        return "fullstack_engineer"
    if "software engineer" in t or "developer" in t:
        return "software_engineer"
    return t.split()[0] if t else "generic"


def _jd_hash(listing) -> str:
    """16-char hash of the JD inputs that feed cv_tailor.

    Captures the fields tailor_summary_and_tagline / tailor_experience_bullets
    / tailor_project_bullets / tailor_cover_letter_prose actually consume:
    title + description + company + required + preferred skills.
    Order-independent for skill lists so reordering doesn't invalidate cache.
    """
    payload = {
        "title": (getattr(listing, "title", "") or "").strip().lower(),
        "company": (getattr(listing, "company", "") or "").strip().lower(),
        "description": (getattr(listing, "description_raw", "") or "").strip(),
        "required": sorted((getattr(listing, "required_skills", None) or [])),
        "preferred": sorted((getattr(listing, "preferred_skills", None) or [])),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _profile_version_hash(
    experience: list[ExperienceEntry] | None,
    matched_projects: list[dict] | None,
) -> str:
    """16-char hash of the profile-side inputs to cv_tailor (experience +
    matched projects). When the user adds a project or updates a job
    title in the profile DB, this hash changes and the cache misses.
    """
    exp_payload = []
    for e in experience or []:
        exp_payload.append({
            "title": e.title, "company": e.company, "dates": e.dates,
            "bullets": e.bullets, "location": e.location,
        })
    proj_payload = []
    for p in matched_projects or []:
        # Project dicts have variable shapes; canonicalise the fields cv_tailor
        # actually reads so casual extra keys don't fragment the cache.
        proj_payload.append({
            "name": p.get("name", ""),
            "description": p.get("description", ""),
            "url": p.get("url", ""),
            "matched_skills": sorted(p.get("matched_skills", []) or []),
        })
    raw = json.dumps(
        {"experience": exp_payload, "projects": proj_payload},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _tailored_cv_cache_init(db) -> None:
    """Lazily create tailored_cv_cache table inside applications.db.

    Schema mirrors the hiring_message_cache table from cache-llm-S3:
    primary key is the (role_archetype, jd_hash, profile_version) tuple,
    payload is the JSON-serialised TailoredCV.
    """
    conn = db._connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tailored_cv_cache ("
        "role_archetype TEXT NOT NULL, jd_hash TEXT NOT NULL, "
        "profile_version TEXT NOT NULL, payload TEXT NOT NULL, "
        "generated_at TEXT NOT NULL, hit_count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (role_archetype, jd_hash, profile_version))"
    )
    conn.commit()


@observe_lookup("applications", "tailored_cv_cache", key_arg=0)
def _tailored_cv_cache_lookup(
    role_archetype: str, jd_hash: str, profile_version: str, *, db=None,
) -> "TailoredCV | None":
    """Return cached TailoredCV or None on miss / TTL expiry.

    Under ``JOBPULSE_TEST_MODE=1`` (set by ``tests/conftest.py``), a default
    ``db=None`` short-circuits to None so unrelated tests don't pick up
    cache entries from prior runs of the same suite. Tests that exercise
    cache behaviour pass an explicit ``db=`` kwarg from their tmp_path.
    """
    if not (role_archetype and jd_hash and profile_version):
        return None
    import os as _os
    if db is None and _os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return None
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    with _TAILORED_CV_CACHE_LOCK:
        _tailored_cv_cache_init(db)
        conn = db._connect()
        row = conn.execute(
            "SELECT payload, generated_at FROM tailored_cv_cache "
            "WHERE role_archetype = ? AND jd_hash = ? AND profile_version = ?",
            (role_archetype, jd_hash, profile_version),
        ).fetchone()
        if not row:
            return None
        try:
            generated = datetime.fromisoformat(row["generated_at"])
            if (datetime.now() - generated).days > _TAILORED_CV_CACHE_TTL_DAYS:
                return None
        except (ValueError, TypeError):
            return None
        try:
            payload = json.loads(row["payload"])
            cv = _tailored_cv_from_payload(payload)
        except Exception as exc:  # noqa: BLE001
            logger.debug("tailored_cv_cache: payload parse failed: %s", exc)
            return None
        conn.execute(
            "UPDATE tailored_cv_cache SET hit_count = hit_count + 1 "
            "WHERE role_archetype = ? AND jd_hash = ? AND profile_version = ?",
            (role_archetype, jd_hash, profile_version),
        )
        conn.commit()
        return cv


def _tailored_cv_cache_store(
    role_archetype: str, jd_hash: str, profile_version: str, cv: "TailoredCV", *,
    db=None,
) -> None:
    """Persist a freshly-generated TailoredCV.

    Under ``JOBPULSE_TEST_MODE=1`` with default ``db=None``, the store is
    a no-op — same rationale as the lookup guard above.
    """
    if not (role_archetype and jd_hash and profile_version) or cv is None:
        return
    import os as _os
    if db is None and _os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    payload = json.dumps(_tailored_cv_to_payload(cv), ensure_ascii=False)
    # Length cap (DB safety, 2026-05-10): a legitimate tailored CV payload
    # is ~3-8 KB. 64 KB cap blocks pathological LLM output bloating the
    # cache table without rejecting any real-world result.
    if len(payload) > 65536:
        logger.warning(
            "tailored_cv_cache: skipping store — payload %d chars exceeds 64 KB cap",
            len(payload),
        )
        return
    with _TAILORED_CV_CACHE_LOCK:
        _tailored_cv_cache_init(db)
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO tailored_cv_cache "
            "(role_archetype, jd_hash, profile_version, payload, generated_at, hit_count) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (role_archetype, jd_hash, profile_version, payload,
             datetime.now().isoformat()),
        )
        conn.commit()


def _tailored_cv_to_payload(cv: "TailoredCV") -> dict:
    """Serialise TailoredCV to a JSON-friendly dict (dataclasses → dicts)."""
    return asdict(cv)


def _tailored_cv_from_payload(payload: dict) -> "TailoredCV":
    """Reconstruct TailoredCV from a JSON-decoded dict.

    Re-hydrates nested dataclasses (`TailoredCoverLetter`, `ExperienceEntry`)
    so the returned object behaves identically to a freshly-built one.
    """
    exp_raw = payload.get("experience") or []
    experience = [ExperienceEntry(**e) for e in exp_raw] if exp_raw else None

    cl_raw = payload.get("cover_letter")
    cover_letter = TailoredCoverLetter(**cl_raw) if cl_raw else None

    return TailoredCV(
        tagline=payload.get("tagline"),
        summary=payload.get("summary"),
        experience=experience,
        projects=payload.get("projects"),
        cover_letter=cover_letter,
    )


def build_required_tagline(yoe: str, jd_title: str, top_skills: str) -> str:
    """Build the required-tagline string for the LLM prompt — pulls degree
    abbreviation from ProfileStore.education() so source code never embeds
    the user's specific institution.

    Returns: e.g. 'MSc Computer Science (UOD) | 2+ YOE | Data Engineer | Python, SQL'
    when the DB has the user's education; falls back to a degree-agnostic
    template when DB is empty.
    """
    try:
        from shared.profile_store import get_profile_store
        edu = get_profile_store().education()
        top = edu[0] if edu else None
        if top:
            inst = (top.institution or "").strip()
            abbr = "".join(w[0] for w in inst.split()[:3] if w[0].isupper()) or (
                inst.split()[0] if inst else ""
            )
            degree_part = f"{top.degree} ({abbr})" if abbr else (top.degree or "")
        else:
            degree_part = ""
    except Exception:
        degree_part = ""
    parts = [p for p in [degree_part, f"{yoe} YOE", jd_title, top_skills] if p]
    return " | ".join(parts)


# Backwards-compatible private alias (still referenced inside this module).
_required_tagline_format = build_required_tagline


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
    parsed: object
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back: find whichever opener comes first ('{' or '['), then take
        # everything up to the matching closer. Picking the earlier opener
        # handles prose prefixes like 'Sure! [{...}]' correctly — naive
        # first-{/last-} would slice the inner object out of the array.
        obj_start = cleaned.find("{")
        arr_start = cleaned.find("[")
        candidates: list[tuple[int, str]] = []
        if obj_start != -1:
            candidates.append((obj_start, "}"))
        if arr_start != -1:
            candidates.append((arr_start, "]"))
        candidates.sort(key=lambda c: c[0])
        parsed = _SENTINEL = object()
        for start, closer in candidates:
            last = cleaned.rfind(closer)
            if last > start:
                try:
                    parsed = json.loads(cleaned[start:last + 1])
                    break
                except json.JSONDecodeError:
                    continue
        if parsed is _SENTINEL:
            raise json.JSONDecodeError(
                f"No valid JSON object or array found in response: {cleaned[:120]!r}",
                raw,
                0,
            )

    # OpenAI's response_format={"type":"json_object"} forces a top-level object,
    # so prompts that conceptually want an array end up wrapped (e.g. the LLM
    # returns {"experience": [...]} instead of [...]). Unwrap when the result
    # is a single-key dict whose value is a list — callers expecting arrays
    # then see them directly. Multi-key dicts (summary+tagline, intro/hook/
    # closing) are returned as-is.
    if isinstance(parsed, dict) and len(parsed) == 1:
        only_value = next(iter(parsed.values()))
        if isinstance(only_value, list):
            return only_value
    return parsed


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

# Soft-skill phrases that recruiters universally flag as filler. Trimmed to
# the genuine clichés — "interviewing", "okrs", "project management",
# "presentation skills", "prioritization" are real concrete activities and
# were producing false positives that flooded Telegram.
_SOFT_SKILL_WORDS = {
    "communication", "teamwork", "leadership", "problem solving", "time management",
    "adaptability", "collaboration", "analytical thinking", "critical thinking",
    "attention to detail", "self motivated", "fast learner", "customer focus",
    "strategic thinking",
}

_SOFT_SKILL_HINT = ", ".join(sorted(_SOFT_SKILL_WORDS))

# Regex is used here only for structural format validation of numeric/percentage
# patterns — not for semantic classification (allowed per codebase rules).
# Matches: 5%, 5$, 5£, 100+ (multi-digit), $5, £5 (currency-prefixed singles).
_METRIC_RE = re.compile(r"\d+[%$£]|\d{2,}|\$\d|£\d")


def validate_summary(summary: str) -> str | None:
    """Returns error string or None if valid.

    Word count enforces the TIH guideline that resume summaries stay under
    50 words — 30 words floor keeps it substantive. `<b>` tags are stripped
    before counting so the bold markup we require doesn't inflate the count.
    """
    plain = re.sub(r"<[^>]+>", " ", summary)
    word_count = len(plain.split())
    if word_count < 30 or word_count > 50:
        return f"Summary word count {word_count} outside 30-50 range"
    summary_lower = summary.lower()
    for word in _SOFT_SKILL_WORDS:
        if word in summary_lower:
            return f"Soft skill word found: '{word}'"
    if "<b>" not in summary:
        return "Summary must contain at least one <b> tag"
    return None


def validate_experience(original: list[ExperienceEntry], tailored: list[ExperienceEntry]) -> str | None:
    """Returns error string or None if valid.

    Per-entry rule: each entry must keep at least one bullet with a numeric
    metric (down from per-bullet, which was too strict — the LLM
    legitimately drops metrics from supporting bullets when emphasising the
    headline number).
    """
    if len(tailored) != len(original):
        return f"Entry count mismatch: expected {len(original)}, got {len(tailored)}"
    for i, entry in enumerate(tailored):
        if not entry.bullets:
            return f"Entry {i} has no bullets"
        for j, bullet in enumerate(entry.bullets):
            if len(bullet) > 220:  # +20 char tolerance — strict 200 caused frequent false positives
                return f"Entry {i} bullet {j} exceeds 220 chars ({len(bullet)})"
        if not any(_METRIC_RE.search(b) for b in entry.bullets):
            return f"Entry {i} has no quantified metric across any bullet"
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
# Validation failure recording (no per-failure Telegram — see
# `tailor_all_sections` which batches one summary message per scan)
# ---------------------------------------------------------------------------


def _record_validation_failure(section: str, company: str, reason: str, text: str) -> None:
    """Log a validation failure. Telegram is no longer fired per-failure —
    it was creating an alert flood when the LLM produced borderline output
    on every job. End-of-scan summary handles user-visible reporting."""
    logger.warning(
        "cv_tailor: %s failed validation for %s — %s | text=%r",
        section, company, reason, text[:200],
    )


def _call_with_correction(prompt: str) -> str | None:
    """Wrap a single ``cognitive_llm_call`` with consistent exception
    handling. Callers handle validation + corrective retry themselves.
    """
    try:
        return cognitive_llm_call(
            task=prompt, domain="cv_tailoring", stakes="medium",
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("cv_tailor: LLM call failed: %s", exc)
        return None


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

    def _build_prompt(corrective: str = "") -> str:
        base = (
            f"You are a CV writer. Generate a tailored tagline and professional summary for a job application.\n\n"
            f"Role: {jd_title}\n"
            f"Company: {company}\n"
            f"Required skills: {', '.join(required_skills)}\n"
            f"Preferred skills: {', '.join(preferred_skills)}\n"
            f"Job description excerpt: {jd_description[:500]}\n\n"
            f"HARD RULES (the response will be auto-rejected if any of these fail):\n"
            # Tagline format is dynamic — caller resolves user's degree/YOE
            # via ProfileStore at runtime instead of hardcoding "MSc Computer
            # Science (UOD)" which would only fit one specific applicant.
            f"- Tagline EXACTLY: {_required_tagline_format(yoe, jd_title, top_skills)}\n"
            f"- Summary length: 30-50 words total (TIH guideline; count words, not characters)\n"
            f"- Summary MUST contain at least one <b>...</b> tag — wrap the role title\n"
            f"- Summary MUST mention '{company}' literally\n"
            f"- Summary MUST reference 2-3 of the required skills above\n"
            f"- Expand common abbreviations on first use: 'Amazon Web Services (AWS)', "
            f"'Google Cloud Platform (GCP)', 'Machine Learning (ML)', "
            f"'Natural Language Processing (NLP)', 'Kubernetes (K8s)' — TIH guideline\n"
            f"- Forbidden filler words anywhere in summary: {_SOFT_SKILL_HINT}\n"
            f"- No em-dashes (—); use commas or hyphens instead\n"
            f"- 2-3 sentences total\n\n"
            # Example uses a generic role/employer, not a specific employer.
            f"Format example: <b>Senior Data Engineer</b> with 3+ years building Python pipelines. "
            f"Built distributed Spark workflows processing 100GB/day. Specialises in dbt, Airflow, and SQL "
            f"optimisation for analytics teams at {company}.\n\n"
            f"Respond ONLY with valid JSON: {{\"tagline\": \"...\", \"summary\": \"...\"}}"
        )
        if corrective:
            base += (
                f"\n\nCORRECTIVE FEEDBACK from previous attempt: {corrective}\n"
                f"Fix this exact issue and return new JSON."
            )
        return base

    raw = _call_with_correction(_build_prompt())
    try:
        data = _parse_llm_json(raw)
        tagline, summary = data["tagline"], data["summary"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning(
            "cv_tailor: JSON parse failure in tailor_summary_and_tagline: %s — raw=%r",
            exc, (raw or "")[:200],
        )
        return None

    error = validate_summary(summary)
    if error:
        # Retry once with the validator's complaint fed back as feedback.
        # Only swap to the retry's content if it passes validation — otherwise
        # keep attempt 1, which may be closer to valid.
        raw2 = _call_with_correction(_build_prompt(corrective=error))
        try:
            data2 = _parse_llm_json(raw2)
            retry_tagline = data2["tagline"]
            retry_summary = data2["summary"]
            retry_error = validate_summary(retry_summary)
            if not retry_error:
                tagline, summary, error = retry_tagline, retry_summary, None
        except (json.JSONDecodeError, KeyError, TypeError):
            pass  # keep first attempt's tagline/summary
        if error:
            _record_validation_failure("summary", company, error, summary)

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

    def _build_prompt(corrective: str = "") -> str:
        base = (
            f"You are a CV writer. Rephrase the experience bullets below using language from the job description.\n\n"
            f"Target role: {jd_title} at {company}\n"
            f"Required skills: {', '.join(required_skills)}\n"
            f"Preferred skills: {', '.join(preferred_skills)}\n\n"
            f"Experience entries:\n{json.dumps(exp_dicts, indent=2)}\n\n"
            f"HARD RULES (validator will reject violations):\n"
            f"- KEEP the same number of entries and the same number of bullets per entry\n"
            f"- Do NOT add or remove bullets\n"
            f"- Each entry MUST have at least one bullet containing a number, %, $, or £\n"
            f"- PRESERVE every original number/percentage/currency exactly as written\n"
            f"- Each bullet under 220 characters\n"
            f"- Start every bullet with a strong action verb\n\n"
            f"Respond ONLY with valid JSON: "
            f"{{\"experience\": [{{\"title\": \"...\", \"company\": \"...\", \"dates\": \"...\", \"bullets\": [...]}}]}}"
        )
        if corrective:
            base += f"\n\nCORRECTIVE FEEDBACK: {corrective}\nFix and return new JSON."
        return base

    def _parse(raw: str | None) -> list[ExperienceEntry] | None:
        try:
            data = _parse_llm_json(raw)
            if not isinstance(data, list) or len(data) != len(experience):
                return None
            for item in data:
                bullets = item.get("bullets") if isinstance(item, dict) else None
                if not isinstance(bullets, list) or not all(isinstance(b, str) for b in bullets):
                    return None
            return [
                ExperienceEntry(
                    title=item["title"], company=item["company"], dates=item["dates"],
                    bullets=item["bullets"], location=experience[i].location,
                )
                for i, item in enumerate(data)
            ]
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            return None

    tailored = _parse(_call_with_correction(_build_prompt()))
    if tailored is None:
        return None

    error = validate_experience(experience, tailored)
    if error:
        retry = _parse(_call_with_correction(_build_prompt(corrective=error)))
        if retry is not None:
            retry_error = validate_experience(experience, retry)
            if not retry_error:
                return retry
            # Both attempts failed — keep the first attempt's content rather
            # than overwriting with retry's (which is no better).
        if error:
            _record_validation_failure(
                "experience", company, error, str([e.bullets for e in tailored]),
            )
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

    def _build_prompt(corrective: str = "") -> str:
        base = (
            f"You are a CV writer. Rewrite the project bullets below to emphasise skills relevant to the job.\n\n"
            f"Target role: {jd_title} at {company}\n"
            f"Required skills: {', '.join(required_skills)}\n"
            f"Preferred skills: {', '.join(preferred_skills)}\n\n"
            f"Projects:\n{json.dumps(prompt_projects, indent=2)}\n\n"
            f"HARD RULES (validator will reject violations):\n"
            f"- Output EXACTLY {len(projects)} projects in the same order as the input\n"
            f"- Each project MUST have 3-4 bullets — not 2, not 5\n"
            f"- PRESERVE every original number from each project's bullets exactly\n"
            f"- Emphasise JD-relevant skills in the FIRST bullet of each project\n\n"
            f"Respond ONLY with valid JSON: {{\"projects\": [{{\"title\": \"...\", \"bullets\": [...]}}]}}"
        )
        if corrective:
            base += f"\n\nCORRECTIVE FEEDBACK: {corrective}\nFix and return new JSON."
        return base

    def _parse(raw: str | None) -> list[dict] | None:
        try:
            data = _parse_llm_json(raw)
            if not isinstance(data, list) or len(data) != len(projects):
                return None
            for item in data:
                bullets = item.get("bullets") if isinstance(item, dict) else None
                if not isinstance(bullets, list) or not all(isinstance(b, str) for b in bullets):
                    return None
            out = []
            for i, item in enumerate(data):
                merged = dict(projects[i])
                merged["title"] = projects[i].get("title", item.get("title", ""))
                merged["bullets"] = item["bullets"]
                out.append(merged)
            return out
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            return None

    tailored = _parse(_call_with_correction(_build_prompt()))
    if tailored is None:
        return None

    error = validate_projects(projects, tailored)
    if error:
        retry = _parse(_call_with_correction(_build_prompt(corrective=error)))
        if retry is not None:
            retry_error = validate_projects(projects, retry)
            if not retry_error:
                return retry
            # Both attempts failed — keep the first attempt's content.
        if error:
            _record_validation_failure(
                "projects", company, error,
                str([p.get("bullets") for p in tailored]),
            )
    return tailored


def tailor_cover_letter_prose(
    company: str,
    role: str,
    required_skills: list[str],
    matched_projects: list[dict],
) -> TailoredCoverLetter | None:
    """Generate intro, hook, and closing paragraphs tailored to the JD."""
    project_titles = [p.get("title", "") for p in matched_projects[:3]]

    def _build_prompt(corrective: str = "") -> str:
        base = (
            f"You are a cover letter writer. Generate tailored intro, hook, and closing paragraphs.\n\n"
            f"Company: {company}\n"
            f"Role: {role}\n"
            f"Required skills: {', '.join(required_skills)}\n"
            f"Relevant projects: {', '.join(project_titles)}\n\n"
            f"HARD RULES (validator will reject violations):\n"
            f"- Each section length: 50-300 characters\n"
            f"- Intro MUST contain the literal word '{company}'\n"
            f"- Hook MUST NOT contain any of these filler words: {_SOFT_SKILL_HINT}\n"
            f"- Hook MUST cite a concrete achievement (use a number/percentage/$/£)\n"
            f"- No em-dashes (—), use commas or hyphens\n"
            f"- Closing MUST express enthusiasm for {company} + look forward to a discussion\n\n"
            f"Respond ONLY with valid JSON: {{\"intro\": \"...\", \"hook\": \"...\", \"closing\": \"...\"}}"
        )
        if corrective:
            base += f"\n\nCORRECTIVE FEEDBACK: {corrective}\nFix and return new JSON."
        return base

    def _parse(raw: str | None) -> TailoredCoverLetter | None:
        try:
            data = _parse_llm_json(raw)
            return TailoredCoverLetter(
                intro=data["intro"], hook=data["hook"], closing=data["closing"],
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    cl = _parse(_call_with_correction(_build_prompt()))
    if cl is None:
        return None

    error = validate_cover_letter(cl, company)
    if error:
        retry = _parse(_call_with_correction(_build_prompt(corrective=error)))
        if retry is not None:
            retry_error = validate_cover_letter(retry, company)
            if not retry_error:
                return retry
            # Both attempts failed — keep the first attempt's content.
        if error:
            _record_validation_failure(
                "cover_letter", company, error,
                f"{cl.intro} {cl.hook} {cl.closing}",
            )
    return cl


def tailor_all_sections(
    listing,
    matched_projects: list[dict],
    experience: list[ExperienceEntry],
) -> TailoredCV:
    """Run all 4 tailoring calls in parallel. Returns TailoredCV with all sections.

    Wraps the LLM section in a `(role_archetype, jd_hash, profile_version)`
    cache so a re-application to the same JD with an unchanged profile
    skips all 4 LLM calls. Cache miss runs the parallel calls and stores
    the result on success; partial-failure results are NOT cached.

    Concurrency: 4-way parallel for cloud OpenAI; serialised (1 worker)
    for local Ollama. Single-tenant Ollama returns empty content for
    2–3 of 4 concurrent calls under load (verified during S2 live
    debugging) — serialising avoids the empty-content failure mode
    while sacrificing wall-clock for the worse case.
    """
    role_archetype = _classify_role_archetype(getattr(listing, "title", ""))
    jd_hash = _jd_hash(listing)
    profile_version = _profile_version_hash(experience, matched_projects)

    cached = _tailored_cv_cache_lookup(role_archetype, jd_hash, profile_version)
    if cached is not None:
        logger.info(
            "tailored_cv_cache: hit on (%s, %s, %s) — skipping 4× LLM calls",
            role_archetype, jd_hash[:8], profile_version[:8],
        )
        return cached

    from shared.agents import is_local_llm
    workers = 1 if is_local_llm() else 4
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cv_tailor") as pool:
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

    cv = TailoredCV(
        tagline=header.tagline if header else None,
        summary=header.summary if header else None,
        experience=tailored_exp,
        projects=tailored_proj,
        cover_letter=cl,
    )

    # Only cache when every section produced output. A partial result
    # (one LLM call returned None / failed validation) would poison the
    # cache for the next 14 days.
    fully_tailored = all((
        cv.tagline, cv.summary, cv.experience, cv.projects, cv.cover_letter,
    ))
    if fully_tailored:
        try:
            _tailored_cv_cache_store(role_archetype, jd_hash, profile_version, cv)
        except Exception as exc:  # noqa: BLE001
            logger.debug("tailored_cv_cache: store failed (continuing): %s", exc)

    return cv
