"""GitHub agent — fetches yesterday's commits and trending repos."""

import json
import subprocess
from datetime import datetime, timedelta
from jobpulse.config import GITHUB_TOKEN, GITHUB_USERNAME
from jobpulse import event_logger


def _gh_api(endpoint: str) -> list:
    """Call GitHub API via gh CLI (uses stored auth, no token needed)."""
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True, timeout=15
        )
        return json.loads(result.stdout) if result.stdout else []
    except Exception as e:
        print(f"[GitHub] API error: {e}")
        return []


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
        all_repos = _gh_api(f"/users/{GITHUB_USERNAME}/repos?sort=pushed&per_page=15")
        repo_names = [r.get("name", "") for r in all_repos]
        s["output"] = f"Found {len(all_repos)} repos"
        s["metadata"] = {"repos": repo_names[:5]}

    commits = []
    repos = set()

    for repo_obj in all_repos:
        repo_name = repo_obj.get("name", "")
        pushed_at = repo_obj.get("pushed_at", "")[:10]

        # Skip repos not pushed yesterday
        if pushed_at != yesterday:
            continue

        # Step 2: Fetch actual commits for this repo from yesterday
        with trail.step("api_call", f"Fetch commits for {repo_name}",
                         step_input=f"Since: {yesterday}") as s:
            repo_commits = _gh_api(
                f"/repos/{GITHUB_USERNAME}/{repo_name}/commits"
                f"?since={yesterday}T00:00:00Z&until={today}T00:00:00Z&per_page=50"
            )
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


def get_trending_repos() -> list[dict]:
    """Fetch top 5 trending GitHub repos using the GitHub search API.

    Uses starred-recently heuristic: repos created in last 7 days sorted by stars.
    More reliable than scraping the trending page HTML.
    """
    from datetime import datetime, timedelta
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    # Search for repos created in last 7 days, sorted by stars
    results = _gh_api(
        f"/search/repositories?q=created:>{week_ago}+stars:>100&sort=stars&order=desc&per_page=5"
    )

    repos = []
    for item in (results.get("items", []) if isinstance(results, dict) else []):
        repos.append({
            "repo": item.get("full_name", ""),
            "description": (item.get("description") or "")[:80],
            "language": item.get("language") or "",
            "stars": item.get("stargazers_count", 0),
            "url": item.get("html_url", ""),
        })

    return repos[:5]


def format_trending(repos: list[dict]) -> str:
    """Format trending repos as clean readable text."""
    if not repos:
        return "  Could not fetch trending repos"
    lines = []
    for i, r in enumerate(repos[:5], 1):
        lang = f" [{r.get('language', '')}]" if r.get("language") else ""
        stars = f" ⭐ {r.get('stars', 0):,}" if r.get("stars") else ""
        lines.append(f"  {i}. {r['repo']}{lang}{stars}")
        if r.get("description"):
            lines.append(f"     {r['description']}")
        if r.get("url"):
            lines.append(f"     {r['url']}")
    return "\n".join(lines)
