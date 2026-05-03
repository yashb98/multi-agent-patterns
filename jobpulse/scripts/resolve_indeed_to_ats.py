"""Backfill: resolve direct ATS URLs for Indeed jobs in production.

Pulls every Indeed job from `applications.db.job_listings`, runs the
no-browser strategies of `platform_bypass.PlatformBypass.resolve_direct_url`
(cache → FormExperienceDB → known ATS patterns), and updates the
`direct_url` column for jobs that resolve.

Optionally launches Playwright for web-search resolution on jobs that
strategies 1-3 didn't resolve.

Usage:
    python -m jobpulse.scripts.resolve_indeed_to_ats           # no browser, cheap pass
    python -m jobpulse.scripts.resolve_indeed_to_ats --browser # add web-search pass
    python -m jobpulse.scripts.resolve_indeed_to_ats --dry-run # show plan, don't update

Bypasses Indeed's Cloudflare wall by switching the apply URL to the
direct ATS source (Greenhouse / Lever / Workday / Ashby / etc.).
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load_indeed_jobs() -> list[dict]:
    """Pull all Indeed jobs from production DB that don't have a direct_url yet."""
    db = REPO_ROOT / "data" / "applications.db"
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT job_id, company, title, url, ats_platform, direct_url, description_raw "
            "FROM job_listings "
            "WHERE url LIKE '%indeed.com%' "
            "  AND (direct_url IS NULL OR direct_url = '') "
            "ORDER BY found_at DESC"
        ).fetchall()
    cols = ["job_id", "company", "title", "url", "ats_platform", "direct_url", "description_raw"]
    return [dict(zip(cols, r)) for r in rows]


def _extract_url_from_jd(description: str | None) -> str | None:
    """Try to find a direct application URL in the JD text itself.

    Indeed sometimes includes 'Apply at: <company.com/careers>' or similar.
    Returns the first URL that matches a known ATS pattern.
    """
    if not description:
        return None
    import re
    # Match http(s) URLs
    urls = re.findall(r"https?://[^\s\)\]<>\"']+", description)
    known_ats = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com",
                 "smartrecruiters.com", "icims.com", "workable.com", "bamboohr.com",
                 "successfactors.com", "taleo.net", "jobvite.com")
    for url in urls:
        url_lower = url.lower()
        if any(ats in url_lower for ats in known_ats):
            return url.rstrip(".,;:")  # strip trailing punctuation
    return None


def _update_direct_url(job_id: str, direct_url: str, ats_platform: str) -> None:
    """Persist resolved URL back to the production DB."""
    db = REPO_ROOT / "data" / "applications.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE job_listings SET direct_url = ?, ats_platform = COALESCE(NULLIF(?, ''), ats_platform) "
            "WHERE job_id = ?",
            (direct_url, ats_platform, job_id),
        )


