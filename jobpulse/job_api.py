"""FastAPI router — HTTP endpoints for the Chrome extension job automation integration.

The extension calls these endpoints instead of using WebSocket.
Mount in mindgraph_app/main.py via: app.include_router(job_api_router)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shared.logging_config import get_logger

logger = get_logger(__name__)

job_api_router = APIRouter(prefix="/api/job", tags=["job"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class JobEvalRequest(BaseModel):
    jd_text: str = Field(description="Full job description text")
    title: str = Field(description="Job title")
    company: str = Field(description="Company name")
    url: str = Field(default="", description="Job listing URL")
    platform: str = Field(default="unknown", description="Platform (reed, linkedin, etc.)")


class JobEvalResponse(BaseModel):
    passed: bool
    score: float
    tier: str
    gate_failed: str | None
    details: dict


class BatchEvalRequest(BaseModel):
    jobs: list[JobEvalRequest] = Field(description="List of jobs to evaluate")


class BatchEvalResponse(BaseModel):
    results: list[JobEvalResponse]


class GenerateCVRequest(BaseModel):
    company: str
    role: str
    matched_projects: list[dict] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    location: str = Field(default="London, UK")
    generate_cover_letter: bool = Field(default=False)


class GenerateCVResponse(BaseModel):
    cv_path: str
    cv_drive_url: str | None
    cl_path: str | None
    cl_drive_url: str | None


class ScanRequest(BaseModel):
    keywords: list[str] = Field(description="Search keywords / job titles")
    location: str = Field(default="United Kingdom")


class ScanJobItem(BaseModel):
    title: str
    company: str
    url: str
    location: str = ""
    salary: str = ""
    platform: str


class ScanResponse(BaseModel):
    jobs: list[ScanJobItem]



class ApplyJobRequest(BaseModel):
    url: str = Field(description="Job listing or application URL")
    platform: str = Field(default="generic", description="ATS platform (greenhouse, lever, etc.)")
    cv_path: str = Field(default="", description="Path to CV PDF (auto-generates if empty)")
    cover_letter_path: str | None = Field(default=None, description="Path to CL PDF")
    dry_run: bool = Field(default=False, description="If true, fill forms but don't submit")
    company: str = Field(default="", description="Company name (for CV generation)")
    role: str = Field(default="", description="Role title (for CV generation)")


class ApplyJobResponse(BaseModel):
    success: bool
    error: str | None = None
    screenshot: str | None = None
    pages_filled: int | None = None
    external_redirect: bool = False
    external_url: str | None = None


class NotifyRequest(BaseModel):
    message: str
    bot: str = Field(default="main", description="'jobs' or 'main'")


class NotifyResponse(BaseModel):
    sent: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_listing_dict(req: JobEvalRequest) -> dict:
    """Build a minimal listing dict compatible with SkillGraphStore.pre_screen_jd()."""
    return {
        "title": req.title,
        "company": req.company,
        "url": req.url,
        "platform": req.platform,
        "description_raw": req.jd_text,
        "required_skills": [],   # will be populated after skill extraction
        "preferred_skills": [],
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@job_api_router.get("/health")
def health() -> dict:
    """Liveness check — no auth required."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@job_api_router.post("/evaluate", response_model=JobEvalResponse)
