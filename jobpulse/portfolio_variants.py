"""Per-archetype bullet variants for CV projects.

Hand-crafted variants for hero projects ensure the #1 project always
shows the right framing. Auto-generated variants for other projects
are cached in data/portfolio_auto.json by the nightly sync.

Public API:
  get_variant_bullets(repo_name, archetype) -> list[str] | None
  generate_portfolio_entry(repo_name, description, readme, languages, url) -> dict | None
  generate_variant_bullets(title, bullets, archetype, archetype_keywords) -> list[str] | None
  load_auto_portfolio() -> dict
  save_auto_portfolio(data) -> None
"""

from __future__ import annotations

import json
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)

_AUTO_PATH = Path(__file__).parent.parent / "data" / "portfolio_auto.json"

# ---------------------------------------------------------------------------
# Hand-crafted variants for hero projects
# ---------------------------------------------------------------------------

MANUAL_VARIANTS: dict[str, dict[str, list[str]]] = {
    "yashb98/multi-agent-patterns": {
        "agentic": [
            'Built a <b>88,500+ LOC</b> production multi-agent system with <b>10+ autonomous agents</b> orchestrated via <b>4 LangGraph</b> patterns, featuring human-in-the-loop flows, Swarm-based routing, and tool-use integration running <b>24/7</b>.',
            'Designed agentic architectures with <b>GRPO experiential learning</b>, persona evolution, and <b>A/B testing</b>, enabling self-improving agents with <b>99.9%</b> uptime across Gmail, Calendar, GitHub, and Notion.',
            'Engineered <b>5-gate pre-screen pipeline</b> with autonomous job scanning and application submission, processing <b>200+ JDs/day</b> with <b>92%+</b> skill match accuracy.',
            'Built multi-source <b>fact-checking</b> agent with Semantic Scholar API, <b>9.5/10</b> accuracy gate, and automated Google Drive document management.',
        ],
        "data_scientist": [
            'Built a <b>88,500+ LOC</b> production system with <b>2,350 tests</b>, deploying <b>ML classification pipelines</b> and statistical correlation engines that reduced costs by <b>96%</b> through hybrid rule-based and ML routing.',
            'Designed <b>GRPO experiential learning</b> framework with A/B testing and adaptive thresholds, applying reinforcement learning to optimise across <b>41 intent categories</b>.',
            'Engineered <b>NLP classification pipeline</b> with 3-tier architecture (regex, semantic embeddings, LLM fallback) and <b>250+ training examples</b> with sub-5ms embedding inference.',
            'Built <b>statistical correlation engine</b> tracking <b>17 verification signals</b> with per-bucket block rate analysis and automated pattern detection.',
        ],
        "data_analyst": [
            'Built <b>4 analytics dashboards</b> tracking conversion funnels, platform breakdowns, and gate statistics across <b>200+ daily job listings</b> with automated weekly comparison reports.',
            'Designed automated <b>ETL pipelines</b> aggregating data from LinkedIn, Indeed, Reed, and Greenhouse APIs into <b>21 SQLite databases</b>, cutting manual data collection by <b>95%</b>.',
            'Engineered <b>ATS scoring system</b> with deterministic keyword matching and statistical analysis, producing <b>0-100</b> match scores with category-level breakdowns.',
            'Built <b>rejection pattern analysis</b> with blocker classification, cohort comparison, and data-driven recommendations improving application conversion rates.',
        ],
        "ai_ml": [
            'Built a <b>88,500+ LOC</b> production AI system with <b>2,350 tests</b>, implementing <b>GRPO experiential learning</b>, NLP pipelines, and embedding-based semantic search across <b>19,400+</b> indexed nodes.',
            'Designed <b>3-tier NLP pipeline</b> (regex, semantic embeddings, LLM) with <b>250+ training examples</b> and sub-5ms inference through TF-IDF vectorisation and cosine similarity.',
            'Engineered hybrid classification reducing LLM calls by <b>96%</b> through rule-based extraction (<b>582-entry taxonomy</b>) with ML fallback, processing <b>200+ JDs/day</b>.',
            'Built <b>fact-checking pipeline</b> with Semantic Scholar API, NLI scoring, and chain-of-thought verification achieving <b>9.5/10</b> accuracy gate.',
        ],
        "data_engineer": [
            'Built a <b>88,500+ LOC</b> production system with <b>21 SQLite databases</b>, <b>412 files</b>, and automated data pipelines running <b>24/7</b> via macOS launchd with cron scheduling.',
            'Designed <b>multi-source data ingestion</b> from GitHub, LinkedIn, Indeed, Reed, arXiv, Gmail, and Notion APIs with adaptive rate limiting and cross-platform deduplication.',
            'Engineered <b>5-gate data processing pipeline</b> with rule-based extraction (<b>582-entry taxonomy</b>), statistical correlation, and SQLite caching, processing <b>200+ records/day</b>.',
            'Built <b>scan learning engine</b> tracking <b>17 signals</b> per session with statistical analysis, automated cooldown scheduling, and persistent SQLite event storage.',
        ],
        "data_platform": [
            'Built a <b>88,500+ LOC</b> production system with <b>2,350 tests</b>, <b>CI/CD</b> via GitHub Actions, and <b>10+ autonomous services</b> running 24/7 with <b>99.9%</b> uptime.',
            'Designed <b>MLOps pipeline</b> with model evaluation, A/B testing, and <b>GRPO experiential learning</b> for automated model selection across <b>41 classification categories</b>.',
            'Engineered <b>rate-limited automation</b> across 5 platforms with adaptive delays, session management, and <b>17-signal verification detection</b>.',
            'Built <b>monitoring and alerting</b> with 5 Telegram bots, automated error reporting, conversion funnel tracking, and weekly dashboards.',
        ],
    },
    "yashb98/DataMind": {
        "agentic": [
            'Built AI analytics platform with <b>48-agent</b> Digital Labor Workforce using multi-agent orchestration for autonomous <b>ETL</b>, anomaly detection, and reporting.',
            'Designed <b>8-layer</b> anti-hallucination stack with NLI scoring and chain-of-thought auditing, achieving <b>95%+</b> factual accuracy on agent-generated insights.',
            'Implemented agent coordination across <b>Apache Kafka</b> streaming, <b>DuckDB</b> analytics, and <b>Pinecone</b> vector search, processing <b>1M+</b> records.',
        ],
        "data_scientist": [
            'Built AI analytics platform processing <b>1M+</b> records with automated anomaly detection, statistical profiling, and ML-driven insight generation.',
            'Designed <b>8-layer</b> anti-hallucination stack with NLI scoring achieving <b>95%+</b> factual accuracy on generated predictions and recommendations.',
            'Implemented <b>DuckDB</b> analytical queries with <b>Pinecone</b> vector search for semantic similarity, enabling natural language queries with sub-second latency.',
        ],
        "data_analyst": [
            'Built AI analytics platform automating <b>ETL</b>, anomaly detection, and dashboard generation across structured and unstructured data, processing <b>1M+</b> records.',
            'Designed automated reporting pipeline with <b>48 agents</b> handling data profiling, trend analysis, and insight generation with <b>95%+</b> accuracy.',
            'Implemented <b>DuckDB</b> for analytical queries and <b>Apache Kafka</b> real-time streaming, enabling sub-second dashboard refreshes.',
        ],
        "data_engineer": [
            'Built multi-cloud lakehouse with <b>Apache Kafka</b> for real-time streaming, <b>DuckDB</b> for analytical queries, and <b>Pinecone</b> for vector search, processing <b>1M+</b> records.',
            'Designed <b>ETL</b> orchestration across 48 processing agents handling data ingestion, transformation, and loading across structured and unstructured sources.',
            'Implemented automated data quality pipeline with <b>8-layer</b> validation stack achieving <b>95%+</b> data accuracy on processed outputs.',
        ],
        "data_platform": [
            'Built AI analytics platform with <b>48-agent</b> workforce deployed via <b>Docker</b>, handling autonomous <b>ETL</b>, anomaly detection, and reporting across <b>1M+</b> records.',
            'Designed <b>Apache Kafka</b> real-time streaming pipeline with <b>DuckDB</b> analytics and <b>Pinecone</b> vector search, achieving sub-second query latency.',
            'Implemented <b>8-layer</b> anti-hallucination stack with automated model output verification, ensuring <b>95%+</b> accuracy in production responses.',
        ],
    },
    "yashb98/Velox_AI": {
        "data_analyst": [
            'Built real-time <b>dashboards</b> tracking voice agent performance across <b>1,000+</b> concurrent sessions, reducing anomaly detection time by <b>70%</b>.',
            'Automated analysis of <b>50K+</b> daily session metrics via <b>API</b> pipelines with statistical profiling, cutting manual reporting by <b>80%</b>.',
            'Delivered <b>sub-150ms</b> response times through performance analysis and query optimisation on <b>GCP</b>.',
        ],
        "data_platform": [
            'Built enterprise voice agent platform handling <b>1,000+</b> concurrent sessions with <b>sub-150ms</b> response times on <b>GCP</b> infrastructure.',
            'Automated <b>API</b>-driven monitoring pipelines tracking <b>50K+</b> daily session metrics with anomaly detection and alerting.',
            'Designed real-time dashboards reducing operational anomaly detection by <b>70%</b> and manual reporting by <b>80%</b>.',
        ],
        "data_engineer": [
            'Built <b>API</b>-driven data pipelines processing <b>50K+</b> daily session metrics from enterprise voice platform with automated ingestion and aggregation.',
            'Designed real-time monitoring infrastructure on <b>GCP</b> handling <b>1,000+</b> concurrent sessions with <b>sub-150ms</b> query latency.',
            'Automated metric collection and reporting pipelines, reducing manual data processing by <b>80%</b>.',
        ],
    },
    "yashb98/nexusmind": {
        "data_scientist": [
            'Built <b>NLP</b> pipelines extracting insights from <b>10K+</b> unstructured documents with <b>94%</b> retrieval precision using vector embeddings.',
            'Developed <b>clustering</b> workflows grouping <b>500+</b> policy documents by topic, risk level, and compliance status using unsupervised learning.',
            'Optimised vector insertion pipeline, reducing indexing time by <b>60%</b> through batch processing and embedding optimisation.',
        ],
        "data_analyst": [
            'Built automated dashboards surfacing compliance metrics from <b>10K+</b> unstructured documents, reducing manual review time by <b>55%</b>.',
            'Developed <b>clustering</b> workflows categorising <b>500+</b> policy documents by topic and risk level for compliance reporting.',
            'Designed <b>NLP</b> extraction pipelines with <b>94%</b> retrieval precision, enabling automated policy analysis and trend identification.',
        ],
        "ai_ml": [
            'Built <b>NLP</b> pipelines with vector embeddings achieving <b>94%</b> retrieval precision on <b>10K+</b> unstructured documents using <b>Pinecone</b>.',
            'Developed unsupervised <b>clustering</b> models grouping <b>500+</b> documents by semantic similarity across topic, risk, and compliance dimensions.',
            'Optimised vector insertion pipeline, reducing indexing time by <b>60%</b> through batch processing and embedding dimensionality tuning.',
        ],
    },
    "yashb98/90Days_Machine_learinng": {
        "data_scientist": [
            '<b>30+ ML projects</b> spanning NLP, forecasting, and statistical testing, with t-test, chi-squared, and ANOVA across <b>15+ datasets</b> improving prediction accuracy by <b>12%</b>.',
            'Implemented <b>ML pipelines</b> with feature engineering, cross-validation, and hyperparameter tuning using <b>Scikit-learn</b> (regression, random forest, gradient boosting, K-means).',
            'Built web scraping pipelines collecting <b>100K+ records</b> and automated <b>EDA</b> workflows with Pandas profiling across <b>20+</b> business domains.',
        ],
        "data_analyst": [
            'Ran statistical tests (t-test, chi-squared, ANOVA) across <b>15+ datasets</b>, standardising data cleaning workflows resolving format issues in <b>40%</b> of raw inputs.',
            'Automated <b>SQL</b> data extraction and <b>EDA</b> workflows with Pandas profiling, generating reproducible analysis reports across <b>20+</b> business domains.',
            'Built <b>30+ projects</b> spanning web scraping, clustering, and forecasting, collecting <b>100K+ records</b> from multiple sources using BeautifulSoup and Scrapy.',
        ],
        "ai_ml": [
            'Implemented <b>ML pipelines</b> with feature engineering, cross-validation, and hyperparameter tuning across regression, random forest, gradient boosting, and K-means models.',
            '<b>30+ projects</b> spanning NLP, clustering, and forecasting with <b>Scikit-learn</b>, improving prediction accuracy by <b>12%</b> through statistical testing.',
            'Built web scraping pipelines collecting <b>100K+ records</b> and automated preprocessing workflows resolving format issues in <b>40%</b> of raw inputs.',
        ],
    },
    "yashb98/Fintech_customer_churn": {
        "data_scientist": [
            'Built churn prediction model achieving <b>87%</b> accuracy using gradient boosting with <b>SHAP</b>-based explainability on <b>10K+</b> customer records.',
            'Identified <b>5</b> key churn drivers through correlation and cohort analysis, with automated <b>A/B test</b> simulation validating intervention effectiveness.',
            'Designed end-to-end <b>ML pipeline</b> with feature selection (mutual information + RFE), model training, and stakeholder presentation reports.',
        ],
        "data_analyst": [
            'Analysed <b>10K+</b> customer records identifying <b>5</b> key churn drivers through correlation analysis, cohort segmentation, and trend visualisation.',
            'Delivered actionable retention strategies reducing predicted churn by <b>18%</b>, with <b>A/B test</b> simulation validating intervention effectiveness.',
            'Built <b>SHAP</b>-based explainability reports translating ML predictions into business recommendations for stakeholder presentations.',
        ],
    },
}


