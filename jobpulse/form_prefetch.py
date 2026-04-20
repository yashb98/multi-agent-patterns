"""Pre-apply form knowledge aggregation.

Queries form_experience_db, form_interaction_log, and navigation_learner
to build a FormHints object for a URL. Injected into merged_answers so
adapters can skip LLM page detection and pre-load expected fields.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FormHints:
    known_domain: bool = False
    platform: str = ""
    expected_pages: int = 0
    field_types: list[str] = field(default_factory=list)
    screening_questions: list[str] = field(default_factory=list)
    page_structures: list[dict] = field(default_factory=list)
    nav_steps: list[dict] | None = None
    apply_count: int = 0
    avg_time_seconds: float = 0.0
    has_file_upload: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


_stats = {"total_lookups": 0, "cache_hits": 0, "cache_misses": 0}


def get_prefetch_stats() -> dict:
    return dict(_stats)


def prefetch_form_hints(
    url: str,
    form_exp_db: str | None = None,
    interaction_db: str | None = None,
    nav_db: str | None = None,
) -> FormHints:
    """Aggregate all known form intelligence for a URL before applying.

    Queries three DBs:
    1. form_experience — domain-level summary (pages, field types, timing)
    2. form_interaction_log — per-page field structure
    3. navigation_learner — replay steps to reach the form

    Returns FormHints with whatever data is available. Never raises.
    """
    hints = FormHints()

    # 1. Form experience (domain-level)
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        exp_db = FormExperienceDB(db_path=form_exp_db)
        exp = exp_db.lookup(url)
        if exp and exp.get("success"):
            hints.known_domain = True
            hints.platform = exp.get("platform", "")
            hints.expected_pages = exp.get("pages_filled", 0)
            hints.field_types = json.loads(exp["field_types"]) if isinstance(exp["field_types"], str) else exp["field_types"]
            hints.screening_questions = json.loads(exp["screening_questions"]) if isinstance(exp["screening_questions"], str) else exp["screening_questions"]
            hints.apply_count = exp.get("apply_count", 0)
            hints.avg_time_seconds = exp.get("time_seconds", 0.0)
    except Exception as exc:
        logger.debug("form_prefetch: experience lookup failed: %s", exc)

    # 2. Page structures (per-page detail)
    try:
        from jobpulse.form_interaction_log import FormInteractionLog
        int_log = FormInteractionLog(db_path=interaction_db)
        pages = int_log.get_page_structure(url)
        if pages:
            hints.page_structures = pages
            hints.has_file_upload = any(p.get("has_file_upload") for p in pages)
            if not hints.known_domain and pages:
                hints.known_domain = True
                hints.expected_pages = len(pages)
    except Exception as exc:
        logger.debug("form_prefetch: interaction log lookup failed: %s", exc)

    # 3. Navigation sequence
    try:
        from jobpulse.navigation_learner import NavigationLearner
        nav = NavigationLearner(db_path=nav_db)
        steps = nav.get_sequence(url)
        if steps:
            hints.nav_steps = steps
    except Exception as exc:
        logger.debug("form_prefetch: navigation lookup failed: %s", exc)

    _stats["total_lookups"] += 1
    if hints.known_domain:
        _stats["cache_hits"] += 1
    else:
        _stats["cache_misses"] += 1

    if hints.known_domain:
        logger.info(
            "form_prefetch: %s — %d pages, %d field types, %d screening Qs, nav=%s, applied %dx",
            url[:60], hints.expected_pages, len(hints.field_types),
            len(hints.screening_questions), "yes" if hints.nav_steps else "no",
            hints.apply_count,
        )

    return hints
