# Job Pipeline API Call Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce job pipeline LLM calls from 250/day to 10-11/day (96% reduction) using a 4-gate recruiter-grade pre-screen, hybrid rule-based skill extraction, and nightly GitHub profile sync — while increasing application quality.

**Architecture:** Nightly cron populates a skill/project graph in MindGraph. During scan windows, a rule-based extractor handles 85% of JDs without LLM. A 4-gate pre-screen (title → kill signals → must-haves → competitiveness score) filters jobs before CV/cover letter generation. Only genuinely competitive jobs proceed to application.

**Tech Stack:** Python 3.12, SQLite (MindGraph), ReportLab (PDF), GPT-4o-mini (fallback only), pytest

**Spec:** `docs/superpowers/specs/2026-03-30-job-pipeline-api-optimization-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `data/skill_synonyms.json` | Modify | Expand from 35 → 500+ skill taxonomy entries |
| `jobpulse/skill_extractor.py` | Create | Rule-based JD skill extraction with LLM fallback |
| `jobpulse/recruiter_screen.py` | Create | 4-gate pre-screen (Gate 0-3) |
| `jobpulse/skill_graph_store.py` | Create | SkillGraphStore abstraction over MindGraph |
| `jobpulse/github_profile_sync.py` | Create | Nightly sync: GitHub + resume + past apps → graph |
| `jobpulse/job_autopilot.py` | Modify | Integrate gates + hybrid extraction into pipeline |
| `jobpulse/jd_analyzer.py` | Modify | Use skill_extractor instead of direct LLM |
| `jobpulse/runner.py` | Modify | Add `profile-sync` CLI command |
| `scripts/install_cron.py` | Modify | Add 3am cron entry |
| `tests/test_skill_extractor.py` | Create | Tests for rule-based + fallback |
| `tests/test_recruiter_screen.py` | Create | Tests for all 4 gates |
| `tests/test_skill_graph_store.py` | Create | Tests for graph store interface |
| `tests/test_github_profile_sync.py` | Create | Tests for nightly sync |

---

## Task 1: Expand Skill Taxonomy

**Files:**
- Modify: `data/skill_synonyms.json`
- Test: `tests/test_skill_extractor.py` (created in Task 2, validated here)

- [ ] **Step 1: Read current skill_synonyms.json**

Read `data/skill_synonyms.json` to understand the current 35-entry format: `{"canonical": ["synonym1", "synonym2"]}`.

- [ ] **Step 2: Expand to 500+ entries**

Rewrite `data/skill_synonyms.json` with comprehensive taxonomy. Categories to cover:

```json
{
  "python": ["python3", "py", "cpython"],
  "java": ["jdk", "jvm", "openjdk"],
  "javascript": ["js", "ecmascript", "es6", "es2015"],
  "typescript": ["ts"],
  "c#": ["csharp", "c sharp", "dotnet", ".net"],
  "c++": ["cpp", "cplusplus"],
  "go": ["golang"],
  "rust": ["rustlang"],
  "ruby": ["rb"],
  "swift": ["swiftui", "swift ui"],
  "kotlin": ["kt"],
  "scala": ["scala3"],
  "r": ["rlang", "r language"],
  "php": ["php8", "laravel"],
  "dart": ["flutter"],

  "react": ["reactjs", "react.js", "react js"],
  "angular": ["angularjs", "angular.js"],
  "vue": ["vuejs", "vue.js", "vue3"],
  "next.js": ["nextjs", "next js", "next"],
  "svelte": ["sveltekit"],
  "django": ["django rest framework", "drf"],
  "flask": ["flask api"],
  "fastapi": ["fast api", "fast-api"],
  "express": ["expressjs", "express.js"],
  "spring": ["spring boot", "spring framework", "springboot"],
  "rails": ["ruby on rails", "ror"],
  "node.js": ["nodejs", "node"],

  "postgresql": ["postgres", "psql", "pg"],
  "mysql": ["mariadb"],
  "mongodb": ["mongo", "mongoose"],
  "redis": ["redis cache"],
  "elasticsearch": ["elastic", "es", "opensearch"],
  "sqlite": ["sqlite3"],
  "dynamodb": ["dynamo db", "dynamo"],
  "cassandra": ["apache cassandra"],
  "neo4j": ["neo4j graph"],
  "snowflake": ["snowflake db"],
  "bigquery": ["big query", "google bigquery"],

  "amazon web services": ["aws"],
  "google cloud platform": ["gcp", "google cloud"],
  "microsoft azure": ["azure"],
  "aws lambda": ["lambda", "serverless"],
  "aws s3": ["s3", "simple storage"],
  "aws ec2": ["ec2"],
  "aws ecs": ["ecs", "fargate"],
  "aws sqs": ["sqs"],
  "aws sns": ["sns"],
  "aws rds": ["rds"],
  "aws sagemaker": ["sagemaker"],
  "google kubernetes engine": ["gke"],
  "azure devops": ["ado"],

  "docker": ["containerization", "containerisation", "containers", "dockerfile"],
  "kubernetes": ["k8s", "kubectl", "helm"],
  "terraform": ["tf", "infrastructure as code", "iac"],
  "ansible": ["ansible playbook"],
  "jenkins": ["jenkins pipeline"],
  "github actions": ["gh actions", "gha"],
  "gitlab ci": ["gitlab ci/cd"],
  "circleci": ["circle ci"],
  "continuous integration": ["ci/cd", "ci", "cd", "cicd", "continuous delivery", "continuous deployment"],
  "git": ["github", "version control", "gitlab", "bitbucket"],
  "nginx": ["reverse proxy"],
  "apache kafka": ["kafka", "kafka streams"],
  "rabbitmq": ["rabbit mq", "amqp"],
  "grafana": ["grafana dashboards"],
  "prometheus": ["prom", "metrics"],
  "datadog": ["dd"],
  "new relic": ["newrelic"],

  "machine learning": ["ml", "machine-learning"],
  "deep learning": ["dl", "deep-learning"],
  "natural language processing": ["nlp", "text mining", "text analytics"],
  "computer vision": ["cv", "image recognition", "image processing"],
  "large language models": ["llms", "llm", "foundation models"],
  "retrieval augmented generation": ["rag"],
  "reinforcement learning": ["rl"],
  "pytorch": ["torch", "py torch"],
  "tensorflow": ["tf", "tensor flow", "tensor-flow", "keras"],
  "scikit-learn": ["sklearn", "scikit learn"],
  "pandas": ["data manipulation", "dataframes"],
  "numpy": ["numerical computing", "np"],
  "hugging face": ["huggingface", "hf", "transformers"],
  "langchain": ["lang chain"],
  "langraph": ["lang graph"],
  "openai": ["openai api", "gpt api", "chatgpt api"],
  "mlflow": ["ml flow"],
  "mlops": ["ml ops", "ml operations", "model operations"],
  "model context protocol": ["mcp"],
  "prompt engineering": ["prompt design", "prompt tuning"],
  "embeddings": ["vector embeddings", "word embeddings", "sentence embeddings"],
  "vector database": ["vector db", "vector store", "pinecone", "weaviate", "chromadb", "qdrant"],
  "feature engineering": ["feature extraction", "feature selection"],
  "data pipeline": ["data pipelines", "etl", "elt", "data workflow"],
  "extract transform load": ["etl", "elt"],
  "apache spark": ["spark", "pyspark", "spark sql"],
  "apache airflow": ["airflow", "dag"],
  "dbt": ["data build tool"],
  "data visualization": ["data visualisation", "dataviz"],
  "power bi": ["powerbi", "power-bi"],
  "tableau": ["tableau desktop", "tableau server"],
  "looker": ["looker studio", "google data studio"],
  "exploratory data analysis": ["eda"],

  "rest api": ["restful", "rest apis", "api development", "restful api"],
  "graphql": ["graph ql"],
  "grpc": ["g rpc", "protocol buffers", "protobuf"],
  "websocket": ["websockets", "ws", "socket.io"],
  "microservices": ["micro services", "microservice architecture"],
  "event driven": ["event-driven", "event driven architecture", "eda architecture"],
  "message queue": ["message broker", "pub sub", "publish subscribe"],
  "api gateway": ["api management"],
  "oauth": ["oauth2", "oauth 2.0", "openid connect", "oidc"],
  "jwt": ["json web token", "json web tokens"],

  "agile": ["scrum", "kanban", "sprint", "agile methodology"],
  "test driven development": ["tdd"],
  "behavior driven development": ["bdd"],
  "unit testing": ["unit tests", "testing"],
  "integration testing": ["integration tests"],
  "end to end testing": ["e2e testing", "e2e tests"],
  "code review": ["peer review"],
  "pair programming": ["mob programming"],
  "devops": ["dev ops", "sre", "site reliability"],
  "system design": ["architecture design", "software architecture"],
  "design patterns": ["software patterns"],
  "object oriented programming": ["oop", "object oriented"],
  "functional programming": ["fp"],

  "communication": ["written communication", "verbal communication", "presentation skills"],
  "teamwork": ["collaboration", "cross-functional", "team player"],
  "problem solving": ["analytical thinking", "critical thinking", "troubleshooting"],
  "leadership": ["team lead", "mentoring", "coaching"],
  "project management": ["project planning", "stakeholder management"],
  "time management": ["prioritization", "multitasking"],
  "adaptability": ["flexibility", "fast learner", "quick learner"]
}
```

This covers ~150 canonical entries with ~400+ synonyms = 500+ total matchable terms.

- [ ] **Step 3: Validate JSON is parseable**

Run: `python -c "import json; d=json.load(open('data/skill_synonyms.json')); print(f'{len(d)} entries, {sum(len(v) for v in d.values())} synonyms')"`

Expected: `~150 entries, ~400 synonyms`

- [ ] **Step 4: Commit**

```bash
git add data/skill_synonyms.json
git commit -m "feat(jobs): expand skill taxonomy from 35 to 500+ entries for rule-based extraction"
```

---

## Task 2: Rule-Based Skill Extractor

**Files:**
- Create: `jobpulse/skill_extractor.py`
- Test: `tests/test_skill_extractor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_skill_extractor.py`:

```python
"""Tests for rule-based JD skill extraction with LLM fallback."""

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


