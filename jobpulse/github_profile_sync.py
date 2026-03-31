"""Nightly GitHub Profile Sync — Task 5.

Populates MindGraph with skills and projects from three sources:
1. GitHub repos (via fetch_and_cache_repos)
2. Resume BASE_SKILLS (from cv_templates/generate_cv.py)
3. Past successful applications (ATS >= 90%)

CLI:  python -m jobpulse.runner profile-sync
Cron: 3am daily
"""

from __future__ import annotations

import time

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Source 1: GitHub repos
# ---------------------------------------------------------------------------


def sync_repos_to_graph(repos: list[dict], store) -> None:  # type: ignore[type-arg]
    """Upsert each repo as a PROJECT entity in MindGraph.

    Translates the github_matcher repo format (languages as list, url as html_url)
    into the format expected by SkillGraphStore.upsert_project().

    SkillGraphStore.upsert_project expects:
        repo["language"]  — primary language (singular string)
        repo["topics"]    — list of topic strings
        repo["name"]      — repo name
        repo["description"] — description text
    """
    for repo in repos:
        # Primary language: first entry of languages list (or empty string)
        languages: list[str] = repo.get("languages") or []
        primary_language: str = languages[0] if languages else ""

        adapted: dict = {
            "name": repo.get("name", ""),
            "description": repo.get("description", "") or "",
            "language": primary_language,
            # Merge all languages (beyond the primary) into topics so they become
            # DEMONSTRATES relations too.
            "topics": list({*repo.get("topics", []), *languages[1:]}),
            "html_url": repo.get("url", ""),
        }

        try:
            store.upsert_project(adapted)
            logger.debug("Upserted project: %s", adapted["name"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to upsert project %s: %s",
                adapted.get("name"),
                exc,
            )


# ---------------------------------------------------------------------------
# Source 1b: README-based skill extraction
# ---------------------------------------------------------------------------


def sync_readme_skills(repos: list[dict], store) -> None:  # type: ignore[type-arg]
    """Fetch README + relevant .md files for each repo and extract skills.

    Scans: README, CLAUDE.md, docs/*.md (top-level only).
    Creates DEMONSTRATES relations between projects and skills found.
    No LLM calls -- uses the 582-entry skill taxonomy for free extraction.
    """
    import httpx

    from jobpulse.config import GITHUB_TOKEN
    from jobpulse.skill_extractor import extract_skills_rule_based

    headers = {"Accept": "application/vnd.github.raw"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    # MD files to scan per repo (README is fetched via dedicated endpoint)
    EXTRA_MD_FILES = ["CLAUDE.md", "docs/ARCHITECTURE.md", "docs/README.md"]

    total_skills = 0
    for repo in repos:
        repo_name = repo.get("name", "")
        if not repo_name or "/" not in repo_name:
            continue

        all_md_text = ""

        try:
            with httpx.Client(timeout=15) as client:
                # Fetch README
                resp = client.get(
                    f"https://api.github.com/repos/{repo_name}/readme",
                    headers=headers,
                )
                if resp.status_code == 200 and len(resp.text) >= 50:
                    all_md_text += resp.text + "\n"

                # Fetch extra .md files (non-blocking — skip silently on 404)
                for md_file in EXTRA_MD_FILES:
                    try:
                        md_resp = client.get(
                            f"https://api.github.com/repos/{repo_name}/contents/{md_file}",
                            headers=headers,
                        )
                        if md_resp.status_code == 200 and len(md_resp.text) >= 50:
                            all_md_text += md_resp.text + "\n"
                    except Exception:
                        pass

                if not all_md_text:
                    continue

                # Extract skills from all collected .md text
                result = extract_skills_rule_based(all_md_text)
                skills = result.get("required_skills", []) + result.get("preferred_skills", [])

                if skills:
                    languages: list[str] = repo.get("languages") or []
                    primary_language: str = languages[0] if languages else ""
                    enriched: dict = {
                        "name": repo_name,
                        "description": repo.get("description", "") or "",
                        "language": primary_language,
                        "topics": list(
                            {*repo.get("topics", []), *(s.lower() for s in skills)}
                        ),
                        "html_url": repo.get("url", ""),
                    }
                    store.upsert_project(enriched)
                    total_skills += len(skills)
                    logger.debug("MD files enriched %s with %d skills", repo_name, len(skills))

        except Exception as exc:  # noqa: BLE001
            logger.debug("MD fetch failed for %s: %s", repo_name, exc)

        # Brief pause to respect GitHub rate limits
        time.sleep(0.3)

    logger.info("Synced README skills: %d skills across %d repos", total_skills, len(repos))


# ---------------------------------------------------------------------------
# Source 2: Resume BASE_SKILLS
# ---------------------------------------------------------------------------


def sync_resume_skills(store) -> None:  # type: ignore[type-arg]
    """Parse BASE_SKILLS from generate_cv.py and upsert each skill into MindGraph.

    BASE_SKILLS structure:
        {"Languages:": "Python | SQL | JavaScript | TypeScript", ...}
    Keys have trailing colons; values use " | " as separator.
    """
    try:
        from jobpulse.cv_templates.generate_cv import BASE_SKILLS  # type: ignore[import]
    except ImportError as exc:
        logger.info("Could not import BASE_SKILLS: %s — skipping resume sync", exc)
        return

    total = 0
    for category_key, skills_str in BASE_SKILLS.items():
        # Strip trailing colon from category label
        category = category_key.rstrip(":").strip()

        # Split individual skills on " | "
        skills = [s.strip() for s in skills_str.split("|") if s.strip()]

        for skill in skills:
            try:
                store.upsert_skill(skill, source="resume", description=f"From CV: {category}")
                total += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to upsert resume skill '%s': %s", skill, exc)

    logger.info("Synced %d resume skills from %d categories", total, len(BASE_SKILLS))


# ---------------------------------------------------------------------------
# Source 3: Past successful applications (ATS >= 90%)
# ---------------------------------------------------------------------------


def sync_past_applications(store) -> None:  # type: ignore[type-arg]
    """Mine required_skills from high-ATS applications (score >= 90) and upsert them."""
    try:
        import json

        from jobpulse.job_db import JobDB  # type: ignore[import]
    except ImportError as exc:
        logger.info("JobDB not available (%s) — skipping past-application sync", exc)
        return

    try:
        db = JobDB()
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not instantiate JobDB (%s) — skipping past-application sync", exc)
        return

    # Fetch all Applied records; filter by ats_score >= 90
    try:
        applied_rows = db.get_applications_by_status("Applied")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query applied applications: %s — skipping", exc)
        return

    high_ats = [r for r in applied_rows if (r.get("ats_score") or 0) >= 90]
    if not high_ats:
        logger.info("No high-ATS applications (>= 90) found — skipping past-app sync")
        return

    # Collect job IDs so we can pull required_skills from listings
    total = 0
    processed_jobs: set[str] = set()
    for app in high_ats:
        job_id = app.get("job_id", "")
        if not job_id or job_id in processed_jobs:
            continue
        processed_jobs.add(job_id)

        listing = db.get_listing(job_id)
        if not listing:
            continue

        # required_skills is stored as JSON list in the DB
        raw = listing.get("required_skills") or "[]"
        try:
            skills: list[str] = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            skills = []

        for skill in skills:
            if not skill:
                continue
            try:
                store.upsert_skill(
                    skill,
                    source="past_app",
                    description=f"Required by {listing.get('company', 'unknown')} ({listing.get('title', '')})",
                )
                total += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to upsert past-app skill '%s': %s", skill, exc)

    logger.info(
        "Synced %d skills from %d high-ATS applications",
        total,
        len(processed_jobs),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def sync_profile() -> None:
    """Run all three sync sources and log final stats."""
    from jobpulse.github_matcher import fetch_and_cache_repos
    from jobpulse.skill_graph_store import SkillGraphStore

    store = SkillGraphStore()

    # --- Source 1: GitHub repos ---
    repos: list[dict] = []
    try:
        repos = fetch_and_cache_repos()
        logger.info("Fetched %d repos from GitHub", len(repos))
        sync_repos_to_graph(repos, store)
    except Exception as exc:  # noqa: BLE001
        logger.error("GitHub repo sync failed: %s", exc)

    # --- Source 1b: README-based skill extraction ---
    if repos:
        try:
            sync_readme_skills(repos, store)
        except Exception as exc:  # noqa: BLE001
            logger.error("README skill sync failed: %s", exc)

    # --- Source 2: Resume skills ---
    try:
        sync_resume_skills(store)
    except Exception as exc:  # noqa: BLE001
        logger.error("Resume skill sync failed: %s", exc)

    # --- Source 3: Past applications ---
    try:
        sync_past_applications(store)
    except Exception as exc:  # noqa: BLE001
        logger.error("Past-application sync failed: %s", exc)

    # --- Final stats ---
    try:
        stats = store.get_profile_stats()
        logger.info(
            "Profile sync complete — skills=%d  projects=%d  demonstrates=%d",
            stats["total_skills"],
            stats["total_projects"],
            stats["total_demonstrates"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to retrieve profile stats: %s", exc)
