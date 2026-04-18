"""Google Jobs scanner via Playwright (real Chrome CDP).

jobspy's HTTP-based Google scraper is broken — Google now requires JS execution
for job search results (udm=8). This scanner connects to a running Chrome
instance via CDP and extracts job listings from the rendered DOM.

Feature-gated: GOOGLE_JOBS_ENABLED=true (default: true).
Requires: python -m jobpulse.runner chrome-pw (Chrome with CDP on port 9222).
"""

from __future__ import annotations

import asyncio
import os
import time

from shared.logging_config import get_logger

logger = get_logger(__name__)

_JS_EXTRACT_JOBS = r"""async () => {
    const cards = document.querySelectorAll('span.gmxZue');
    const jobs = [];

    for (let i = 0; i < cards.length; i++) {
        const card = cards[i];
        const goEOPd = card.querySelector('div.GoEOPd');
        if (!goEOPd) continue;

        const titleEl = goEOPd.querySelector('div.tNxQIb');
        const companyEl = goEOPd.querySelector('div.wHYlTd.MKCbgd:not(.FqK3wc)');
        const locViaEl = goEOPd.querySelector('div.wHYlTd.FqK3wc');

        const title = titleEl?.textContent?.trim() || '';
        const company = companyEl?.textContent?.trim() || '';
        const locViaText = locViaEl?.textContent?.trim() || '';
        const viaSplit = locViaText.split('\u2022');
        const location = (viaSplit[0] || '').trim();

        if (!title) continue;

        titleEl.click();

        let desc = '';
        let applyUrl = '';
        const titlePrefix = title.substring(0, Math.min(title.length, 25));

        // Wait for a Fp3I9 section whose heading matches this job's title
        let section = null;
        for (let attempt = 0; attempt < 20; attempt++) {
            await new Promise(r => setTimeout(r, 300));
            const panel = document.querySelector('div.HV5Yde');
            if (!panel) continue;
            const secs = panel.querySelectorAll('div.Fp3I9');
            for (const sec of secs) {
                const headings = sec.querySelectorAll('[role=heading]');
                for (const h of headings) {
                    const ht = h.textContent.trim();
                    if (ht.includes(titlePrefix)) {
                        section = sec;
                        break;
                    }
                }
                if (section) break;
            }
            if (section) break;
        }

        if (!section) {
            jobs.push({ title, company, location, description: '', url: '', date_posted: '' });
            continue;
        }

        await new Promise(r => setTimeout(r, 300));

        // Expand full description within this section
        const showMore = section.querySelector('div.TOQyFc');
        if (showMore) {
            showMore.click();
            await new Promise(r => setTimeout(r, 600));
        }

        // Extract description from this section only
        const secText = section.innerText;
        const descIdx = secText.indexOf('Job description');
        desc = descIdx >= 0
            ? secText.substring(descIdx + 15).trim().substring(0, 4000)
            : secText.substring(0, 4000);

        // Extract apply URL from this section only
        section.querySelectorAll('a').forEach(a => {
            if (applyUrl) return;
            const t = a.textContent.trim().toLowerCase();
            if (t.includes('apply') && a.href && !a.href.includes('google.com')) {
                applyUrl = a.href;
            }
        });

        const dateEl = card.querySelector('div.ApHyTb');
        const dateRaw = dateEl?.innerText?.trim() || '';
        const dateMatch = dateRaw.match(/(\d+\s+(?:day|hour|minute|week|month)s?\s+ago)/i);

        jobs.push({
            title, company, location,
            description: desc, url: applyUrl,
            date_posted: dateMatch ? dateMatch[1] : '',
        });
    }
    return jobs;
}"""


def normalize_to_job_listing(raw: dict) -> dict:
    """Normalize a scraped Google Jobs result to a JobListing-compatible dict."""
    return {
        "title": raw.get("title", ""),
        "company": raw.get("company", ""),
        "location": raw.get("location", ""),
        "description": raw.get("description", ""),
        "url": raw.get("url", ""),
        "apply_url": raw.get("url", ""),
        "date_posted": raw.get("date_posted", ""),
        "source": "google_jobs",
        "platform": "google_jobs",
    }


async def _scan_google_jobs_async(
    search_terms: list[str],
    location: str,
    max_results: int = 50,
) -> list[dict]:
    """Connect to Chrome via CDP and scrape Google Jobs for each search term."""
    from jobpulse.playwright_driver import PlaywrightDriver

    driver = PlaywrightDriver()
    all_results: list[dict] = []

    try:
        await driver.connect()
        page = driver.page

        for term in search_terms:
            if len(all_results) >= max_results:
                break

            query = f"{term} jobs near {location}"
            url = f"https://www.google.com/search?q={query}&udm=8&tbs=qdr:d"

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2.5)

                jobs = await page.evaluate(_JS_EXTRACT_JOBS)
                remaining = max_results - len(all_results)
                jobs = jobs[:remaining]

                logger.info(
                    "Google Jobs: found %d listings for '%s' in %s",
                    len(jobs), term, location,
                )
                all_results.extend(jobs)
            except Exception as exc:
                logger.error("Google Jobs scan failed for '%s': %s", term, exc)

            await asyncio.sleep(1.5 + (time.monotonic() % 1))

    except ConnectionError as exc:
        logger.error(
            "Google Jobs: Chrome not running — start with: "
            "python -m jobpulse.runner chrome-pw (%s)",
            exc,
        )
    finally:
        await driver.close()

    return all_results


def scan_google_jobs(
    search_terms: list[str],
    location: str,
    max_results: int = 50,
) -> list[dict]:
    """Scan Google Jobs via Playwright (real Chrome), return normalized dicts.

    Requires Chrome running with CDP: python -m jobpulse.runner chrome-pw
    """
    if os.environ.get("GOOGLE_JOBS_ENABLED", "true").lower() != "true":
        logger.debug("Google Jobs scanner disabled (GOOGLE_JOBS_ENABLED != true)")
        return []

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(
                asyncio.run,
                _scan_google_jobs_async(search_terms, location, max_results),
            ).result(timeout=300)
    else:
        raw = asyncio.run(
            _scan_google_jobs_async(search_terms, location, max_results)
        )

    return [normalize_to_job_listing(r) for r in raw]
