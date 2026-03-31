"""SkillGraphStore — abstraction over MindGraph for skill/project entities + 4-gate pre-screen.

Stores skills and projects as knowledge graph entities, runs a deterministic
4-gate recruiter pre-screen against job listings.  Interface is Neo4j-ready:
swap internals later, keep the public API stable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from mindgraph_app.storage import get_conn, init_db, upsert_entity, upsert_relation

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_DEFAULT_SYNONYMS_PATH = str(Path(__file__).parent.parent / "data" / "skill_synonyms.json")

# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class ProjectMatch:
    """A portfolio project that overlaps with JD skills."""

    name: str
    description: str
    skill_overlap: int
    matched_skills: list[str]
    url: str = ""


@dataclass
class PreScreenResult:
    """Result of the 4-gate recruiter pre-screen."""

    gate0_passed: bool = True
    gate1_passed: bool = True
    gate1_kill_reason: str | None = None
    gate2_passed: bool = True
    gate2_fail_reason: str | None = None
    gate3_score: float = 0.0
    tier: str = "skip"
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    best_projects: list[ProjectMatch] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Foreign domains for kill-signal detection
# ---------------------------------------------------------------------------

_FOREIGN_DOMAINS: dict[str, set[str]] = {
    "ios": {"swift", "swiftui", "xcode", "uikit", "coredata"},
    "android": {"kotlin", "android", "jetpack compose"},
    "embedded": {"c", "rtos", "firmware", "vhdl", "fpga"},
    "mainframe": {"cobol", "jcl", "cics", "db2"},
}

# ---------------------------------------------------------------------------
# Stack clusters for coherence scoring
# ---------------------------------------------------------------------------

_STACK_CLUSTERS: dict[str, set[str]] = {
    "python_backend": {
        "python", "fastapi", "django", "flask", "celery", "sqlalchemy",
        "pydantic", "uvicorn", "gunicorn", "poetry",
    },
    "python_ml": {
        "python", "tensorflow", "pytorch", "scikit-learn", "pandas", "numpy",
        "keras", "jupyter", "mlflow", "huggingface",
    },
    "javascript_frontend": {
        "javascript", "typescript", "react", "vue", "angular", "next.js",
        "nextjs", "svelte", "tailwind", "webpack", "vite",
    },
    "devops": {
        "docker", "kubernetes", "terraform", "ansible", "jenkins", "github actions",
        "ci/cd", "aws", "gcp", "azure", "helm", "prometheus", "grafana",
    },
    "data": {
        "sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
        "kafka", "spark", "airflow", "dbt", "snowflake", "bigquery",
    },
}


# ---------------------------------------------------------------------------
# SkillGraphStore
# ---------------------------------------------------------------------------


class SkillGraphStore:
    """Abstraction over MindGraph for skill/project graph + pre-screening."""

    def __init__(self, synonyms_path: str | None = None):
        init_db()
        self._synonyms: dict[str, list[str]] = {}
        self._reverse_synonyms: dict[str, str] = {}
        self._load_synonyms(synonyms_path or _DEFAULT_SYNONYMS_PATH)

    # ------------------------------------------------------------------
    # Synonyms
    # ------------------------------------------------------------------

    def _load_synonyms(self, path: str) -> None:
        try:
            with open(path) as f:
                self._synonyms = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._synonyms = {}

        self._reverse_synonyms = {}
        for canonical, aliases in self._synonyms.items():
            canonical_lower = canonical.lower().strip()
            for alias in aliases:
                self._reverse_synonyms[alias.lower().strip()] = canonical_lower

    def _normalize(self, name: str) -> str:
        """Normalize a skill name: strip, lowercase, resolve synonyms."""
        n = name.lower().strip()
        return self._reverse_synonyms.get(n, n)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def upsert_skill(self, name: str, source: str = "github", description: str = "") -> str:
        """Add/update a SKILL entity.  Returns the entity ID."""
        normalized = self._normalize(name)
        desc = description or f"Skill from {source}"
        return upsert_entity(normalized, "SKILL", desc)

    def upsert_project(self, repo: dict, deep_analysis: str | None = None) -> str:
        """Add/update a PROJECT entity + DEMONSTRATES relations.

        ``repo`` is expected to have at minimum: name, description,
        html_url, language, topics.
        """
        name = repo.get("name", "unknown")
        description = repo.get("description", "") or ""
        url = repo.get("html_url", "")

        pid = upsert_entity(name, "PROJECT", description)

        # Collect skills from repo metadata
        skill_names: list[str] = []
        lang = repo.get("language")
        if lang:
            skill_names.append(lang)
        for topic in repo.get("topics", []):
            skill_names.append(topic)

        # Extract additional skills from deep_analysis text
        if deep_analysis:
            # Simple word extraction: find known skills / synonyms in the text
            words = re.findall(r"[A-Za-z0-9#+./]+", deep_analysis)
            for w in words:
                normalized = self._normalize(w)
                # Check if it's a known canonical skill or synonym
                if normalized in self._synonyms or w.lower().strip() in self._reverse_synonyms:
                    skill_names.append(w)

        # Upsert each skill and create DEMONSTRATES relation
        for skill_name in skill_names:
            sid = self.upsert_skill(skill_name, source="project")
            upsert_relation(pid, sid, "DEMONSTRATES", f"Project {name} demonstrates {skill_name}")

        return pid

    def get_skill_profile(self) -> set[str]:
        """All SKILL entity names from DB, normalized."""
        conn = get_conn()
        rows = conn.execute(
            "SELECT name FROM knowledge_entities WHERE entity_type = 'SKILL'"
        ).fetchall()
        conn.close()
        return {row["name"].lower().strip() for row in rows}

    def get_projects_for_skills(self, jd_skills: list[str]) -> list[ProjectMatch]:
        """Find projects demonstrating given skills, ranked by overlap count."""
        profile = self.get_skill_profile()
        conn = get_conn()

        # Get all projects
        projects = conn.execute(
            "SELECT id, name, description FROM knowledge_entities WHERE entity_type = 'PROJECT'"
        ).fetchall()

        results: list[ProjectMatch] = []
        for proj in projects:
            # Get skills this project demonstrates
            rels = conn.execute(
                "SELECT to_id FROM knowledge_relations WHERE from_id = ? AND type = 'DEMONSTRATES'",
                (proj["id"],),
            ).fetchall()
            proj_skill_ids = {r["to_id"] for r in rels}

            # Get actual skill names for these IDs
            if not proj_skill_ids:
                continue
            placeholders = ",".join("?" * len(proj_skill_ids))
            skill_rows = conn.execute(
                f"SELECT name FROM knowledge_entities WHERE id IN ({placeholders})",
                list(proj_skill_ids),
            ).fetchall()
            proj_skills = {row["name"].lower().strip() for row in skill_rows}

            # Compute overlap with JD skills
            matched = []
            for jd_skill in jd_skills:
                normalized = self._normalize(jd_skill)
                if normalized in proj_skills:
                    matched.append(jd_skill)

            if matched:
                results.append(ProjectMatch(
                    name=proj["name"],
                    description=proj["description"] or "",
                    skill_overlap=len(matched),
                    matched_skills=matched,
                    url="",
                ))

        conn.close()

        # Sort by overlap descending
        results.sort(key=lambda m: m.skill_overlap, reverse=True)
        return results

    def get_skill_recency(self) -> dict[str, date]:
        """Placeholder -- returns empty dict for now."""
        return {}

    def get_profile_stats(self) -> dict:
        """Returns counts of skills, projects, and demonstrates relations."""
        conn = get_conn()
        total_skills = conn.execute(
            "SELECT COUNT(*) FROM knowledge_entities WHERE entity_type = 'SKILL'"
        ).fetchone()[0]
        total_projects = conn.execute(
            "SELECT COUNT(*) FROM knowledge_entities WHERE entity_type = 'PROJECT'"
        ).fetchone()[0]
        total_demonstrates = conn.execute(
            "SELECT COUNT(*) FROM knowledge_relations WHERE type = 'DEMONSTRATES'"
        ).fetchone()[0]
        conn.close()
        return {
            "total_skills": total_skills,
            "total_projects": total_projects,
            "total_demonstrates": total_demonstrates,
        }

    # ------------------------------------------------------------------
    # Pre-Screen
    # ------------------------------------------------------------------

    def pre_screen_jd(self, listing) -> PreScreenResult:
        """Run Gate 1 + Gate 2 + Gate 3.  Gate 0 is external."""
        result = PreScreenResult()
        profile = self.get_skill_profile()

        # Extract listing fields (support both dict and object)
        required = [s.lower().strip() for s in _get(listing, "required_skills", [])]
        preferred = [s.lower().strip() for s in _get(listing, "preferred_skills", [])]
        description = _get(listing, "description_raw", "")

        if not required:
            # No required skills listed -- can't evaluate meaningfully
            result.tier = "skip"
            return result

        # Compute matched / missing
        matched = [s for s in required if self._skill_match(s, profile)]
        missing = [s for s in required if not self._skill_match(s, profile)]
        result.matched_skills = matched
        result.missing_skills = missing

        # Gate 1: Kill signals
        gate1_passed, kill_reason = self._check_kill_signals(
            required, profile, description
        )
        result.gate1_passed = gate1_passed
        result.gate1_kill_reason = kill_reason
        if not gate1_passed:
            result.tier = "reject"
            return result

        # Gate 2: Must-haves
        projects = self.get_projects_for_skills(required + preferred)
        result.best_projects = projects[:4]
        gate2_passed, gate2_reason = self._check_must_haves(
            required, matched, projects
        )
        result.gate2_passed = gate2_passed
        result.gate2_fail_reason = gate2_reason
        if not gate2_passed:
            result.tier = "skip"
            return result

        # Gate 3: Competitiveness score
        score, breakdown = self._score_competitiveness(
            required, preferred, matched, missing, projects, profile
        )
        result.gate3_score = score
        result.breakdown = breakdown

        if score >= 75:
            result.tier = "strong"
        elif score >= 55:
            result.tier = "apply"
        else:
            result.tier = "skip"

        return result

    # ------------------------------------------------------------------
    # Gate 1: Kill Signals
    # ------------------------------------------------------------------

    def _check_kill_signals(
        self,
        required: list[str],
        profile: set[str],
        description: str,
    ) -> tuple[bool, str | None]:
        # K1: Seniority — 3+ years or more
        years_matches = re.findall(r"\b(\d+)\+?\s*years?\b", description, re.IGNORECASE)
        for m in years_matches:
            if int(m) >= 3:
                return False, f"Seniority kill: JD requires {m}+ years experience"

        # K2: Primary technical skill — find the first non-soft-skill required skill
        _SOFT_SKILLS = {
            "project management", "communication", "teamwork", "leadership",
            "problem solving", "time management", "adaptability", "collaboration",
            "analytical thinking", "critical thinking", "stakeholder management",
            "presentation skills", "mentoring", "coaching", "prioritization",
            "attention to detail", "self motivated", "fast learner",
        }
        primary = None
        for skill in required:
            if self._normalize(skill) not in _SOFT_SKILLS:
                primary = skill
                break
        if primary and self._normalize(primary) not in profile and not self._skill_match(primary, profile):
            return False, f"Primary skill kill: '{primary}' not in profile"

        # K3: Foreign domain — top 3 required all in one foreign domain
        if len(required) >= 3:
            top3 = {self._normalize(s) for s in required[:3]}
            for domain_name, domain_skills in _FOREIGN_DOMAINS.items():
                if top3.issubset(domain_skills):
                    return False, f"Foreign domain kill: top-3 skills all in '{domain_name}'"

        return True, None

    # ------------------------------------------------------------------
    # Gate 2: Must-Haves
    # ------------------------------------------------------------------

    def _check_must_haves(
        self,
        required: list[str],
        matched: list[str],
        projects: list[ProjectMatch],
    ) -> tuple[bool, str | None]:
        # M1: >= 3 of top-5 required skills in profile
        top5 = required[:5]
        top5_matched = [s for s in top5 if s in [m.lower() for m in matched]]
        if len(top5_matched) < 3:
            return False, f"M1 fail: only {len(top5_matched)}/5 top required skills matched (need 3)"

        # M2: >= 2 projects demonstrating 2+ JD skills
        strong_projects = [p for p in projects if p.skill_overlap >= 2]
        if len(strong_projects) < 2:
            return False, f"M2 fail: only {len(strong_projects)} projects with 2+ skill overlap (need 2)"

        # M3: >= 92% of required skills must match (percentage-based, not absolute count)
        if len(required) > 0:
            match_pct = len(matched) / len(required) * 100
        else:
            match_pct = 0
        if match_pct < 92:
            return False, (
                f"M3 fail: {len(matched)}/{len(required)} required skills ({match_pct:.0f}%) — need >= 92%"
            )

        return True, None

    # ------------------------------------------------------------------
    # Gate 3: Competitiveness Score
    # ------------------------------------------------------------------

    def _score_competitiveness(
        self,
        required: list[str],
        preferred: list[str],
        matched: list[str],
        missing: list[str],
        projects: list[ProjectMatch],
        profile: set[str],
    ) -> tuple[float, dict]:
        breakdown: dict[str, float] = {}

        # --- Hard Skill (0-35) ---
        hard_raw = 0.0
        max_hard_raw = len(required) * 3 if required else 1
        project_demonstrated = set()
        for p in projects:
            for s in p.matched_skills:
                project_demonstrated.add(self._normalize(s))

        for skill in required:
            norm = self._normalize(skill)
            in_profile = self._skill_match(skill, profile)
            in_project = norm in project_demonstrated
            if in_profile and in_project:
                hard_raw += 3
            elif in_profile:
                hard_raw += 1

        hard_score = min(35.0, (hard_raw / max_hard_raw) * 35) if max_hard_raw > 0 else 0
        breakdown["hard_skill"] = round(hard_score, 1)

        # --- Project Evidence (0-25) ---
        proj_raw = 0.0
        for p in projects[:4]:
            if p.skill_overlap >= 3:
                proj_raw += 6
            elif p.skill_overlap >= 1:
                proj_raw += 3
        proj_score = min(25.0, proj_raw)
        breakdown["project_evidence"] = round(proj_score, 1)

        # --- Stack Coherence (0-15) ---
        matched_normalized = {self._normalize(s) for s in matched}
        clusters_hit = 0
        for cluster_skills in _STACK_CLUSTERS.values():
            if matched_normalized & cluster_skills:
                clusters_hit += 1
        if clusters_hit <= 2:
            coherence = 15.0
        elif clusters_hit == 3:
            coherence = 10.0
        else:
            coherence = 5.0
        breakdown["stack_coherence"] = coherence

        # --- Domain Relevance (0-15) ---
        # Heuristic: check how many required skills are in known clusters,
        # plus AI/ML keyword detection for roles where industry field is generic.
        required_normalized = {self._normalize(s) for s in required}
        domain_overlap = 0
        for cluster_skills in _STACK_CLUSTERS.values():
            overlap = len(required_normalized & cluster_skills)
            domain_overlap = max(domain_overlap, overlap)

        # AI/ML keyword detection: if 3+ AI/ML-related skills appear in required,
        # treat as AI/ML domain match regardless of the industry field.
        _AI_ML_KEYWORDS = {
            "python", "machine learning", "deep learning", "nlp",
            "natural language processing", "pytorch", "tensorflow",
            "llm", "large language models", "computer vision", "cv",
            "reinforcement learning", "transformers", "huggingface",
            "langchain", "langgraph", "rag", "embeddings", "vector",
            "scikit-learn", "keras", "mlflow", "mlops", "data science",
            "neural networks", "generative ai", "genai", "agents",
            "openai", "anthropic", "gpt", "claude",
        }
        ai_ml_hits = len(required_normalized & _AI_ML_KEYWORDS)

        # User domains we can match against
        user_domains = {"ai/ml", "data science", "fintech", "saas", "technology"}

        if ai_ml_hits >= 3:
            # Strong AI/ML signal from required skills -- boost domain score
            domain_overlap = max(domain_overlap, 4)

        if domain_overlap >= 4:
            domain_score = 15.0  # direct match
        elif domain_overlap >= 2:
            domain_score = 10.0  # adjacent
        elif domain_overlap >= 1:
            domain_score = 5.0   # transferable
        else:
            domain_score = 7.5   # neutral
        breakdown["domain_relevance"] = domain_score

        # --- Recency (0-10) ---
        recency_score = 7.0  # placeholder
        breakdown["recency"] = recency_score

        total = hard_score + proj_score + coherence + domain_score + recency_score
        return round(total, 1), breakdown

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _skill_match(self, skill: str, profile: set[str]) -> bool:
        """Check if skill or any synonym is in profile."""
        normalized = self._normalize(skill)
        if normalized in profile:
            return True
        # Check all synonyms of the canonical form
        for alias in self._synonyms.get(normalized, []):
            if alias.lower().strip() in profile:
                return True
        return False


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _get(obj, attr: str, default=None):
    """Get attribute from dict or object."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)