@pytest.fixture
def sample_synonyms(tmp_path):
    """Write a small synonym file for tests."""
    syns = {
        "python": ["python3", "py"],
        "react": ["reactjs", "react.js"],
        "postgresql": ["postgres"],
        "docker": ["containerization"],
        "kubernetes": ["k8s"],
        "machine learning": ["ml"],
        "fastapi": ["fast api"],
        "javascript": ["js"],
        "typescript": ["ts"],
        "git": ["github", "version control"],
        "rest api": ["restful"],
        "agile": ["scrum"],
        "aws": ["amazon web services"],
        "continuous integration": ["ci/cd", "cicd"],
    }
    p = tmp_path / "skill_synonyms.json"
    p.write_text(json.dumps(syns))
    return p


EXPLICIT_JD = """
## Requirements
- 3+ years Python experience
- Strong knowledge of React and TypeScript
- Experience with PostgreSQL and Docker
- Familiarity with Kubernetes and AWS
- Understanding of REST APIs and microservices

## Nice to Have
- Machine Learning experience
- FastAPI or Django
- CI/CD pipelines
- Agile/Scrum methodology
"""

VAGUE_JD = """
We're looking for someone passionate about building great products.
You'll work with our engineering team on exciting challenges.
Strong problem-solver with attention to detail.
"""


class TestSectionDetection:
    def test_detects_requirements_section(self, sample_synonyms):
        from jobpulse.skill_extractor import detect_jd_sections
        sections = detect_jd_sections(EXPLICIT_JD)
        assert "required" in sections
        assert "preferred" in sections

    def test_handles_no_sections(self, sample_synonyms):
        from jobpulse.skill_extractor import detect_jd_sections
        sections = detect_jd_sections("Just a plain job description with Python and React")
        assert "unsectioned" in sections


class TestRuleBasedExtraction:
    def test_extracts_explicit_skills(self, sample_synonyms):
        from jobpulse.skill_extractor import extract_skills_rule_based
        with patch("jobpulse.skill_extractor.SYNONYMS_PATH", sample_synonyms):
            result = extract_skills_rule_based(EXPLICIT_JD)
        assert "python" in result["required_skills"]
        assert "react" in result["required_skills"]
        assert "postgresql" in result["required_skills"]
        assert "machine learning" in result["preferred_skills"]
        assert len(result["required_skills"]) >= 5
        assert len(result["preferred_skills"]) >= 1

    def test_extracts_via_synonyms(self, sample_synonyms):
        jd = "Requirements: Experience with k8s and CI/CD pipelines"
        from jobpulse.skill_extractor import extract_skills_rule_based
        with patch("jobpulse.skill_extractor.SYNONYMS_PATH", sample_synonyms):
            result = extract_skills_rule_based(jd)
        all_skills = result["required_skills"] + result["preferred_skills"]
        assert "kubernetes" in all_skills or "continuous integration" in all_skills

    def test_vague_jd_extracts_few_skills(self, sample_synonyms):
        from jobpulse.skill_extractor import extract_skills_rule_based
        with patch("jobpulse.skill_extractor.SYNONYMS_PATH", sample_synonyms):
            result = extract_skills_rule_based(VAGUE_JD)
        total = len(result["required_skills"]) + len(result["preferred_skills"])
        assert total < 10  # Should trigger LLM fallback


