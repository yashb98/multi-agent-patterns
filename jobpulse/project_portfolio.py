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
            'Built a <b>88,500+ LOC</b> production AI system with <b>2,350 tests</b>, <b>412 files</b>, and <b>21 databases</b>, shipping <b>10+ autonomous agents</b> (Gmail, Calendar, GitHub, Notion, Budget) running <b>24/7</b> via Telegram with <b>99.9%</b> uptime.',
            'Designed <b>4 LangGraph</b> orchestration patterns with GRPO experiential learning, persona evolution, and <b>A/B testing</b>, reducingLLM costs by <b>96%</b> ($5.63 → $0.23/month) through hybrid rule-based + ML classification.',
            'Engineered <b>5-gate pre-screen pipeline</b> with statistical correlation engine, adaptive rate limiting, and <b>17-signal verification wall learning</b>, processing<b>200+ JDs/day</b> with <b>92%+</b> skill match threshold.',
            'Built multi-source <b>fact-checking</b> pipeline using Semantic Scholar API with <b>9.5/10</b> accuracy gate, SQLite caching, and automated <b>Google Drive</b> integration for document management.',
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
            'Built AI analytics platform with <b>48-agent</b> Digital Labor Workforce for autonomous data processing, handling<b>ETL</b>, anomaly detection, and reporting across structured and unstructured data sources.',
            'Designed <b>8-layer</b> anti-hallucination stack with NLI scoring and chain-of-thought auditing, achieving <b>95%+</b> factual accuracy on generated insights.',
            'Implemented multi-cloud lakehouse with <b>Apache Kafka</b> for real-time streaming, <b>DuckDB</b> for analytical queries, and <b>Pinecone</b> for vector search, processing<b>1M+</b> records with sub-second query latency.',
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
            '<b>30+ projects</b> spanning NLP, web scraping, clustering, forecasting, and statistical testing, building scraping pipelines collecting <b>100K+ records</b> from multiple sources using BeautifulSoup and Scrapy.',
            'Ran statistical tests (t-test, chi-squared, ANOVA) across <b>15+ datasets</b>, improving prediction accuracy by <b>12%</b> and standardising data cleaning workflows resolving format issues in <b>40%</b> of raw inputs.',
            'Implemented <b>ML pipelines</b> with feature engineering, cross-validation, and hyperparameter tuning using <b>Scikit-learn</b>, including regression, random forest, gradient boosting, and K-means clustering.',
            'Automated <b>SQL</b> data extraction and <b>EDA</b> workflows with Pandas profiling, generating reproducible analysis reports across <b>20+</b> business domains.',
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
            'Built churn prediction model achieving <b>87%</b> accuracy using gradient boosting and feature engineering, analysing<b>10K+</b> customer records identifying <b>5</b> key churn drivers through correlation and cohort analysis.',
            'Delivered actionable retention strategies reducing predicted churn by <b>18%</b>, with automated <b>A/B test</b> simulation to validate intervention effectiveness before deployment.',
            'Designed end-to-end <b>ML pipeline</b> with data ingestion, feature selection (mutual information + RFE), model training, and <b>SHAP</b>-based explainability reports for stakeholder presentations.',
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
    # --- Added: repos from last 1 year not previously in PORTFOLIO ---
    "yashb98/LetsFineTune": {
        "title": "LetsFineTune - LLM Fine-Tuning Collection | Python | LoRA | Unsloth | Hugging Face",
        "url": "https://github.com/yashb98/LetsFineTune",
        "bullets": [
            'Fine-tuned multiple language models using <b>LoRA/QLoRA</b> with <b>Unsloth</b> and Hugging Face Transformers, building reproducible training pipelines from dataset preparation to evaluation.',
            'Implemented parameter-efficient fine-tuning reducing training compute by <b>90%+</b> compared to full fine-tuning, with configurable hyperparameter sweeps.',
            'Built end-to-end notebooks covering data preprocessing, tokenisation, model training, and automated metrics logging.',
        ],
    },
    "yashb98/code-graph-mcp": {
        "title": "Code Graph MCP Server | TypeScript | OXC Parser | Graphology",
        "url": "https://github.com/yashb98/code-graph-mcp",
        "bullets": [
            'Built type-aware code knowledge graph <b>MCP server</b> for TypeScript/TSX projects using OXC parser and graphology, enabling structural analysis of imports, exports, and symbols.',
            'Implemented <b>Louvain</b> community detection algorithm for automatic module clustering and codebase architecture discovery.',
            'Designed MCP tool interface exposing <b>20+</b> graph queries for code navigation, dependency analysis, and symbol resolution.',
        ],
    },
    "yashb98/agentforge-arena": {
        "title": "AgentForge Arena - AI Agent Competition Platform | Python",
        "url": "https://github.com/yashb98/agentforge-arena",
        "bullets": [
            'Built AI agent competition platform where autonomous agents compete to build projects and win hackathons with automated evaluation.',
            'Designed multi-agent evaluation framework with scoring and ranking across multiple challenge categories.',
            'Implemented agent orchestration with sandboxed execution environments for safe autonomous code generation.',
        ],
    },
    "yashb98/SoloQuest": {
        "title": "SoloQuest - Full-Stack Application | TypeScript",
        "url": "https://github.com/yashb98/SoloQuest",
        "bullets": [
            'Built full-stack TypeScript application with modern development practices and type-safe architecture.',
            'Implemented end-to-end feature development with component-based UI and API integration.',
        ],
    },
    "yashb98/AIEngine-Hackathon": {
        "title": "RegBot - AI Compliance Agent for SMEs | TypeScript | Next.js | Vercel",
        "url": "https://github.com/yashb98/AIEngine-Hackathon",
        "bullets": [
            'Built AI-powered compliance agent for Scottish SMEs, automating regulatory deadline tracking and admin reduction by <b>50%</b>.',
            'Designed conversational AI interface for compliance queries, deployed on <b>Vercel</b> with Next.js for production availability.',
            'Won hackathon recognition for practical SME tooling addressing real regulatory compliance gaps.',
        ],
    },
    "yashb98/Prism": {
        "title": "PRISM - Recursive Language Model Research Agent | Python | LangGraph",
        "url": "https://github.com/yashb98/Prism",
        "bullets": [
            'Built production-grade agentic research system using <b>Recursive Language Model</b> paradigm, decomposing million-token corpora into verifiable reasoning chains.',
            'Implemented step-level monitoring with process-recursive inference, enabling transparent audit trails for every research conclusion.',
            'Designed multi-stage decomposition pipeline turning complex research queries into atomic, verifiable sub-tasks.',
        ],
    },
    "yashb98/AI-ML-interview-Prep": {
        "title": "AI/ML Interview Preparation | Python | Machine Learning",
        "url": "https://github.com/yashb98/AI-ML-interview-Prep",
        "bullets": [
            'Curated comprehensive AI/ML interview preparation covering algorithms, system design, and practical coding problems.',
            'Built implementations of core ML algorithms including neural networks, decision trees, and ensemble methods.',
        ],
    },
    "yashb98/Machine-Learning_University_of_Dundee": {
        "title": "Machine Learning Coursework | Python | Scikit-learn | NumPy",
        "url": "https://github.com/yashb98/Machine-Learning_University_of_Dundee",
        "bullets": [
            'Completed <b>15+</b> lab exercises and assignments covering supervised learning, unsupervised learning, and neural networks at the University of Dundee.',
            'Implemented classification, regression, and clustering algorithms from scratch using <b>NumPy</b> and <b>Scikit-learn</b>.',
            'Built practical ML pipelines with cross-validation, feature engineering, and model evaluation workflows.',
        ],
    },
    "yashb98/BrainWave": {
        "title": "BrainWave - Modern UI/UX Website | React.js | Tailwind CSS",
        "url": "https://github.com/yashb98/BrainWave",
        "bullets": [
            'Built modern UI/UX website with <b>React.js</b> and <b>Tailwind CSS</b>, featuring seamless animations and responsive design.',
            'Implemented component-based architecture with Vite for fast development and optimised production builds.',
        ],
    },
    "yashb98/pytorch": {
        "title": "PyTorch Deep Learning Notebooks | Python | PyTorch",
        "url": "https://github.com/yashb98/pytorch",
        "bullets": [
            'Built deep learning implementations in <b>PyTorch</b> covering CNNs, RNNs, and transfer learning architectures.',
            'Implemented training pipelines with custom datasets, data augmentation, and performance benchmarking.',
        ],
    },
    "yashb98/genvoice-connect": {
        "title": "GenVoice Connect - AI Voice Assistant | TypeScript | Next.js | Generative AI",
        "url": "https://github.com/yashb98/genvoice-connect",
        "bullets": [
            'Built intelligent virtual assistant enabling website navigation through conversational <b>voice commands</b> using Generative AI.',
            'Designed voice-to-action pipeline replacing traditional skill-based interfaces with natural language understanding.',
            'Deployed with <b>Next.js</b> for production-ready server-side rendering and real-time voice interaction.',
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
    archetype: str | None = None,
) -> list[dict]:
    """Select the top N projects that best match the JD skills.

    Uses SkillGraphStore to find projects with highest skill overlap,
    then looks up CV-ready entries from PORTFOLIO + auto-generated entries.

    When archetype is provided, swaps in archetype-specific bullet variants
    so the CV emphasises the aspects most relevant to the JD type.

    Returns list of dicts matching generate_cv_pdf's projects format:
        [{"title": "...", "url": "...", "bullets": ["...", ...]}, ...]

    Falls back to DEFAULT_PROJECTS from generate_cv.py if no matches found.
    """
    from jobpulse.portfolio_variants import get_auto_entry, get_or_generate_variant_bullets
    from jobpulse.skill_graph_store import SkillGraphStore

    store = SkillGraphStore()
    all_skills = required_skills + (preferred_skills or [])

    try:
        matches = store.get_projects_for_skills(all_skills)
    except Exception as exc:
        logger.warning("project_portfolio: SkillGraphStore query failed: %s", exc)
        matches = []

    prioritized = []
    for match in matches:
        entry = PORTFOLIO.get(match.name)
        if not entry:
            entry = get_auto_entry(match.name)
        if entry:
            # Priority is a soft boost (1.15x for priority=1), not a hard sort
            priority = entry.get("priority", 99)
            boost = 1.15 if priority == 1 else 1.0
            score = match.relevance_score * boost
            prioritized.append((score, match, entry))

    prioritized.sort(key=lambda x: -x[0])

    selected: list[dict] = []
    for _score, match, entry in prioritized:
        if len(selected) >= top_n:
            break
        if entry:
            numbered = dict(entry)
            if archetype:
                numbered["bullets"] = get_or_generate_variant_bullets(
                    match.name, archetype,
                    entry["title"], entry["bullets"],
                    required_skills,
                )
            numbered["title"] = f"{len(selected) + 1}. {entry['title']}"
            selected.append(numbered)

    if not selected:
        from jobpulse.cv_templates.generate_cv import DEFAULT_PROJECTS
        return DEFAULT_PROJECTS

    return selected