def evaluate_job(req: JobEvalRequest) -> JobEvalResponse:
    """Run Gates 0-4 on a single job description.

    Gate 0: Title relevance (recruiter_screen.gate0_title_relevance)
    Gates 1-3: Skill/project pre-screening (SkillGraphStore.pre_screen_jd)
    Gate 4: JD quality + company background (gate4_quality.check_jd_quality)
    """
    # --- Import dependencies (optional — 503 if unavailable) ---
    try:
        from jobpulse.recruiter_screen import gate0_title_relevance
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"recruiter_screen unavailable: {exc}") from exc

    try:
        from jobpulse.skill_graph_store import SkillGraphStore
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"skill_graph_store unavailable: {exc}") from exc

    try:
        from jobpulse.gate4_quality import check_jd_quality
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"gate4_quality unavailable: {exc}") from exc

    try:
        from jobpulse.skill_extractor import extract_skills
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"skill_extractor unavailable: {exc}") from exc

    details: dict[str, Any] = {}

    # Gate 0 — title relevance
    try:
        # Use a permissive config so extension-submitted jobs aren't killed by
        # missing search-config; the extension has already decided the title is relevant.
        gate0_config: dict = {
            "titles": [req.title],
            "exclude_keywords": [],
        }
        gate0_passed = gate0_title_relevance(req.title, req.jd_text, gate0_config)
        details["gate0"] = {"passed": gate0_passed}
        if not gate0_passed:
            return JobEvalResponse(
                passed=False,
                score=0.0,
                tier="reject",
                gate_failed="gate0",
                details=details,
            )
    except Exception as exc:
        logger.error("evaluate_job: gate0 error for '%s': %s", req.title, exc)
        raise HTTPException(status_code=500, detail=f"Gate 0 error: {exc}") from exc

    # Skill extraction (needed for Gates 1-3 and Gate 4)
    try:
        extracted_skills: list[str] = extract_skills(req.jd_text)
    except Exception as exc:
        logger.warning("evaluate_job: skill extraction failed, using empty list: %s", exc)
        extracted_skills = []

    # Gate 4 — JD quality check (run before expensive Gates 1-3)
    try:
        gate4_result = check_jd_quality(req.jd_text, extracted_skills)
        details["gate4"] = {
            "passed": gate4_result.passed,
            "reason": gate4_result.reason,
            "skill_count": gate4_result.skill_count,
            "boilerplate_count": gate4_result.boilerplate_count,
        }
        if not gate4_result.passed:
            return JobEvalResponse(
                passed=False,
                score=0.0,
                tier="reject",
                gate_failed="gate4",
                details=details,
            )
    except Exception as exc:
        logger.error("evaluate_job: gate4 error for '%s': %s", req.title, exc)
        raise HTTPException(status_code=500, detail=f"Gate 4 error: {exc}") from exc

    # Gates 1-3 — skill/project pre-screening
    try:
        store = SkillGraphStore()
        listing = _build_listing_dict(req)
        listing["required_skills"] = extracted_skills
        prescreen = store.pre_screen_jd(listing)

        details["gates_1_3"] = {
            "tier": prescreen.tier,
            "matched_skills": prescreen.matched_skills,
            "missing_skills": prescreen.missing_skills,
            "gate1_passed": prescreen.gate1_passed,
            "gate1_kill_reason": prescreen.gate1_kill_reason,
            "gate2_passed": prescreen.gate2_passed,
            "gate2_fail_reason": prescreen.gate2_fail_reason,
            "best_projects": [
                (p if isinstance(p, dict) else {"name": str(p)})
                for p in (prescreen.best_projects or [])
            ],
        }

        if prescreen.tier == "reject":
            failed_gate = "gate1" if not prescreen.gate1_passed else "gate2"
            return JobEvalResponse(
                passed=False,
                score=0.0,
                tier="reject",
                gate_failed=failed_gate,
                details=details,
            )

        # Derive a score from match ratio (gates 1-3 produce a tier, not a numeric score)
        total = len(extracted_skills) or 1
        matched = len(prescreen.matched_skills or [])
        score = round(matched / total, 2)
        tier = prescreen.tier or "pass"

    except Exception as exc:
        logger.error("evaluate_job: gates 1-3 error for '%s': %s", req.title, exc)
        raise HTTPException(status_code=500, detail=f"Gates 1-3 error: {exc}") from exc

    return JobEvalResponse(
        passed=True,
        score=score,
        tier=tier,
        gate_failed=None,
        details=details,
    )


@job_api_router.post("/evaluate-batch", response_model=BatchEvalResponse)
def evaluate_batch(req: BatchEvalRequest) -> BatchEvalResponse:
    """Batch evaluate multiple jobs sequentially.

    LLM calls inside the gate pipeline are not cheap to parallelize, so jobs
    are processed one at a time.
    """
    results: list[JobEvalResponse] = []
    for job in req.jobs:
        try:
            result = evaluate_job(job)
        except HTTPException as exc:
            # Surface as a failed-gate result rather than aborting the whole batch
            result = JobEvalResponse(
                passed=False,
                score=0.0,
                tier="error",
                gate_failed="internal",
                details={"error": exc.detail},
            )
        results.append(result)
    return BatchEvalResponse(results=results)