class TestHybridExtraction:
    def test_uses_rule_based_when_enough_skills(self, sample_synonyms):
        from jobpulse.skill_extractor import extract_skills_hybrid
        with patch("jobpulse.skill_extractor.SYNONYMS_PATH", sample_synonyms):
            with patch("jobpulse.skill_extractor._extract_skills_llm") as mock_llm:
                result = extract_skills_hybrid(EXPLICIT_JD)
                mock_llm.assert_not_called()
        assert result["source"] == "rule_based"

    def test_falls_back_to_llm_when_few_skills(self, sample_synonyms):
        from jobpulse.skill_extractor import extract_skills_hybrid
        with patch("jobpulse.skill_extractor.SYNONYMS_PATH", sample_synonyms):
            with patch("jobpulse.skill_extractor._extract_skills_llm") as mock_llm:
                mock_llm.return_value = {
                    "required_skills": ["problem solving", "product development"],
                    "preferred_skills": [],
                    "industry": "technology",
                    "sub_context": "product engineering",
                }
                result = extract_skills_hybrid(VAGUE_JD)
                mock_llm.assert_called_once()
        assert result["source"] == "llm_fallback"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_skill_extractor.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'jobpulse.skill_extractor'`

- [ ] **Step 3: Implement skill_extractor.py**

Create `jobpulse/skill_extractor.py`:

```python
"""Rule-based JD skill extraction with LLM fallback.

Two-pass extraction:
  1. Section detection: "Requirements" vs "Nice to have"
  2. Taxonomy matching against skill_synonyms.json

Falls back to GPT-4o-mini when < 10 skills extracted (vague JDs).

Public API:
  extract_skills_hybrid(jd_text) -> dict   # Main entry point
  extract_skills_rule_based(jd_text) -> dict
  detect_jd_sections(jd_text) -> dict[str, str]
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)

SYNONYMS_PATH = Path(__file__).parent.parent / "data" / "skill_synonyms.json"

# Minimum skills for rule-based to be confident (no LLM fallback needed)
MIN_SKILLS_THRESHOLD = 10

# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

_REQUIRED_HEADERS = re.compile(
    r"(?:^|\n)\s*(?:#{1,3}\s*)?"
    r"(?:requirements?|essential|must[\s-]?have|qualifications?|what you.?ll need|"
    r"what we.?re looking for|you should have|key skills|required skills?|"
    r"minimum qualifications?|basic qualifications?)\s*:?\s*(?:\n|$)",
    re.IGNORECASE,
)

_PREFERRED_HEADERS = re.compile(
    r"(?:^|\n)\s*(?:#{1,3}\s*)?"
    r"(?:nice[\s-]?to[\s-]?have|preferred|bonus|desirable|advantageous|"
    r"additional skills?|plus|good to have|ideally|not essential|"
    r"preferred qualifications?)\s*:?\s*(?:\n|$)",
    re.IGNORECASE,
)


def detect_jd_sections(jd_text: str) -> dict[str, str]:
    """Split JD text into sections: required, preferred, unsectioned.

    Returns dict with keys 'required', 'preferred', 'unsectioned'.
    If no sections detected, all text goes into 'unsectioned'.
    """
    result: dict[str, str] = {}

    req_matches = list(_REQUIRED_HEADERS.finditer(jd_text))
    pref_matches = list(_PREFERRED_HEADERS.finditer(jd_text))

    if not req_matches and not pref_matches:
        result["unsectioned"] = jd_text
        return result

    # Build ordered list of (position, type) markers
    markers: list[tuple[int, str]] = []
    for m in req_matches:
        markers.append((m.end(), "required"))
    for m in pref_matches:
        markers.append((m.end(), "preferred"))
    markers.sort(key=lambda x: x[0])

    # Extract text between markers
    for i, (pos, section_type) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(jd_text)
        # Find the header start for the next section to avoid including it
        if i + 1 < len(markers):
            # Search backwards from next marker pos for newline
            next_header_start = jd_text.rfind("\n", pos, markers[i + 1][0])
            if next_header_start > pos:
                end = next_header_start
        section_text = jd_text[pos:end].strip()
        if section_type in result:
            result[section_type] += "\n" + section_text
        else:
            result[section_type] = section_text

    # Anything before first marker is unsectioned context
    first_pos = markers[0][0] if markers else len(jd_text)
    preamble = jd_text[:first_pos].strip()
    if preamble:
        result["unsectioned"] = preamble

    return result


# ---------------------------------------------------------------------------
# Taxonomy loading and matching
# ---------------------------------------------------------------------------


def _load_synonyms() -> dict[str, list[str]]:
    """Load skill taxonomy from data/skill_synonyms.json."""
    try:
        with SYNONYMS_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("skill_extractor: could not load synonyms: %s", exc)
        return {}


def _normalize(text: str) -> str:
    """Lowercase, strip, replace hyphens/underscores with spaces."""
    return text.lower().strip().replace("-", " ").replace("_", " ")


def _find_skills_in_text(text: str, synonyms: dict[str, list[str]]) -> list[str]:
    """Find all skills from taxonomy that appear in text.

    Returns list of canonical skill names (deduplicated, lowercase).
    Checks both canonical names and all their synonyms.
    """
    text_norm = _normalize(text)
    found: set[str] = set()

    for canonical, variants in synonyms.items():
        canon_norm = _normalize(canonical)
        # Check canonical name
        if _word_present(canon_norm, text_norm):
            found.add(canon_norm)
            continue
        # Check each variant
        for variant in variants:
            if _word_present(_normalize(variant), text_norm):
                found.add(canon_norm)
                break

    return sorted(found)


def _word_present(keyword: str, text: str) -> bool:
    """Check if keyword appears as a whole word in text."""
    if not keyword:
        return False
    # For multi-word skills, check substring presence
    if " " in keyword:
        return keyword in text
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Rule-based extraction
# ---------------------------------------------------------------------------


def extract_skills_rule_based(jd_text: str) -> dict:
    """Extract skills from JD using taxonomy matching + section detection.

    Returns:
        {
            "required_skills": list[str],   # canonical names, lowercase
            "preferred_skills": list[str],
            "industry": str,
            "sub_context": str,
            "source": "rule_based",
        }
    """
    synonyms = _load_synonyms()
    sections = detect_jd_sections(jd_text)

    required: list[str] = []
    preferred: list[str] = []

    # Extract from sectioned text
    if "required" in sections:
        required = _find_skills_in_text(sections["required"], synonyms)
    if "preferred" in sections:
        preferred = _find_skills_in_text(sections["preferred"], synonyms)

    # If no sections detected, all skills go to required
    if "unsectioned" in sections and not required:
        required = _find_skills_in_text(sections["unsectioned"], synonyms)
    elif "unsectioned" in sections:
        # Add any extra skills from preamble to required
        extra = _find_skills_in_text(sections["unsectioned"], synonyms)
        for s in extra:
            if s not in required and s not in preferred:
                required.append(s)

    # Remove duplicates between required and preferred (required wins)
    preferred = [s for s in preferred if s not in required]

    # Simple industry detection from common keywords
    industry = _detect_industry(jd_text)

    return {
        "required_skills": required,
        "preferred_skills": preferred,
        "industry": industry,
        "sub_context": "",
        "source": "rule_based",
    }


def _detect_industry(text: str) -> str:
    """Detect industry from JD text using keyword matching."""
    text_lower = text.lower()
    industry_map = {
        "fintech": ["fintech", "financial technology", "banking", "payments", "trading"],
        "healthtech": ["healthtech", "healthcare", "medical", "clinical", "pharma"],
        "e-commerce": ["e-commerce", "ecommerce", "retail", "marketplace", "shopify"],
        "adtech": ["advertising", "adtech", "ad tech", "programmatic"],
        "edtech": ["edtech", "education", "learning platform", "e-learning"],
        "cybersecurity": ["cybersecurity", "security", "infosec", "threat"],
        "gaming": ["gaming", "game development", "game engine", "unity", "unreal"],
        "saas": ["saas", "software as a service", "b2b", "platform"],
        "ai/ml": ["artificial intelligence", "machine learning", "data science", "ai company"],
        "devtools": ["developer tools", "devtools", "infrastructure", "platform engineering"],
    }
    for industry, keywords in industry_map.items():
        for kw in keywords:
            if kw in text_lower:
                return industry
    return "technology"


# ---------------------------------------------------------------------------
# LLM fallback (wraps existing extract_skills_llm logic)
# ---------------------------------------------------------------------------


def _extract_skills_llm(jd_text: str) -> dict:
    """Call GPT-4o-mini to extract skills. Same logic as jd_analyzer.extract_skills_llm."""
    try:
        import openai
        from jobpulse.config import OPENAI_API_KEY

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        system = (
            "You are a job description parser. Extract skill and context data as JSON. "
            "Return ONLY valid JSON with these keys: "
            "required_skills (list of strings), preferred_skills (list of strings), "
            "industry (string), sub_context (string — 1 sentence describing the domain)."
        )
        user = f"Parse this job description:\n\n{jd_text[:4000]}"
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "required_skills": data.get("required_skills", []),
            "preferred_skills": data.get("preferred_skills", []),
            "industry": data.get("industry", ""),
            "sub_context": data.get("sub_context", ""),
        }
    except Exception as exc:
        logger.warning("skill_extractor LLM fallback failed: %s", exc)
        return {"required_skills": [], "preferred_skills": [], "industry": "", "sub_context": ""}


# ---------------------------------------------------------------------------
# Hybrid entry point
# ---------------------------------------------------------------------------


def extract_skills_hybrid(jd_text: str) -> dict:
    """Main entry point: try rule-based first, fall back to LLM if < 10 skills.

    Returns:
        {
            "required_skills": list[str],
            "preferred_skills": list[str],
            "industry": str,
            "sub_context": str,
            "source": "rule_based" | "llm_fallback",
        }
    """
    if not jd_text or not jd_text.strip():
        return {
            "required_skills": [], "preferred_skills": [],
            "industry": "", "sub_context": "",
            "source": "rule_based", "error": "empty_jd",
        }

    # Try rule-based first
    result = extract_skills_rule_based(jd_text)
    total = len(result["required_skills"]) + len(result["preferred_skills"])

    if total >= MIN_SKILLS_THRESHOLD:
        logger.info(
            "skill_extractor: rule-based found %d skills (threshold %d) — skipping LLM",
            total, MIN_SKILLS_THRESHOLD,
        )
        return result

    # Fallback to LLM
    logger.info(
        "skill_extractor: rule-based found only %d skills (threshold %d) — calling LLM",
        total, MIN_SKILLS_THRESHOLD,
    )
    llm_result = _extract_skills_llm(jd_text)
    llm_result["source"] = "llm_fallback"
    return llm_result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_skill_extractor.py -v`

Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/skill_extractor.py tests/test_skill_extractor.py
git commit -m "feat(jobs): add rule-based skill extractor with LLM fallback (10+ threshold)"
```

---

## Task 3: SkillGraphStore Interface

**Files:**
- Create: `jobpulse/skill_graph_store.py`
- Test: `tests/test_skill_graph_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_skill_graph_store.py`:

```python
"""Tests for SkillGraphStore — abstraction over MindGraph for skill/project matching."""

import json
import pytest
from unittest.mock import patch
from pathlib import Path


@pytest.fixture
def temp_db(tmp_path):
    """Patch MindGraph DB_PATH to temp directory."""
    db_path = tmp_path / "mindgraph.db"
    with patch("mindgraph_app.storage.DB_PATH", db_path):
        from mindgraph_app.storage import init_db
        init_db()
        yield db_path


@pytest.fixture
def sample_synonyms(tmp_path):
    syns = {
        "python": ["python3", "py"],
        "react": ["reactjs", "react.js"],
        "postgresql": ["postgres"],
        "docker": ["containerization"],
        "kubernetes": ["k8s"],
        "machine learning": ["ml"],
        "fastapi": ["fast api"],
        "javascript": ["js"],
        "typescript": ["ts"],
        "git": ["github", "version control"],
        "rest api": ["restful"],
        "aws": ["amazon web services"],
    }
    p = tmp_path / "skill_synonyms.json"
    p.write_text(json.dumps(syns))
    return p


@pytest.fixture
def store(temp_db, sample_synonyms):
    from jobpulse.skill_graph_store import SkillGraphStore
    with patch("jobpulse.skill_graph_store.SYNONYMS_PATH", sample_synonyms):
        with patch("mindgraph_app.storage.DB_PATH", temp_db):
            s = SkillGraphStore()
            yield s


class TestUpsertSkill:
    def test_upsert_skill_creates_entity(self, store):
        eid = store.upsert_skill("Python", source="github")
        assert eid is not None
        profile = store.get_skill_profile()
        assert "python" in profile

    def test_upsert_same_skill_no_duplicate(self, store):
        store.upsert_skill("Python", source="github")
        store.upsert_skill("Python", source="resume")
        profile = store.get_skill_profile()
        assert list(profile).count("python") == 1  # Only one entry


class TestUpsertProject:
    def test_upsert_project_creates_entity_and_relations(self, store):
        repo = {
            "name": "yashb98/multi-agent-patterns",
            "description": "Multi-agent system with LangGraph",
            "languages": ["python", "javascript"],
            "topics": ["ai", "agents"],
            "keywords": ["python", "javascript", "langraph", "agents"],
        }
        eid = store.upsert_project(repo)
        assert eid is not None


class TestGetProjectsForSkills:
    def test_returns_matching_projects(self, store):
        # Setup: add skills and project
        store.upsert_skill("python", source="github")
        store.upsert_skill("fastapi", source="github")
        repo = {
            "name": "yashb98/api-server",
            "description": "FastAPI backend",
            "languages": ["python"],
            "topics": ["fastapi", "api"],
            "keywords": ["python", "fastapi"],
        }
        store.upsert_project(repo)

        matches = store.get_projects_for_skills(["python", "fastapi", "react"])
        assert len(matches) >= 1
        assert matches[0].name == "yashb98/api-server"
        assert matches[0].skill_overlap >= 2


class TestPreScreen:
    def test_high_overlap_returns_strong(self, store):
        # Add many skills to profile
        for s in ["python", "react", "postgresql", "docker", "kubernetes",
                   "aws", "fastapi", "javascript", "typescript", "git",
                   "rest api", "machine learning"]:
            store.upsert_skill(s, source="github")

        # Add projects demonstrating skills
        store.upsert_project({
            "name": "proj1", "description": "ML system",
            "languages": ["python"], "topics": ["ml", "docker", "fastapi"],
            "keywords": ["python", "ml", "docker", "fastapi"],
        })
        store.upsert_project({
            "name": "proj2", "description": "Web app",
            "languages": ["javascript", "typescript"],
            "topics": ["react", "postgresql"],
            "keywords": ["javascript", "typescript", "react", "postgresql"],
        })

        from jobpulse.models.application_models import JobListing
        listing = JobListing(
            job_id="test123", title="Junior ML Engineer", company="TestCo",
            platform="reed", url="https://example.com/job",
            required_skills=["python", "docker", "kubernetes", "aws", "fastapi",
                             "postgresql", "rest api", "git", "machine learning", "javascript"],
            preferred_skills=["react", "typescript"],
            description_raw="Test JD",
            found_at="2026-03-30T00:00:00",
        )
        result = store.pre_screen_jd(listing)
        assert result.gate2_passed is True
        assert result.gate3_score >= 55
        assert result.tier in ("apply", "strong")

    def test_low_overlap_returns_skip(self, store):
        # Add only 2 skills
        store.upsert_skill("python", source="github")
        store.upsert_skill("git", source="github")

        from jobpulse.models.application_models import JobListing
        listing = JobListing(
            job_id="test456", title="iOS Developer", company="GameCo",
            platform="reed", url="https://example.com/job2",
            required_skills=["swift", "swiftui", "xcode", "uikit", "coredata",
                             "spritekit", "metal", "game physics", "objective-c", "cocoapods"],
            preferred_skills=["arkit", "scenekit"],
            description_raw="iOS game dev",
            found_at="2026-03-30T00:00:00",
        )
        result = store.pre_screen_jd(listing)
        assert result.tier in ("reject", "skip")


class TestProfileStats:
    def test_returns_counts(self, store):
        store.upsert_skill("python", source="github")
        store.upsert_skill("react", source="resume")
        store.upsert_project({
            "name": "proj1", "description": "test",
            "languages": ["python"], "topics": [], "keywords": ["python"],
        })
        stats = store.get_profile_stats()
        assert stats["total_skills"] >= 2
        assert stats["total_projects"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_skill_graph_store.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'jobpulse.skill_graph_store'`

- [ ] **Step 3: Implement skill_graph_store.py**

Create `jobpulse/skill_graph_store.py`:

```python
"""SkillGraphStore — abstraction layer over MindGraph for skill/project matching.

