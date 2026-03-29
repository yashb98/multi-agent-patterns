"""Generate tailored CV PDF in Arial with recruiter-optimised layout.

Usage:
    from jobpulse.cv_templates.generate_cv import generate_cv_pdf
    path = generate_cv_pdf(
        job_title="Junior Software Engineer",
        company="OakNorth",
        location="London, UK",
        projects=[...],
        extra_skills=["Claude Code", "Cursor"],
    )
"""

import os
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

from jobpulse.config import DATA_DIR
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Register Arial
_FONTS_REGISTERED = False

def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont('MyArial', '/System/Library/Fonts/Supplemental/Arial.ttf'))
    pdfmetrics.registerFont(TTFont('MyArialB', '/System/Library/Fonts/Supplemental/Arial Bold.ttf'))
    pdfmetrics.registerFont(TTFont('MyArialI', '/System/Library/Fonts/Supplemental/Arial Italic.ttf'))
    registerFontFamily('MyArial', normal='MyArial', bold='MyArialB', italic='MyArialI')
    _FONTS_REGISTERED = True


# ── Fixed identity (from Resume Prompt) ──

IDENTITY = {
    "name": "Yash Bishnoi",
    "phone": "07909445288",
    "email": "bishnoiyash274@gmail.com",
    "linkedin": "https://linkedin.com/in/yash-bishnoi-2ab36a1a5",
    "github": "https://github.com/yashb98",
    "portfolio": "https://yashbishnoi.io",
}

EDUCATION = [
    {
        "degree": "MSc Computer Science",
        "institution": "University of Dundee",
        "dates": "Jan 2025 - Jan 2026",
        "dissertation": "Deep Learning for Facial 3D Reconstruction - Simulator",
        "modules": "Machine Learning | Advanced Programming | Software Engineering | Database Systems | Web Development",
    },
    {
        "degree": "MBA (Finance)",
        "institution": "JECRC University",
        "dates": "2019 - 2021",
        "cgpa": "8.21/10",
    },
]

EXPERIENCE = [
    {
        "title": "Team Leader",
        "company": "Co-op",
        "dates": "Apr 2025 - Present",
        "bullets": [
            'Built <b>data-driven</b> scoring processes for inventory optimisation, shipping measurable impact with <b>20%</b> stockout reduction.',
            'Owned end-to-end operational decision-making at pace, translating shifting priorities into structured <b>data-informed</b> actions.',
            'Led a <b>team of 8</b> in a fast-paced environment, collaborating <b>cross-functionally</b> to align store performance with business targets.',
            'Communicated performance insights to area management, directly informing commercial and promotional strategy.',
        ],
    },
    {
        "title": "Market Research Analyst",
        "company": "Nidhi Herbal",
        "dates": "Jul 2021 - Sep 2024",
        "bullets": [
            'Built <b>Power BI</b> dashboards with <b>DAX</b> measures tracking pricing, sales trends, and revenue performance for senior management.',
            'Automated <b>SQL</b> and Excel ETL workflows using <b>Python</b> and openpyxl, cutting monthly report prep time by <b>35%</b>.',
            'Translated complex analytical outputs into actionable business recommendations for <b>cross-functional</b> commercial teams.',
            'Standardised data cleaning pipelines across three reporting streams, improving model input quality and reliability.',
        ],
    },
]