@job_api_router.post("/generate-cv", response_model=GenerateCVResponse)
def generate_cv(req: GenerateCVRequest) -> GenerateCVResponse:
    """Generate CV PDF (and optionally a Cover Letter PDF) then upload to Google Drive."""
    try:
        from jobpulse.cv_templates.generate_cv import generate_cv_pdf
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"generate_cv unavailable: {exc}") from exc

    try:
        from jobpulse.drive_uploader import upload_cv, upload_cover_letter
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"drive_uploader unavailable: {exc}") from exc

    # --- Generate CV ---
    try:
        cv_path = generate_cv_pdf(
            company=req.company,
            location=req.location,
            projects=req.matched_projects or None,
            extra_skills=None,
        )
    except Exception as exc:
        logger.error("generate_cv: CV generation failed for '%s': %s", req.company, exc)
        raise HTTPException(status_code=500, detail=f"CV generation failed: {exc}") from exc

    # --- Upload CV to Drive ---
    try:
        cv_drive_url: str | None = upload_cv(cv_path, req.company)
    except Exception as exc:
        logger.warning("generate_cv: Drive upload failed for CV: %s", exc)
        cv_drive_url = None

    # --- Optional Cover Letter ---
    cl_path_str: str | None = None
    cl_drive_url: str | None = None

    if req.generate_cover_letter:
        try:
            from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
        except ImportError as exc:
            raise HTTPException(
                status_code=503, detail=f"generate_cover_letter unavailable: {exc}"
            ) from exc

        try:
            cl_path = generate_cover_letter_pdf(
                company=req.company,
                role=req.role,
                location=req.location,
                matched_projects=req.matched_projects or None,
                required_skills=req.required_skills or None,
            )
            cl_path_str = str(cl_path)
        except Exception as exc:
            logger.error(
                "generate_cv: CL generation failed for '%s': %s", req.company, exc
            )
            raise HTTPException(
                status_code=500, detail=f"Cover letter generation failed: {exc}"
            ) from exc

        try:
            from pathlib import Path as _Path
            cl_drive_url = upload_cover_letter(_Path(cl_path_str), req.company)
        except Exception as exc:
            logger.warning("generate_cv: Drive upload failed for CL: %s", exc)
            cl_drive_url = None

    return GenerateCVResponse(
        cv_path=str(cv_path),
        cv_drive_url=cv_drive_url,
        cl_path=cl_path_str,
        cl_drive_url=cl_drive_url,
    )


@job_api_router.post("/scan-reed", response_model=ScanResponse)
def scan_reed(req: ScanRequest) -> ScanResponse:
    """Scan the Reed API for jobs matching the given keywords and location."""
    try:
        from jobpulse.job_scanner import scan_reed as _scan_reed
        from jobpulse.models.application_models import SearchConfig
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"job_scanner unavailable: {exc}") from exc

    try:
        config = SearchConfig(titles=req.keywords, location=req.location)
        raw_jobs: list[dict] = _scan_reed(config)
    except Exception as exc:
        logger.error("scan_reed endpoint: scan failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Reed scan failed: {exc}") from exc

    jobs = [
        ScanJobItem(
            title=j.get("title", ""),
            company=j.get("company", ""),
            url=j.get("url", j.get("job_url", "")),
            location=j.get("location", ""),
            salary=str(j.get("salary", j.get("salary_min", ""))),
            platform="reed",
        )
        for j in raw_jobs
    ]
    return ScanResponse(jobs=jobs)


