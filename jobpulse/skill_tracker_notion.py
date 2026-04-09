"""Notion Skill Tracker — sync extracted JD skills to Notion for user verification.

When the pipeline extracts skills from JDs, any skill NOT already verified gets
added to a Notion database as "Pending". The user reviews skills in Notion and
marks them "I Know" or "Don't Know". The system reads back approvals and adds
verified skills to the MindGraph profile via SkillGraphStore.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR, NOTION_PARENT_PAGE_ID
from jobpulse.notion_client import notion_api as _notion_api

logger = get_logger(__name__)

DB_ID_CACHE_PATH = DATA_DIR / "skill_tracker_db_id.txt"



# _notion_api imported from jobpulse.notion_client (centralized, with retry + 401 handling)


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

_FRAMEWORKS = {
    "react", "django", "flask", "fastapi", "spring", "angular", "vue",
    "next.js", "express", "rails", "langchain", "pytorch", "tensorflow",
    "sklearn", "nextjs", "nuxt", "svelte", "nestjs", "gin", "echo",
    "streamlit", "gradio", "pandas", "numpy", "scipy",
}
_CLOUDS = {
    "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "lambda",
    "s3", "ec2", "ecs", "eks", "fargate", "cloudformation", "cdk",
    "pulumi", "heroku", "vercel", "netlify", "digitalocean",
}
_TOOLS = {
    "git", "jenkins", "github actions", "jira", "confluence", "postman",
    "selenium", "playwright", "grafana", "prometheus", "mlflow", "power bi",
    "tableau", "datadog", "sentry", "kibana", "elasticsearch", "redis",
    "rabbitmq", "kafka", "airflow", "dbt", "snowflake", "bigquery",
}
_METHODS = {
    "agile", "scrum", "tdd", "ci/cd", "devops", "mlops", "microservices",
    "rest api", "graphql", "event-driven", "domain-driven design", "kanban",
}
_SOFT = {
    "communication", "leadership", "teamwork", "problem solving",
    "project management", "time management", "mentoring", "stakeholder management",
}


def _detect_category(skill: str) -> str:
    """Simple keyword-based category detection."""
    skill_lower = skill.lower()
    if skill_lower in _FRAMEWORKS:
        return "Framework"
    if skill_lower in _CLOUDS:
        return "Cloud"
    if skill_lower in _TOOLS:
        return "Tool"
    if skill_lower in _METHODS:
        return "Method"
    if skill_lower in _SOFT:
        return "Soft Skill"
    return "Technical"


# ---------------------------------------------------------------------------
# DB ID caching
# ---------------------------------------------------------------------------


def _load_cached_db_id() -> str | None:
    """Load cached Skill Tracker DB ID from disk."""
    if DB_ID_CACHE_PATH.exists():
        db_id = DB_ID_CACHE_PATH.read_text().strip()
        if db_id:
            return db_id
    return None


def _save_cached_db_id(db_id: str) -> None:
    """Persist the Skill Tracker DB ID to disk."""
    DB_ID_CACHE_PATH.write_text(db_id)


def _validate_db_id(db_id: str) -> bool:
    """Check whether a cached DB ID is still valid by querying it."""
    resp = _notion_api("POST", f"/databases/{db_id}/query", {"page_size": 1})
    return "results" in resp


# ---------------------------------------------------------------------------
# Database creation
# ---------------------------------------------------------------------------


def ensure_skill_tracker_db() -> str | None:
    """Create the Skill Tracker database in Notion if it doesn't exist.

    Uses NOTION_PARENT_PAGE_ID as the parent page.
    Returns the database ID, or None on failure.
    Caches the DB ID in data/skill_tracker_db_id.txt for reuse.
    """
    if not NOTION_API_KEY:
        logger.warning("NOTION_API_KEY not set — skill tracker disabled")
        return None
    if not NOTION_PARENT_PAGE_ID:
        logger.warning("NOTION_PARENT_PAGE_ID not set — skill tracker disabled")
        return None

    # Check cache first
    cached = _load_cached_db_id()
    if cached and _validate_db_id(cached):
        return cached

    # Create the database
    payload = {
        "parent": {"page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"text": {"content": "Skill Tracker"}}],
        "properties": {
            "Skill": {"title": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "Pending", "color": "yellow"},
                        {"name": "I Know", "color": "green"},
                        {"name": "Don't Know", "color": "red"},
                        {"name": "Learning", "color": "blue"},
                    ]
                }
            },
            "Times Seen": {"number": {"format": "number"}},
            "Category": {
                "select": {
                    "options": [
                        {"name": "Technical", "color": "purple"},
                        {"name": "Framework", "color": "blue"},
                        {"name": "Cloud", "color": "orange"},
                        {"name": "Tool", "color": "gray"},
                        {"name": "Method", "color": "green"},
                        {"name": "Soft Skill", "color": "yellow"},
                    ]
                }
            },
            "Source JDs": {"rich_text": {}},
            "First Seen": {"date": {}},
            "Last Seen": {"date": {}},
        },
    }

    resp = _notion_api("POST", "/databases", payload)
    db_id = resp.get("id")
    if db_id:
        _save_cached_db_id(db_id)
        logger.info("Created Skill Tracker database: %s", db_id)
        return db_id

    logger.error("Failed to create Skill Tracker DB: %s", resp.get("message", "unknown"))
    return None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _find_skill_page(db_id: str, skill_name: str) -> dict | None:
    """Query the Skill Tracker database for a skill by title (case-insensitive)."""
    query = {
        "filter": {
            "property": "Skill",
            "title": {"equals": skill_name},
        }
    }
    resp = _notion_api("POST", f"/databases/{db_id}/query", query)
    results = resp.get("results", [])
    return results[0] if results else None


def _query_by_status(db_id: str, status: str) -> list[dict]:
    """Query the Skill Tracker database for all pages with a given status."""
    all_results: list[dict] = []
    start_cursor: str | None = None

    while True:
        query: dict = {
            "filter": {
                "property": "Status",
                "select": {"equals": status},
            },
            "page_size": 100,
        }
        if start_cursor:
            query["start_cursor"] = start_cursor

        resp = _notion_api("POST", f"/databases/{db_id}/query", query)
        all_results.extend(resp.get("results", []))

        if resp.get("has_more") and resp.get("next_cursor"):
            start_cursor = resp["next_cursor"]
        else:
            break

    return all_results


def _extract_title(page: dict) -> str:
    """Extract the title text from a Notion page."""
    try:
        return page["properties"]["Skill"]["title"][0]["text"]["content"]
    except (KeyError, IndexError):
        return ""


def _extract_rich_text(page: dict, prop: str) -> str:
    """Extract rich_text content from a Notion page property."""
    try:
        return page["properties"][prop]["rich_text"][0]["text"]["content"]
    except (KeyError, IndexError):
        return ""


def _extract_number(page: dict, prop: str) -> int:
    """Extract a number property from a Notion page."""
    try:
        val = page["properties"][prop]["number"]
        return int(val) if val is not None else 0
    except (KeyError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sync_skills_to_notion(
    skills: list[str],
    company: str,
    category_hint: str = "Technical",
) -> None:
    """For each skill, check if it exists in Notion tracker.

    If exists: increment Times Seen, append company to Source JDs.
    If new: create as 'Pending' with Times Seen=1.
    Skip skills already marked 'I Know' — they're verified.
    """
    db_id = ensure_skill_tracker_db()
    if not db_id:
        return

    today = date.today().isoformat()

    for skill in skills:
        skill = skill.strip()
        if not skill:
            continue

        existing = _find_skill_page(db_id, skill)

        if existing:
            # Check status — skip "I Know" skills
            try:
                status = existing["properties"]["Status"]["select"]["name"]
            except (KeyError, TypeError):
                status = ""

            if status == "I Know":
                continue

            # Update: increment Times Seen, append company, update Last Seen
            page_id = existing["id"]
            times_seen = _extract_number(existing, "Times Seen")
            source_jds = _extract_rich_text(existing, "Source JDs")

            # Append company if not already listed
            companies = [c.strip() for c in source_jds.split(",") if c.strip()]
            if company not in companies:
                companies.append(company)
            new_source_jds = ", ".join(companies)

            update_payload = {
                "properties": {
                    "Times Seen": {"number": times_seen + 1},
                    "Source JDs": {
                        "rich_text": [{"text": {"content": new_source_jds[:2000]}}]
                    },
                    "Last Seen": {"date": {"start": today}},
                }
            }
            _notion_api("PATCH", f"/pages/{page_id}", update_payload)
            logger.debug("Updated skill '%s' — seen %dx", skill, times_seen + 1)
        else:
            # Create new skill as Pending
            category = _detect_category(skill) if category_hint == "Technical" else category_hint
            create_payload = {
                "parent": {"database_id": db_id},
                "properties": {
                    "Skill": {"title": [{"text": {"content": skill}}]},
                    "Status": {"select": {"name": "Pending"}},
                    "Times Seen": {"number": 1},
                    "Category": {"select": {"name": category}},
                    "Source JDs": {
                        "rich_text": [{"text": {"content": company}}]
                    },
                    "First Seen": {"date": {"start": today}},
                    "Last Seen": {"date": {"start": today}},
                },
            }
            _notion_api("POST", "/pages", create_payload)
            logger.debug("Created pending skill '%s' from %s", skill, company)


def get_verified_skills() -> list[str]:
    """Query Notion for all skills with Status='I Know'.

    Returns list of skill names (lowercase).
    Used to populate the MindGraph profile.
    """
    db_id = ensure_skill_tracker_db()
    if not db_id:
        return []

    pages = _query_by_status(db_id, "I Know")
    return [_extract_title(p).lower() for p in pages if _extract_title(p)]


def get_pending_skills() -> list[dict]:
    """Query Notion for all 'Pending' skills.

    Returns list of {skill, times_seen, source_jds}.
    """
    db_id = ensure_skill_tracker_db()
    if not db_id:
        return []

    pages = _query_by_status(db_id, "Pending")
    result = []
    for page in pages:
        title = _extract_title(page)
        if not title:
            continue
        result.append({
            "skill": title,
            "times_seen": _extract_number(page, "Times Seen"),
            "source_jds": _extract_rich_text(page, "Source JDs"),
        })

    # Sort by times_seen descending
    result.sort(key=lambda x: x["times_seen"], reverse=True)
    return result


def sync_verified_to_profile() -> int:
    """Pull all 'I Know' skills from Notion and upsert to SkillGraphStore.

    Returns count of skills synced.
    """
    verified = get_verified_skills()
    if not verified:
        return 0

    try:
        from jobpulse.skill_graph_store import SkillGraphStore
        store = SkillGraphStore()
        count = 0
        for skill in verified:
            store.upsert_skill(skill, source="notion_verified")
            count += 1
        logger.info("Synced %d verified skills to profile", count)
        return count
    except Exception as exc:
        logger.error("Failed to sync verified skills to profile: %s", exc)
        return 0
