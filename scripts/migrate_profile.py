#!/usr/bin/env python3
"""One-time migration: seed user_profile.db from current hardcoded data.

Extracts all personal data that was scattered across generate_cv.py,
screening_answers.py, cover_letter_agent.py, config.py env vars, and
project_portfolio.py into a single ProfileStore database.

Run once:  python scripts/migrate_profile.py
Verify:    python scripts/migrate_profile.py --verify

Safe to re-run — uses INSERT OR IGNORE / ON CONFLICT for all tables.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.profile_store import ProfileStore, get_profile_store


def migrate(store: ProfileStore) -> None:
    # Clear list-based tables to make re-runs idempotent
    for table in ("experience", "education", "certifications", "community", "cv_projects"):
        store._conn.execute(f"DELETE FROM {table}")
    store._conn.commit()

    # ── Identity (from env vars / config.py) ──
    store.set_identity(
        first_name=os.getenv("APPLICANT_FIRST_NAME", "Yash"),
        last_name=os.getenv("APPLICANT_LAST_NAME", "Bishnoi"),
        email=os.getenv("APPLICANT_EMAIL", "bishnoiyash274@gmail.com"),
        phone=os.getenv("APPLICANT_PHONE", ""),
        linkedin=os.getenv("APPLICANT_LINKEDIN", "yash-bishnoi"),
        github=os.getenv("APPLICANT_GITHUB", "yashb98"),
        portfolio=os.getenv("APPLICANT_PORTFOLIO", "yashbishnoi.io"),
        location=os.getenv("APPLICANT_LOCATION", "Dundee, UK"),
        education=os.getenv("APPLICANT_EDUCATION", "MSc Computer Science, University of Dundee"),
    )

    # ── Experience (from generate_cv.py EXPERIENCE) ──
    _EXPERIENCE = [
        {
            "title": "Team Leader", "company": "Co-op",
            "dates": "Apr 2025 - Present",
            "bullets": [
                '<b>Led</b> a team of 8, coordinating shift operations through clear <b>communication</b> and collaborative <b>decision making</b> under time pressure.',
                'Developed <b>forecasting</b> processes for stock replenishment, reducing stockout incidents by <b>20%</b> through data-driven decisions and <b>adaptability</b> to demand shifts.',
                'Automated analysis of sales trends and merchandising KPIs, demonstrating <b>creativity</b> in identifying patterns that drive operational impact.',
                'Delivered <b>actionable insights</b> to area management, bridging data analysis with commercial execution through strong <b>stakeholder communication</b>.',
            ],
        },
        {
            "title": "Market Research Analyst", "company": "Nidhi Herbal",
            "dates": "Jul 2021 - Sep 2024",
            "bullets": [
                'Built <b>Power BI</b> dashboards with <b>DAX</b> enabling real-time <b>insights</b> into sales performance, supplier ROI, and trend analysis.',
                'Automated <b>SQL</b> and Excel ETL workflows using <b>Python</b> and openpyxl, cutting monthly report prep time by <b>35%</b>.',
                'Collaborated across cross-functional teams, translating complex <b>analytical findings</b> into clear recommendations through effective <b>teamwork</b> and <b>presentation skills</b>.',
                'Applied <b>critical thinking</b> to identify correlations within large, messy supplier and market datasets, driving strategic <b>decision making</b> for business growth.',
            ],
        },
    ]
    for i, exp in enumerate(_EXPERIENCE):
        store.add_experience(
            exp["title"], exp["company"], exp["dates"],
            bullets=exp["bullets"], sort_order=i,
        )

    # ── Education (from generate_cv.py EDUCATION) ──
    store.add_education(
        "MSc Computer Science", "University of Dundee", "Jan 2025 - Jan 2026",
        dissertation="Deep Learning for Facial 3D Reconstruction - Simulator",
        dissertation_url="https://github.com/yashb98/Deep-Learning-for-Facial-3D-Reconstruction---Simulator",
        modules="Machine Learning | Advanced Programming Techniques | Design Methods | Software Engineering | Software Development | Web Development | Database Systems",
        sort_order=0,
    )
    store.add_education(
        "MBA (Finance)", "JECRC University", "2019 - 2021",
        grade="8.21/10", sort_order=1,
    )

    # ── Certifications (from generate_cv.py CERTIFICATIONS) ──
    _CERTS = [
        ("1. IBM Machine Learning", "July 2023", "https://www.coursera.org/account/accomplishments/specialization/certificate/SL9P2Q6Z43JP"),
        ("2. SQL Essential Learning", "September 2023", "https://www.linkedin.com/learning/certificates/sql-essential"),
        ("3. Feature Engineering", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/feature-engineering"),
        ("4. Data Cleaning", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/data-cleaning"),
        ("5. Exploratory Data Analysis for Machine Learning", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/eda-ml"),
        ("6. Deep Learning and Reinforcement Learning", "July 2023", "https://www.coursera.org/account/accomplishments/certificate/S2MJH2ZQ8WF4"),
        ("7. Introduction to Model Context Protocol", "Feb 2026", "https://verify.skilljar.com/c/nn63q5jje52u"),
    ]
    for i, (name, date, url) in enumerate(_CERTS):
        store.add_certification(name, date, url, sort_order=i)

    # ── Community (from generate_cv.py COMMUNITY) ──
    _COMMUNITY = [
        ("Quackathon 2025 Participant.", 'Built a data-driven prototype in 4 hours with a cross-functional team, demonstrating <b>adaptability</b>, <b>teamwork</b>, and rapid <b>decision making</b> under extreme time pressure.'),
        ("Friends International, Dundee Chapter.", 'Led community initiatives supporting international students, strengthening <b>leadership</b>, cross-cultural <b>collaboration</b>, and <b>communication</b> with diverse stakeholders.'),
        ("Peer Mentor for Coding Challenges.", 'Mentored peers on Python, SQL, and statistical analysis, demonstrating <b>communication</b> skills by distilling complex technical concepts into clear, digestible guidance.'),
    ]
    for i, (title, text) in enumerate(_COMMUNITY):
        store.add_community(title, text, sort_order=i)

    # ── CV Default Projects (from generate_cv.py DEFAULT_PROJECTS) ──
    _PROJECTS = [
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
    for i, proj in enumerate(_PROJECTS):
        store.add_cv_project(proj["title"], proj["url"], proj["bullets"], sort_order=i)

    # ── Base Skills (from generate_cv.py BASE_SKILLS) ──
    _BASE_SKILLS = {
        "Languages:": "Python | SQL | JavaScript | TypeScript",
        "AI/ML:": "NLP | Text Analysis | Clustering | Scikit-learn | PyTorch | TensorFlow | Pandas | NumPy | LangChain | Hugging Face | RAG",
        "DevOps:": "Docker | Kubernetes | AWS | GCP | Azure | CI/CD | GitHub Actions | Terraform | Linux",
        "BI/Tools:": "Power BI | DAX | Looker | Excel | APIs | FastAPI | Flask | Git | GitHub | Web Scraping",
        "Practices:": "Statistical Testing | Forecasting | Dashboards | Data Modelling | EDA | Data Cleaning | MLOps | A/B Testing | Documentation",
    }
    for category, skills in _BASE_SKILLS.items():
        store.set_base_skill_category(category, skills)

    # ── Skill Experience (from screening_answers.py SKILL_EXPERIENCE) ──
    _SKILL_EXP = {
        "python": 3, "sql": 3,
        "machine learning": 2, "ml": 2, "deep learning": 2,
        "natural language processing": 2, "nlp": 2,
        "large language model": 2, "llm": 2, "generative ai": 2,
        "artificial intelligence": 2, "ai": 2,
        "data science": 2, "data analysis": 2, "data analytics": 2,
        "tensorflow": 2, "pytorch": 2, "scikit-learn": 2, "sklearn": 2,
        "pandas": 2, "numpy": 2, "scipy": 2,
        "computer vision": 2, "reinforcement learning": 2,
        "mlops": 2, "model deployment": 2,
        "a/b testing": 2, "ab testing": 2, "statistical analysis": 2,
        "neural network": 2, "transformer": 2,
        "software engineering": 2, "software development": 2,
        "git": 2, "docker": 2, "linux": 2,
        "aws": 2, "cloud": 2, "gcp": 2, "azure": 2,
        "ci/cd": 2, "devops": 2,
        "api": 2, "rest": 2, "fastapi": 2, "flask": 2,
        "spark": 2, "hadoop": 2, "airflow": 2,
        "etl": 2, "data pipeline": 2, "data engineering": 2,
        "tableau": 2, "power bi": 2,
        "nosql": 2, "mongodb": 2, "redis": 2,
        "postgresql": 2, "mysql": 2,
        "agile": 2, "scrum": 2, "jira": 2,
        "r": 2, "matlab": 2, "java": 2, "c++": 2,
        "javascript": 2, "typescript": 2, "react": 2,
        "team management": 3, "leadership": 3, "team leader": 3,
    }
    for skill, years in _SKILL_EXP.items():
        store.set_skill_experience(skill, years)

    # ── Role Salary (from screening_answers.py ROLE_SALARY) ──
    _ROLE_SALARY = {
        "data scientist": 38000, "graduate data scientist": 35000,
        "junior data scientist": 36000, "early careers data scientist": 35000,
        "data science intern": 25000,
        "machine learning engineer": 38000, "ml engineer": 38000,
        "graduate ml engineer": 35000, "junior ml engineer": 37000,
        "early careers ml engineer": 35000, "machine learning intern": 25000,
        "ai engineer": 38000, "graduate ai engineer": 35000,
        "junior ai engineer": 35000, "early careers ai engineer": 35000,
        "ai intern": 25000,
        "data engineer": 35000, "graduate data engineer": 32000,
        "junior data engineer": 35000, "early careers data engineer": 32000,
        "data engineer intern": 24000,
        "software engineer": 35000, "graduate software engineer": 32000,
        "junior software engineer": 33000, "early careers software engineer": 32000,
        "software engineer intern": 25000,
        "data analyst": 30000, "default": 30000,
    }
    for role, salary in _ROLE_SALARY.items():
        store.set_role_salary(role, salary)

    # ── Screening Defaults (from screening_answers.py COMMON_ANSWERS) ──
    _DEFAULTS = {
        "notice_period": "Immediately",
        "employment_status": "Yes",
        "current_job_title": "Team Leader",
        "current_employer": "Co-op",
        "relocation": "Yes, within the UK",
        "highest_education": "Master's Degree",
        "degree_subject": "MSc Computer Science",
        "english_proficiency": "Native or bilingual",
        "languages": "English (Native), Hindi (Native)",
        "driving_license": "Yes",
        "employment_type": "Full-time",
        "daily_rate": "150",
        "current_salary": "22000",
    }
    for q_type, answer in _DEFAULTS.items():
        store.set_screening_default(q_type, answer)

    # ── Sensitive Fields (encrypted) ──
    # DEI
    store.set_sensitive("gender", "Male", "dei")
    store.set_sensitive("sexual_orientation", "Heterosexual/Straight", "dei")
    store.set_sensitive("ethnicity", "Asian or Asian British - Indian", "dei")
    store.set_sensitive("disability", "No", "dei")
    store.set_sensitive("veteran", "No", "dei")
    store.set_sensitive("religion", "Hindu", "dei")
    store.set_sensitive("marital_status", "Single", "dei")
    store.set_sensitive("pronouns", "He/Him", "dei")
    store.set_sensitive("age_group", "25-29", "dei")

    # Immigration
    store.set_sensitive("visa_status",
        "Student Visa; converting to Graduate Visa from 9 May 2026 (valid 2 years)",
        "immigration")
    store.set_sensitive("visa_status_full", "Tier 4 (General) Student Visa", "immigration")
    store.set_sensitive("visa_type", "Graduate Visa", "immigration")
    store.set_sensitive("requires_sponsorship", "false", "immigration")
    store.set_sensitive("right_to_work_uk", "true", "immigration")

    # Financial
    store.set_sensitive("current_salary", "22000", "financial")

    print(f"Migration complete. Profile DB at: {store._db_path}")
    _print_stats(store)


def _print_stats(store: ProfileStore) -> None:
    ident = store.identity()
    print(f"  Identity: {ident.full_name} ({ident.email})")
    print(f"  Experience: {len(store.experience())} entries")
    print(f"  Education: {len(store.education())} entries")
    print(f"  Certifications: {len(store.certifications())} entries")
    print(f"  Community: {len(store.community())} entries")
    print(f"  CV Projects: {len(store.cv_projects())} entries")
    print(f"  Base Skills: {len(store.base_skills())} categories")
    se = store.skill_experience()
    print(f"  Skill Experience: {len(se)} skills")
    rs = store.role_salary()
    print(f"  Role Salary: {len(rs)} roles")
    print(f"  Screening Defaults: {len(store.all_screening_defaults())} entries")
    print(f"  Sensitive Fields: {len(store.all_sensitive())} entries (encrypted)")


def verify(store: ProfileStore) -> None:
    ident = store.identity()
    assert ident.full_name, "Identity: full_name is empty"
    assert ident.email, "Identity: email is empty"
    assert len(store.experience()) >= 2, "Experience: expected >= 2 entries"
    assert len(store.education()) >= 2, "Education: expected >= 2 entries"
    assert len(store.certifications()) >= 7, "Certifications: expected >= 7"
    assert len(store.cv_projects()) >= 4, "CV Projects: expected >= 4"
    assert len(store.base_skills()) >= 5, "Base Skills: expected >= 5 categories"
    se = store.skill_experience()
    assert isinstance(se, dict) and len(se) >= 50, f"Skill Experience: expected >= 50, got {len(se)}"
    rs = store.role_salary()
    assert isinstance(rs, dict) and len(rs) >= 20, f"Role Salary: expected >= 20, got {len(rs)}"
    assert store.sensitive("gender") == "Male", "Sensitive: gender decryption failed"
    assert store.sensitive("visa_status"), "Sensitive: visa_status is empty"
    assert store.sensitive("current_salary") == "22000", "Sensitive: salary decryption failed"
    print("All verification checks passed.")
    _print_stats(store)


if __name__ == "__main__":
    store = get_profile_store()
    if "--verify" in sys.argv:
        verify(store)
    else:
        migrate(store)