@job_api_router.post("/scan-linkedin", response_model=ScanResponse)
def scan_linkedin(req: ScanRequest) -> ScanResponse:
    """Scan LinkedIn guest API for jobs matching the given keywords and location."""
    try:
        from jobpulse.job_scanner import scan_linkedin as _scan_linkedin
        from jobpulse.models.application_models import SearchConfig
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"job_scanner unavailable: {exc}") from exc

    try:
        config = SearchConfig(titles=req.keywords, location=req.location)
        raw_jobs: list[dict] = _scan_linkedin(config)
    except Exception as exc:
        logger.error("scan_linkedin endpoint: scan failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"LinkedIn scan failed: {exc}") from exc

    jobs = [
        ScanJobItem(
            title=j.get("title", ""),
            company=j.get("company", ""),
            url=j.get("url", j.get("job_url", "")),
            location=j.get("location", ""),
            salary="",
            platform="linkedin",
        )
        for j in raw_jobs
    ]
    return ScanResponse(jobs=jobs)



@job_api_router.post("/notify", response_model=NotifyResponse)
def notify(req: NotifyRequest) -> NotifyResponse:
    """Send a Telegram notification via the jobs bot or the main bot."""
    if req.bot == "jobs":
        try:
            from jobpulse.telegram_bots import send_jobs
        except ImportError as exc:
            raise HTTPException(
                status_code=503, detail=f"telegram_bots unavailable: {exc}"
            ) from exc
        try:
            sent = send_jobs(req.message)
        except Exception as exc:
            logger.error("notify: send_jobs failed: %s", exc)
            raise HTTPException(status_code=500, detail=f"Telegram send failed: {exc}") from exc
    else:
        try:
            from jobpulse.telegram_agent import send_message
        except ImportError as exc:
            raise HTTPException(
                status_code=503, detail=f"telegram_agent unavailable: {exc}"
            ) from exc
        try:
            sent = send_message(req.message)
        except Exception as exc:
            logger.error("notify: send_message failed: %s", exc)
            raise HTTPException(status_code=500, detail=f"Telegram send failed: {exc}") from exc

    return NotifyResponse(sent=bool(sent))


@job_api_router.post("/apply", response_model=ApplyJobResponse)
def apply_job_endpoint(req: ApplyJobRequest) -> ApplyJobResponse:
    """Trigger a job application via the applicator.

    This is the endpoint the extension calls after a user approves a job
    in the side panel. It runs the full pipeline: rate limit → CV gen →
    apply_job → fill + submit via extension WebSocket.
    """
    from pathlib import Path

    from jobpulse.applicator import apply_job

    # Auto-generate CV if no path provided
    cv_path: Path | None = Path(req.cv_path) if req.cv_path else None
    if cv_path is None or not cv_path.exists():
        try:
            from jobpulse.cv_templates.generate_cv import generate_cv_pdf
            from jobpulse.project_portfolio import get_best_projects_for_jd
            from jobpulse.cv_templates.generate_cv import get_role_profile

            role_profile = get_role_profile(req.role or "Software Engineer")
            matched_projects = get_best_projects_for_jd([], [])
            cv_path = generate_cv_pdf(
                company=req.company or "Company",
                location="United Kingdom",
                tagline=role_profile.get("tagline"),
                summary=role_profile.get("summary"),
                projects=matched_projects,
            )
            logger.info("apply endpoint: auto-generated CV at %s", cv_path)
        except Exception as exc:
            logger.error("apply endpoint: CV generation failed: %s", exc)
            return ApplyJobResponse(success=False, error=f"CV generation failed: {exc}")

    cl_path: Path | None = Path(req.cover_letter_path) if req.cover_letter_path else None

    # Lazy CL generator for Greenhouse/Lever — only runs if adapter finds a CL field
    cl_generator = None
    if cl_path is None and req.company and req.role:
        def cl_generator():
            try:
                from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
                from jobpulse.project_portfolio import get_best_projects_for_jd
                matched = get_best_projects_for_jd([], [])
                return generate_cover_letter_pdf(
                    company=req.company,
                    role=req.role,
                    matched_projects=matched,
                    required_skills=[],
                )
            except Exception as exc:
                logger.warning("apply endpoint: lazy CL generation failed: %s", exc)
                return None

    try:
        result = apply_job(
            url=req.url,
            ats_platform=req.platform,
            cv_path=cv_path,
            cover_letter_path=cl_path,
            cl_generator=cl_generator,
            dry_run=req.dry_run,
        )
    except Exception as exc:
        logger.error("apply endpoint: apply_job failed: %s", exc)
        return ApplyJobResponse(success=False, error=str(exc))

    return ApplyJobResponse(
        success=result.get("success", False),
        error=result.get("error"),
        screenshot=result.get("screenshot"),
        pages_filled=result.get("pages_filled"),
        external_redirect=result.get("external_redirect", False),
        external_url=result.get("external_url"),
    )