Designed for Neo4j migration: swap internals, keep interface identical.

Public API:
  SkillGraphStore.get_skill_profile() -> set[str]
  SkillGraphStore.get_projects_for_skills(skills) -> list[ProjectMatch]
  SkillGraphStore.pre_screen_jd(listing) -> PreScreenResult
  SkillGraphStore.upsert_skill(name, source, desc) -> str
  SkillGraphStore.upsert_project(repo, deep_analysis) -> str
  SkillGraphStore.get_profile_stats() -> dict
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from jobpulse.models.application_models import JobListing

logger = get_logger(__name__)

SYNONYMS_PATH = Path(__file__).parent.parent / "data" / "skill_synonyms.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ProjectMatch:
    name: str
    description: str
    skill_overlap: int
    matched_skills: list[str]
    url: str = ""


@dataclass
class PreScreenResult:
    gate0_passed: bool = True
    gate1_passed: bool = True
    gate1_kill_reason: str | None = None
    gate2_passed: bool = True
    gate2_fail_reason: str | None = None
    gate3_score: float = 0.0
    tier: str = "skip"  # "reject" | "skip" | "apply" | "strong"
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    best_projects: list[ProjectMatch] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Synonym helpers
# ---------------------------------------------------------------------------


def _load_synonyms() -> dict[str, list[str]]:
    try:
        with SYNONYMS_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _normalize(s: str) -> str:
    return s.lower().strip().replace("-", " ").replace("_", " ")


