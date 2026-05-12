"""Platform bypass — resolve direct ATS URLs when aggregators block with security walls.

When Indeed/LinkedIn/TotalJobs/Reed/Glassdoor block with Cloudflare or CAPTCHA,
this module resolves the direct employer ATS URL (Greenhouse, Lever, Workday, etc.)
through cached mappings, web search, and known ATS patterns.

Integrates with ALL learning systems:
- NavigationLearner — cache company→ATS URL as a navigation sequence
- FormExperienceDB — check for known company domains
- GotchasDB — store bypass as a platform gotcha
- OptimizationEngine — emit platform_bypass signals with before/after
- ExperienceMemory — store successful bypasses as experiences
- TrajectoryStore — log bypass as a trajectory step
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from shared.logging_config import get_logger

logger = get_logger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "platform_bypass.db"

_AGGREGATOR_DOMAINS = frozenset({
    "indeed.com", "uk.indeed.com", "indeed.co.uk",
    "linkedin.com", "www.linkedin.com",
    "totaljobs.com", "www.totaljobs.com",
    "reed.co.uk", "www.reed.co.uk",
    "glassdoor.com", "glassdoor.co.uk", "www.glassdoor.com",
    "cwjobs.co.uk", "www.cwjobs.co.uk",
})

_ATS_BOARD_PATTERNS: dict[str, str] = {
    "greenhouse": "boards.greenhouse.io/{slug}",
    "lever": "jobs.lever.co/{slug}",
    "ashby": "jobs.ashbyhq.com/{slug}",
    "workday": "{slug}.wd3.myworkdayjobs.com",
    "smartrecruiters": "careers.smartrecruiters.com/{slug}",
    "icims": "careers-{slug}.icims.com",
}


@dataclass
class BypassResult:
    resolved: bool
    direct_url: str = ""
    strategy_used: str = ""
    search_queries: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str = ""


def is_aggregator_domain(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        return host in _AGGREGATOR_DOMAINS or any(agg in host for agg in _AGGREGATOR_DOMAINS)
    except Exception:
        return False


class PlatformBypass:
    """Resolves direct ATS URLs when aggregator platforms block."""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = str(db_path or _DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bypass_cache (
                    company TEXT NOT NULL,
                    ats_url TEXT NOT NULL,
                    ats_platform TEXT DEFAULT '',
                    strategy TEXT DEFAULT '',
                    success_count INTEGER DEFAULT 1,
                    last_used TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (company)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bypass_company
                ON bypass_cache(company)
            """)

    def _get_cached(self, company: str) -> str | None:
        company_key = company.strip().lower()
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT ats_url FROM bypass_cache WHERE company = ?",
                (company_key,),
            ).fetchone()
        if row:
            logger.info("platform_bypass: cache hit for %s → %s", company, row[0])
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "UPDATE bypass_cache SET last_used = ?, success_count = success_count + 1 WHERE company = ?",
                    (datetime.now(UTC).isoformat(), company_key),
                )
            return row[0]
        return None

    def _store_cached(self, company: str, ats_url: str, ats_platform: str, strategy: str) -> None:
        company_key = company.strip().lower()
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO bypass_cache (company, ats_url, ats_platform, strategy, last_used, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(company) DO UPDATE SET
                       ats_url = excluded.ats_url,
                       ats_platform = excluded.ats_platform,
                       strategy = excluded.strategy,
                       last_used = excluded.last_used,
                       success_count = success_count + 1""",
                (company_key, ats_url, ats_platform, strategy, now, now),
            )

    async def resolve_direct_url(
        self,
        job: dict[str, Any],
        blocked_url: str,
        page: Any | None = None,
    ) -> BypassResult:
        """Resolve a direct ATS URL for a blocked aggregator listing.

        Resolution order:
        1. Local SQLite cache
        2. FormExperienceDB domain lookup
        3. Known ATS pattern matching (company slug → board URL)
        4. Web search via Playwright browser
        """
        start = time.monotonic()
        company = job.get("company", "").strip()
        title = job.get("title", "").strip()

        if not company:
            return BypassResult(resolved=False, error="no company name in job data")

        # ── Strategy 1: Local cache ──
        cached = self._get_cached(company)
        if cached:
            return BypassResult(
                resolved=True,
                direct_url=cached,
                strategy_used="cache",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # ── Strategy 2: FormExperienceDB domain lookup ──
        fe_url = await self._check_form_experience(company)
        if fe_url:
            self._store_cached(company, fe_url, "", "form_experience")
            self._emit_learning_signals(company, blocked_url, fe_url, "form_experience", title)
            return BypassResult(
                resolved=True,
                direct_url=fe_url,
                strategy_used="form_experience",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # ── Strategy 3: Known ATS pattern matching ──
        ats_url = self._try_ats_patterns(company)
        if ats_url:
            self._store_cached(company, ats_url, "", "ats_pattern")
            self._emit_learning_signals(company, blocked_url, ats_url, "ats_pattern", title)
            return BypassResult(
                resolved=True,
                direct_url=ats_url,
                strategy_used="ats_pattern",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # ── Strategy 4: Web search via Playwright ──
        if page:
            search_result = await self._web_search_ats(company, title, page)
            if search_result:
                ats_platform = self._detect_ats_from_url(search_result)
                self._store_cached(company, search_result, ats_platform, "web_search")
                self._emit_learning_signals(company, blocked_url, search_result, "web_search", title)
                return BypassResult(
                    resolved=True,
                    direct_url=search_result,
                    strategy_used="web_search",
                    duration_ms=(time.monotonic() - start) * 1000,
                    search_queries=[f"{company} {title} careers apply"],
                )

        return BypassResult(
            resolved=False,
            error="all resolution strategies exhausted",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def _check_form_experience(self, company: str) -> str | None:
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            fe_db = FormExperienceDB()
            slug = company.lower().replace(" ", "").replace("-", "")
            for suffix in (".com", ".co.uk", ".io", ".jobs"):
                domain = f"{slug}{suffix}"
                result = fe_db.lookup(domain)
                if result:
                    logger.info("platform_bypass: FormExperienceDB hit for %s → %s", company, domain)
                    return f"https://{domain}/careers"
        except Exception as exc:
            logger.debug("FormExperienceDB lookup failed: %s", exc)
        return None

    def _try_ats_patterns(self, company: str) -> str | None:
        """Try known ATS board URL patterns with the company slug.

        Verifies the URL is REAL, not a catch-all placeholder. Many ATS
        boards (notably Ashby + SmartRecruiters) return 200 OK for any slug
        but serve a generic catch-all page, not a company-specific board.

        Verification (in order):
        1. Reject obvious empty placeholders by body size (Ashby: ~6KB).
        2. Reject SmartRecruiters/etc. catch-all pages whose H1/title says
           "SmartRecruiters Jobs", "Job Search", or just "Jobs".
        3. Require the company name (or a meaningful token) to appear in
           the page body — proves the slug actually maps to this company.
        """
        try:
            import httpx
        except ImportError:
            return None

        slug = company.lower().replace(" ", "").replace("'", "").replace("&", "and")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        if not slug:
            return None

        # Tokens to check for: company name parts (whole + first significant word)
        company_lower = company.lower()
        company_tokens = [t for t in company_lower.split() if len(t) >= 4 and t not in ("the", "and", "ltd", "inc", "llc", "limited", "company", "group")]

        for ats_name, pattern in _ATS_BOARD_PATTERNS.items():
            url = f"https://{pattern.format(slug=slug)}"
            try:
                # GET (not HEAD) — we need the body to verify it's real.
                resp = httpx.get(url, timeout=10, follow_redirects=True)
                if resp.status_code >= 400:
                    continue
                body = resp.text or ""
                body_lower = body.lower()
                body_size = len(body)

                # Heuristic 1: body size — real boards are large.
                # Empty Ashby placeholder is ~6KB. Real boards 100KB+.
                if body_size < 15000:
                    logger.debug(
                        "platform_bypass: ATS slug %r at %s returned 200 but body is %dB — "
                        "treating as placeholder, skipping",
                        slug, url, body_size,
                    )
                    continue

                # Heuristic 2: H1 / title must NOT match catch-all markers.
                # SmartRecruiters returns "SmartRecruiters Jobs" / "Job Search"
                # for unknown slugs (a 31KB generic page, passes size threshold).
                import re
                catch_all_markers = (
                    "smartrecruiters jobs", "smartrecruiters job search",
                    "job search", "jobs at smartrecruiters",
                )
                # Extract H1 + title text
                h1_match = re.search(r"<h1[^>]*>([^<]{1,120})</h1>", body, re.I)
                title_match = re.search(r"<title[^>]*>([^<]{1,200})</title>", body, re.I)
                h1_text = (h1_match.group(1).strip().lower() if h1_match else "")
                title_text = (title_match.group(1).strip().lower() if title_match else "")
                page_id_text = f"{h1_text} | {title_text}"
                if any(m in page_id_text for m in catch_all_markers):
                    logger.debug(
                        "platform_bypass: ATS URL %s has catch-all H1/title %r — skipping",
                        url, page_id_text[:80],
                    )
                    continue
                # Title alone of just "Jobs" is also a catch-all (Ashby empty)
                if title_text in ("jobs", "job search", ""):
                    logger.debug(
                        "platform_bypass: ATS URL %s has generic title %r — skipping",
                        url, title_text,
                    )
                    continue

                # Heuristic 3: H1 / title should reference the company.
                # The h1 is the strongest signal — real boards put the company
                # name in the page title (e.g. <h1>HP</h1>, <h1>Air Apps</h1>).
                slug_in_id = slug in page_id_text
                token_in_id = any(t in page_id_text for t in company_tokens)
                if not (slug_in_id or token_in_id):
                    logger.debug(
                        "platform_bypass: ATS URL %s — H1/title %r doesn't reference company — skipping",
                        url, page_id_text[:80],
                    )
                    continue

                logger.info(
                    "platform_bypass: ATS pattern VERIFIED — %s → %s (body=%dKB, h1=%r)",
                    company, url, body_size // 1000, h1_text[:60],
                )
                return url
            except Exception as exc:
                logger.debug("ATS probe failed for %s: %s", url, exc)
                continue
        return None

    async def _web_search_ats(self, company: str, title: str, page: Any) -> str | None:
        """Search Google via the existing Playwright browser for the direct ATS URL."""
        queries = [
            f"{company} {title} careers apply",
            f"{company} jobs apply online",
        ]

        for query in queries:
            try:
                search_url = f"https://www.google.com/search?q={quote_plus(query)}"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                import asyncio
                await asyncio.sleep(2)

                links = await page.evaluate("""() => {
                    const results = [];
                    document.querySelectorAll('a[href]').forEach(a => {
                        const href = a.href;
                        if (!href) return;
                        const dominated = [
                            'greenhouse.io', 'lever.co', 'ashbyhq.com',
                            'myworkdayjobs.com', 'smartrecruiters.com',
                            'icims.com', '/careers', '/jobs', '/apply'
                        ];
                        if (dominated.some(d => href.includes(d))) {
                            results.push(href);
                        }
                    });
                    return results.slice(0, 5);
                }""")

                if links:
                    best = links[0]
                    logger.info("platform_bypass: web search found %d ATS links for '%s', using: %s",
                                len(links), query, best)
                    return best
            except Exception as exc:
                logger.debug("Web search failed for '%s': %s", query, exc)
                continue
        return None

    @staticmethod
    def _detect_ats_from_url(url: str) -> str:
        try:
            from jobpulse.ats_api_scanner import detect_ats_provider
            provider, _ = detect_ats_provider(url)
            return provider or ""
        except Exception:
            return ""

    def _emit_learning_signals(
        self, company: str, blocked_url: str, direct_url: str, strategy: str, title: str,
    ) -> None:
        """Emit signals to ALL learning systems for a successful bypass."""

        # ── NavigationLearner ──
        try:
            from jobpulse.navigation_learner import NavigationLearner
            nav = NavigationLearner()
            domain = urlparse(direct_url).netloc.lower()
            nav.save_sequence(
                domain,
                steps=[{"action": "platform_bypass", "from_url": blocked_url, "to_url": direct_url}],
                success=True,
                platform=strategy,
            )
        except Exception as exc:
            logger.debug("NavigationLearner signal failed: %s", exc)

        # ── GotchasDB ──
        try:
            from jobpulse.form_engine.gotchas import GotchasDB
            gotchas = GotchasDB()
            blocked_domain = urlparse(blocked_url).netloc.lower()
            gotchas.store(
                domain=blocked_domain,
                selector_pattern="security_wall_bypass",
                problem=f"Persistent security wall on {blocked_domain} for {company}",
                solution=f"Bypass via {strategy}: redirect to {direct_url}",
                engine="platform_bypass",
            )
        except Exception as exc:
            logger.debug("GotchasDB signal failed: %s", exc)

        # ── OptimizationEngine ──
        try:
            from shared.optimization import get_optimization_engine
            engine = get_optimization_engine()
            engine.emit(
                "adaptation",
                source_loop="platform_bypass",
                domain=urlparse(blocked_url).netloc.lower(),
                agent_name="platform_bypass",
                payload={
                    "company": company,
                    "title": title,
                    "blocked_url": blocked_url,
                    "direct_url": direct_url,
                    "strategy": strategy,
                },
            )
        except Exception as exc:
            logger.debug("OptimizationEngine signal failed: %s", exc)

        # ── ExperienceMemory ──
        try:
            from shared.experiential_learning import ExperienceMemory, Experience
            mem = ExperienceMemory()
            mem.add(Experience(
                task_description=f"Platform bypass for {company} ({strategy})",
                successful_pattern=f"Blocked on {blocked_url} → resolved to {direct_url} via {strategy}",
                score=8.0,
                domain="platform_bypass",
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                last_accessed=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
        except Exception as exc:
            logger.debug("ExperienceMemory signal failed: %s", exc)

        # ── TrajectoryStore ──
        try:
            from shared.optimization import get_optimization_engine
            engine = get_optimization_engine()
            blocked_domain = urlparse(blocked_url).netloc.lower()
            tid = engine.start_trajectory(
                pipeline="platform_bypass",
                domain=blocked_domain,
                agent_name="platform_bypass",
                session_id=f"bypass_{company}_{int(time.time())}",
            )
            from shared.optimization._trajectory import TrajectoryStep
            engine.log_step(tid, TrajectoryStep(
                step_index=0,
                action="resolve_direct_url",
                target=company,
                input_value=blocked_url,
                output_value=direct_url,
                outcome="success",
                duration_ms=0,
                metadata={"strategy": strategy},
            ))
            engine.complete_trajectory(tid, final_outcome="success", final_score=8.5)
        except Exception as exc:
            logger.debug("TrajectoryStore signal failed: %s", exc)

        logger.info("platform_bypass: emitted learning signals for %s → %s (%s)", company, direct_url, strategy)


_instance: PlatformBypass | None = None


def get_platform_bypass() -> PlatformBypass:
    global _instance
    if _instance is None:
        _instance = PlatformBypass()
    return _instance
