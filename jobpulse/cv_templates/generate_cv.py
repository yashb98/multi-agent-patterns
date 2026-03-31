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
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
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
        "dates": "Jan 2025 \u2013 Jan 2026",
        "dissertation": "Deep Learning for Facial 3D Reconstruction - Simulator",
        "modules": "Machine Learning | Advanced Programming Techniques | Design Methods | Software Engineering | Software Development | Web Development | Database Systems",
    },
    {
        "degree": "MBA (Finance)",
        "institution": "JECRC University",
        "dates": "2019 \u2013 2021",
        "cgpa": "8.21/10",
    },
]

EXPERIENCE = [
    {
        "title": "Team Leader",
        "company": "Co-op",
        "dates": "Apr 2025 \u2013 Present",
        "bullets": [
            '<b>Automated</b> analysis of sales trends and merchandising KPIs, identifying patterns that drive real operational impact.',
            'Developed <b>forecasting</b> processes for stock replenishment, reducing stockout incidents by <b>20%</b> through data-driven decisions.',
            'Worked with high <b>autonomy</b>, owning shift-level analytical decisions and translating complex data into strategic actions.',
            'Delivered clear, <b>actionable insights</b> to area management, bridging data analysis with commercial execution.',
        ],
    },
    {
        "title": "Market Research Analyst",
        "company": "Nidhi Herbal",
        "dates": "Jul 2021 \u2013 Sep 2024",
        "bullets": [
            'Built <b>Power BI</b> dashboards with <b>DAX</b> enabling real-time <b>insights</b> into sales performance, supplier ROI, and trend analysis.',
            'Automated <b>SQL</b> and Excel ETL workflows using <b>Python</b> and openpyxl, cutting monthly report prep time by <b>35%</b>.',
            'Distilled complex <b>analytical findings</b> into clear, strategic recommendations for senior management and cross-functional teams.',
            'Identified <b>correlations</b> and opportunities within large, messy supplier and market datasets driving business growth decisions.',
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
    ("7. Introduction to Model Context Protocol", "Feb 2026", "http://verify.skilljar.com/c/nn63q5jje52u"),
]

COMMUNITY = [
    {
        "title": "Quackathon 2025 Participant.",
        "text": "Built a data-driven prototype in 4 hours, identifying performance patterns and delivering actionable insights under extreme time pressure.",
    },
    {
        "title": "Friends International, Dundee Chapter.",
        "text": "Supported international students through community initiatives, strengthening cross-functional collaboration and async stakeholder communication.",
    },
    {
        "title": "Peer Mentor for Coding Challenges.",
        "text": "Mentored peers on Python, SQL, and statistical analysis techniques, distilling complex concepts into clear, digestible guidance.",
    },
]

# Base skills — 4 clean categories
BASE_SKILLS = {
    "Languages:": "Python | SQL | JavaScript | TypeScript",
    "AI/ML:": "NLP | Text Analysis | Clustering | Scikit-learn | PyTorch | TensorFlow | Pandas | NumPy | LangChain | Hugging Face | Web Scraping",
    "BI/Tools:": "Power BI | DAX | Looker | Excel | APIs | FastAPI | Flask | Docker | AWS | GCP | Azure | Git | GitHub",
    "Practices:": "Statistical Testing | Forecasting | Dashboards | Data Modelling | EDA | Data Cleaning | MLOps | Documentation",
}

# Default projects
DEFAULT_PROJECTS = [
    {
        "title": "1. Velox AI - Enterprise AI Voice Agent Platform | Python | FastAPI | Docker | GCP",
        "url": "https://github.com/yashb98/Velox_AI",
        "bullets": [
            'Built real-time <b>dashboards</b> tracking performance metrics, trends, and engagement patterns across 1,000+ concurrent sessions.',
            'Automated <b>analysis</b> of key session metrics via <b>API</b>-driven data pipelines feeding structured outputs for decision-making.',
            'Identified <b>performance patterns</b> and optimisation levers through statistical analysis of latency and usage data on GCP.',
        ],
    },
    {
        "title": "2. Cloud Sentinel - AI Powered Cloud Security Platform with Python, React, Docker, Redis, Pinecone",
        "url": "https://github.com/yashb98/nexusmind",
        "bullets": [
            'Built <b>NLP</b> and <b>text-based analysis</b> pipelines extracting insights from unstructured documents with 94% retrieval precision.',
            'Developed <b>clustering</b> and classification workflows grouping policy documents by topic, risk level, and compliance status.',
            'Built <b>dashboards</b> surfacing audit results, compliance metrics, and <b>content performance</b> indicators in real time.',
            'Delivered clear, <b>actionable insights</b> from messy data sources, bridging data science with strategic compliance decisions.',
            'Turns out, batching vector insertions cut indexing time by 60%; finding the right lever mattered more than model complexity.',
        ],
    },
    {
        "title": "3. 90 Days Machine learning",
        "url": "https://github.com/yashb98/90Days_Machine_learinng",
        "bullets": [
            '30+ projects spanning <b>NLP</b>, <b>web scraping</b>, <b>clustering</b>, <b>forecasting</b>, and statistical testing using <b>Python</b>, <b>SQL</b>, and Scikit-learn.',
            'Built <b>web scraping</b> pipelines using BeautifulSoup and Scrapy to collect, clean, and structure data from multiple online sources.',
            'Ran <b>statistical tests</b> (A/B testing, hypothesis validation, correlation analysis) to measure experimental outcomes and validate insights.',
            'The tricky part was handling messy, multi-source datasets; I didn\'t expect format inconsistencies to compound that fast.',
        ],
    },
    {
        "title": "4. Deep Learning for Facial 3D Reconstructions with PyTorch and Computer Vision",
        "url": "https://github.com/yashb98/Deep-Learning-for-Facial-3D-Reconstruction---Simulator",
        "bullets": [
            'Built <b>data models</b> to forecast reconstruction quality using a custom encoder-decoder in PyTorch, achieving 0.89 SSIM.',
            '10,000+ synthetic samples generated with automated <b>Python</b> pipelines, identifying <b>patterns</b> and correlations in spatial data.',
            'Distilled complex technical findings into clear, digestible insights for academic review and non-technical presentation.',
        ],
    },
]


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

    F = 'MyArial'
    SZ = 9.5
    PW = A4[0] - 28 * mm
    LN = 11.5
    LW = 27 * mm

    # --- Styles (tight but readable) ---
    name_s = ParagraphStyle('N', fontName=F, fontSize=20, alignment=TA_CENTER,
                            spaceAfter=1, leading=24, textColor=TEXT_COLOR)
    tag_s = ParagraphStyle('T', fontName=F, fontSize=SZ, alignment=TA_CENTER,
                           spaceAfter=1, leading=LN, textColor=SUB_COLOR)
    contact_s = ParagraphStyle('C', fontName=F, fontSize=SZ, alignment=TA_CENTER,
                               spaceAfter=4, leading=LN, textColor=TEXT_COLOR)
    sec_s = ParagraphStyle('S', fontName=F, fontSize=11, spaceBefore=6, spaceAfter=1,
                           leading=13, textColor=HEADER_COLOR)
    body_s = ParagraphStyle('B', fontName=F, fontSize=SZ, spaceAfter=1,
                            leading=LN, textColor=TEXT_COLOR)
    right_s = ParagraphStyle('R', fontName=F, fontSize=SZ, alignment=TA_RIGHT,
                             spaceAfter=1, leading=LN, textColor=TEXT_COLOR)
    bullet_s = ParagraphStyle('Bu', fontName=F, fontSize=SZ, leftIndent=14,
                              firstLineIndent=-8, spaceAfter=1, leading=LN,
                              textColor=TEXT_COLOR)
    sl = ParagraphStyle('SL', fontName=F, fontSize=SZ, leading=LN, textColor=TEXT_COLOR)
    sv = ParagraphStyle('SV', fontName=F, fontSize=SZ, leading=LN, textColor=TEXT_COLOR)
    sr = ParagraphStyle('Sr', fontName=F, fontSize=SZ, alignment=TA_RIGHT,
                        spaceAfter=0, leading=LN, textColor=TEXT_COLOR)
    it = ParagraphStyle('I', fontName=F, fontSize=SZ, spaceAfter=1,
                        leading=LN, textColor=TEXT_COLOR)
    center_s = ParagraphStyle('Cn', fontName=F, fontSize=SZ, alignment=TA_CENTER,
                              spaceAfter=1, leading=LN, textColor=LINK_COLOR)
    comm_s = ParagraphStyle('Co', fontName=F, fontSize=SZ, spaceAfter=2,
                            leading=LN, textColor=TEXT_COLOR)

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
                  colWidths=[PW * split, PW * (1 - split)])
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        el.append(t)

    def skill_row(label, vals):
        t = Table([[Paragraph(B(label), sl), Paragraph(vals, sv)]],
                  colWidths=[LW, PW - LW])
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
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
    tag = tagline or 'MSc Computer Science (UOD) | Software Engineer | Python | AI/ML | Claude Code | System Design'
    el.append(Paragraph(tag, tag_s))
    el.append(Paragraph(
        f'UK | {IDENTITY["phone"]} | '
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
            f'{B("Software Engineer")} who built a {B("54,500 LOC")} production AI system with '
            f'{B("350 tests")} using {B("Claude Code")} as primary development tool. '
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
            el.append(Paragraph(f'{B("Dissertation:")} {edu["dissertation"]}', body_s))
        if "modules" in edu:
            el.append(Paragraph(f'{B("Core Modules:")} {edu["modules"]}', body_s))
        if "cgpa" in edu:
            el.append(Paragraph(f'CGPA: {edu["cgpa"]}', body_s))

    # ── TECHNICAL SKILLS ──
    section('Technical Skills')
    for label, vals in BASE_SKILLS.items():
        skill_row(label, vals)

    # ── PROJECTS ──
    section('Projects')
    proj_list = projects or DEFAULT_PROJECTS
    for i, proj in enumerate(proj_list):
        row(B(proj["title"]), L(proj["url"], '(Link)'), split=0.90)
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
        verify_text = f'{cert_date} \u2013 {L(cert_url, "Verify")}' if cert_url else cert_date
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
