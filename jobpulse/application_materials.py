"""Deferred CV / lazy cover letter — generate PDFs at apply time, not during scan."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

if TYPE_CHECKING:
    from jobpulse.job_db import JobDB

logger = get_logger(__name__)


def _application_dir(company: str | None) -> Path:
    """Canonical per-company materials directory.

    All CV/CL/screenshot artefacts for a company live under
    ``DATA_DIR/applications/<safe_company>/``. This matches:
      • cv_templates.generate_cv default (line 469)
      • cv_templates.generate_cover_letter default (line 355)
      • drive_uploader read path (looks for Yash_Bishnoi_<Company>.pdf)
      • post_apply_hook attachment lookup

    Pre-2026-05-04 application_materials wrote to ``applications/<job_id>/``
    (sha-named), causing drive_uploader to silently fail "file not found"
    because it expected the company-named dir. Live regression confirmed
    on Contentful — files split across two dirs, Drive upload failed.

    For multi-job-per-company collisions (rare for a single applicant),
    cv_templates' filename naming ``{name}_{company}.pdf`` is sufficient
    because the file would simply be overwritten by the latest run.
    """
    safe = (company or "Company").replace(" ", "_").replace("/", "_")
    return DATA_DIR / "applications" / safe


_LOCATION_BLEED_TOKENS = (
    "provided", "required", "subject to", "depending", "consistently", "reliable",
    "wifi", "hours", "benefit", "equipment", "setup", "set-up", "available",
    "preferred", "ideally", "must", "willing", "able to", "reliable", "based in",
    "candidates", "applicants", "essential", "expected",
)


def _sanitize_location(raw: str | None, *, remote: bool = False) -> str:
    """Reject JD prose bleed and return a clean location string.

    The Indeed/aggregator scrapers occasionally extract the first 1-3 words
    after "remote, " from prose like "remote, provided the working location
    has consistently reliable Wifi". That junk then bleeds into the CV
    contact line. This guard rejects strings that:
      • exceed 60 chars (real locations are short),
      • contain JD-prose markers (provided, required, wifi, …),
      • start with a non-capitalised word and aren't a known short token,
      • are an obvious sentence fragment (>5 tokens, no commas → prose).

    Falls back to ``"Remote (UK)"`` when ``remote=True`` else
    ``"United Kingdom"``.
    """
    fallback = "Remote (UK)" if remote else "United Kingdom"
    if not raw:
        return fallback
    s = raw.strip()
    if not s:
        return fallback
    low = s.lower()
    if len(s) > 60:
        return fallback
    if any(tok in low for tok in _LOCATION_BLEED_TOKENS):
        return fallback
    tokens = s.replace(",", " ").split()
    if len(tokens) > 5 and "," not in s:
        return fallback
    return s


def _parse_skill_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip().startswith("[") else []
        except json.JSONDecodeError:
            return []
    return []


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

    # Boost extra_skills with user-corrected skill values. Append to the
    # existing "Also proficient in:" row rather than introducing a separate
    # row whose label ("Corrected: …") would render as user-visible text in
    # the CV's Technical Skills section.
    try:
        from jobpulse.correction_capture import CorrectionCapture
        user_skills = CorrectionCapture().get_skill_correction_values(min_occurrences=2)
        if user_skills:
            label = "Also proficient in:"
            existing_vals = extra.get(label, "") if extra else ""
            existing_lower = {
                s.strip().lower() for s in existing_vals.split("|") if s.strip()
            }
            additions: list[str] = []
            for skill in user_skills:
                norm = skill.strip()
                if norm and norm.lower() not in existing_lower:
                    additions.append(norm)
                    existing_lower.add(norm.lower())
            if additions:
                merged = " | ".join(
                    [s for s in existing_vals.split(" | ") if s.strip()] + additions
                )
                if extra is None:
                    extra = {}
                extra[label] = merged
    except Exception as exc:
        # Correction-driven skill boost is the OPRAL learning chain's last
        # mile — debug-only swallow means a broken CorrectionCapture path
        # produces silent skill drift across every tailored CV. Promote to
        # warning so the regression is visible.
        logger.warning(
            "application_materials: correction skill boost failed for %s — "
            "tailored CV will not include user-corrected skills: %s",
            job_id[:12], exc, exc_info=True,
        )

    # Tailor all CV sections via parallel LLM calls
    experience_entries = get_profile_store().experience()

    class _ListingProxy:
        pass

    listing_proxy = _ListingProxy()
    listing_proxy.title = row.get("title") or "Software Engineer"
    listing_proxy.company = row.get("company") or "Company"
    listing_proxy.required_skills = required
    listing_proxy.preferred_skills = preferred
    listing_proxy.description_raw = row.get("description_raw") or ""

    tailored = None
    try:
        tailored = tailor_all_sections(listing_proxy, matched_projects, experience_entries)
    except Exception as exc:
        logger.warning("application_materials: tailoring failed, using templates: %s", exc)

    # Resolve with fallbacks to static templates
    role_profile = get_role_profile(row.get("title") or "Software Engineer")

    tagline = (tailored.tagline if tailored and tailored.tagline else None) or role_profile.get("tagline")
    summary = (tailored.summary if tailored and tailored.summary else None) or role_profile.get("summary")
    projects = (tailored.projects if tailored and tailored.projects else None) or matched_projects

    exp_dicts = None
    if tailored and tailored.experience:
        exp_dicts = [
            {"title": e.title, "company": e.company, "dates": e.dates, "bullets": e.bullets}
            for e in tailored.experience
        ]

    out_dir = str(_application_dir(row.get("company")))

    try:
        cv_path = generate_cv_pdf(
            company=row.get("company") or "Company",
            location=_sanitize_location(row.get("location"), remote=bool(row.get("remote"))),
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
        out_dir = str(_application_dir(row.get("company")))

        cl_prose = None
        try:
            cl_prose = tailor_cover_letter_prose(
                company=row.get("company") or "Company",
                role=row.get("title") or "Role",
                required_skills=required + preferred,
                matched_projects=matched,
            )
        except Exception as exc:
            # CL tailoring is the value-add for the lazy-CL path; silent
            # failure ships generic templates without operator signal.
            # Promote to warning to mirror the eager-CL path in
            # scan_pipeline.generate_materials (S8 audit M-B).
            logger.warning(
                "application_materials: CL tailoring failed for %s — "
                "lazy CL will use generic template: %s",
                row.get("company") or "Company", exc, exc_info=True,
            )

        try:
            return generate_cover_letter_pdf(
                company=row.get("company") or "Company",
                role=row.get("title") or "Role",
                location=_sanitize_location(row.get("location"), remote=bool(row.get("remote"))),
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