# ---------------------------------------------------------------------------
# Auto-generated portfolio cache
# ---------------------------------------------------------------------------


def load_auto_portfolio() -> dict:
    try:
        with open(_AUTO_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"entries": {}, "variants": {}, "last_synced": {}}


def save_auto_portfolio(data: dict) -> None:
    _AUTO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_AUTO_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("portfolio_variants: saved auto portfolio to %s", _AUTO_PATH)


# ---------------------------------------------------------------------------
# Public API: variant selection
# ---------------------------------------------------------------------------


def get_variant_bullets(repo_name: str, archetype: str) -> list[str] | None:
    """Return archetype-specific bullets for hero projects only (instant, no LLM).

    For non-hero projects, returns None — caller should use
    get_or_generate_variant_bullets() for on-demand JD-aware generation.
    """
    return MANUAL_VARIANTS.get(repo_name, {}).get(archetype)


def get_auto_entry(repo_name: str) -> dict | None:
    """Return an auto-generated PORTFOLIO entry for a repo not in the manual PORTFOLIO."""
    auto = load_auto_portfolio()
    return auto.get("entries", {}).get(repo_name)


def get_or_generate_variant_bullets(
    repo_name: str,
    archetype: str,
    title: str,
    default_bullets: list[str],
    jd_skills: list[str],
) -> list[str]:
    """Return tailored bullets for a project based on archetype + JD skills.

    Priority:
    1. Manual variants (hero projects) — instant, no LLM
    2. Cached on-demand variants — instant, previously generated
    3. Generate on-demand via LLM using actual JD skills — cache for reuse

    Falls back to default_bullets if generation fails.
    """
    manual = MANUAL_VARIANTS.get(repo_name, {}).get(archetype)
    if manual:
        return manual

    auto = load_auto_portfolio()
    cached = auto.get("variants", {}).get(repo_name, {}).get(archetype)
    if cached:
        return cached

    generated = _generate_jd_aware_bullets(title, default_bullets, archetype, jd_skills)
    if not generated:
        return default_bullets

    variants = auto.setdefault("variants", {})
    variants.setdefault(repo_name, {})[archetype] = generated
    save_auto_portfolio(auto)

    return generated