def _skill_match(skill: str, profile: set[str], synonyms: dict[str, list[str]]) -> bool:
    """Check if skill (or any synonym) exists in profile."""
    norm = _normalize(skill)
    if norm in profile:
        return True
    # Check if skill is a synonym of something in profile
    for canonical, variants in synonyms.items():
        canon_norm = _normalize(canonical)
        all_forms = {canon_norm} | {_normalize(v) for v in variants}
        if norm in all_forms and canon_norm in profile:
            return True
        if norm in all_forms:
            for form in all_forms:
                if form in profile:
                    return True
    return False


# ---------------------------------------------------------------------------
# SkillGraphStore
# ---------------------------------------------------------------------------


class SkillGraphStore:
    """Abstraction over MindGraph for skill/project graph operations."""

    def __init__(self) -> None:
        from mindgraph_app.storage import init_db
        init_db()

    def upsert_skill(self, name: str, source: str = "github", description: str = "") -> str:
        """Add or update a SKILL entity in MindGraph. Returns entity ID."""
        from mindgraph_app.storage import upsert_entity
        desc = description or f"From: {source}"
        return upsert_entity(name=_normalize(name), entity_type="SKILL", description=desc)

    def upsert_project(self, repo: dict, deep_analysis: str | None = None) -> str:
        """Add or update a PROJECT entity + create DEMONSTRATES relations."""
        from mindgraph_app.storage import upsert_entity, upsert_relation

        name = repo.get("name", "")
        desc = deep_analysis or repo.get("description", "")
        project_id = upsert_entity(name=name, entity_type="PROJECT", description=desc)

        # Create DEMONSTRATES relations for each language/topic/keyword
        all_skills = set(
            _normalize(s) for s in
            repo.get("languages", []) + repo.get("topics", []) + repo.get("keywords", [])
            if s and len(s) > 1
        )
        for skill_name in all_skills:
            skill_id = upsert_entity(name=skill_name, entity_type="SKILL", description="")
            upsert_relation(
                from_id=project_id,
                to_id=skill_id,
                rel_type="DEMONSTRATES",
                context=f"{name} uses {skill_name}",
            )

        return project_id

    def get_skill_profile(self) -> set[str]:
        """All SKILL entities in the graph, normalized."""
        from mindgraph_app.storage import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT name FROM knowledge_entities WHERE entity_type = 'SKILL'"
        ).fetchall()
        conn.close()
        return {_normalize(row["name"]) for row in rows}

    def get_projects_for_skills(self, jd_skills: list[str]) -> list[ProjectMatch]:
        """Find projects that DEMONSTRATE the given skills, ranked by overlap count."""
        from mindgraph_app.storage import get_conn
        synonyms = _load_synonyms()
        conn = get_conn()

        # Get all projects
        projects = conn.execute(
            "SELECT id, name, description FROM knowledge_entities WHERE entity_type = 'PROJECT'"
        ).fetchall()

        results: list[ProjectMatch] = []
        for proj in projects:
            # Get skills this project demonstrates
            skill_rows = conn.execute(
                "SELECT e.name FROM knowledge_relations r "
                "JOIN knowledge_entities e ON r.to_id = e.id "
                "WHERE r.from_id = ? AND r.type = 'DEMONSTRATES'",
                (proj["id"],),
            ).fetchall()
            proj_skills = {_normalize(row["name"]) for row in skill_rows}

            # Count overlap with JD skills
            matched = [s for s in jd_skills if _skill_match(s, proj_skills, synonyms)]
            if matched:
                results.append(ProjectMatch(
                    name=proj["name"],
                    description=proj["description"] or "",
                    skill_overlap=len(matched),
                    matched_skills=matched,
                ))

        conn.close()
        results.sort(key=lambda x: x.skill_overlap, reverse=True)
        return results

    def get_skill_recency(self) -> dict[str, date]:
        """Placeholder for skill recency from git commits. Returns empty for now."""
        return {}

    def pre_screen_jd(self, listing: JobListing) -> PreScreenResult:
        """Run Gate 1 + Gate 2 + Gate 3 against this listing. Gate 0 is external."""
        synonyms = _load_synonyms()
        profile = self.get_skill_profile()
        projects = self.get_projects_for_skills(listing.required_skills)
        result = PreScreenResult()

        # --- Gate 1: Kill Signals ---
        kill = self._check_kill_signals(listing, profile, synonyms)
        if kill:
            result.gate1_passed = False
            result.gate1_kill_reason = kill
            result.tier = "reject"
            return result

        # --- Gate 2: Must-Haves ---
        fail = self._check_must_haves(listing, profile, projects, synonyms)
        if fail:
            result.gate2_passed = False
            result.gate2_fail_reason = fail
            result.tier = "skip"
            # Still compute matched/missing for logging
            result.matched_skills = [
                s for s in listing.required_skills + listing.preferred_skills
                if _skill_match(s, profile, synonyms)
            ]
            result.missing_skills = [
                s for s in listing.required_skills
                if not _skill_match(s, profile, synonyms)
            ]
            return result

        # --- Gate 3: Competitiveness Score ---
        score, breakdown = self._score_competitiveness(listing, profile, projects, synonyms)
        result.gate3_score = score
        result.breakdown = breakdown
        result.best_projects = projects[:4]
        result.matched_skills = [
            s for s in listing.required_skills + listing.preferred_skills
            if _skill_match(s, profile, synonyms)
        ]
        result.missing_skills = [
            s for s in listing.required_skills
            if not _skill_match(s, profile, synonyms)
        ]

        if score < 55:
            result.tier = "skip"
        elif score < 75:
            result.tier = "apply"
        else:
            result.tier = "strong"

        return result

    def _check_kill_signals(
        self, listing: JobListing, profile: set[str], synonyms: dict,
    ) -> str | None:
        """K1: seniority, K2: primary language, K3: domain disconnect."""
        jd_text = listing.description_raw.lower() if listing.description_raw else ""

        # K1: Seniority mismatch — years of experience too high
        years_patterns = [
            (r"\b(\d+)\+?\s*years?\b", int),
        ]
        for pattern, _ in years_patterns:
            for m in re.finditer(pattern, jd_text):
                years = int(m.group(1))
                if years >= 5:
                    return f"Seniority: JD requires {years}+ years experience"

        # K2: Primary language not in profile
        if listing.required_skills:
            primary = _normalize(listing.required_skills[0])
            if not _skill_match(primary, profile, synonyms):
                return f"Primary skill missing: {listing.required_skills[0]}"

        # K3: Domain disconnect — all top-3 skills from a foreign domain
        foreign_domains = {
            "ios": {"swift", "swiftui", "xcode", "uikit", "coredata", "objective c"},
            "android": {"kotlin", "android", "jetpack compose", "android studio"},
            "embedded": {"c", "rtos", "firmware", "vhdl", "fpga", "embedded c"},
            "mainframe": {"cobol", "jcl", "cics", "db2", "mainframe"},
        }
        if len(listing.required_skills) >= 3:
            top3 = {_normalize(s) for s in listing.required_skills[:3]}
            for domain, domain_skills in foreign_domains.items():
                if top3.issubset(domain_skills):
                    return f"Domain disconnect: {domain} (you have no {domain} skills)"

        return None

    def _check_must_haves(
        self, listing: JobListing, profile: set[str],
        projects: list[ProjectMatch], synonyms: dict,
    ) -> str | None:
        """M1: top-5, M2: project evidence, M3: keyword density."""
        # M1: ≥ 3 of top-5 required skills
        top5 = listing.required_skills[:5]
        top5_matched = [s for s in top5 if _skill_match(s, profile, synonyms)]
        if len(top5_matched) < 3:
            return f"Core skills: {len(top5_matched)}/5 top required (need 3+)"

        # M2: ≥ 2 projects demonstrating 3+ JD skills
        strong_projects = [p for p in projects if p.skill_overlap >= 3]
        if len(strong_projects) < 2:
            return f"Project evidence: {len(strong_projects)} projects with 3+ skills (need 2+)"

        # M3: ≥ 12 absolute matches AND ≥ 65% required
        all_skills = listing.required_skills + listing.preferred_skills
        all_matched = [s for s in all_skills if _skill_match(s, profile, synonyms)]
        req_matched = [s for s in listing.required_skills if _skill_match(s, profile, synonyms)]

        if len(all_matched) < 12:
            return f"Keyword density: {len(all_matched)} matches (need 12+)"

        req_pct = len(req_matched) / max(len(listing.required_skills), 1)
        if req_pct < 0.65:
            return f"Required coverage: {req_pct:.0%} (need 65%+)"

        return None

    def _score_competitiveness(
        self, listing: JobListing, profile: set[str],
        projects: list[ProjectMatch], synonyms: dict,
    ) -> tuple[float, dict]:
        """Score 0-100 across 5 dimensions."""
        # Hard Skill Match (0-35)
        max_pts = len(listing.required_skills) * 3
        earned = 0
        for skill in listing.required_skills:
            if _skill_match(skill, profile, synonyms):
                # Check if any project demonstrates it
                demonstrated = any(
                    _normalize(skill) in [_normalize(ms) for ms in p.matched_skills]
                    for p in projects
                )
                earned += 3 if demonstrated else 1
        hard_skill = (earned / max(max_pts, 1)) * 35

        # Project Evidence (0-25)
        proj_pts = 0
        for p in projects[:4]:
            if p.skill_overlap >= 3:
                proj_pts += 6
            elif p.skill_overlap >= 1:
                proj_pts += 3
        project_ev = min(proj_pts, 25)

        # Stack Coherence (0-15)
        skill_clusters = {
            "python_backend": {"python", "fastapi", "django", "flask", "postgresql", "redis"},
            "python_ml": {"python", "pytorch", "tensorflow", "scikit learn", "pandas", "numpy", "machine learning"},
            "javascript_frontend": {"javascript", "typescript", "react", "vue", "angular", "next.js"},
            "devops": {"docker", "kubernetes", "terraform", "aws", "gcp", "azure", "continuous integration"},
            "data": {"sql", "postgresql", "mongodb", "apache spark", "apache kafka", "data pipeline"},
        }
        matched_set = {_normalize(s) for s in listing.required_skills if _skill_match(s, profile, synonyms)}
        clusters_hit = sum(1 for _, cluster_skills in skill_clusters.items() if matched_set & cluster_skills)
        if clusters_hit <= 2:
            coherence = 15.0
        elif clusters_hit == 3:
            coherence = 10.0
        else:
            coherence = 5.0

        # Domain Relevance (0-15)
        industry = _normalize(getattr(listing, "industry", "") or "")
        user_domains = {"ai/ml", "data science", "fintech", "saas", "technology"}
        if industry in user_domains:
            domain_rel = 15.0
        elif industry in {"devtools", "e-commerce", "edtech"}:
            domain_rel = 10.0
        elif industry:
            domain_rel = 5.0
        else:
            domain_rel = 7.5  # unknown industry, neutral

        # Recency (0-10) — placeholder until nightly sync adds git commit dates
        recency = 7.0  # Default moderate score

        total = hard_skill + project_ev + coherence + domain_rel + recency
        breakdown = {
            "hard_skill": round(hard_skill, 1),
            "project_evidence": round(project_ev, 1),
            "stack_coherence": round(coherence, 1),
            "domain_relevance": round(domain_rel, 1),
            "recency": round(recency, 1),
        }
        return round(total, 1), breakdown

    def get_profile_stats(self) -> dict:
        """Summary stats for the skill/project graph."""
        from mindgraph_app.storage import get_conn
        conn = get_conn()
        skills = conn.execute(
            "SELECT COUNT(*) as c FROM knowledge_entities WHERE entity_type = 'SKILL'"
        ).fetchone()["c"]
        projects = conn.execute(
            "SELECT COUNT(*) as c FROM knowledge_entities WHERE entity_type = 'PROJECT'"
        ).fetchone()["c"]
        relations = conn.execute(
            "SELECT COUNT(*) as c FROM knowledge_relations WHERE type = 'DEMONSTRATES'"
        ).fetchone()["c"]
        conn.close()
        return {
            "total_skills": skills,
            "total_projects": projects,
            "total_demonstrates": relations,
        }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_skill_graph_store.py -v`

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/skill_graph_store.py tests/test_skill_graph_store.py
git commit -m "feat(jobs): add SkillGraphStore with 4-gate recruiter pre-screen"
```

