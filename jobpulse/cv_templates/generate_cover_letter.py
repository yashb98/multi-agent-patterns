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

import json
import os
import re
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
from jobpulse.cv_templates import sanitize_pdf as _sanitize_pdf
from shared.logging_config import get_logger

logger = get_logger(__name__)


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
    """Pad *points* to exactly 4 with generic education/certification entries."""
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
            "Collaboration & Communication:",
            "Experienced working in cross-functional teams with agile delivery practices.",
        ),
        (
            "Problem Solving & Adaptability:",
            "Track record of rapidly learning new frameworks and delivering under tight deadlines.",
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

    On any failure (network, bad JSON, missing key) the original *points*
    are returned unchanged — the PDF will still be generated.
    """
    from shared.agents import get_openai_client
    from jobpulse.utils.safe_io import safe_openai_call

    client = get_openai_client()

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

    raw = safe_openai_call(
        client,
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
        caller="polish_cover_letter_points",
    )

    if raw is None:
        return points

    try:
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        parsed = json.loads(cleaned)
        if not isinstance(parsed, list) or len(parsed) < 4:
            return points
        result = [(item["header"], item["detail"]) for item in parsed[:4]]
        return result
    except (json.JSONDecodeError, KeyError, TypeError):
        return points

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


IDENTITY = {
    "name": "Yash Bishnoi",
    "phone": "07909445288",
    "email": "bishnoiyash274@gmail.com",
    "linkedin": "https://linkedin.com/in/yash-bishnoi-2ab36a1a5",
    "github": "https://github.com/yashb98",
    "portfolio": "https://yashbishnoi.io",
}


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

    # Build dynamic points from matched projects if available
    if points is None and matched_projects and required_skills:
        points = build_dynamic_points(matched_projects, required_skills)
        try:
            points = polish_points_llm(points, role, company, required_skills)
        except Exception:
            pass  # Use unpolished points on LLM failure

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
    left.append(Paragraph(B(IDENTITY["name"]), name_s))
    left.append(Spacer(1, 10))
    left.append(Paragraph(B('Cover Letter'), title_s))
    left.append(Spacer(1, 5))
    left.append(HRFlowable(width="100%", thickness=4, spaceAfter=14, color=HexColor(ORANGE_RED)))
    left.append(Paragraph(location, contact_s))
    left.append(Paragraph(IDENTITY["phone"], contact_s))
    left.append(Paragraph(L(f'mailto:{IDENTITY["email"]}', 'Email'), contact_link_s))
    left.append(Spacer(1, 10))
    left.append(Paragraph(L(IDENTITY["linkedin"], 'LinkedIn'), contact_link_s))
    left.append(Paragraph(L(IDENTITY["github"], 'GitHub'), contact_link_s))
    left.append(Paragraph(L(IDENTITY["portfolio"], 'Portfolio'), contact_link_s))

    # ── RIGHT BODY ──
    right = []
    right.append(Paragraph('Dear Hiring Team,', greeting_s))

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
            f'I built a {B("54,500 LOC")} production system with {B("350 tests")} entirely using AI-native '
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
             f'I built a production system ({B("multi-agent-patterns")}, {B("54,500 LOC")}) using {B("Claude Code")} '
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
    right.append(Paragraph(B(IDENTITY["name"]), closing_name_s))

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
