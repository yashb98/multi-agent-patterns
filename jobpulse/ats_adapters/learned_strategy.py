"""LearnedStrategy — runtime-synthesized BasePlatformStrategy from FormExperienceDB.

Once a domain has ≥3 successful applications, FormExperienceDB has enough data
(container selectors, timing averages, fill techniques, field mappings) to
construct a strategy without anyone hand-writing a new adapter.

The synthesized strategy reads from FE on demand — it doesn't snapshot the
data, so it stays current as the domain accumulates more applications.
"""
from __future__ import annotations

from urllib.parse import urlparse

from shared.logging_config import get_logger
from jobpulse.ats_adapters.strategy import BasePlatformStrategy

logger = get_logger(__name__)


def _get_fe_db():
    """Lazy accessor — patchable in tests."""
    from jobpulse.form_experience_db import FormExperienceDB
    return FormExperienceDB()


def _normalize_domain(value: str | None) -> str:
    """Match FormExperienceDB.normalize_domain semantics."""
    if not value:
        return ""
    s = value.strip().lower()
    if "://" in s:
        s = urlparse(s).netloc
    else:
        s = s.split("/", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s


class LearnedStrategy(BasePlatformStrategy):
    """Strategy synthesized at runtime from FormExperienceDB data.

    All overrides read from FE on demand. Methods return safe defaults
    (matching BasePlatformStrategy) when no data is available.
    """

    name: str = "learned"
    min_page_time: float = 5.0

    def __init__(self, domain: str, apply_count: int = 0):
        self._domain = _normalize_domain(domain)
        self.apply_count = apply_count
        self.name = f"learned:{self._domain}"

    def detect(self, url: str) -> bool:
        if not url:
            return False
        return _normalize_domain(url) == self._domain

    def form_container_hint(self) -> str | None:
        try:
            return _get_fe_db().get_container(self._domain)
        except Exception as exc:
            logger.warning(
                "LearnedStrategy[%s]: form_container_hint FE lookup failed: %s",
                self._domain, exc,
            )
            return None

    def expected_field_range(self) -> tuple[int, int]:
        try:
            mappings = _get_fe_db().get_field_mappings(self._domain)
        except Exception as exc:
            logger.warning(
                "LearnedStrategy[%s]: expected_field_range FE lookup failed: %s",
                self._domain, exc,
            )
            return (1, 30)
        n = len(mappings) if mappings else 0
        if n > 0:
            return (max(1, n - 2), n + 5)
        return (1, 30)

    def extra_label_mappings(self) -> dict[str, str]:
        try:
            return _get_fe_db().get_field_mappings(self._domain) or {}
        except Exception as exc:
            logger.warning(
                "LearnedStrategy[%s]: extra_label_mappings FE lookup failed: %s",
                self._domain, exc,
            )
            return {}