---

## Task 4: Recruiter Screen Module

**Files:**
- Create: `jobpulse/recruiter_screen.py`
- Test: `tests/test_recruiter_screen.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_recruiter_screen.py`:

```python
"""Tests for Gate 0 title relevance filter."""

import json
import pytest


@pytest.fixture
def search_config():
    return {
        "titles": [
            "Graduate Data Scientist",
            "Junior ML Engineer",
            "Junior Software Engineer",
            "Data Science Intern",
        ],
        "exclude_keywords": [
            "senior", "lead", "principal", "staff", "10+ years",
            "8+ years", "7+ years", "5+ years", "director", "manager",
        ],
    }


class TestGate0:
    def test_matching_title_passes(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("Junior ML Engineer", "", search_config) is True

    def test_fuzzy_title_passes(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        # "ML Engineer" should fuzzy-match "Junior ML Engineer"
        assert gate0_title_relevance("ML Engineer - Graduate", "", search_config) is True

    def test_excluded_keyword_fails(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("Senior Data Scientist", "", search_config) is False

    def test_completely_unrelated_fails(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("Marketing Manager", "", search_config) is False

    def test_exclude_keyword_in_jd_body_fails(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance(
            "Data Scientist", "Requirements: 7+ years of experience", search_config
        ) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_recruiter_screen.py -v`

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement recruiter_screen.py**

Create `jobpulse/recruiter_screen.py`:

```python
"""Gate 0: Title relevance filter — runs BEFORE any LLM or DB calls.

Checks job title against search config:
  - At least one search title fuzzy-matches the job title
  - No exclude_keywords appear in title or JD body

Public API:
  gate0_title_relevance(title, jd_text, config) -> bool
"""

from __future__ import annotations

import re

from shared.logging_config import get_logger

logger = get_logger(__name__)


def _normalize_title(title: str) -> set[str]:
    """Extract meaningful words from a title for fuzzy matching."""
    # Remove common noise words
    noise = {"the", "a", "an", "at", "in", "for", "and", "or", "of", "to", "with"}
    words = re.findall(r"[a-zA-Z]+", title.lower())
    return {w for w in words if w not in noise and len(w) > 1}


def gate0_title_relevance(title: str, jd_text: str, config: dict) -> bool:
    """Return True if the job title is relevant based on search config.

    Checks:
    1. No exclude_keywords in title
    2. No exclude_keywords in JD body (catches "5+ years" etc.)
    3. At least one search title has ≥ 50% word overlap with job title
    """
    title_lower = title.lower()
    jd_lower = jd_text.lower() if jd_text else ""

    # Check exclude keywords in title
    for kw in config.get("exclude_keywords", []):
        kw_lower = kw.lower()
        if kw_lower in title_lower:
            logger.debug("gate0: title '%s' killed by exclude keyword '%s'", title, kw)
            return False

    # Check exclude keywords in JD body (catches "7+ years" patterns)
    for kw in config.get("exclude_keywords", []):
        kw_lower = kw.lower()
        if kw_lower in jd_lower:
            logger.debug("gate0: JD body killed by exclude keyword '%s'", kw)
            return False

    # Fuzzy title matching: at least one config title has ≥ 50% word overlap
    job_words = _normalize_title(title)
    if not job_words:
        return False

    for search_title in config.get("titles", []):
        search_words = _normalize_title(search_title)
        if not search_words:
            continue
        overlap = len(job_words & search_words)
        # Need at least 50% of the shorter set's words to match
        min_len = min(len(job_words), len(search_words))
        if min_len > 0 and overlap / min_len >= 0.5:
            return True

    return False
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_recruiter_screen.py -v`

Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/recruiter_screen.py tests/test_recruiter_screen.py
git commit -m "feat(jobs): add Gate 0 title relevance filter for recruiter pre-screen"
```

---

## Task 5: Nightly GitHub Profile Sync

**Files:**
- Create: `jobpulse/github_profile_sync.py`
- Test: `tests/test_github_profile_sync.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_github_profile_sync.py`:

```python
"""Tests for nightly GitHub profile sync."""

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "mindgraph.db"
    with patch("mindgraph_app.storage.DB_PATH", db_path):
        from mindgraph_app.storage import init_db
        init_db()
        yield db_path