CERTIFICATIONS = [
    ("IBM Machine Learning", "July 2023", "https://www.coursera.org/account/accomplishments/specialization/certificate/SL9P2Q6Z43JP"),
    ("Deep Learning and Reinforcement Learning", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/S2MJH2ZQ8WF4"),
    ("Introduction to Model Context Protocol", "Feb 2026", "http://verify.skilljar.com/c/nn63q5jje52u"),
]

# Base skills (always included)
BASE_SKILLS = {
    "Languages:": "Python | SQL | JavaScript | TypeScript",
    "AI/ML:": "LangGraph | OpenAI API | Anthropic SDK | Claude Code | Cursor | Copilot | Codex | ChatGPT | PyTorch | Scikit-learn | Prompt Engineering | LLM Integration",
    "AI Methods:": "GRPO (Training-Free) | DSPy | GEPA | RLM | Persona Evolution | Experiential Learning | A/B Testing | RAG | GraphRAG | HNSW | NLP | Embeddings",
    "Data:": "SQLite | PostgreSQL | Apache Kafka | DuckDB | Pinecone | Pandas | NumPy | Knowledge Graphs | Entity Extraction",
    "Cloud/Infra:": "Docker | Kubernetes | GCP | AWS | Azure | FastAPI | Flask | Playwright | Redis | Git | GitHub | MLflow | Pydantic | Whisper | MCP",
    "APIs:": "REST API | Telegram Bot API | Notion API | Gmail API | Semantic Scholar API | GitHub API | Stripe | Twilio | Deepgram | ElevenLabs",
    "Practices:": "AI-Native Development | Rapid Prototyping | System Design | Process Optimisation | AI Governance | CI/CD | MLOps | Automation | Unit Testing",
}

# Default projects (can be overridden per application)
DEFAULT_PROJECTS = [
    {
        "title": "1. Multi-Agent Orchestration System | Python | LangGraph | Claude Code | OpenAI",
        "url": "https://github.com/yashb98/multi-agent-patterns",
        "bullets": [
            'Built a <b>54,500 LOC</b> production AI system using <b>Claude Code</b> as primary development tool, with <b>350 tests</b>, 215 files, and 5 databases.',
            'Designed 4 <b>LangGraph</b> orchestration patterns with <b>GRPO</b> experiential learning, <b>persona evolution</b>, and autonomous agent routing.',
            'Shipped <b>10+</b> daily automation agents (Gmail, Calendar, GitHub, Notion, Budget, arXiv) running 24/7 via Telegram with <b>NLP</b> 3-tier intent classification.',
            'Built multi-source <b>fact-checking</b> pipeline using <b>Semantic Scholar</b> API and <b>Playwright</b> browser automation with rate limiting.',
        ],
    },
    {
        "title": "2. LetsBuild - Autonomous Portfolio Factory | Python | Anthropic SDK | Docker",
        "url": "https://github.com/yashb98/LetsBuild",
        "bullets": [
            'Architected a 10-layer agentic pipeline using <b>Anthropic Claude SDK</b> with <b>tool_use</b> for guaranteed structured output.',
            'Implemented <b>Docker</b> sandbox management with compiled policy gates and self-learning <b>ReasoningBank</b> with <b>HNSW</b> vector search.',
            'Built <b>RLM</b> recursive language model synthesis for processing million-token contexts via sub-LM orchestration.',
        ],
    },
    {
        "title": "3. DataMind - AI-Native Analytics Platform | Python | FastAPI | Next.js | LangGraph",
        "url": "https://github.com/yashb98/DataMind",
        "bullets": [
            'Built an AI-native analytics platform with a <b>48-agent</b> Digital Labor Workforce for autonomous data ingestion, cleaning, and dashboard creation.',
            'Designed 8-layer anti-hallucination stack with <b>NLI</b> scoring, self-consistency, and chain-of-thought auditing.',
            'Implemented multi-cloud lakehouse architecture with <b>Apache Kafka</b>, <b>DuckDB</b>, and <b>Pinecone</b> for scalable data processing.',
        ],
    },
    {
        "title": "4. Velox AI - Enterprise AI Voice Agent Platform | TypeScript | FastAPI | Docker | GCP",
        "url": "https://github.com/yashb98/Velox_AI",
        "bullets": [
            'Deployed low-latency ML serving on <b>GCP</b> processing <b>1,000+</b> concurrent sessions at <b>sub-150ms</b> response times with 5-layer <b>RAG</b>.',
            'Built <b>monitoring</b> and alerting infrastructure tracking model performance, latency drift, and session health in production.',
            'Containerised full stack using <b>Docker</b> and <b>Kubernetes</b>-ready configs with <b>CI/CD</b> pipeline integration.',
        ],
    },
]


def generate_cv_pdf(
    company: str,
    location: str = "London, UK",
    tagline: str | None = None,
    summary: str | None = None,
    projects: list[dict] | None = None,
    extra_skills: dict[str, str] | None = None,
    output_dir: str | None = None,
) -> Path:
    """Generate a tailored CV PDF.

    Args:
        company: Company name (used in filename)
        location: Job location (shown in contact line)
        tagline: Custom tagline. Defaults to standard one.
        summary: Custom professional summary. Defaults to standard one.
        projects: Custom project list. Defaults to DEFAULT_PROJECTS.
        extra_skills: Additional skills to merge into BASE_SKILLS.
        output_dir: Directory for output. Defaults to data/applications/{company}/

    Returns:
        Path to generated PDF.
    """
    _register_fonts()

    F = 'MyArial'
    SZ = 10
    BLUE = '#0563C1'
    PW = A4[0] - 28 * mm
    LN = 13
    LW = 29 * mm

    name_s = ParagraphStyle('N', fontName=F, fontSize=22, alignment=TA_CENTER, spaceAfter=2, leading=26)
    tag_s = ParagraphStyle('T', fontName=F, fontSize=SZ, alignment=TA_CENTER, spaceAfter=2, leading=LN, textColor='#444444')
    contact_s = ParagraphStyle('C', fontName=F, fontSize=SZ, alignment=TA_CENTER, spaceAfter=6, leading=LN)
    sec_s = ParagraphStyle('S', fontName=F, fontSize=11, spaceBefore=8, spaceAfter=2, leading=14)
    body_s = ParagraphStyle('B', fontName=F, fontSize=SZ, spaceAfter=2, leading=LN)
    right_s = ParagraphStyle('R', fontName=F, fontSize=SZ, alignment=TA_RIGHT, spaceAfter=2, leading=LN)
    bullet_s = ParagraphStyle('Bu', fontName=F, fontSize=SZ, leftIndent=14, spaceAfter=1, leading=LN)
    sl = ParagraphStyle('SL', fontName=F, fontSize=SZ, leading=LN)
    sv = ParagraphStyle('SV', fontName=F, fontSize=SZ, leading=LN)
    sr = ParagraphStyle('Sr', fontName=F, fontSize=SZ, alignment=TA_RIGHT, spaceAfter=1, leading=LN)
    it = ParagraphStyle('I', fontName=F, fontSize=SZ, spaceAfter=1, leading=LN)

    def B(t): return f'<b>{t}</b>'
    def I(t): return f'<i>{t}</i>'
    def L(url, text): return f'<link href="{url}" color="{BLUE}"><u>{text}</u></link>'

    def section(text):
        el.append(Spacer(1, 4))
        el.append(Paragraph(B(text.upper()), sec_s))
        el.append(HRFlowable(width="100%", thickness=1, spaceAfter=4, color='#333333'))

    def bul(text):
        el.append(Paragraph(f'- {text}', bullet_s))

    def row(left, right, ls=body_s, rs=right_s, split=0.68):
        t = Table([[Paragraph(left, ls), Paragraph(right, rs)]], colWidths=[PW * split, PW * (1 - split)])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0),
                                ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
        el.append(t)

    def skill_row(label, vals):
        t = Table([[Paragraph(B(label), sl), Paragraph(vals, sv)]], colWidths=[LW, PW - LW])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 1.5),
                                ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5)]))
        el.append(t)

    # Setup output
    if output_dir:
        out = Path(output_dir)
    else:
        safe_company = company.replace(' ', '_').replace('/', '_')
        out = DATA_DIR / "applications" / safe_company
    out.mkdir(parents=True, exist_ok=True)

    safe_name = IDENTITY["name"].replace(' ', '_')
    safe_co = company.replace(' ', '_')
    cv_path = out / f"{safe_name}_{safe_co}.pdf"

    doc = SimpleDocTemplate(str(cv_path), pagesize=A4,
                            leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    el = []

    # Header
    el.append(Paragraph(B(IDENTITY["name"]), name_s))
    el.append(Spacer(1, 1))
    tag = tagline or 'MSc Computer Science (UOD) | Software Engineer | Python | AI/ML | Claude Code | System Design'
    el.append(Paragraph(tag, tag_s))
    el.append(Paragraph(
        f'{location} | {IDENTITY["phone"]} | '
        f'{L("mailto:" + IDENTITY["email"], "Email")} | '
        f'{L(IDENTITY["linkedin"], "LinkedIn")} | '
        f'{L(IDENTITY["github"], "GitHub")} | '
        f'{L(IDENTITY["portfolio"], "Portfolio")}', contact_s))

    # Summary
    section('Professional Summary')
    if summary:
        el.append(Paragraph(summary, body_s))
    else:
        el.append(Paragraph(
            f'{B("Software Engineer")} who built a {B("54,500 LOC")} production AI system with {B("350 tests")} using '
            f'{B("Claude Code")} as primary development tool. Researches and deploys emerging {B("AI tools")} '
            f'({B("Cursor")}, {B("Copilot")}, {B("Codex")}) into working systems used by real teams. '
            f'Specialises in {B("rapid prototyping")}, {B("Python")} {B("API integrations")}, and '
            f'{B("multi-agent orchestration")}. Ships work that translates technical capability into '
            f'measurable business value.', body_s))

    # Skills
    section('Technical Skills')
    skills = dict(BASE_SKILLS)
    if extra_skills:
        for k, v in extra_skills.items():
            if k in skills:
                skills[k] = skills[k] + ' | ' + v
            else:
                skills[k] = v
    for label, vals in skills.items():
        skill_row(label, vals)

    # Projects
    section('Projects')
    proj_list = projects or DEFAULT_PROJECTS
    for proj in proj_list:
        row(B(proj["title"]), L(proj["url"], '(Link)'), split=0.88)
        for b in proj["bullets"]:
            bul(b)
        el.append(Spacer(1, 3))

    # Experience
    section('Experience')
    for i, exp in enumerate(EXPERIENCE):
        if i > 0:
            el.append(Spacer(1, 3))
        row(B(exp["title"]), I(exp["dates"]))
        el.append(Paragraph(I(exp["company"]), it))
        for b in exp["bullets"]:
            bul(b)

    # Education
    section('Education')
    for i, edu in enumerate(EDUCATION):
        if i > 0:
            el.append(Spacer(1, 2))
        row(f'{B(edu["degree"])}, {edu["institution"]}', I(edu["dates"]))
        if "dissertation" in edu:
            el.append(Paragraph(f'{B("Dissertation:")} {edu["dissertation"]}', body_s))
        if "modules" in edu:
            el.append(Paragraph(f'{B("Core Modules:")} {edu["modules"]}', body_s))
        if "cgpa" in edu:
            el.append(Paragraph(f'CGPA: {edu["cgpa"]}', body_s))

    # Certifications
    section('Certifications')
    for cert, date, url in CERTIFICATIONS:
        row(cert, f'{date} - {L(url, "Verify")}', body_s, sr)

    doc.build(el)
    logger.info("CV generated: %s", cv_path)
    return cv_path
