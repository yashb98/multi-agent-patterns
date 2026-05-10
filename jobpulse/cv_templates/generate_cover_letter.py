"""Generate tailored Cover Letter PDF with two-column sidebar layout.

Uses Raleway Bold (name/title), Spectral (body), Lato (contact) with
orange-red accent color matching the template.

Usage:
    from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
    path = generate_cover_letter_pdf(
        company="OakNorth",
        role="Junior Software Engineer (AI Native Pod)",
        location="London, UK",
        points=[("Skill:", "Experience detail"), ...],
    )
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

from jobpulse.config import DATA_DIR
from jobpulse.cv_templates import build_applicant_identity, get_project_stats, sanitize_pdf as _sanitize_pdf
from shared.db_observability import observe_lookup
from shared.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Cover-letter polish cache (cache-llm-S5)
# ---------------------------------------------------------------------------

_COVER_LETTER_CACHE_TTL_DAYS = 30
_COVER_LETTER_CACHE_LOCK = threading.Lock()


def _cover_letter_inputs_hash(
    role: str, company: str, required_skills: list[str],
    points: list[tuple[str, str]],
) -> str:
    """16-char hash of every input that affects polish_points_llm output.

    Includes the deterministic input points (built from the profile +
    matched_projects upstream) so a profile/project change invalidates
    the cache automatically. Skill list is sorted for order-independence.
    """
    payload = {
        "role": (role or "").strip().lower(),
        "company": (company or "").strip().lower(),
        "required": sorted([s for s in (required_skills or [])][:8]),
        "points": [[h or "", d or ""] for h, d in (points or [])],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cover_letter_role_archetype(role: str) -> str:
    """Mirrors `screening_answers._classify_role_archetype` /
    `cv_tailor._classify_role_archetype` for cross-cluster consistency."""
    if not role:
        return "generic"
    t = role.lower().strip()
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


def _cover_letter_cache_init(db) -> None:
    """Lazily create cover_letter_cache table inside applications.db.

    Schema mirrors hiring_message_cache (S3) and tailored_cv_cache (S4):
    primary key is the (company, role_archetype, inputs_hash) tuple,
    payload is the JSON-encoded list[(header, detail)] tuples.
    """
    conn = db._connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cover_letter_cache ("
        "company TEXT NOT NULL, role_archetype TEXT NOT NULL, "
        "inputs_hash TEXT NOT NULL, payload TEXT NOT NULL, "
        "generated_at TEXT NOT NULL, hit_count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (company, role_archetype, inputs_hash))"
    )
    conn.commit()


@observe_lookup("applications", "cover_letter_cache", key_arg=0)
def _cover_letter_cache_lookup(
    company: str, role_archetype: str, inputs_hash: str, *, db=None,
) -> "list[tuple[str, str]] | None":
    """Return cached polished points or None on miss / TTL expiry.

    Under ``JOBPULSE_TEST_MODE=1`` (set by ``tests/conftest.py``) with
    default ``db=None``, short-circuits to None — same guard as
    `cv_tailor._tailored_cv_cache_lookup`.
    """
    if not (company and role_archetype and inputs_hash):
        return None
    if db is None and os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return None
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    key = (company.lower().strip(), role_archetype.lower().strip(), inputs_hash)
    with _COVER_LETTER_CACHE_LOCK:
        _cover_letter_cache_init(db)
        conn = db._connect()
        row = conn.execute(
            "SELECT payload, generated_at FROM cover_letter_cache "
            "WHERE company = ? AND role_archetype = ? AND inputs_hash = ?",
            key,
        ).fetchone()
        if not row:
            return None
        try:
            generated = datetime.fromisoformat(row["generated_at"])
            if (datetime.now() - generated).days > _COVER_LETTER_CACHE_TTL_DAYS:
                return None
        except (ValueError, TypeError):
            return None
        try:
            payload = json.loads(row["payload"])
            points = [tuple(p) for p in payload]
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.debug("cover_letter_cache: payload parse failed: %s", exc)
            return None
        conn.execute(
            "UPDATE cover_letter_cache SET hit_count = hit_count + 1 "
            "WHERE company = ? AND role_archetype = ? AND inputs_hash = ?",
            key,
        )
        conn.commit()
        return points


def _cover_letter_cache_store(
    company: str, role_archetype: str, inputs_hash: str,
    points: list[tuple[str, str]], *, db=None,
) -> None:
    """Persist freshly-polished points.

    Under ``JOBPULSE_TEST_MODE=1`` with default ``db=None``, no-op."""
    if not (company and role_archetype and inputs_hash) or not points:
        return
    if db is None and os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    payload = json.dumps([[h, d] for h, d in points], ensure_ascii=False)
    key = (company.lower().strip(), role_archetype.lower().strip(), inputs_hash)
    with _COVER_LETTER_CACHE_LOCK:
        _cover_letter_cache_init(db)
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO cover_letter_cache "
            "(company, role_archetype, inputs_hash, payload, generated_at, hit_count) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (*key, payload, datetime.now().isoformat()),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Dynamic point generation
# ---------------------------------------------------------------------------

_METRIC_RE = re.compile(r'\d+[%$£]|\d{2,}|\$\d|\£\d')


def build_dynamic_points(
    matched_projects: list[dict],
    required_skills: list[str],
) -> list[tuple[str, str]]:
    """Build 4 cover-letter points from matched projects + required skills.

    Each point header lists the JD skills the project demonstrates; the detail
    is the first bullet containing a metric (falls back to first bullet).

    If fewer than 4 projects are provided, generic education/cert points pad
    the list to exactly 4.
    """
    if not matched_projects:
        # Fall through to default points in the PDF generator
        return _default_pad_points([])

    points: list[tuple[str, str]] = []
    skills_lower = [s.lower() for s in required_skills]

    for proj in matched_projects[:4]:
        bullets: list[str] = proj.get("bullets", [])
        title: str = proj.get("title", "Project")

        # Find which required skills overlap with this project's bullets
        combined_text = " ".join(bullets).lower() + " " + title.lower()
        overlapping = [s for s, sl in zip(required_skills, skills_lower) if sl in combined_text]

        header = ", ".join(overlapping[:4]) + ":" if overlapping else f"{title}:"

        # Prefer a bullet with a metric
        detail = ""
        for b in bullets:
            if _METRIC_RE.search(b):
                detail = b
                break
        if not detail and bullets:
            detail = bullets[0]
        if not detail:
            detail = f"Built {title} demonstrating production-grade engineering."

        points.append((header, detail))

    return _default_pad_points(points)


def _default_pad_points(points: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Pad *points* to exactly 4 with generic technical entries.

    Headers intentionally avoid the soft-skill words that `cv_tailor`'s
    validator forbids (collaboration, communication, problem solving,
    adaptability) so the cover-letter padding doesn't ship language the CV
    pipeline is rejecting.
    """
    pad = [
        (
            "Education & Continuous Learning:",
            "MSc Computer Science with focus on AI/ML systems and software engineering.",
        ),
        (
            "Certifications & Self-Study:",
            "Completed advanced courses in cloud architecture, DevOps, and distributed systems.",
        ),
        (
            "Production Engineering:",
            "Shipped systems with CI/CD, monitoring, and rate-limited automation in real deployments.",
        ),
        (
            "Rapid Delivery:",
            "Track record of shipping new frameworks end-to-end under tight deadlines.",
        ),
    ]
    idx = 0
    while len(points) < 4 and idx < len(pad):
        points.append(pad[idx])
        idx += 1
    return points[:4]