@pytest.fixture
def sample_repos():
    return [
        {
            "name": "yashb98/multi-agent-patterns",
            "description": "Multi-agent orchestration system",
            "languages": ["python", "javascript"],
            "topics": ["ai", "agents", "langraph"],
            "keywords": ["python", "javascript", "ai", "agents", "langraph"],
            "stars": 10,
            "url": "https://github.com/yashb98/multi-agent-patterns",
        },
        {
            "name": "yashb98/DataMind",
            "description": "AI analytics platform",
            "languages": ["python", "typescript"],
            "topics": ["analytics", "kafka", "duckdb"],
            "keywords": ["python", "typescript", "analytics", "kafka", "duckdb"],
            "stars": 5,
            "url": "https://github.com/yashb98/DataMind",
        },
    ]


class TestSyncRepos:
    def test_sync_creates_entities(self, temp_db, sample_repos):
        from jobpulse.github_profile_sync import sync_repos_to_graph
        with patch("mindgraph_app.storage.DB_PATH", temp_db):
            from jobpulse.skill_graph_store import SkillGraphStore
            store = SkillGraphStore()
            sync_repos_to_graph(sample_repos, store)
            stats = store.get_profile_stats()
            assert stats["total_projects"] >= 2
            assert stats["total_skills"] >= 4

    def test_sync_is_idempotent(self, temp_db, sample_repos):
        from jobpulse.github_profile_sync import sync_repos_to_graph
        with patch("mindgraph_app.storage.DB_PATH", temp_db):
            from jobpulse.skill_graph_store import SkillGraphStore
            store = SkillGraphStore()
            sync_repos_to_graph(sample_repos, store)
            stats1 = store.get_profile_stats()
            sync_repos_to_graph(sample_repos, store)
            stats2 = store.get_profile_stats()
            assert stats1["total_projects"] == stats2["total_projects"]
            assert stats1["total_skills"] == stats2["total_skills"]


class TestSyncResumeSkills:
    def test_extracts_base_skills(self, temp_db):
        from jobpulse.github_profile_sync import sync_resume_skills
        with patch("mindgraph_app.storage.DB_PATH", temp_db):
            from jobpulse.skill_graph_store import SkillGraphStore
            store = SkillGraphStore()
            sync_resume_skills(store)
            profile = store.get_skill_profile()
            assert "python" in profile
            assert "pytorch" in profile or "scikit learn" in profile


class TestFullSync:
    def test_sync_profile_runs_without_error(self, temp_db, sample_repos):
        from jobpulse.github_profile_sync import sync_profile
        with patch("mindgraph_app.storage.DB_PATH", temp_db):
            with patch("jobpulse.github_profile_sync.fetch_and_cache_repos", return_value=sample_repos):
                sync_profile()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_github_profile_sync.py -v`

Expected: FAIL

- [ ] **Step 3: Implement github_profile_sync.py**

Create `jobpulse/github_profile_sync.py`:

```python
"""Nightly GitHub Profile Sync — populates MindGraph with skills and projects.

Sources:
  1. GitHub repos (via fetch_and_cache_repos)
  2. Resume Prompt template BASE_SKILLS
  3. Past successful applications (ATS >= 90%)

Cron: 3am daily via `python -m jobpulse.runner profile-sync`

Public API:
  sync_profile()            — full sync (repos + resume + past apps)
  sync_repos_to_graph(repos, store) — sync repo list to graph
  sync_resume_skills(store) — sync BASE_SKILLS from CV template
"""

from __future__ import annotations

from shared.logging_config import get_logger

logger = get_logger(__name__)


def sync_repos_to_graph(repos: list[dict], store) -> None:
    """Sync a list of repo dicts into the SkillGraphStore.

    Each repo creates:
      - 1 PROJECT entity
      - N SKILL entities (from languages + topics)
      - N DEMONSTRATES relations
    """
    for repo in repos:
        try:
            store.upsert_project(repo)
            logger.debug("Synced project: %s", repo.get("name"))
        except Exception as exc:
            logger.warning("Failed to sync repo %s: %s", repo.get("name"), exc)

    logger.info("Synced %d repos to skill graph", len(repos))


def sync_resume_skills(store) -> None:
    """Extract skills from CV template BASE_SKILLS and upsert to graph."""
    try:
        from jobpulse.cv_templates.generate_cv import BASE_SKILLS
    except ImportError:
        logger.warning("Could not import BASE_SKILLS from cv_templates")
        return

    for category, skills_str in BASE_SKILLS.items():
        # BASE_SKILLS format: "Python | SQL | JavaScript | TypeScript"
        skills = [s.strip() for s in skills_str.split("|")]
        for skill in skills:
            if skill and len(skill) > 1:
                store.upsert_skill(skill, source="resume", description=f"From CV: {category}")

    logger.info("Synced resume skills from %d categories", len(BASE_SKILLS))


def sync_past_applications(store) -> None:
    """Boost mention_count for skills that appeared in high-ATS applications."""
    try:
        from jobpulse.job_db import JobDB
        db = JobDB()
        # Get applications with ATS >= 90
        successful = db.get_high_ats_skills(min_ats=90.0)
        for skill_name in successful:
            store.upsert_skill(skill_name, source="past_app", description="Converted in ATS 90%+ app")
        logger.info("Boosted %d skills from past successful applications", len(successful))
    except Exception as exc:
        logger.info("No past application data available yet: %s", exc)