# ---------------------------------------------------------------------------
# On-demand LLM generation (called at CV time with actual JD context)
# ---------------------------------------------------------------------------

_VARIANT_PROMPT = """Reframe these CV project bullets for a {archetype_label} role. The job requires: {jd_skills}.

Project: {title}
Original bullets:
{bullets_text}

Rules:
- Write exactly 3 bullets
- Emphasise aspects matching the job's required skills: {jd_skills}
- Naturally weave in soft skills (teamwork, communication, decision making, adaptability, leadership) where they fit the project context. Do NOT list them, demonstrate them through action.
- Keep all quantified metrics from originals, reframe the narrative
- Wrap key metrics in <b> HTML tags
- Professional tone, no conversational language
- No em-dashes, en-dashes, or double dashes

Output ONLY a JSON list: ["bullet1", "bullet2", "bullet3"]"""

_ARCHETYPE_LABELS = {
    "agentic": "AI/Agentic Engineer",
    "data_scientist": "Data Scientist",
    "data_analyst": "Data Analyst",
    "ai_ml": "AI/ML Engineer",
    "data_engineer": "Data Engineer",
    "data_platform": "ML Platform/MLOps Engineer",
}


def _generate_jd_aware_bullets(
    title: str,
    bullets: list[str],
    archetype: str,
    jd_skills: list[str],
) -> list[str] | None:
    """Generate JD-tailored bullet variants via LLM at CV time."""
    label = _ARCHETYPE_LABELS.get(archetype)
    if not label:
        return None

    from shared.agents import get_llm, smart_llm_call

    bullets_text = "\n".join(f"- {b}" for b in bullets)
    prompt = _VARIANT_PROMPT.format(
        title=title,
        bullets_text=bullets_text,
        archetype_label=label,
        jd_skills=", ".join(jd_skills[:15]),
    )

    try:
        llm = get_llm(model="gpt-5-mini", temperature=0.3)
        response = smart_llm_call(llm, prompt)
        text = response.content if hasattr(response, "content") else str(response)

        start = text.find("[")
        end = text.rfind("]") + 1
        if start < 0 or end <= start:
            return None

        parsed = json.loads(text[start:end])
        if not isinstance(parsed, list) or len(parsed) < 2:
            return None

        return parsed[:4]
    except Exception as exc:
        logger.warning("portfolio_variants: on-demand generation failed for %s/%s: %s", title, archetype, exc)
        return None