def polish_points_llm(
    points: list[tuple[str, str]],
    role: str,
    company: str,
    required_skills: list[str],
) -> list[tuple[str, str]]:
    """Optionally refine deterministic points with GPT-5o-mini.

    Wraps the LLM call in a `(company, role_archetype, inputs_hash)` cache
    so a re-application to the same JD with the same input points skips
    the LLM entirely. Cache miss runs the LLM and stores the polished
    output on success; LLM failures (None / malformed / <4 items) return
    the unpolished points and do NOT poison the cache.
    """
    # Cache lookup: same role/company/skills/points → cached refinement.
    role_archetype = _cover_letter_role_archetype(role)
    inputs_hash = _cover_letter_inputs_hash(role, company, required_skills, points)
    cached = _cover_letter_cache_lookup(company, role_archetype, inputs_hash)
    if cached is not None:
        logger.info(
            "cover_letter_cache: hit on (%s, %s, %s) — skipping LLM polish",
            (company or "")[:40], role_archetype[:30], inputs_hash[:8],
        )
        return cached

    # Route through CognitiveEngine (default-on) for creative refinement
    from shared.agents import cognitive_llm_call

    formatted = json.dumps(
        [{"header": h, "detail": d} for h, d in points],
        indent=2,
    )

    prompt = (
        f"You are writing 4 numbered points for a cover letter for {role} at {company}. "
        f"Key skills: {', '.join(required_skills[:8])}.\n\n"
        f"Refine these points to sound more professional and tailored. "
        f"Keep ALL metrics and numbers exactly as they are. "
        f"Return ONLY a JSON array of objects with \"header\" and \"detail\" keys.\n\n"
        f"{formatted}"
    )

    raw = cognitive_llm_call(
        task=prompt,
        domain="cover_letter",
        stakes="medium",
    )

    if raw is None:
        return points

    # Reuse the shared LLM-JSON helper so we get the same robustness
    # (markdown fences, prose prefixes, single-key-dict unwrapping) as
    # cv_tailor without re-implementing it here.
    from jobpulse.cv_tailor import _parse_llm_json

    try:
        parsed = _parse_llm_json(raw)
        if not isinstance(parsed, list) or len(parsed) < 4:
            return points
        result = [(item["header"], item["detail"]) for item in parsed[:4]]
    except (json.JSONDecodeError, KeyError, TypeError):
        return points

    # Cache only when the LLM returned 4 well-formed points. Malformed
    # output already short-circuited above; no risk of caching a bad
    # refinement.
    try:
        _cover_letter_cache_store(company, role_archetype, inputs_hash, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cover_letter_cache: store failed (continuing): %s", exc)

    return result

# Template colors
ORANGE_RED = '#f2511b'
BURNT_ORANGE = '#d44500'

# Font paths
FONT_DIR = DATA_DIR / "fonts"

_FONTS_REGISTERED = False

def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont('Raleway', str(FONT_DIR / 'Raleway-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('RalewayBold', str(FONT_DIR / 'Raleway-Bold.ttf')))
    registerFontFamily('Raleway', normal='Raleway', bold='RalewayBold')
    pdfmetrics.registerFont(TTFont('Spectral', str(FONT_DIR / 'Spectral-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('SpectralBold', str(FONT_DIR / 'Spectral-Bold.ttf')))
    registerFontFamily('Spectral', normal='Spectral', bold='SpectralBold')
    pdfmetrics.registerFont(TTFont('Lato', str(FONT_DIR / 'Lato-Regular.ttf')))
    _FONTS_REGISTERED = True


def generate_cover_letter_pdf(
    company: str,
    role: str,
    location: str = "London, UK",
    intro: str | None = None,
    hook: str | None = None,
    points: list[tuple[str, str]] | None = None,
    closing: str | None = None,
    output_dir: str | None = None,
    matched_projects: list[dict] | None = None,
    required_skills: list[str] | None = None,
) -> Path:
    """Generate a tailored Cover Letter PDF.

    Args:
        company: Company name
        role: Job title
        location: Job location (shown in sidebar)
        intro: Opening paragraph. Auto-generated if None.
        hook: Second paragraph hook. Auto-generated if None.
        points: List of (header, detail) tuples for the 4 numbered reasons.
        closing: Closing paragraph. Auto-generated if None.
        output_dir: Output directory. Defaults to data/applications/{company}/
        matched_projects: Project dicts with title/url/bullets from project_portfolio.
        required_skills: Skills extracted from the JD.

    Returns:
        Path to generated PDF.
    """
    _register_fonts()
    identity = build_applicant_identity()

    # Build dynamic points from matched projects if available
    if points is None and matched_projects and required_skills:
        points = build_dynamic_points(matched_projects, required_skills)
        try:
            points = polish_points_llm(points, role, company, required_skills)
        except Exception as exc:
            # Don't swallow silently — polish failure means the generic
            # deterministic points ship instead of LLM-refined ones, which is
            # an operator-visible quality drop. Same shape as scan_pipeline
            # CV tailoring (S8 audit M-B). Non-blocking.
            logger.warning(
                "generate_cover_letter: polish_points_llm failed for %s — "
                "using unpolished deterministic points: %s",
                company, exc, exc_info=True,
            )

    # Build dynamic intro from matched projects
    if intro is None and matched_projects:
        project_names = ", ".join(p["title"] for p in matched_projects[:3])
        intro = (
            f'I am writing to express my strong interest in the <b>{role}</b> '
            f'role at <b>{company}</b>. My portfolio includes projects directly relevant '
            f'to your requirements, including {project_names}.'
        )

    # Build dynamic hook from required skills
    if hook is None and required_skills:
        skill_str = ", ".join(f'<b>{s}</b>' for s in required_skills[:5])
        hook = (
            f'With hands-on experience in {skill_str}, I have built production systems '
            f'that demonstrate these skills with measurable impact.'
        )

    LEFT_COL_W = 33 * mm
    GAP = 5 * mm
    RIGHT_COL_W = A4[0] - LEFT_COL_W - GAP - 28 * mm

    name_s = ParagraphStyle('Name', fontName='RalewayBold', fontSize=18, leading=22)
    title_s = ParagraphStyle('Title', fontName='RalewayBold', fontSize=14, leading=18, spaceBefore=6, textColor=ORANGE_RED)
    contact_s = ParagraphStyle('Contact', fontName='Lato', fontSize=9.5, leading=14, spaceBefore=3, textColor=BURNT_ORANGE)
    contact_link_s = ParagraphStyle('CL', fontName='Lato', fontSize=9.5, leading=14, spaceBefore=3)
    body_s = ParagraphStyle('Body', fontName='Spectral', fontSize=10, leading=13.5, spaceAfter=8)
    point_s = ParagraphStyle('Point', fontName='Spectral', fontSize=10, leading=13.5, spaceAfter=10)
    greeting_s = ParagraphStyle('Greet', fontName='Spectral', fontSize=10, leading=13.5, spaceAfter=10)
    closing_name_s = ParagraphStyle('CN', fontName='SpectralBold', fontSize=10, leading=13.5, textColor=BURNT_ORANGE)
    closing_s = ParagraphStyle('Close', fontName='Spectral', fontSize=10, leading=13.5, spaceAfter=4)

    def B(t): return f'<b>{t}</b>'
    def L(url, text, color=BURNT_ORANGE): return f'<link href="{url}" color="{color}"><u>{text}</u></link>'

    # ── LEFT SIDEBAR ──
    left = []
    left.append(Paragraph(B(identity["name"]), name_s))
    left.append(Spacer(1, 10))
    left.append(Paragraph(B('Cover Letter'), title_s))
    left.append(Spacer(1, 5))
    left.append(HRFlowable(width="100%", thickness=4, spaceAfter=14, color=HexColor(ORANGE_RED)))
    left.append(Paragraph(location, contact_s))
    left.append(Paragraph(identity["phone"], contact_s))
    left.append(Paragraph(L(f'mailto:{identity["email"]}', 'Email'), contact_link_s))
    left.append(Spacer(1, 10))
    left.append(Paragraph(L(identity["linkedin"], 'LinkedIn'), contact_link_s))
    left.append(Paragraph(L(identity["github"], 'GitHub'), contact_link_s))
    left.append(Paragraph(L(identity["portfolio"], 'Portfolio'), contact_link_s))

    # ── RIGHT BODY ──
    right = []
    right.append(Paragraph('Dear Hiring Team,', greeting_s))

    _s = get_project_stats()
    _loc = _s.get("loc_display", "142,500+")
    _tests = _s.get("tests_display", "3,350+")

    # Intro
    if intro:
        right.append(Paragraph(intro, body_s))
    else:
        right.append(Paragraph(
            f'I am writing to express my strong interest in the {B(role)} '
            f'role at {B(company)}. With hands-on experience building production AI systems using '
            f'{B("Claude Code")}, I am enthusiastic about contributing to {company}\'s mission '
            f'through AI-native engineering.', body_s))

    # Hook
    if hook:
        right.append(Paragraph(hook, body_s))
    else:
        right.append(Paragraph(
            f'I built a {B(_loc + " LOC")} production system with {B(_tests + " tests")} entirely using AI-native '
            f'development practices. This is not theoretical interest - it is how I work every day.', body_s))

    right.append(Paragraph(
        'I have read the job description and feel that I\'m a great fit due to the following reasons:', body_s))

    # 4 numbered points
    if points:
        for i, (header, detail) in enumerate(points, 1):
            right.append(Paragraph(f'{i}. {B(header)} {detail}', point_s))
    else:
        default_points = [
            ("AI Coding Tools (Claude Code, Cursor, Copilot):",
             f'I built a production system ({B("multi-agent-patterns")}, {B(_loc + " LOC")}) using {B("Claude Code")} '
             f'as my primary development tool.'),
            ("Python and API Integrations:",
             f'Every project uses {B("Python")} with production APIs: {B("Anthropic SDK")}, {B("OpenAI API")}, '
             f'Telegram, Notion, Gmail. I shipped {B("10+")} autonomous agents running 24/7.'),
            ("Rapid Prototyping and Deployment:",
             f'My {B("LetsBuild")} project is an autonomous 10-layer pipeline using agentic architectures '
             f'and {B("Docker")} sandboxing.'),
            ("System Design and AI Governance:",
             f'I designed compiled policy gates, multi-source fact-checking with honest scoring, and '
             f'rate-limited automation with human-in-the-loop approval flows.'),
        ]
        for i, (header, detail) in enumerate(default_points, 1):
            right.append(Paragraph(f'{i}. {B(header)} {detail}', point_s))

    # Closing
    if closing:
        right.append(Paragraph(closing, body_s))
    else:
        right.append(Paragraph(
            f'With an MSc in Computer Science and daily hands-on experience building AI-native systems, I '
            f'possess a deep understanding of what it takes to ship AI tools into production. My blend of '
            f'technical depth, rapid execution, and genuine passion positions me as an ideal fit for the '
            f'dynamic environment at {B(company)}.', body_s))

    right.append(Paragraph(
        f'Once again, thank you for considering my application. I look forward to discussing how my '
        f'skills and experiences align with {company}\'s goals.', body_s))

    right.append(Spacer(1, 8))
    right.append(Paragraph('Best regards,', closing_s))
    right.append(Paragraph(B(identity["name"]), closing_name_s))

    # ── BUILD ──
    if output_dir:
        out = Path(output_dir)
    else:
        safe_company = company.replace(' ', '_').replace('/', '_')
        out = DATA_DIR / "applications" / safe_company
    out.mkdir(parents=True, exist_ok=True)

    safe_co = company.replace(' ', '_')
    cl_path = out / f"Cover_Letter_{safe_co}.pdf"

    doc = SimpleDocTemplate(str(cl_path), pagesize=A4,
                            leftMargin=10 * mm, rightMargin=14 * mm, topMargin=20 * mm, bottomMargin=15 * mm)

    layout = Table([[left, right]], colWidths=[LEFT_COL_W + 2 * mm, GAP + RIGHT_COL_W])
    layout.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 4),
        ('LEFTPADDING', (1, 0), (1, 0), 8),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LINEAFTER', (0, 0), (0, -1), 0.75, HexColor('#cccccc')),
    ]))

    doc.build([layout])

    _sanitize_pdf(cl_path)
    logger.info("Cover letter generated: %s", cl_path)
    return cl_path
