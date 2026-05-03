"""Backfill: extract real external ATS URLs for Indeed jobs in the Notion Job Tracker.

Connects to the **existing JobPulse Chrome instance via CDP** (the persistent
profile that has accumulated real-user trust signals over time). This is
fundamentally different from a fresh Playwright launch — that gets caught by
Indeed's bot management, while a real Chrome session that you've actually used
to browse Indeed for weeks looks like a returning human.

Pipeline per job:
  1. Connect to your existing Chrome via CDP (auto-launches if needed)
  2. Navigate to the Indeed URL
  3. Wait for redirect chain to settle (rc/clk → viewjob)
  4. If page.url is off-Indeed → done
  5. Else find the "Apply on company site" button, move mouse human-like
     via Bezier curve (PlaywrightDriver._move_mouse_to), then click
  6. Listen for popup OR same-tab nav, return whichever produces a
     non-Indeed URL
  7. Persist to applications.db.job_listings.direct_url AND
     platform_bypass.db.bypass_cache (so the orchestrator picks it up)

Usage:
    python -m jobpulse.runner chrome-pw     # start real Chrome with CDP first
    python -m jobpulse.scripts.resolve_indeed_to_ats           # run backfill
    python -m jobpulse.scripts.resolve_indeed_to_ats --dry-run # show plan
    python -m jobpulse.scripts.resolve_indeed_to_ats --limit N # first N only
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load_found_indeed_jobs() -> list[dict]:
    """Pull Status='Found' jobs from Notion, keep Indeed-source ones."""
    from jobpulse.job_notion_sync import fetch_found_jobs_from_notion
    rows = fetch_found_jobs_from_notion()
    return [r for r in rows if "indeed.com" in (r.get("url") or "").lower()]


def _job_key_from_indeed(url: str) -> str | None:
    try:
        return (parse_qs(urlparse(url).query).get("jk") or [None])[0]
    except Exception:
        return None


def _update_local_db(notion_url: str, direct_url: str) -> bool:
    """Update applications.db.job_listings.direct_url for any row matching the URL."""
    db = REPO_ROOT / "data" / "applications.db"
    if not db.exists():
        return False
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "UPDATE job_listings SET direct_url = ? WHERE url = ?",
            (direct_url, notion_url),
        )
        if cur.rowcount > 0:
            return True
        jk = _job_key_from_indeed(notion_url)
        if jk:
            cur = conn.execute(
                "UPDATE job_listings SET direct_url = ? WHERE url LIKE ?",
                (direct_url, f"%jk={jk}%"),
            )
            return cur.rowcount > 0
    return False


def _seed_bypass_cache(company: str, direct_url: str) -> None:
    try:
        from jobpulse.platform_bypass import get_platform_bypass
        get_platform_bypass()._store_cached(
            company, direct_url, ats_platform="", strategy="indeed_redirect_cdp",
        )
    except Exception:
        pass


async def _resolve_one(driver, url: str, debug: bool = True) -> str | None:
    """Use the JobPulse PlaywrightDriver to navigate Indeed and capture external URL."""
    page = driver.page
    if page is None:
        return None

    # Reset mouse position to a neutral spot — prevents prior Bezier endpoint
    # from drifting off-screen across iterations.
    try:
        vp = page.viewport_size or {"width": 1280, "height": 720}
        await page.mouse.move(vp["width"] // 2, vp["height"] // 4)
        if hasattr(driver, "_mouse_x"):
            driver._mouse_x = vp["width"] // 2
            driver._mouse_y = vp["height"] // 4
    except Exception:
        pass

    # Close any leftover popup tabs from previous iterations (Indeed often
    # opens a popup on apply that we close, but stragglers may interfere).
    try:
        ctx = page.context
        for p in list(ctx.pages):
            if p is page:
                continue
            try:
                await p.close()
            except Exception:
                pass
    except Exception:
        pass

    # Step 1: navigate (PlaywrightDriver.navigate handles cookies, snapshot capture)
    try:
        await driver.navigate(url)
    except Exception as exc:
        if debug:
            print(f"          [debug] navigate raised: {exc}")

    # Step 2: poll for redirect resolution (8s)
    pre_url = page.url or ""
    for _ in range(16):
        await asyncio.sleep(0.5)
        cur = page.url or ""
        if cur and "indeed.com" not in cur.lower() and cur.startswith("http"):
            if debug:
                print(f"          [debug] auto-redirect → {cur[:120]}")
            return cur

    final_url = page.url or ""
    if debug:
        print(f"          [debug] settled at: {final_url[:120]}")

    if final_url and "indeed.com" not in final_url.lower():
        return final_url

    # Step 3: still on Indeed — find the apply button, move mouse human-like, click.
    apply_loc = page.locator(
        "button:has-text('Apply on company'), a:has-text('Apply on company'), "
        "button:has-text('Apply now'), a:has-text('Apply now')"
    ).first
    try:
        if await apply_loc.count() == 0:
            if debug:
                print(f"          [debug] no apply button found")
            return None
    except Exception:
        return None

    # Set up popup + network listeners BEFORE the click
    captured: list[str] = []

    def _on_response(resp):
        try:
            u = resp.url or ""
            if u and "indeed.com" not in u.lower() and u.startswith("http"):
                # Skip known Indeed-owned ancillary domains
                ancillary = (
                    "hrtechprivacy", "hiringlab", "indeedevents",
                    "googleapis", "doubleclick", "google-analytics",
                    "googletagmanager", "facebook.com", "fonts.gstatic",
                    "cookielaw", "cdn.jsdelivr",
                )
                if not any(a in u.lower() for a in ancillary):
                    captured.append(u)
            if 300 <= resp.status < 400:
                loc = resp.headers.get("location", "")
                if loc and "indeed.com" not in loc.lower() and loc.startswith("http"):
                    captured.append(loc)
        except Exception:
            pass

    page.on("response", _on_response)
    ctx = page.context
    popup_task = asyncio.create_task(
        asyncio.wait_for(ctx.wait_for_event("page"), timeout=10.0),
    )

    # Step 4: human-like mouse movement to the button via Bezier curve
    try:
        await driver._smart_scroll(apply_loc)
        await driver._move_mouse_to(apply_loc)
    except Exception as exc:
        if debug:
            print(f"          [debug] human mouse move failed: {exc}")

    # Step 5: click
    try:
        await apply_loc.click(timeout=5000)
    except Exception as exc:
        if debug:
            print(f"          [debug] click failed: {exc}")

    # Step 6: race popup vs same-tab navigation
    found: str | None = None
    pre_click_url = page.url or final_url
    for _ in range(20):  # ~10s
        await asyncio.sleep(0.5)
        if popup_task.done():
            try:
                new_page = popup_task.result()
                await asyncio.sleep(2.5)
                pu = new_page.url or ""
                try:
                    await new_page.close()
                except Exception:
                    pass
                if pu and "indeed.com" not in pu.lower():
                    found = pu
                    if debug:
                        print(f"          [debug] popup→ {pu[:120]}")
                    break
            except Exception:
                pass
        cur = page.url or ""
        if cur and cur != pre_click_url and "indeed.com" not in cur.lower():
            found = cur
            if debug:
                print(f"          [debug] same-tab→ {cur[:120]}")
            break

    if not popup_task.done():
        popup_task.cancel()
    try:
        page.remove_listener("response", _on_response)
    except Exception:
        pass

    if found:
        return found

    # Step 7: fallback — any URL captured by the network listener
    if captured:
        if debug:
            print(f"          [debug] network captured {len(captured)} non-Indeed URLs; first: {captured[0][:120]}")
        return captured[0]

    if debug:
        print(f"          [debug] click had no useful effect")
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and print but do not persist")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only first N jobs (0 = all)")
    args = parser.parse_args()

    print("Loading Status='Found' Indeed jobs from Notion Job Tracker...")
    jobs = _load_found_indeed_jobs()
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"Found {len(jobs)} Indeed jobs with status=Found in Notion\n")
    if not jobs:
        return 0

    # Connect to existing Chrome via CDP — this is the trust-signaled session
    from jobpulse.playwright_driver import PlaywrightDriver
    driver = PlaywrightDriver()
    try:
        await driver.connect()
    except Exception as exc:
        print(f"\nERROR: could not connect to Chrome via CDP: {exc}")
        print("Start Chrome first: python -m jobpulse.runner chrome-pw")
        return 1

    print(f"Connected via CDP. Page: {driver.page.url if driver.page else '(no page)'}\n")

    results: dict[str, dict] = {}
    for i, j in enumerate(jobs, 1):
        company = j.get("company", "")
        title = j.get("title", "")
        url = j.get("url", "")
        notion_id = j.get("notion_page_id", "")
        print(f"[{i:3d}/{len(jobs)}] {company[:30]:30s} | {title[:40]:40s}")
        try:
            external = await _resolve_one(driver, url, debug=True)
            if external:
                print(f"          → {external[:100]}")
                results[notion_id] = {
                    "company": company, "title": title,
                    "indeed_url": url, "direct_url": external,
                }
        except Exception as exc:
            print(f"          [error] {exc}")
        await asyncio.sleep(8.0)  # throttle — Indeed rate-limits rapid apply clicks

    if not args.dry_run and results:
        print(f"\nPersisting {len(results)} resolutions...")
        db_updates = 0
        for nid, info in results.items():
            if _update_local_db(info["indeed_url"], info["direct_url"]):
                db_updates += 1
            _seed_bypass_cache(info["company"], info["direct_url"])
        print(f"  applications.db: {db_updates} rows updated")
        print(f"  bypass_cache:    {len(results)} entries seeded")
    elif args.dry_run:
        print(f"\n[DRY RUN] {len(results)} captures — not persisting.")

    print("\n=== Summary ===")
    print(f"Indeed jobs processed:  {len(jobs)}")
    print(f"External URLs captured: {len(results)} ({len(results)/len(jobs)*100:.0f}%)")
    by_host = Counter()
    for info in results.values():
        host = urlparse(info["direct_url"]).netloc.lower().removeprefix("www.")
        by_host[host] += 1
    if by_host:
        print(f"By destination host:")
        for host, count in by_host.most_common(10):
            print(f"  {host:40s} {count:3d}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
