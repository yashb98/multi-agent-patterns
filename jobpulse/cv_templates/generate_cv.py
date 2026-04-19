"""Generate tailored CV PDF in Arial with professional layout.

Matches the reference design: teal section headers, clean spacing,
proper bullet alignment, 5 skill categories, 8 certifications,
Community & Leadership section, References.

Usage:
    from jobpulse.cv_templates.generate_cv import generate_cv_pdf
    path = generate_cv_pdf(company="OakNorth", location="London, UK")
"""

import os
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

from jobpulse.config import DATA_DIR
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Colors (extracted from reference)
# ---------------------------------------------------------------------------

HEADER_COLOR = '#1a5276'   # Dark teal for section headers
LINK_COLOR = '#0563C1'     # Blue for hyperlinks
TEXT_COLOR = '#1a1a1a'     # Near-black for body text
SUB_COLOR = '#444444'      # Gray for tagline


# ---------------------------------------------------------------------------
# Fixed identity
# ---------------------------------------------------------------------------

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
        "dissertation_url": "https://github.com/yashb98/Deep-Learning-for-Facial-3D-Reconstruction---Simulator",
        "modules": "Machine Learning | Advanced Programming Techniques | Design Methods | Software Engineering | Software Development | Web Development | Database Systems",
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
            '<b>Led</b> a team of 8, coordinating shift operations through clear <b>communication</b> and collaborative <b>decision making</b> under time pressure.',
            'Developed <b>forecasting</b> processes for stock replenishment, reducing stockout incidents by <b>20%</b> through data-driven decisions and <b>adaptability</b> to demand shifts.',
            'Automated analysis of sales trends and merchandising KPIs, demonstrating <b>creativity</b> in identifying patterns that drive operational impact.',
            'Delivered <b>actionable insights</b> to area management, bridging data analysis with commercial execution through strong <b>stakeholder communication</b>.',
        ],
    },
    {
        "title": "Market Research Analyst",
        "company": "Nidhi Herbal",
        "dates": "Jul 2021 - Sep 2024",
        "bullets": [
            'Built <b>Power BI</b> dashboards with <b>DAX</b> enabling real-time <b>insights</b> into sales performance, supplier ROI, and trend analysis.',
            'Automated <b>SQL</b> and Excel ETL workflows using <b>Python</b> and openpyxl, cutting monthly report prep time by <b>35%</b>.',
            'Collaborated across cross-functional teams, translating complex <b>analytical findings</b> into clear recommendations through effective <b>teamwork</b> and <b>presentation skills</b>.',
            'Applied <b>critical thinking</b> to identify correlations within large, messy supplier and market datasets, driving strategic <b>decision making</b> for business growth.',
        ],
    },
]

