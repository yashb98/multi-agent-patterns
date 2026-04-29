"""Calibration API — CV scrutiny, gate effectiveness, project selection, company reliability.

Endpoints feed the calibration dashboard at /calibration.html.
"""

import sqlite3
from pathlib import Path
from fastapi import APIRouter
from shared.logging_config import get_logger

logger = get_logger(__name__)
calibration_router = APIRouter(prefix="/api/calibration")

DATA_DIR = Path(__file__).parent.parent / "data"


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════════════════════════
# CV Scrutiny
# ═══════════════════════════════════════════════════════════════════════


@calibration_router.get("/cv-scrutiny")
def get_cv_scrutiny_calibration():
    """CV scrutiny LLM score vs actual interview rate (scatter data)."""
    db_path = DATA_DIR / "cv_scrutiny_calibration.db"
    if not db_path.exists():
        return {"scatter": [], "histogram": [], "threshold_suggestion": None}

    try:
        conn = _db(db_path)
        rows = conn.execute(
            """SELECT llm_score, got_interview, got_offer, user_overrode,
                      b1_warning_count, created_at
               FROM cv_scrutiny_calibration
               ORDER BY created_at DESC
               LIMIT 500"""
        ).fetchall()
        conn.close()

        scatter = [
            {
                "score": r["llm_score"],
                "interview": bool(r["got_interview"]),
                "offer": bool(r["got_offer"]),
                "overridden": bool(r["user_overrode"]),
                "warnings": r["b1_warning_count"],
            }
            for r in rows
        ]

        # Histogram: count per score bucket
        buckets: dict[int, dict] = {}
        for r in rows:
            s = r["llm_score"]
            if s not in buckets:
                buckets[s] = {"score": s, "total": 0, "interviews": 0, "offers": 0}
            buckets[s]["total"] += 1
            if r["got_interview"]:
                buckets[s]["interviews"] += 1
            if r["got_offer"]:
                buckets[s]["offers"] += 1

        histogram = sorted(buckets.values(), key=lambda x: x["score"])

        # Suggest optimal threshold: maximize interview rate above threshold
        threshold_suggestion = None
        if len(rows) >= 10:
            best_threshold = 7.0
            best_rate = 0.0
            for t in range(4, 10):
                above = [r for r in rows if r["llm_score"] >= t]
                if len(above) >= 5:
                    rate = sum(r["got_interview"] for r in above) / len(above)
                    if rate > best_rate:
                        best_rate = rate
                        best_threshold = t
            threshold_suggestion = {
                "threshold": best_threshold,
                "expected_interview_rate": round(best_rate, 2),
                "sample_size": len(rows),
            }

        return {
            "scatter": scatter,
            "histogram": histogram,
            "threshold_suggestion": threshold_suggestion,
        }
    except Exception as e:
        logger.error("CV scrutiny calibration failed: %s", e)
        return {"scatter": [], "histogram": [], "threshold_suggestion": None, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# Gate Effectiveness
# ═══════════════════════════════════════════════════════════════════════


@calibration_router.get("/gates")
def get_gate_effectiveness():
    """Gate decisions and conversion rates by domain/archetype."""
    db_path = DATA_DIR / "applications.db"
    if not db_path.exists():
        return {"domains": [], "overall": {}}

    try:
        conn = _db(db_path)
        # Compute conversion from applications db status field
        rows = conn.execute(
            """SELECT match_tier, status, COUNT(*) as count
               FROM applications
               WHERE match_tier IS NOT NULL
               GROUP BY match_tier, status"""
        ).fetchall()
        conn.close()

        # Build per-tier stats
        tiers: dict[str, dict] = {}
        for r in rows:
            tier = r["match_tier"] or "unknown"
            if tier not in tiers:
                tiers[tier] = {"tier": tier, "total": 0, "applied": 0, "interview": 0, "offer": 0, "rejected": 0}
            tiers[tier]["total"] += r["count"]
            status = (r["status"] or "").lower()
            if status in ("applied", "submitted"):
                tiers[tier]["applied"] += r["count"]
            elif status in ("interview", "phone screen", "onsite"):
                tiers[tier]["interview"] += r["count"]
            elif status in ("offer", "accepted"):
                tiers[tier]["offer"] += r["count"]
            elif status in ("rejected", "declined"):
                tiers[tier]["rejected"] += r["count"]

        domains = list(tiers.values())
        total_apps = sum(d["total"] for d in domains)
        total_interviews = sum(d["interview"] for d in domains)

        return {
            "domains": domains,
            "overall": {
                "total_applications": total_apps,
                "total_interviews": total_interviews,
                "overall_interview_rate": round(total_interviews / total_apps, 2) if total_apps else 0,
            },
        }
    except Exception as e:
        logger.error("Gate effectiveness failed: %s", e)
        return {"domains": [], "overall": {}, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# Project Selection
# ═══════════════════════════════════════════════════════════════════════


@calibration_router.get("/projects")
def get_project_selection_outcomes():
    """Project selection outcomes per archetype."""
    db_path = DATA_DIR / "project_selection_outcomes.db"
    if not db_path.exists():
        return {"archetypes": []}

    try:
        conn = _db(db_path)
        # If table exists, read it; otherwise return empty
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        if not any(t["name"] == "project_outcomes" for t in tables):
            conn.close()
            return {"archetypes": []}

        rows = conn.execute(
            """SELECT archetype, project_id, interview_count, offer_count, total_applied,
                      avg_ats_score_delta
               FROM project_outcomes
               ORDER BY archetype, interview_count DESC"""
        ).fetchall()
        conn.close()

        archetypes: dict[str, list] = {}
        for r in rows:
            arch = r["archetype"] or "unknown"
            if arch not in archetypes:
                archetypes[arch] = []
            archetypes[arch].append({
                "project": r["project_id"],
                "interviews": r["interview_count"],
                "offers": r["offer_count"],
                "total": r["total_applied"],
                "ats_delta": r["avg_ats_score_delta"],
                "interview_rate": round(r["interview_count"] / r["total_applied"], 2)
                if r["total_applied"] else 0,
            })

        return {"archetypes": [{"name": k, "projects": v} for k, v in archetypes.items()]}
    except Exception as e:
        logger.error("Project selection outcomes failed: %s", e)
        return {"archetypes": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# Company Reliability
# ═══════════════════════════════════════════════════════════════════════


@calibration_router.get("/companies")
def get_company_reliability():
    """Company interview rates."""
    db_path = DATA_DIR / "company_reliability.db"
    if not db_path.exists():
        return {"companies": []}

    try:
        conn = _db(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        if not any(t["name"] == "company_stats" for t in tables):
            conn.close()
            return {"companies": []}

        rows = conn.execute(
            """SELECT company, total_applied, interview_count, offer_count,
                      ghost_count, last_applied
               FROM company_stats
               WHERE total_applied >= 3
               ORDER BY interview_count DESC"""
        ).fetchall()
        conn.close()

        companies = []
        for r in rows:
            total = r["total_applied"]
            interviews = r["interview_count"]
            companies.append({
                "company": r["company"],
                "total": total,
                "interviews": interviews,
                "offers": r["offer_count"],
                "ghosts": r["ghost_count"],
                "interview_rate": round(interviews / total, 2) if total else 0,
                "last_applied": r["last_applied"],
            })

        return {"companies": companies}
    except Exception as e:
        logger.error("Company reliability failed: %s", e)
        return {"companies": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# Screening Answers
# ═══════════════════════════════════════════════════════════════════════


@calibration_router.get("/screening")
def get_screening_stats():
    """Screening cache hit rate and correction stats."""
    corrections_db = DATA_DIR / "field_corrections.db"
    cache_db = DATA_DIR / "screening_semantic_cache.db"

    result: dict[str, Any] = {"corrections_by_field": [], "cache_stats": {}}

    # Corrections
    if corrections_db.exists():
        try:
            conn = _db(corrections_db)
            rows = conn.execute(
                """SELECT field_label, COUNT(*) as corrections
                   FROM field_corrections
                   GROUP BY field_label
                   ORDER BY corrections DESC
                   LIMIT 20"""
            ).fetchall()
            conn.close()
            result["corrections_by_field"] = [
                {"field": r["field_label"], "corrections": r["corrections"]} for r in rows
            ]
        except Exception as e:
            logger.warning("Screening corrections query failed: %s", e)

    # Cache
    if cache_db.exists():
        try:
            conn = _db(cache_db)
            total = conn.execute(
                "SELECT COUNT(*) FROM screening_semantic_cache"
            ).fetchone()[0]
            hits = conn.execute(
                "SELECT COUNT(*) FROM screening_semantic_cache WHERE outcome = 1"
            ).fetchone()[0]
            misses = conn.execute(
                "SELECT COUNT(*) FROM screening_semantic_cache WHERE outcome = 0"
            ).fetchone()[0]
            conn.close()
            result["cache_stats"] = {
                "total_cached": total,
                "successful": hits,
                "failed": misses,
                "success_rate": round(hits / total, 2) if total else 0,
            }
        except Exception as e:
            logger.warning("Screening cache query failed: %s", e)

    return result
