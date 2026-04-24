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
    validated: bool = False
    platform: str = ""
    expected_pages: int = 0
    field_types: list[str] = field(default_factory=list)
    screening_questions: list[str] = field(default_factory=list)
    page_structures: list[dict] = field(default_factory=list)
    nav_steps: list[dict] | None = None
    apply_count: int = 0
    avg_time_seconds: float = 0.0
    has_file_upload: bool = False
    correction_accuracy: float | None = None
    frequently_corrected_fields: list[str] = field(default_factory=list)
    match_ratio: float = 0.0
    diverged_fields: list[str] = field(default_factory=list)

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

    # 4. Correction accuracy for this domain
    try:
        from urllib.parse import urlparse
        from jobpulse.correction_capture import CorrectionCapture

        domain = urlparse(url).netloc.lower().removeprefix("www.") if "://" in url else url
        cc = CorrectionCapture()
        accuracy = cc.get_domain_accuracy(domain)
        if accuracy is not None:
            hints.correction_accuracy = accuracy
            corrected = cc.get_field_corrections_by_domain(domain)
            hints.frequently_corrected_fields = [
                c["field_label"] for c in corrected[:5]
            ]
            if accuracy < 0.8:
                logger.info(
                    "form_prefetch: LOW ACCURACY domain %s (%.0f%%) — %d fields often corrected",
                    domain, accuracy * 100, len(hints.frequently_corrected_fields),
                )
    except Exception as exc:
        logger.debug("form_prefetch: correction accuracy lookup failed: %s", exc)

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


def validate_hints_against_live(
    hints: FormHints,
    live_field_types: list[str],
    live_page_count: int | None = None,
    url: str = "",
    *,
    form_exp_db: str | None = None,
    match_threshold: float = 0.8,
) -> FormHints:
    """Validate stored form hints against the actual DOM scan.

    Call AFTER scanning the live page to verify stored experience still matches.
    If match rate < threshold, demotes hints.known_domain so the caller falls
    back to LLM page detection instead of trusting stale data.

    Returns the updated FormHints (mutated in place).
    """
    if not hints.known_domain or not url:
        return hints

    try:
        from jobpulse.form_experience_db import FormExperienceDB
        exp_db = FormExperienceDB(db_path=form_exp_db)
        result = exp_db.validate_against_live(
            url, live_field_types, live_page_count,
            match_threshold=match_threshold,
        )
        hints.validated = result["trusted"]
        hints.match_ratio = result["match_ratio"]
        hints.diverged_fields = result["diverged_fields"]

        if not result["trusted"]:
            hints.known_domain = False
            _stats["cache_hits"] = max(0, _stats["cache_hits"] - 1)
            _stats["cache_misses"] += 1
            logger.info(
                "form_prefetch: INVALIDATED hints for %s — match %.0f%%, diverged: %s",
                url[:60], result["match_ratio"] * 100,
                result["diverged_fields"][:5],
            )
        else:
            hints.validated = True
            logger.info(
                "form_prefetch: VALIDATED hints for %s — match %.0f%%",
                url[:60], result["match_ratio"] * 100,
            )
    except Exception as exc:
        logger.debug("form_prefetch: validation failed: %s", exc)

    return hints
