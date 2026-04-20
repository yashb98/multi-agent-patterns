"""Perplexity Sonar API client for company research and salary intelligence."""

from __future__ import annotations

import random
import re
import sqlite3
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import httpx
from pydantic import BaseModel
from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)


class CompanyResearch(BaseModel):
    """Structured company research result."""

    company: str
    description: str = ""
    industry: str = ""
    size: str = ""
    employee_count: int | None = None
    tech_stack: list[str] = []
    recent_news: list[str] = []
    red_flags: list[str] = []
    culture: str = ""
    glassdoor_rating: float | None = None
    researched_at: str = ""


class SalaryResearch(BaseModel):
    """Structured salary research result."""

    role: str
    company: str
    location: str
    min_gbp: int = 0
    median_gbp: int = 0
    max_gbp: int = 0
    source: str = ""
    researched_at: str = ""


class PerplexityClient:
    """Perplexity API client with SQLite cache."""

    BASE_URL = "https://api.perplexity.ai/chat/completions"
    MODEL_FAST = "sonar"
    MODEL_DEEP = "sonar-pro"

    def __init__(self, api_key: str | None = None, cache_path: Path | None = None):
        from jobpulse.config import PERPLEXITY_API_KEY
        self.api_key = api_key or PERPLEXITY_API_KEY
        self._cache_path = cache_path or DATA_DIR / "perplexity_cache.db"
        if self._cache_path:
            self._init_cache()

    def _init_cache(self) -> None:
        if self._cache_path is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._cache_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(key TEXT PRIMARY KEY, type TEXT, data TEXT, expires_at REAL)"
            )

    def _get_cache(self, key: str, cache_type: str) -> str | None:
        if self._cache_path is None:
            return None
        try:
            with sqlite3.connect(str(self._cache_path)) as conn:
                row = conn.execute(
                    "SELECT data FROM cache WHERE key = ? AND type = ? AND expires_at > ?",
                    (key, cache_type, time.time()),
                ).fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def _store_cache(self, key: str, cache_type: str, data: str, ttl_days: int = 7) -> None:
        if self._cache_path is None:
            return
        try:
            expires = time.time() + ttl_days * 86400
            with sqlite3.connect(str(self._cache_path)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO cache (key, type, data, expires_at) VALUES (?, ?, ?, ?)",
                    (key, cache_type, data, expires),
                )
        except Exception as exc:
            logger.debug("Cache store failed: %s", exc)

    def _query(self, prompt: str, model: str | None = None, max_retries: int = 2) -> str:
        """Make Perplexity Sonar API call with exponential backoff retry."""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                resp = httpx.post(
                    self.BASE_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model or self.MODEL_FAST,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()
                retryable = any(k in err_str for k in (
                    "timeout", "connection", "429", "too many requests",
                    "rate limit", "service unavailable", "bad gateway",
                ))
                if attempt >= max_retries or not retryable:
                    raise
                delay = min(2 ** attempt, 30) * (0.5 + random.random())
                logger.warning(
                    "Perplexity call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt + 1, max_retries + 1, str(exc)[:100], delay,
                )
                time.sleep(delay)
        raise last_error

    def research_company(self, company: str, deep: bool = False) -> CompanyResearch:
        """Research a company. Cached for 7 days."""
        cached = self._get_cache(company, "company")
        if cached:
            try:
                return CompanyResearch.model_validate_json(cached)
            except (ValueError, KeyError):
                pass  # Stale cache format — re-fetch

        model = self.MODEL_DEEP if deep else self.MODEL_FAST

        try:
            raw = self._query(
                f"Company research for job application: {company}. "
                f"Return: 1) What the company does (1 sentence), "
                f"2) Industry and size (startup/SME/enterprise, employee count), "
                f"3) Tech stack (languages, frameworks, cloud), "
                f"4) Recent news (funding, layoffs, product launches), "
                f"5) Red flags (lawsuits, mass layoffs, glassdoor rating < 3.0), "
                f"6) Engineering culture (remote/hybrid, blog posts, open source).",
                model=model,
            )
            result = self._parse_company(company, raw)
            self._store_cache(company, "company", result.model_dump_json(), ttl_days=7)
            return result
        except Exception as exc:
            logger.warning("Perplexity company research failed for %s: %s", company, exc)
            return CompanyResearch(company=company)

    def research_salary(self, role: str, company: str, location: str) -> SalaryResearch:
        """Research salary range. Cached for 30 days."""
        cache_key = f"{role}@{company}@{location}"
        cached = self._get_cache(cache_key, "salary")
        if cached:
            try:
                return SalaryResearch.model_validate_json(cached)
            except (ValueError, KeyError):
                pass  # Stale cache format — re-fetch

        try:
            raw = self._query(
                f"What is the salary range for {role} at {company} in {location} in 2026? "
                f"Check Glassdoor, Levels.fyi, LinkedIn Salary Insights. "
                f"Return: min, median, max in GBP. If company-specific data unavailable, "
                f"use industry average for {location}."
            )
            result = self._parse_salary(role, company, location, raw)
            self._store_cache(cache_key, "salary", result.model_dump_json(), ttl_days=30)
            return result
        except Exception as exc:
            logger.warning("Perplexity salary research failed: %s", exc)
            return SalaryResearch(role=role, company=company, location=location)

    def _parse_company(self, company: str, raw: str) -> CompanyResearch:
        """Parse free-text company research into structured model."""
        now = datetime.now(UTC).isoformat()
        tech_pattern = re.findall(
            r"\b(Python|Java|JavaScript|TypeScript|Go|Rust|C\+\+|Ruby|"
            r"React|Next\.js|FastAPI|Django|Flask|Node\.js|"
            r"AWS|GCP|Azure|Docker|Kubernetes|PostgreSQL|MongoDB|Redis)\b",
            raw,
            re.IGNORECASE,
        )
        tech_stack = list(dict.fromkeys(t.strip() for t in tech_pattern))

        return CompanyResearch(
            company=company,
            description=raw[:300].strip(),
            tech_stack=tech_stack,
            researched_at=now,
        )

    def _parse_salary(self, role: str, company: str, location: str, raw: str) -> SalaryResearch:
        """Parse salary text into structured model."""
        now = datetime.now(UTC).isoformat()
        amounts = re.findall(r"£([\d,]+)", raw)
        nums = sorted(int(a.replace(",", "")) for a in amounts)

        min_gbp = nums[0] if len(nums) >= 1 else 0
        max_gbp = nums[-1] if len(nums) >= 2 else min_gbp
        median_gbp = nums[len(nums) // 2] if nums else 0

        return SalaryResearch(
            role=role,
            company=company,
            location=location,
            min_gbp=min_gbp,
            median_gbp=median_gbp,
            max_gbp=max_gbp,
            source="perplexity",
            researched_at=now,
        )