async def _resolve_with_browser(jobs: list[dict]) -> dict[str, tuple[str, str]]:
    """Strategy 4 — Playwright web search for jobs not resolved by 1-3.

    Returns: {job_id: (direct_url, strategy)}
    """
    resolved: dict[str, tuple[str, str]] = {}
    try:
        from playwright.async_api import async_playwright
        from jobpulse.platform_bypass import get_platform_bypass

        pb = get_platform_bypass()
        async with async_playwright() as p:
            # Launch headless for backfill — we're just reading search results
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            for j in jobs:
                try:
                    result = await pb.resolve_direct_url(
                        job={"company": j["company"], "title": j["title"]},
                        blocked_url=j["url"],
                        page=page,
                    )
                    if result.resolved and result.direct_url:
                        resolved[j["job_id"]] = (result.direct_url, result.strategy_used)
                        print(f"  ✓ [{result.strategy_used:14s}] {j['company']}: {result.direct_url[:80]}")
                except Exception as exc:
                    print(f"  ✗ [error]         {j['company']}: {exc}")
            await browser.close()
    except ImportError:
        print("playwright not installed — skipping browser resolution pass")
    except Exception as exc:
        print(f"Browser pass failed: {exc}")
    return resolved


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true",
                        help="Run the Playwright web-search pass for unresolved jobs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show resolution plan without updating the DB")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only the first N jobs (0 = all)")
    args = parser.parse_args()

    jobs = _load_indeed_jobs()
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"Found {len(jobs)} Indeed jobs without direct_url\n")

    if not jobs:
        return 0

    from jobpulse.platform_bypass import get_platform_bypass, PlatformBypass

    # Pass 0: extract URL from JD text (free, deterministic)
    print("=== Pass 0: JD-text URL extraction (free) ===")
    pass0_resolved: dict[str, tuple[str, str]] = {}
    for j in jobs:
        url = _extract_url_from_jd(j.get("description_raw"))
        if url:
            pass0_resolved[j["job_id"]] = (url, "jd_text")
            print(f"  ✓ [jd_text]        {j['company']}: {url[:80]}")
    print(f"Pass 0 resolved: {len(pass0_resolved)} / {len(jobs)}\n")

    remaining = [j for j in jobs if j["job_id"] not in pass0_resolved]

    # Pass 1-3: cache → FormExperienceDB → ATS patterns (no browser, cheap)
    print(f"=== Pass 1-3: cache + FE + ATS patterns ({len(remaining)} jobs) ===")
    pb = get_platform_bypass()
    pass123_resolved: dict[str, tuple[str, str]] = {}
    for j in remaining:
        try:
            # Run resolve_direct_url with page=None to skip browser strategies
            result = await pb.resolve_direct_url(
                job={"company": j["company"], "title": j["title"]},
                blocked_url=j["url"],
                page=None,
            )
            if result.resolved and result.direct_url:
                pass123_resolved[j["job_id"]] = (result.direct_url, result.strategy_used)
                print(f"  ✓ [{result.strategy_used:14s}] {j['company']}: {result.direct_url[:80]}")
        except Exception as exc:
            print(f"  ✗ [error]         {j['company']}: {exc}")
    print(f"Pass 1-3 resolved: {len(pass123_resolved)} / {len(remaining)}\n")

    remaining = [j for j in remaining if j["job_id"] not in pass123_resolved]

    # Pass 4: Playwright web search (optional)
    pass4_resolved: dict[str, tuple[str, str]] = {}
    if args.browser and remaining:
        print(f"=== Pass 4: Playwright web search ({len(remaining)} jobs) ===")
        pass4_resolved = await _resolve_with_browser(remaining)
        print(f"Pass 4 resolved: {len(pass4_resolved)} / {len(remaining)}\n")
    elif remaining:
        print(f"Skipped Pass 4 (--browser not set): {len(remaining)} jobs unresolved\n")

    # Aggregate + persist
    all_resolved = {**pass0_resolved, **pass123_resolved, **pass4_resolved}
    total_resolved = len(all_resolved)
    by_strategy = Counter(s for _, s in all_resolved.values())

    print("=== Summary ===")
    print(f"Total Indeed jobs scanned: {len(jobs)}")
    print(f"Total resolved: {total_resolved} ({total_resolved/len(jobs)*100:.0f}%)")
    print(f"By strategy:")
    for strat, count in by_strategy.most_common():
        print(f"  {strat:20s} {count:3d}")
    print(f"Unresolved: {len(jobs) - total_resolved}")

    if args.dry_run:
        print("\n[DRY RUN] No DB updates applied.")
        return 0

    print("\nApplying updates to applications.db ...")
    for job_id, (direct_url, strategy) in all_resolved.items():
        ats_platform = ""
        try:
            from jobpulse.platform_bypass import PlatformBypass
            ats_platform = PlatformBypass._detect_ats_from_url(direct_url)
        except Exception:
            pass
        _update_direct_url(job_id, direct_url, ats_platform)
    print(f"Updated {total_resolved} job_listings rows with direct_url.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