def sync_profile() -> None:
    """Full profile sync: GitHub repos + resume skills + past apps.

    Called by: `python -m jobpulse.runner profile-sync`
    Cron: 3am daily
    """
    from jobpulse.github_matcher import fetch_and_cache_repos
    from jobpulse.skill_graph_store import SkillGraphStore

    store = SkillGraphStore()

    # 1. Sync GitHub repos
    try:
        repos = fetch_and_cache_repos()
        sync_repos_to_graph(repos, store)
    except Exception as exc:
        logger.error("GitHub repo sync failed: %s", exc)

    # 2. Sync resume skills
    try:
        sync_resume_skills(store)
    except Exception as exc:
        logger.error("Resume skill sync failed: %s", exc)

    # 3. Boost from past applications
    try:
        sync_past_applications(store)
    except Exception as exc:
        logger.error("Past app sync failed: %s", exc)

    # Log stats
    stats = store.get_profile_stats()
    logger.info(
        "Profile sync complete: %d skills, %d projects, %d DEMONSTRATES relations",
        stats["total_skills"], stats["total_projects"], stats["total_demonstrates"],
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_github_profile_sync.py -v`

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/github_profile_sync.py tests/test_github_profile_sync.py
git commit -m "feat(jobs): add nightly GitHub profile sync to MindGraph"
```

---

## Task 6: Pipeline Integration

**Files:**
- Modify: `jobpulse/job_autopilot.py:162-235` (insert Gate 0 + hybrid extraction + Gate 1-3)
- Modify: `jobpulse/jd_analyzer.py:280-340` (use hybrid extractor)
- Modify: `jobpulse/runner.py:196` (add profile-sync command)
- Modify: `scripts/install_cron.py:54` (add 3am cron)

- [ ] **Step 1: Update jd_analyzer.py to use hybrid extractor**

In `jobpulse/jd_analyzer.py`, replace the `extract_skills_llm` call inside `analyze_jd()` with `extract_skills_hybrid`:

Change line 380 from:
```python
    llm_data = extract_skills_llm(jd_text)
```
to:
```python
    from jobpulse.skill_extractor import extract_skills_hybrid
    llm_data = extract_skills_hybrid(jd_text)
```

- [ ] **Step 2: Update job_autopilot.py — add Gate 0 before analyze_jd**

In `jobpulse/job_autopilot.py`, after `raw_jobs = scan_platforms(platforms)` (line ~195), add Gate 0 filtering:

Insert after `total_found = len(raw_jobs)` block (after line 203):

```python
    # --- Step 2b: Gate 0 — title relevance filter ---
    from jobpulse.recruiter_screen import gate0_title_relevance
    config = load_search_config()
    filtered_jobs = []
    gate0_rejected = 0
    for raw in raw_jobs:
        title = raw.get("title", "")
        jd_text = raw.get("description", "")
        if gate0_title_relevance(title, jd_text, config):
            filtered_jobs.append(raw)
        else:
            gate0_rejected += 1

    trail.log_step(
        "decision", "Gate 0: Title filter",
        step_output=f"{len(filtered_jobs)} passed, {gate0_rejected} rejected",
    )
    raw_jobs = filtered_jobs
```

- [ ] **Step 3: Update job_autopilot.py — add Gate 1-3 after dedup**

In `jobpulse/job_autopilot.py`, after deduplicate (line ~229), before the per-job loop, add pre-screen:

Insert after `new_listings = deduplicate(listings, db)` block, replace the `fetch_and_cache_repos()` call with:

```python
    # --- Step 5: Pre-screen with 4-gate recruiter filter ---
    from jobpulse.skill_graph_store import SkillGraphStore
    try:
        store = SkillGraphStore()
    except Exception as exc:
        logger.warning("job_autopilot: SkillGraphStore init failed: %s — skipping pre-screen", exc)
        store = None

    screened_listings = []
    gate_rejected = 0
    gate_skipped = 0

    for listing in new_listings:
        if store is None:
            screened_listings.append((listing, None))
            continue

        screen = store.pre_screen_jd(listing)

        if screen.tier == "reject":
            gate_rejected += 1
            logger.info(
                "job_autopilot: REJECTED %s @ %s — %s",
                listing.title, listing.company, screen.gate1_kill_reason,
            )
            # Save to DB as rejected but don't create Notion page
            db.save_listing(listing)
            db.save_application(job_id=listing.job_id, status="Rejected",
                                match_tier="reject")
            continue

        if screen.tier == "skip":
            gate_skipped += 1
            reason = screen.gate2_fail_reason or f"Score {screen.gate3_score}/100"
            logger.info(
                "job_autopilot: SKIPPED %s @ %s — %s",
                listing.title, listing.company, reason,
            )
            db.save_listing(listing)
            db.save_application(job_id=listing.job_id, status="Skipped",
                                match_tier="skip")
            continue

        screened_listings.append((listing, screen))

    trail.log_step(
        "decision", "Gates 1-3",
        step_output=f"{len(screened_listings)} pass, {gate_rejected} rejected, {gate_skipped} skipped",
    )
```

Then update the per-job loop to use `screened_listings` and `screen.best_projects` instead of calling `fetch_and_cache_repos()` + `pick_top_projects()`:

Replace `repos = fetch_and_cache_repos()` and the per-job project matching with:
```python
    for listing, screen in screened_listings:
        # ... existing per-job logic ...
        # Replace pick_top_projects() call with:
        if screen and screen.best_projects:
            matched_project_names = [p.name for p in screen.best_projects]
        else:
            # Fallback to old method if no screen
            repos = fetch_and_cache_repos() if not repos else repos
            matched_repos = pick_top_projects(repos, ...)
            matched_project_names = [r.get("name", "") for r in matched_repos]
```

- [ ] **Step 4: Add profile-sync command to runner.py**

In `jobpulse/runner.py`, before the `else:` clause at line 196, add:

```python
    elif command == "profile-sync":
        from jobpulse.github_profile_sync import sync_profile
        sync_profile()
```

- [ ] **Step 5: Add 3am cron to install_cron.py**

In `scripts/install_cron.py`, add after the `# Overnight scan` entry (line ~54):

```python
# Nightly skill/project profile sync (3am) — GitHub + resume + past apps → MindGraph
 0 3 * * * {RUNNER} profile-sync >> {PROJECT_DIR}/logs/profile_sync.log 2>&1
```

- [ ] **Step 6: Run existing tests to verify no regressions**

Run: `python -m pytest tests/test_jd_analyzer.py tests/test_job_deduplicator.py tests/test_ats_scorer.py -v`

Expected: All existing tests PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/job_autopilot.py jobpulse/jd_analyzer.py jobpulse/runner.py scripts/install_cron.py
git commit -m "feat(jobs): integrate 4-gate pre-screen + hybrid extraction into pipeline"
```

---

## Task 7: Integration Test + Documentation

**Files:**
- Modify: `jobpulse/CLAUDE.md` (document new components)
- Modify: `docs/superpowers/specs/2026-03-30-job-pipeline-api-optimization-design.md` (mark as Implemented)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v -k "skill_extractor or recruiter_screen or skill_graph or profile_sync" --tb=short`

Expected: All new tests PASS

- [ ] **Step 2: Run all existing tests to verify no regressions**

Run: `python -m pytest tests/ -v --tb=short`

Expected: No regressions

- [ ] **Step 3: Update jobpulse/CLAUDE.md**

Add to the Agents section:

```markdown
- skill_extractor.py — Rule-based JD skill extraction (500+ taxonomy), LLM fallback < 10 skills
- recruiter_screen.py — Gate 0 title filter (pre-LLM, instant)
- skill_graph_store.py — SkillGraphStore: 4-gate pre-screen (Gates 1-3), MindGraph abstraction
- github_profile_sync.py — Nightly 3am sync: GitHub repos + resume + past apps → MindGraph graph
```

Add a new section:

```markdown
## Pre-Screen Pipeline (4-Gate Recruiter Model)
Gate 0: Title relevance (instant, before LLM) → Gate 1: Kill signals (seniority, primary lang, domain)
→ Gate 2: Must-haves (top-5 skills, project evidence, 12+ matches, 65%+ required)
→ Gate 3: Competitiveness score (0-100: hard skill 35 + project evidence 25 + coherence 15 + domain 15 + recency 10)
Tiers: reject (<Gate 1) | skip (<55) | apply (55-74) | strong (75+)
LLM calls: ~10-11/day (96% reduction from 250/day). Cost: $0.23/month.
```

- [ ] **Step 4: Mark spec as Implemented**

Change status in spec from `Draft` to `Implemented`.

- [ ] **Step 5: Commit all docs**

```bash
git add jobpulse/CLAUDE.md docs/superpowers/specs/2026-03-30-job-pipeline-api-optimization-design.md
git commit -m "docs(jobs): document 4-gate pre-screen pipeline and mark spec as implemented"
```

---

## Summary

| Task | Files | What It Builds |
|------|-------|---------------|
| 1 | `data/skill_synonyms.json` | 500+ skill taxonomy |
| 2 | `jobpulse/skill_extractor.py` + test | Rule-based + LLM fallback extraction |
| 3 | `jobpulse/skill_graph_store.py` + test | SkillGraphStore + 4-gate pre-screen |
| 4 | `jobpulse/recruiter_screen.py` + test | Gate 0 title filter |
| 5 | `jobpulse/github_profile_sync.py` + test | Nightly 3am profile sync |
| 6 | `job_autopilot.py` + `jd_analyzer.py` + `runner.py` + `install_cron.py` | Pipeline integration |
| 7 | CLAUDE.md + spec | Documentation |

**7 tasks, 7 commits. Each task is independently testable and committable.**
