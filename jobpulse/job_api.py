"""FastAPI routes for Chrome extension <-> Python communication.

All job pipeline logic (gate evaluation, CV generation, scanning)
exposed as HTTP endpoints. Extension calls these via fetch().
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from jobpulse.jd_analyzer import analyze_jd
from jobpulse.recruiter_screen import gate0_title_relevance
from jobpulse.skill_graph_store import SkillGraphStore
from jobpulse.gate4_quality import check_jd_quality
from jobpulse.job_scanner import scan_reed, scan_linkedin
from jobpulse.models.application_models import SearchConfig
from shared.logging_config import get_logger

logger = get_logger(__name__)

job_api_router = APIRouter(prefix="/api/job")

# --- Lazy singletons ---

_store: SkillGraphStore | None = None


def _get_store() -> SkillGraphStore:
    global _store
    if _store is None:
        _store = SkillGraphStore()
    return _store


# --- Request/Response Models ---


class EvaluateRequest(BaseModel):
    url: str
    title: str
    company: str
    platform: str
    jd_text: str
    apply_url: str = ""


class EvaluateResponse(BaseModel):
    passed: bool
    score: float = 0
    tier: str = "reject"
    gate_failed: str | None = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    details: str = ""


class BatchEvaluateRequest(BaseModel):
    jobs: list[EvaluateRequest]


class ScanRequest(BaseModel):
    titles: list[str]
    location: str = "United Kingdom"


class GenerateCVRequest(BaseModel):
    company: str
    role: str
    location: str = ""
    matched_projects: list[str] = []
    required_skills: list[str] = []
    generate_cover_letter: bool = False


class NotifyRequest(BaseModel):
    message: str
    bot: str = "jobs"


class RalphLearnRequest(BaseModel):
    platform: str
    url: str
    step_name: str
    error_signature: str
    fix_type: str
    fix_payload: dict = {}
    confidence: float = 0.5


# --- Endpoints ---


@job_api_router.get("/health")
def health():
    return {"status": "ok"}


@job_api_router.post("/evaluate", response_model=EvaluateResponse)
def evaluate_job(req: EvaluateRequest):
    """Run Gates 0-4A on a single job."""
    # Gate 0: title relevance
    config = {
        "titles": [
            "data scientist",
            "data analyst",
            "data engineer",
            "machine learning engineer",
            "ai engineer",
            "software engineer",
        ],
        "exclude_keywords": [],
    }
    if not gate0_title_relevance(req.title, req.jd_text, config):
        return EvaluateResponse(
            passed=False, gate_failed="gate0", details="Title filter rejected"
        )

    # Analyze JD
    listing = analyze_jd(
        url=req.url,
        title=req.title,
        company=req.company,
        platform=req.platform,
        jd_text=req.jd_text,
        apply_url=req.apply_url,
    )

    # Gates 1-3: skill match
    store = _get_store()
    screen = store.pre_screen_jd(listing)

    if not screen.gate1_passed:
        return EvaluateResponse(
            passed=False,
            gate_failed="gate1",
            tier="reject",
            details="Kill signal triggered",
        )
    if not screen.gate2_passed:
        return EvaluateResponse(
            passed=False,
            gate_failed="gate2",
            tier="skip",
            details="Must-have skills missing",
        )

    # Gate 4A: JD quality
    jd_quality = check_jd_quality(req.jd_text, listing.required_skills or [])
    if not jd_quality.passed:
        return EvaluateResponse(
            passed=False,
            gate_failed="gate4a",
            tier="skip",
            details=jd_quality.reason,
        )

    return EvaluateResponse(
        passed=True,
        score=screen.gate3_score,
        tier=screen.tier,
        matched_skills=list(screen.matched_skills),
        missing_skills=list(screen.missing_skills),
        details=screen.breakdown if hasattr(screen, "breakdown") else "",
    )


@job_api_router.post("/evaluate-batch")
def evaluate_batch(req: BatchEvaluateRequest):
    """Evaluate multiple jobs. Returns list of EvaluateResponse."""
    results = []
    for job in req.jobs:
        try:
            result = evaluate_job(job)
            results.append(result.model_dump())
        except Exception as e:
            logger.error("evaluate_batch: error on %s: %s", job.url, e)
            results.append(
                {"passed": False, "gate_failed": "error", "details": str(e)}
            )
    return {"results": results}


@job_api_router.post("/scan-reed")
def api_scan_reed(req: ScanRequest):
    """Run Reed API scan from Python."""
    config = SearchConfig(titles=req.titles, location=req.location)
    jobs = scan_reed(config)
    return {"jobs": jobs, "count": len(jobs)}


@job_api_router.post("/scan-linkedin")
def api_scan_linkedin(req: ScanRequest):
    """Run LinkedIn guest API scan from Python."""
    config = SearchConfig(titles=req.titles, location=req.location)
    jobs = scan_linkedin(config)
    return {"jobs": jobs, "count": len(jobs)}


@job_api_router.post("/generate-cv")
def api_generate_cv(req: GenerateCVRequest):
    """Generate CV (and optionally cover letter) PDFs."""
    from jobpulse.cv_templates.generate_cv import generate_cv_pdf

    cv_path = generate_cv_pdf(req.company, req.location)

    cl_path = None
    if req.generate_cover_letter:
        from jobpulse.cv_templates.generate_cover_letter import (
            generate_cover_letter_pdf,
        )

        cl_path = generate_cover_letter_pdf(
            req.company,
            req.role,
            req.matched_projects,
            req.required_skills,
        )

    return {
        "cv_path": str(cv_path),
        "cover_letter_path": str(cl_path) if cl_path else None,
    }


@job_api_router.post("/ralph-learn")
def api_ralph_learn(req: RalphLearnRequest):
    """Store a Ralph Loop learned fix pattern."""
    from jobpulse.ralph_loop.pattern_store import PatternStore
    from jobpulse.config import DATA_DIR

    store = PatternStore(str(DATA_DIR / "ralph_patterns.db"))
    store.save_fix(
        platform=req.platform,
        step_name=req.step_name,
        error_signature=req.error_signature,
        fix_type=req.fix_type,
        fix_payload=req.fix_payload,
        confidence=req.confidence,
    )
    return {"status": "saved"}


@job_api_router.post("/notify")
def api_notify(req: NotifyRequest):
    """Send a Telegram notification."""
    from jobpulse.telegram_utils import send_telegram_message
    from jobpulse.config import TELEGRAM_JOBS_BOT_TOKEN, TELEGRAM_CHAT_ID

    token = TELEGRAM_JOBS_BOT_TOKEN
    send_telegram_message(req.message, token=token, chat_id=TELEGRAM_CHAT_ID)
    return {"status": "sent"}
