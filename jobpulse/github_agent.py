"""GitHub agent — fetches yesterday's commits and trending repos."""

import json
import os
import subprocess
from datetime import datetime, timedelta
from jobpulse.config import GITHUB_TOKEN, GITHUB_USERNAME
from jobpulse import event_logger
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _find_gh() -> str:
    """Find gh CLI binary — cron doesn't have /opt/homebrew/bin in PATH."""
    for path in ["/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"]:
        if os.path.exists(path):
            return path
    return "gh"  # fallback to PATH lookup


GH_BIN = _find_gh()


def _gh_api(endpoint: str) -> tuple[list, str | None]:
    """Call gh api and return (parsed_data, error_string | None)."""
    try:
        result = subprocess.run(
            [_find_gh(), "api", endpoint, "--paginate"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or f"gh api exited with code {result.returncode}"
            logger.error("gh api error for %s: %s", endpoint, err)
            return [], err
        if not result.stdout.strip():
            return [], None  # Success, but no data
        data = json.loads(result.stdout)
        if not isinstance(data, list):
            data = [data]
        return data, None
    except Exception as exc:
        logger.error("gh api error: %s", exc)
        return [], str(exc)


def get_yesterday_commits(trigger: str = "scheduled_check") -> dict:
    """Fetch yesterday's commits across all repos.

    Uses the Commits API per-repo (not Events API) because the Events API
    strips the commits array from older PushEvents, causing 0-commit bugs.
    """
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("github_agent", trigger)

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    # Step 1: Get recently pushed repos
    with trail.step("api_call", "Fetch recently pushed repos",
                     step_input=f"User: {GITHUB_USERNAME}") as s:
        all_repos, api_err = _gh_api(f"/users/{GITHUB_USERNAME}/repos?sort=pushed&per_page=15")
        if api_err:
            logger.warning("GitHub API failed: %s", api_err)
            trail.log_step("error", f"GitHub API failed: {api_err}")
        repo_names = [r.get("name", "") for r in all_repos]
        s["output"] = f"Found {len(all_repos)} repos"
        s["metadata"] = {"repos": repo_names[:5]}

    commits = []
    repos = set()

    for repo_obj in all_repos:
        repo_name = repo_obj.get("name", "")
        pushed_at = repo_obj.get("pushed_at", "")[:10]

        # Skip repos not pushed recently (yesterday or later).
        # pushed_at reflects the LATEST push, so a repo pushed both
        # yesterday and today shows today's date. Using >= yesterday
        # ensures we don't miss repos that were pushed again after the
        # target date.
        if pushed_at < yesterday:
            continue

        # Step 2: Fetch actual commits for this repo from yesterday
        with trail.step("api_call", f"Fetch commits for {repo_name}",
                         step_input=f"Since: {yesterday}") as s:
            repo_commits, api_err = _gh_api(
                f"/repos/{GITHUB_USERNAME}/{repo_name}/commits"
                f"?since={yesterday}T00:00:00Z&until={today}T00:00:00Z&per_page=50"
            )
            if api_err:
                logger.warning("GitHub API failed: %s", api_err)
                trail.log_step("error", f"GitHub API failed: {api_err}")
            s["output"] = f"{len(repo_commits)} commits in {repo_name}"
            s["metadata"] = {"repo": repo_name, "count": len(repo_commits)}

        for c in repo_commits:
            msg = c.get("commit", {}).get("message", "").split("\n")[0][:100]
            sha = c.get("sha", "")[:7]
            commits.append({"repo": repo_name, "message": msg, "sha": sha})
            repos.add(repo_name)

    result = {
        "date": yesterday,
        "total_commits": len(commits),
        "repos": sorted(list(repos)),
        "commits": commits,
    }

    # Log to simulation events
    if commits:
        event_logger.log_event(
            event_type="github_activity",
            agent_name="github_agent",
            action="commits_fetched",
            content=f"{len(commits)} commit(s) across {', '.join(sorted(repos))}",
            metadata={"commit_count": len(commits), "repos": sorted(list(repos)), "date": yesterday},
        )

    trail.finalize(f"{len(commits)} commit(s) across {len(repos)} repo(s) on {yesterday}")
    return result


def format_commits(data: dict) -> str:
    """Format commit data as readable text."""
    if not data["commits"]:
        return "  No commits yesterday — let's fix that today!"
    repo_list = ", ".join(data["repos"])
    lines = [f"  {data['total_commits']} commit(s) across {repo_list}"]
    for c in data["commits"][:10]:
        lines.append(f"  • {c['repo']}: {c['message']}")
    return "\n".join(lines)


def get_trending_repos(count: int = 10) -> list[dict]:
    """Fetch today's trending GitHub repos by scraping github.com/trending.

    Parses each <article> individually so missing fields (language, description)
    don't cause misalignment across repos. Uses ?since=daily to ensure fresh
    daily results.
    """
    import httpx
    import re

    repos: list[dict] = []
    try:
        resp = httpx.get(
            "https://github.com/trending?since=daily",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            },
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text

        # Split by <article to parse each repo card independently.
        # This avoids misalignment when a repo lacks a description or language.
        articles = re.split(r"<article\b", html)[1:]  # skip pre-first-article
        for article_html in articles[:count]:
            # Repo link: h2 > a href="/owner/repo"
            link_m = re.search(r'<h2[^>]*>\s*<a[^>]*href="(/[^"]+)"', article_html)
            if not link_m:
                continue
            link = link_m.group(1)
            full_name = link.strip("/")

            # Description
            desc_m = re.search(r'<p class="col-9[^"]*"[^>]*>\s*(.+?)\s*</p>', article_html)
            description = desc_m.group(1).strip()[:80] if desc_m else ""

            # Language
            lang_m = re.search(
                r'<span itemprop="programmingLanguage">([^<]+)</span>', article_html
            )
            language = lang_m.group(1).strip() if lang_m else ""

            # Stars today
            stars_m = re.search(r'(\d[\d,]*)\s+stars\s+today', article_html)
            stars = int(stars_m.group(1).replace(",", "")) if stars_m else 0

            repos.append({
                "repo": full_name,
                "description": description,
                "language": language,
                "stars": stars,
                "url": f"https://github.com{link}",
            })

    except Exception as e:
        logger.error("Trending scrape failed, falling back to search API: %s", e)
        # Fallback: repos created in the last 7 days sorted by stars — gives
        # genuinely new/hot repos rather than the same all-time popular ones.
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        results, api_err = _gh_api(
            f"/search/repositories?q=created:>{week_ago}+stars:>100"
            f"&sort=stars&order=desc&per_page={count}"
        )
        if api_err:
            logger.warning("GitHub API failed: %s", api_err)
        # _gh_api wraps non-list responses in a list; search results come as
        # a single dict with an "items" key, so unwrap it if present.
        results_data = results[0] if results and isinstance(results[0], dict) and "items" in results[0] else {}
        for item in results_data.get("items", []):
            repos.append({
                "repo": item.get("full_name", ""),
                "description": (item.get("description") or "")[:80],
                "language": item.get("language") or "",
                "stars": item.get("stargazers_count", 0),
                "url": item.get("html_url", ""),
            })

    return repos[:count]


def format_trending(repos: list[dict]) -> str:
    """Format trending repos as clean readable text."""
    if not repos:
        return "  Could not fetch trending repos"
    lines = []
    for i, r in enumerate(repos, 1):
        lang = f" [{r.get('language', '')}]" if r.get("language") else ""
        stars = f" ⭐ {r.get('stars', 0):,}" if r.get("stars") else ""
        lines.append(f"  {i}. {r['repo']}{lang}{stars}")
        if r.get("description"):
            lines.append(f"     {r['description']}")
        if r.get("url"):
            lines.append(f"     {r['url']}")
    return "\n".join(lines)