CERTIFICATIONS = [
    ("1. IBM Machine Learning", "July 2023", "https://www.coursera.org/account/accomplishments/specialization/certificate/SL9P2Q6Z43JP"),
    ("2. SQL Essential Learning", "September 2023", "https://www.linkedin.com/learning/certificates/sql-essential"),
    ("3. Feature Engineering", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/feature-engineering"),
    ("4. Data Cleaning", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/data-cleaning"),
    ("5. Exploratory Data Analysis for Machine Learning", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/eda-ml"),
    ("6. Deep Learning and Reinforcement Learning", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/S2MJH2ZQ8WF4"),
    ("7. Introduction to Model Context Protocol", "Feb 2026", "https://verify.skilljar.com/c/nn63q5jje52u"),
]

COMMUNITY = [
    {
        "title": "Quackathon 2025 Participant.",
        "text": "Built a data-driven prototype in 4 hours with a cross-functional team, demonstrating <b>adaptability</b>, <b>teamwork</b>, and rapid <b>decision making</b> under extreme time pressure.",
    },
    {
        "title": "Friends International, Dundee Chapter.",
        "text": "Led community initiatives supporting international students, strengthening <b>leadership</b>, cross-cultural <b>collaboration</b>, and <b>communication</b> with diverse stakeholders.",
    },
    {
        "title": "Peer Mentor for Coding Challenges.",
        "text": "Mentored peers on Python, SQL, and statistical analysis, demonstrating <b>communication</b> skills by distilling complex technical concepts into clear, digestible guidance.",
    },
]

# Base skills — 5 clean categories
BASE_SKILLS = {
    "Languages:": "Python | SQL | JavaScript | TypeScript",
    "AI/ML:": "NLP | Text Analysis | Clustering | Scikit-learn | PyTorch | TensorFlow | Pandas | NumPy | LangChain | Hugging Face | RAG",
    "DevOps:": "Docker | Kubernetes | AWS | GCP | Azure | CI/CD | GitHub Actions | Terraform | Linux",
    "BI/Tools:": "Power BI | DAX | Looker | Excel | APIs | FastAPI | Flask | Git | GitHub | Web Scraping",
    "Practices:": "Statistical Testing | Forecasting | Dashboards | Data Modelling | EDA | Data Cleaning | MLOps | A/B Testing | Documentation",
}

# Default projects
DEFAULT_PROJECTS = [
    {
        "title": "1. Velox AI - Enterprise AI Voice Agent Platform | Python | FastAPI | Docker | GCP",
        "url": "https://github.com/yashb98/Velox_AI",
        "bullets": [
            'Built real-time <b>dashboards</b> tracking performance metrics across <b>1,000+</b> concurrent sessions, reducing latency anomaly detection time by <b>70%</b>.',
            'Automated <b>analysis</b> of <b>50K+</b> daily session metrics via <b>API</b>-driven data pipelines, cutting manual reporting effort by <b>80%</b>.',
            'Identified <b>performance patterns</b> through statistical analysis on GCP, delivering <b>sub-150ms</b> response times at scale.',
        ],
    },
    {
        "title": "2. Cloud Sentinel - AI Powered Cloud Security Platform with Python, React, Docker, Redis, Pinecone",
        "url": "https://github.com/yashb98/nexusmind",
        "bullets": [
            'Built <b>NLP</b> and <b>text-based analysis</b> pipelines extracting insights from <b>10K+</b> unstructured documents with <b>94%</b> retrieval precision.',
            'Developed <b>clustering</b> and classification workflows grouping <b>500+</b> policy documents by topic, risk level, and compliance status.',
            'Built <b>dashboards</b> surfacing audit results and compliance metrics, reducing manual review time by <b>55%</b>.',
            'Delivered clear, <b>actionable insights</b> from messy data sources, bridging data science with strategic compliance decisions.',
            'Optimised vector insertion pipeline, reducing indexing time by <b>60%</b> through batch processing and query tuning.',
        ],
    },
    {
        "title": "3. 90 Days Machine learning",
        "url": "https://github.com/yashb98/90Days_Machine_learinng",
        "bullets": [
            '<b>30+</b> projects spanning <b>NLP</b>, <b>web scraping</b>, <b>clustering</b>, <b>forecasting</b>, and statistical testing using <b>Python</b>, <b>SQL</b>, and Scikit-learn.',
            'Built <b>web scraping</b> pipelines collecting and structuring <b>100K+</b> records from multiple online sources using BeautifulSoup and Scrapy.',
            'Ran <b>statistical tests</b> (A/B testing, hypothesis validation, correlation analysis) across <b>15+</b> datasets, improving prediction accuracy by <b>12%</b>.',
            'Standardised <b>data cleaning</b> workflows across multi-source datasets, resolving format inconsistencies that impacted <b>40%</b> of raw inputs.',
        ],
    },
    {
        "title": "4. Deep Learning for Facial 3D Reconstructions with PyTorch and Computer Vision",
        "url": "https://github.com/yashb98/Deep-Learning-for-Facial-3D-Reconstruction---Simulator",
        "bullets": [
            'Built custom encoder-decoder in <b>PyTorch</b> achieving <b>0.89 SSIM</b> reconstruction quality, outperforming baseline by <b>15%</b>.',
            'Generated <b>10,000+</b> synthetic samples with automated <b>Python</b> pipelines, identifying spatial <b>patterns</b> across <b>3</b> coordinate systems.',
            'Presented findings to academic panel, translating complex model architectures into clear visual narratives for non-technical stakeholders.',
        ],
    },
]


# ---------------------------------------------------------------------------
# Role-adaptive tagline and summary
# ---------------------------------------------------------------------------

_ROLE_PROFILES: dict[str, dict[str, str]] = {
    "data scientist": {
        "tagline": "MSc Computer Science (UOD) | 2+ YOE | Data Scientist | Python | Machine Learning | SQL | NLP",
        "summary": (
            '<b>Data Scientist</b> with hands-on experience building production ML systems, '
            'statistical models, and data pipelines. Built a <b>88,500+ LOC</b> autonomous system '
            'with <b>2,350 tests</b> integrating ML-based classification, NLP pipelines, and '
            'experiential learning (GRPO). Specialises in <b>Python</b>, <b>SQL</b>, '
            '<b>machine learning</b>, and translating complex data into <b>actionable business insights</b>.'
        ),
    },
    "data analyst": {
        "tagline": "MSc Computer Science (UOD) | 3+ YOE | Data Analyst | Python | SQL | Power BI | Statistical Analysis",
        "summary": (
            '<b>Data Analyst</b> with experience building dashboards, automating ETL workflows, '
            'and delivering actionable insights. Built <b>Power BI</b> dashboards with <b>DAX</b> '
            'for real-time sales and supplier analysis. Automated <b>SQL</b> and <b>Python</b> '
            'data pipelines, cutting report prep time by <b>35%</b>. Specialises in '
            '<b>statistical testing</b>, <b>forecasting</b>, and <b>data-driven decision making</b>.'
        ),
    },
    "ml engineer": {
        "tagline": "MSc Computer Science (UOD) | 2+ YOE | ML Engineer | Python | PyTorch | MLOps | System Design",
        "summary": (
            '<b>ML Engineer</b> who built a <b>88,500+ LOC</b> production AI system with '
            '<b>2,350 tests</b> and <b>MLOps</b> pipelines. Designed multi-agent orchestration with '
            '<b>GRPO experiential learning</b> and <b>persona evolution</b>. Deployed '
            '<b>10+ autonomous agents</b> running 24/7 with rate-limited automation, '
            'compiled policy gates, and <b>Docker</b>-based sandboxing.'
        ),
    },
    "ai engineer": {
        "tagline": "MSc Computer Science (UOD) | 2+ YOE | AI Engineer | Python | LangChain | RAG | Multi-Agent Systems",
        "summary": (
            '<b>AI Engineer</b> who built a <b>88,500+ LOC</b> production multi-agent system with '
            '<b>4 LangGraph orchestration patterns</b>, <b>GRPO experiential learning</b>, and '
            '<b>RAG retrieval</b>. Shipped <b>10+ autonomous agents</b> with fact-checking, '
            'persona evolution, and human-in-the-loop approval flows. Specialises in '
            '<b>agentic architectures</b>, <b>tool-use</b>, and <b>production AI deployment</b>.'
        ),
    },
    "data engineer": {
        "tagline": "MSc Computer Science (UOD) | 2+ YOE | Data Engineer | Python | SQL | ETL | Airflow | Cloud",
        "summary": (
            '<b>Data Engineer</b> with hands-on experience building data pipelines, ETL workflows, '
            'and database systems. Built a <b>88,500+ LOC</b> autonomous system with '
            '<b>20 SQLite databases</b>, automated data ingestion, and scheduled processing. '
            'Specialises in <b>Python</b>, <b>SQL</b>, <b>pipeline orchestration</b>, '
            'and <b>scalable data infrastructure</b>.'
        ),
    },
    "software engineer": {
        "tagline": "MSc Computer Science (UOD) | 2+ YOE | Software Engineer | Python | System Design | APIs | Testing",
        "summary": (
            '<b>Software Engineer</b> who built a <b>88,500+ LOC</b> production system with '
            '<b>2,350 tests</b>, <b>RESTful APIs</b>, and multi-service architecture. '
            'Designed modular components with clean interfaces, comprehensive testing, '
            'and CI/CD automation. Specialises in <b>Python</b>, <b>system design</b>, '
            '<b>API development</b>, and <b>production-grade software delivery</b>.'
        ),
    },
}


_SOFT_SKILL_WORDS = {
    "communication", "teamwork", "leadership", "problem solving", "time management",
    "adaptability", "collaboration", "analytical thinking", "critical thinking",
    "stakeholder management", "mentoring", "coaching", "prioritization",
    "attention to detail", "self motivated", "fast learner", "customer focus",
    "decision making", "interviewing", "okrs", "presentation skills",
    "project management", "strategic thinking", "negotiation",
}


def build_extra_skills(required_skills: list[str], preferred_skills: list[str]) -> dict[str, str]:
    """Build extra skills line showing JD-relevant TECHNICAL skills not already in BASE_SKILLS.

    Filters out soft skills. Returns dict like {"Also proficient in:": "Spark | Databricks | ..."}.
    """
    # Flatten BASE_SKILLS values into a set (including common synonyms)
    base_set = set()
    _SYNONYMS = {
        "aws": "amazon web services", "amazon web services": "aws",
        "gcp": "google cloud platform", "google cloud platform": "gcp",
        "k8s": "kubernetes", "kubernetes": "k8s",
        "ml": "machine learning", "machine learning": "ml",
        "nlp": "natural language processing", "natural language processing": "nlp",
        "dl": "deep learning", "deep learning": "dl",
        "ci/cd": "continuous integration", "continuous integration": "ci/cd",
        "js": "javascript", "javascript": "js",
        "ts": "typescript", "typescript": "ts",
        "tf": "tensorflow", "tensorflow": "tf",
        "sklearn": "scikit-learn", "scikit-learn": "sklearn",
        "hf": "hugging face", "hugging face": "hf",
        "eda": "exploratory data analysis", "exploratory data analysis": "eda",
        "a/b testing": "ab testing", "ab testing": "a/b testing",
        "mlops": "ml ops", "ml ops": "mlops",
    }
    for vals in BASE_SKILLS.values():
        for s in vals.split(" | "):
            s_lower = s.strip().lower()
            base_set.add(s_lower)
            if s_lower in _SYNONYMS:
                base_set.add(_SYNONYMS[s_lower])

    base_set.update({
        "machine learning", "ai", "artificial intelligence", "deep learning",
        "data science", "data analysis", "statistical analysis", "statistics",
        "python programming", "sql querying",
    })

    # Find JD skills not in base and not soft skills
    all_jd = required_skills + preferred_skills
    extra = []
    seen = set()
    for skill in all_jd:
        skill_lower = skill.lower().strip()
        if (skill_lower not in base_set
                and skill_lower not in seen
                and skill_lower not in _SOFT_SKILL_WORDS
                and len(skill_lower) > 2):
            extra.append(skill.strip().title())
            seen.add(skill_lower)

    if not extra:
        return {}

    return {"Also proficient in:": " | ".join(extra[:12])}


def get_role_profile(role_title: str) -> dict[str, str]:
    """Match a JD role title to the best tagline + summary profile.

    Returns dict with 'tagline' and 'summary' keys, or empty dict if no match.
    """
    role_lower = role_title.lower()
    for key, profile in _ROLE_PROFILES.items():
        if key in role_lower:
            return profile
    # Fallback: check individual keywords
    if any(kw in role_lower for kw in ("data scien", "ds ", "machine learn")):
        return _ROLE_PROFILES["data scientist"]
    if any(kw in role_lower for kw in ("data analy", "bi ", "business intel")):
        return _ROLE_PROFILES["data analyst"]
    if any(kw in role_lower for kw in ("ml eng", "mlops")):
        return _ROLE_PROFILES["ml engineer"]
    if any(kw in role_lower for kw in ("ai eng", "llm", "agent")):
        return _ROLE_PROFILES["ai engineer"]
    if any(kw in role_lower for kw in ("data eng", "etl", "pipeline eng")):
        return _ROLE_PROFILES["data engineer"]
    if any(kw in role_lower for kw in ("software eng", "backend", "fullstack", "full stack")):
        return _ROLE_PROFILES["software engineer"]
    return {}


# ---------------------------------------------------------------------------
# PDF Generator
# ---------------------------------------------------------------------------


def generate_cv_pdf(
    company: str,
    location: str = "London, UK",
    tagline: str | None = None,
    summary: str | None = None,
    projects: list[dict] | None = None,
    extra_skills: dict[str, str] | None = None,
    output_dir: str | None = None,
) -> Path:
    """Generate a tailored CV PDF matching the reference design.

    Returns Path to generated PDF.
    """
    _register_fonts()

    if tagline:
        tagline = normalize_text_for_ats(tagline)[0]
    if summary:
        summary = normalize_text_for_ats(summary)[0]
    if projects:
        projects = [
            {**p, "title": normalize_text_for_ats(p["title"])[0],
             "bullets": [normalize_text_for_ats(b)[0] for b in p["bullets"]]}
            for p in projects
        ]
    if extra_skills:
        extra_skills = {k: normalize_text_for_ats(v)[0] for k, v in extra_skills.items()}

    F = 'MyArial'
    SZ = 9.5
    PW = A4[0] - 28 * mm
    LN = 11.5
    LW = 29 * mm

    # --- Styles (tight but readable) ---
    name_s = ParagraphStyle('N', fontName=F, fontSize=20, alignment=TA_CENTER,
                            spaceAfter=1, leading=24, textColor=TEXT_COLOR)
    tag_s = ParagraphStyle('T', fontName=F, fontSize=SZ, alignment=TA_CENTER,
                           spaceAfter=1, leading=LN, textColor=SUB_COLOR)
    contact_s = ParagraphStyle('C', fontName=F, fontSize=SZ, alignment=TA_CENTER,
                               spaceAfter=4, leading=LN, textColor=TEXT_COLOR)
    sec_s = ParagraphStyle('S', fontName=F, fontSize=11, spaceBefore=6, spaceAfter=1,
                           leading=13, textColor=HEADER_COLOR, leftIndent=0)
    body_s = ParagraphStyle('B', fontName=F, fontSize=SZ, spaceAfter=1,
                            leading=LN, textColor=TEXT_COLOR, alignment=TA_LEFT,
                            leftIndent=0)
    right_s = ParagraphStyle('R', fontName=F, fontSize=SZ, alignment=TA_RIGHT,
                             spaceAfter=1, leading=LN, textColor=TEXT_COLOR)
    bullet_s = ParagraphStyle('Bu', fontName=F, fontSize=SZ, leftIndent=8,
                              firstLineIndent=-8, spaceAfter=1, leading=LN,
                              textColor=TEXT_COLOR, alignment=TA_LEFT)
    sr = ParagraphStyle('Sr', fontName=F, fontSize=SZ, alignment=TA_RIGHT,
                        spaceAfter=0, leading=LN, textColor=TEXT_COLOR)
    it = ParagraphStyle('I', fontName=F, fontSize=SZ, spaceAfter=1,
                        leading=LN, textColor=TEXT_COLOR, leftIndent=0)
    center_s = ParagraphStyle('Cn', fontName=F, fontSize=SZ, alignment=TA_CENTER,
                              spaceAfter=1, leading=LN, textColor=LINK_COLOR)
    comm_s = ParagraphStyle('Co', fontName=F, fontSize=SZ, spaceAfter=2,
                            leading=LN, textColor=TEXT_COLOR, alignment=TA_LEFT)

    # --- Helpers ---
    def B(t): return f'<b>{t}</b>'
    def I(t): return f'<i>{t}</i>'
    def L(url, text):
        if not url:
            return text
        return f'<link href="{url}" color="{LINK_COLOR}"><u>{text}</u></link>'

    def section(text):
        el.append(Spacer(1, 3))
        el.append(Paragraph(B(text), sec_s))
        el.append(HRFlowable(width="100%", thickness=0.8, spaceAfter=3,
                              color=HexColor(HEADER_COLOR)))

    def bul(text):
        el.append(Paragraph(f'\u2022  {text}', bullet_s))

    def row(left, right, ls=body_s, rs=right_s, split=0.70):
        t = Table([[Paragraph(left, ls), Paragraph(right, rs)]],
                  colWidths=[PW * split, PW * (1 - split)],
                  hAlign='LEFT')
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        el.append(t)

    skill_label_s = ParagraphStyle('SL', fontName=F, fontSize=SZ, leading=LN,
                                    textColor=TEXT_COLOR, alignment=TA_LEFT)
    skill_val_s = ParagraphStyle('SV', fontName=F, fontSize=SZ, leading=LN,
                                 textColor=TEXT_COLOR, alignment=TA_LEFT)

    def skill_row(label, vals):
        t = Table([[Paragraph(B(label), skill_label_s), Paragraph(vals, skill_val_s)]],
                  colWidths=[LW, PW - LW], hAlign='LEFT')
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        el.append(t)

    # --- Setup output ---
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
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    el = []

    # ── HEADER ──
    el.append(Paragraph(B(IDENTITY["name"]), name_s))
    el.append(Spacer(1, 1))
    tag = tagline or 'MSc Computer Science (UOD) | 2+ YOE | Software Engineer | Python | AI/ML | NLP | System Design'
    el.append(Paragraph(tag, tag_s))
    el.append(Paragraph(
        f'{location} | {IDENTITY["phone"]} | '
        f'{L("mailto:" + IDENTITY["email"], "Email")} | '
        f'{L(IDENTITY["linkedin"], "LinkedIn")} | '
        f'{L(IDENTITY["github"], "GitHub")} | '
        f'{L(IDENTITY["portfolio"], "Portfolio")}', contact_s))

    # ── PROFESSIONAL SUMMARY ──
    section('Professional Summary')
    if summary:
        el.append(Paragraph(summary, body_s))
    else:
        el.append(Paragraph(
            f'{B("Software Engineer")} who built a {B("88,500+ LOC")} production AI system with '
            f'{B("2,350 tests")} using {B("Claude Code")} as primary development tool. '
            f'Researches and deploys emerging {B("AI tools")} ({I("Cursor")}, {I("Copilot")}, '
            f'{I("Codex")}) into working systems used by real teams. Specialises in '
            f'{B("rapid prototyping")}, {B("Python API integrations")}, and '
            f'{B("multi-agent orchestration")}. Ships work that translates technical '
            f'capability into measurable business value.', body_s))

    # ── EDUCATION ──
    section('Education')
    for i, edu in enumerate(EDUCATION):
        if i > 0:
            el.append(Spacer(1, 3))
        row(f'{B(edu["degree"])}, {edu["institution"]}', edu["dates"])
        if "dissertation" in edu:
            diss_text = edu["dissertation"]
            if "dissertation_url" in edu:
                diss_text = f'{L(edu["dissertation_url"], edu["dissertation"])}'
            el.append(Paragraph(f'{B("Dissertation:")} {diss_text}', body_s))
        if "modules" in edu:
            el.append(Paragraph(f'{B("Core Modules:")} {edu["modules"]}', body_s))
        if "cgpa" in edu:
            el.append(Paragraph(f'CGPA: {edu["cgpa"]}', body_s))

    # ── TECHNICAL SKILLS ──
    section('Technical Skills')
    for label, vals in BASE_SKILLS.items():
        skill_row(label, vals)
    if extra_skills:
        for label, vals in extra_skills.items():
            skill_row(label, vals)

    # ── PROJECTS ──
    section('Projects')
    proj_list = projects or DEFAULT_PROJECTS
    import re as _re
    for i, proj in enumerate(proj_list):
        title = _re.sub(r"^\d+\.\s*", "", proj["title"])
        row(B(f"{i+1}. {title}"), L(proj["url"], '(Link)'), split=0.90)
        for b in proj["bullets"]:
            bul(b)
        if i < len(proj_list) - 1:
            el.append(Spacer(1, 2))

    # ── EXPERIENCE ──
    section('Experience')
    for i, exp in enumerate(EXPERIENCE):
        if i > 0:
            el.append(Spacer(1, 2))
        row(B(exp["title"]), exp["dates"])
        el.append(Paragraph(exp["company"], it))
        for b in exp["bullets"]:
            bul(b)

    # ── CERTIFICATIONS ──
    section('Certifications')
    for cert_name, cert_date, cert_url in CERTIFICATIONS:
        verify_text = f'{cert_date} - {L(cert_url, "Verify")}' if cert_url else cert_date
        row(B(cert_name), verify_text, body_s, sr)

    # ── COMMUNITY AND LEADERSHIP ──
    section('Community and Leadership')
    for i, item in enumerate(COMMUNITY):
        el.append(Paragraph(
            f'{B(f"{i+1}.  {item["title"]}")}  {item["text"]}', comm_s))

    # ── REFERENCES ──
    section('References')
    el.append(Paragraph(f'{B(I("Available upon request"))}', center_s))

    doc.build(el)
    logger.info("CV generated: %s", cv_path)
    return cv_path


# ---------------------------------------------------------------------------
# ATS Unicode normalization
# ---------------------------------------------------------------------------

_UNICODE_REPLACEMENTS: dict[str, str] = {
    "\u2014": "-",
    "\u2013": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201C": '"',
    "\u201D": '"',
    "\u2026": "...",
    "\u00A0": " ",
    "\u200B": "",
    "\u200C": "",
    "\u200D": "",
    "\u2060": "",
    "\uFEFF": "",
}


def normalize_text_for_ats(text: str) -> tuple[str, dict[str, int]]:
    """Replace Unicode characters that ATS parsers handle poorly.

    Returns (normalized_text, replacement_counts).
    """
    counts: dict[str, int] = {}
    for char, replacement in _UNICODE_REPLACEMENTS.items():
        n = text.count(char)
        counts[char] = n
        if n:
            text = text.replace(char, replacement)
    return text, counts
