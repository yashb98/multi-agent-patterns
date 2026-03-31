"""Project Portfolio — maps GitHub repos to CV-ready entries.

Each project has a title line, URL, and 3-5 bullet points with metrics.
Used by the CV generator to dynamically select projects per JD.

Public API:
  get_best_projects_for_jd(required_skills, preferred_skills, top_n=4) -> list[dict]
  get_project_entry(repo_name) -> dict | None
"""

from __future__ import annotations

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Portfolio — repo name → CV-ready entry
# ---------------------------------------------------------------------------

PORTFOLIO: dict[str, dict] = {
    "yashb98/multi-agent-patterns": {
        "title": "Multi-Agent Orchestration System | Python | LangGraph | OpenAI",
        "url": "https://github.com/yashb98/multi-agent-patterns",
        "priority": 1,  # Always first if it matches — strongest project
        "bullets": [
            'Built a <b>61,500+ LOC</b> production AI system with <b>488 tests</b>, <b>259 files</b>, and <b>5 databases</b> — actively growing.',
            'Designed <b>4 LangGraph</b> orchestration patterns with GRPO experiential learning and persona evolution.',
            'Shipped <b>10+</b> autonomous agents (Gmail, Calendar, GitHub, Notion, Budget) running <b>24/7</b> via Telegram.',
            'Built multi-source <b>fact-checking</b> pipeline using Semantic Scholar API with <b>9.5/10</b> accuracy gate.',
        ],
    },
    "yashb98/Velox_AI": {
        "title": "Velox AI - Enterprise AI Voice Agent Platform | Python | FastAPI | Docker | GCP",
        "url": "https://github.com/yashb98/Velox_AI",
        "bullets": [
            'Built real-time <b>dashboards</b> tracking performance across <b>1,000+</b> concurrent sessions, reducing anomaly detection by <b>70%</b>.',
            'Automated analysis of <b>50K+</b> daily session metrics via <b>API</b>-driven pipelines, cutting manual reporting by <b>80%</b>.',
            'Delivered <b>sub-150ms</b> response times at scale through statistical analysis and optimisation on <b>GCP</b>.',
        ],
    },
    "yashb98/nexusmind": {
        "title": "Cloud Sentinel - AI Cloud Security Platform | Python | React | Docker | Redis | Pinecone",
        "url": "https://github.com/yashb98/nexusmind",
        "bullets": [
            'Built <b>NLP</b> pipelines extracting insights from <b>10K+</b> unstructured documents with <b>94%</b> retrieval precision.',
            'Developed <b>clustering</b> workflows grouping <b>500+</b> policy documents by topic, risk level, and compliance status.',
            'Reduced manual review time by <b>55%</b> through automated dashboards surfacing compliance metrics.',
            'Optimised vector insertion pipeline, reducing indexing time by <b>60%</b> through batch processing.',
        ],
    },
    "yashb98/DataMind": {
        "title": "DataMind - AI Analytics Platform | Python | FastAPI | Next.js | LangGraph",
        "url": "https://github.com/yashb98/DataMind",
        "bullets": [
            'Built AI analytics platform with <b>48-agent</b> Digital Labor Workforce for autonomous data processing.',
            'Designed <b>8-layer</b> anti-hallucination stack with NLI scoring and chain-of-thought auditing.',
            'Implemented multi-cloud lakehouse with <b>Apache Kafka</b>, <b>DuckDB</b>, and <b>Pinecone</b>.',
        ],
    },
    "yashb98/LetsBuild": {
        "title": "LetsBuild - Autonomous Portfolio Factory | Python | Anthropic SDK | Docker",
        "url": "https://github.com/yashb98/LetsBuild",
        "bullets": [
            'Architected <b>10-layer</b> agentic pipeline using Anthropic Claude SDK with <b>tool_use</b> for structured output.',
            'Implemented <b>Docker</b> sandbox management with compiled policy gates and self-learning ReasoningBank.',
            'Built <b>RLM</b> recursive language model for processing million-token contexts via sub-LM orchestration.',
        ],
    },
    "yashb98/90Days_Machine_learinng": {
        "title": "90 Days Machine Learning | Python | SQL | Scikit-learn",
        "url": "https://github.com/yashb98/90Days_Machine_learinng",
        "bullets": [
            '<b>30+</b> projects spanning NLP, web scraping, clustering, forecasting, and statistical testing.',
            'Built scraping pipelines collecting <b>100K+</b> records from multiple sources using BeautifulSoup and Scrapy.',
            'Ran statistical tests across <b>15+</b> datasets, improving prediction accuracy by <b>12%</b>.',
            'Standardised data cleaning workflows, resolving format issues impacting <b>40%</b> of raw inputs.',
        ],
    },
    "yashb98/Deep-Learning-for-Facial-3D-Reconstruction---Simulator": {
        "title": "Deep Learning for Facial 3D Reconstruction | PyTorch | Computer Vision",
        "url": "https://github.com/yashb98/Deep-Learning-for-Facial-3D-Reconstruction---Simulator",
        "bullets": [
            'Built custom encoder-decoder in <b>PyTorch</b> achieving <b>0.89 SSIM</b>, outperforming baseline by <b>15%</b>.',
            'Generated <b>10,000+</b> synthetic samples with automated pipelines, identifying spatial patterns across <b>3</b> coordinate systems.',
            'Presented findings to academic panel, translating complex architectures into clear visual narratives.',
        ],
    },
    "yashb98/Fintech_customer_churn": {
        "title": "Fintech Customer Churn Prediction | Python | Scikit-learn | Pandas",
        "url": "https://github.com/yashb98/Fintech_customer_churn",
        "bullets": [
            'Built churn prediction model achieving <b>87%</b> accuracy using gradient boosting and feature engineering.',
            'Analysed <b>10K+</b> customer records identifying <b>5</b> key churn drivers through correlation analysis.',
            'Delivered actionable retention strategies reducing predicted churn by <b>18%</b>.',
        ],
    },
    "yashb98/Credit_Card_Fraud_Detection": {
        "title": "Credit Card Fraud Detection | Python | Scikit-learn | Imbalanced Learning",
        "url": "https://github.com/yashb98/Credit_Card_Fraud_Detection",
        "bullets": [
            'Built fraud detection model with <b>99.2%</b> precision on highly imbalanced dataset (<b>0.17%</b> fraud rate).',
            'Applied SMOTE oversampling and ensemble methods, reducing false positives by <b>35%</b>.',
            'Processed <b>284K+</b> transactions with automated feature engineering pipeline.',
        ],
    },
    "yashb98/Credit_risk_analysis": {
        "title": "Credit Risk Analysis | Python | Statistical Modelling | Pandas",
        "url": "https://github.com/yashb98/Credit_risk_analysis",
        "bullets": [
            'Built risk scoring model analysing <b>30K+</b> loan applications with logistic regression and decision trees.',
            'Identified <b>8</b> key risk factors through statistical analysis, improving default prediction by <b>22%</b>.',
            'Automated report generation cutting analyst prep time by <b>40%</b>.',
        ],
    },
    "yashb98/Mlops_Image_classification-Project": {
        "title": "MLOps Image Classification Pipeline | Python | Docker | AWS | MLflow",
        "url": "https://github.com/yashb98/Mlops_Image_classification-Project",
        "bullets": [
            'Built end-to-end <b>MLOps</b> pipeline with <b>Docker</b> containerisation, <b>MLflow</b> tracking, and <b>AWS</b> deployment.',
            'Automated model training, evaluation, and deployment with <b>CI/CD</b> pipeline integration.',
            'Achieved <b>94%</b> classification accuracy with automated hyperparameter tuning.',
        ],
    },
    "yashb98/Text-Summarizer-Project": {
        "title": "Text Summarisation Pipeline | Python | NLP | Transformers",
        "url": "https://github.com/yashb98/Text-Summarizer-Project",
        "bullets": [
            'Built abstractive text summarisation pipeline using <b>Hugging Face</b> transformers with <b>ROUGE-L 0.42</b>.',
            'Processed <b>5K+</b> documents with automated preprocessing and evaluation pipeline.',
            'Deployed via <b>FastAPI</b> endpoint with <b>Docker</b> containerisation for production readiness.',
        ],
    },
    "yashb98/movies-recommender-system": {
        "title": "Movie Recommender System | Python | Scikit-learn | Content-Based Filtering",
        "url": "https://github.com/yashb98/movies-recommender-system",
        "bullets": [
            'Built content-based recommendation engine analysing <b>5K+</b> movies using cosine similarity.',
            'Implemented TF-IDF vectorisation on plot descriptions, achieving <b>78%</b> recommendation relevance.',
            'Deployed interactive demo with Streamlit for real-time movie suggestions.',
        ],
    },
    "yashb98/Foresight": {
        "title": "Foresight - AI Forecasting Platform | Python | FastAPI | Docker",
        "url": "https://github.com/yashb98/Foresight",
        "bullets": [
            'Built time-series forecasting platform with <b>ARIMA</b>, <b>Prophet</b>, and deep learning models.',
            'Automated data ingestion from <b>5+</b> sources with scheduled pipeline runs via <b>Docker</b>.',
            'Delivered <b>15%</b> improvement in forecast accuracy over naive baseline through ensemble methods.',
        ],
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_project_entry(repo_name: str) -> dict | None:
    """Look up a single project's CV entry by repo name."""
    return PORTFOLIO.get(repo_name)


def get_best_projects_for_jd(
    required_skills: list[str],
    preferred_skills: list[str] | None = None,
    top_n: int = 4,
) -> list[dict]:
    """Select the top N projects that best match the JD skills.

    Uses SkillGraphStore to find projects with highest skill overlap,
    then looks up CV-ready entries from PORTFOLIO.

    Returns list of dicts matching generate_cv_pdf's projects format:
        [{"title": "...", "url": "...", "bullets": ["...", ...]}, ...]

    Falls back to DEFAULT_PROJECTS from generate_cv.py if no matches found.
    """
    from jobpulse.skill_graph_store import SkillGraphStore

    store = SkillGraphStore()
    all_skills = required_skills + (preferred_skills or [])

    try:
        matches = store.get_projects_for_skills(all_skills)
    except Exception as exc:
        logger.warning("project_portfolio: SkillGraphStore query failed: %s", exc)
        matches = []

    # Sort matches: priority projects first (lower number = higher priority), then by skill overlap
    prioritized = []
    for match in matches:
        entry = PORTFOLIO.get(match.name)
        if entry:
            priority = entry.get("priority", 99)
            prioritized.append((priority, match.skill_overlap, match, entry))

    # Sort by priority first (1 before 99), then by skill overlap desc
    prioritized.sort(key=lambda x: (x[0], -x[1]))

    selected: list[dict] = []
    for _priority, _overlap, match, entry in prioritized:
        if len(selected) >= top_n:
            break
        if entry:
            numbered = dict(entry)
            numbered["title"] = f"{len(selected) + 1}. {entry['title']}"
            selected.append(numbered)

    if not selected:
        from jobpulse.cv_templates.generate_cv import DEFAULT_PROJECTS
        return DEFAULT_PROJECTS

    return selected