# ---------------------------------------------------------------------------
# Portfolio entry generation (used by nightly sync for missing repos)
# ---------------------------------------------------------------------------

_ENTRY_PROMPT = """Generate a CV project entry for this GitHub repository. Output ONLY valid JSON.

Repository: {repo_name}
Description: {description}
Languages: {languages}
Topics: {topics}

README excerpt (first 2000 chars):
{readme}

Output format:
{{"title": "Project Name | Tech1 | Tech2 | Tech3", "bullets": ["bullet1", "bullet2", "bullet3"]}}

Rules:
- Title: concise project name + key technologies separated by pipes
- 3 achievement bullets with quantified metrics
- Wrap key metrics in <b> HTML tags (e.g., <b>95%</b>)
- Professional tone, no conversational language
- No em-dashes, en-dashes, or double dashes
- If README lacks metrics, estimate reasonable ones from the project scope"""


def generate_portfolio_entry(
    repo_name: str,
    description: str,
    readme_content: str,
    languages: list[str],
    topics: list[str],
    url: str,
) -> dict | None:
    """Generate a PORTFOLIO entry for a repo via LLM. Returns dict with title, url, bullets."""
    from shared.agents import get_llm, smart_llm_call

    prompt = _ENTRY_PROMPT.format(
        repo_name=repo_name.split("/")[-1],
        description=description or "No description",
        languages=", ".join(languages) if languages else "Python",
        topics=", ".join(topics) if topics else "N/A",
        readme=readme_content[:2000] if readme_content else "No README available",
    )

    try:
        llm = get_llm(model="gpt-5-mini", temperature=0.3)
        response = smart_llm_call(llm, prompt)
        text = response.content if hasattr(response, "content") else str(response)

        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return None

        parsed = json.loads(text[start:end])
        if "title" not in parsed or "bullets" not in parsed:
            return None

        return {
            "title": parsed["title"],
            "url": url,
            "bullets": parsed["bullets"][:4],
        }
    except Exception as exc:
        logger.warning("portfolio_variants: entry generation failed for %s: %s", repo_name, exc)
        return None
