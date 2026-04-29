"""Weekly optimization job — autonomously reads all learning tables and produces
actionable recommendations.

Produces a structured report that the user can approve/reject via Telegram:

    python -m jobpulse.runner weekly-optimize

Usage:
    from jobpulse.weekly_optimize import WeeklyOptimizer
    report = WeeklyOptimizer().generate_report()
    print(report.to_markdown())
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)


@dataclass
class Recommendation:
    """A single actionable recommendation."""

    category: str  # gate, cv, project, profile, company, screening
    action: str  # e.g., "lower_threshold", "update_value", "block"
    target: str  # e.g., "gate_3_ml_ai", "salary_expectation", "Acme Inc"
    current_value: str | None = None
    suggested_value: str | None = None
    confidence: float = 0.0  # 0.0–1.0
    evidence: str = ""
    impact_estimate: str = ""  # e.g., "+5% interview rate"


@dataclass
class WeeklyReport:
    """Complete weekly optimization report."""

    generated_at: str
    period_start: str
    period_end: str
    recommendations: list[Recommendation] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        lines = [
            "📊 *Weekly Optimization Report*",
            f"Period: {self.period_start[:10]} → {self.period_end[:10]}",
            f"Generated: {self.generated_at[:19]}",
            "",
        ]

        by_category: dict[str, list[Recommendation]] = {}
        for r in self.recommendations:
            by_category.setdefault(r.category, []).append(r)

        for cat, recs in sorted(by_category.items()):
            lines.append(f"*{cat.upper().replace('_', ' ')}* ({len(recs)})")
            for r in sorted(recs, key=lambda x: x.confidence, reverse=True):
                lines.append(
                    f"  • {r.action} `{r.target}`"
                    f"{' (conf: ' + str(int(r.confidence * 100)) + '%)' if r.confidence else ''}"
                )
                if r.current_value and r.suggested_value:
                    lines.append(f"    {r.current_value} → {r.suggested_value}")
                if r.evidence:
                    lines.append(f"    _{r.evidence[:120]}_")
            lines.append("")

        lines.append(f"Total recommendations: {len(self.recommendations)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "recommendations": [
                {
                    "category": r.category,
                    "action": r.action,
                    "target": r.target,
                    "current_value": r.current_value,
                    "suggested_value": r.suggested_value,
                    "confidence": r.confidence,
                    "evidence": r.evidence,
                    "impact_estimate": r.impact_estimate,
                }
                for r in self.recommendations
            ],
            "stats": self.stats,
        }


class WeeklyOptimizer:
    """Reads all learning tables and produces optimization recommendations."""

    def __init__(self) -> None:
        self._recommendations: list[Recommendation] = []
        self._stats: dict[str, Any] = {}

    def generate_report(self, days: int = 7) -> WeeklyReport:
        """Generate a weekly optimization report.

        Args:
            days: Lookback period in days (default 7).
        """
        now = datetime.now(UTC)
        period_start = (now - timedelta(days=days)).isoformat()
        period_end = now.isoformat()

        self._recommendations = []
        self._stats = {}

        self._analyze_gate_thresholds()
        self._analyze_cv_scrutiny()
        self._analyze_project_selection()
        self._analyze_corrections()
        self._analyze_company_reliability()
        self._analyze_screening_cache()

        return WeeklyReport(
            generated_at=now.isoformat(),
            period_start=period_start,
            period_end=period_end,
            recommendations=self._recommendations,
            stats=self._stats,
        )

    # ------------------------------------------------------------------
    # 1. Gate Thresholds
    # ------------------------------------------------------------------

    def _analyze_gate_thresholds(self) -> None:
        """Suggest threshold adjustments per domain family."""
        db_path = str(DATA_DIR / "gate_thresholds.db")
        if not Path(db_path).exists():
            return

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT family, COUNT(*) as samples, SUM(got_interview) as interviews
                   FROM gate_threshold_outcomes
                   GROUP BY family""",
            ).fetchall()

        for row in rows:
            family = row["family"]
            samples = row["samples"]
            interviews = row["interviews"] or 0
            if samples < 5:
                continue

            rate = interviews / samples
            # If interview rate is low, suggest raising threshold (be more selective)
            # If interview rate is high, suggest lowering threshold (cast wider net)
            if rate < 0.1 and samples >= 10:
                self._recommendations.append(Recommendation(
                    category="gate",
                    action="raise_threshold",
                    target=f"gate_3_{family}",
                    current_value="adaptive",
                    suggested_value="+0.05",
                    confidence=min(samples / 20.0, 0.9),
                    evidence=f"{family}: only {rate:.0%} interview rate ({interviews}/{samples})",
                    impact_estimate="Fewer low-quality applications",
                ))
            elif rate > 0.3 and samples >= 10:
                self._recommendations.append(Recommendation(
                    category="gate",
                    action="lower_threshold",
                    target=f"gate_3_{family}",
                    current_value="adaptive",
                    suggested_value="-0.05",
                    confidence=min(samples / 20.0, 0.9),
                    evidence=f"{family}: strong {rate:.0%} interview rate ({interviews}/{samples})",
                    impact_estimate="More high-quality applications",
                ))

        self._stats["gate_threshold_samples"] = sum(r["samples"] for r in rows) if rows else 0

    # ------------------------------------------------------------------
    # 2. CV Scrutiny Calibration
    # ------------------------------------------------------------------

    def _analyze_cv_scrutiny(self) -> None:
        """Suggest CV review threshold adjustments from actual outcomes."""
        db_path = str(DATA_DIR / "cv_scrutiny_calibration.db")
        if not Path(db_path).exists():
            return

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT llm_score, got_interview FROM cv_scrutiny_calibration
                   WHERE got_interview IS NOT NULL""",
            ).fetchall()

        if len(rows) < 5:
            return

        # Grid search optimal threshold
        best_threshold = 7.0
        best_rate = 0.0
        for threshold in [x / 10 for x in range(40, 91)]:
            passed = [r for r in rows if r["llm_score"] >= threshold]
            if not passed:
                continue
            rate = sum(1 for p in passed if p["got_interview"]) / len(passed)
            if rate > best_rate:
                best_rate = rate
                best_threshold = threshold

        current_default = 7.0
        if abs(best_threshold - current_default) >= 0.5:
            direction = "lower" if best_threshold < current_default else "raise"
            self._recommendations.append(Recommendation(
                category="cv",
                action=f"{direction}_threshold",
                target="cv_scrutiny",
                current_value=str(current_default),
                suggested_value=str(round(best_threshold, 1)),
                confidence=min(len(rows) / 20.0, 0.9),
                evidence=f"Grid search found optimal at {best_threshold:.1f} ({best_rate:.0%} interview rate)",
                impact_estimate=f"{'More' if direction == 'lower' else 'Fewer'} CVs flagged for review",
            ))

        self._stats["cv_scrutiny_samples"] = len(rows)

    # ------------------------------------------------------------------
    # 3. Project Selection
    # ------------------------------------------------------------------

    def _analyze_project_selection(self) -> None:
        """Suggest project ranking adjustments per archetype."""
        db_path = str(DATA_DIR / "project_selection_outcomes.db")
        if not Path(db_path).exists():
            return

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT archetype,
                        SUM(times_selected) as total,
                        SUM(interviews) as interviews, SUM(offers) as offers
                   FROM project_selection_outcomes
                   GROUP BY archetype""",
            ).fetchall()

        for row in rows:
            archetype = row["archetype"]
            total = row["total"]
            interviews = row["interviews"] or 0
            offers = row["offers"] or 0
            if total < 3:
                continue

            rate = interviews / total
            if rate > 0.5:
                self._recommendations.append(Recommendation(
                    category="project",
                    action="boost_archetype",
                    target=archetype,
                    current_value=f"{rate:.0%} interview rate",
                    suggested_value="Rank higher in CV selection",
                    confidence=min(total / 10.0, 0.9),
                    evidence=f"{archetype}: {interviews}/{total} interviews, {offers} offers",
                    impact_estimate="Higher ATS scores for this archetype",
                ))
            elif rate < 0.1 and total >= 5:
                self._recommendations.append(Recommendation(
                    category="project",
                    action="review_archetype",
                    target=archetype,
                    current_value=f"{rate:.0%} interview rate",
                    suggested_value="Consider different projects",
                    confidence=min(total / 10.0, 0.9),
                    evidence=f"{archetype}: only {interviews}/{total} interviews",
                    impact_estimate="Better project-role alignment",
                ))

        self._stats["project_selection_archetypes"] = len(rows)

    # ------------------------------------------------------------------
    # 4. Corrections → Profile Updates
    # ------------------------------------------------------------------

    def _analyze_corrections(self) -> None:
        """Suggest profile updates from repeated corrections."""
        db_path = str(DATA_DIR / "field_corrections.db")
        if not Path(db_path).exists():
            return

        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT field_label, agent_value, user_value, COUNT(*) as cnt
                   FROM field_corrections
                   WHERE created_at > ?
                   GROUP BY field_label, agent_value, user_value
                   HAVING cnt >= 3
                   ORDER BY cnt DESC LIMIT 10""",
                (cutoff,),
            ).fetchall()

        for row in rows:
            field = row["field_label"]
            agent_val = row["agent_value"]
            user_val = row["user_value"]
            count = row["cnt"]

            self._recommendations.append(Recommendation(
                category="profile",
                action="update_profile",
                target=field,
                current_value=agent_val[:60],
                suggested_value=user_val[:60],
                confidence=min(count / 10.0, 0.9),
                evidence=f"Corrected {count} times this week",
                impact_estimate="Fewer future corrections",
            ))

        self._stats["weekly_corrections"] = sum(r["cnt"] for r in rows) if rows else 0

    # ------------------------------------------------------------------
    # 5. Company Reliability → Blocklist
    # ------------------------------------------------------------------

    def _analyze_company_reliability(self) -> None:
        """Suggest companies to block or approve."""
        db_path = str(DATA_DIR / "applications.db")
        if not Path(db_path).exists():
            return

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT company,
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'Interview' THEN 1 ELSE 0 END) as interviews
                   FROM applications
                   WHERE company IS NOT NULL AND company != ''
                   GROUP BY company
                   HAVING total >= 5""",
            ).fetchall()

        for row in rows:
            company = row["company"]
            total = row["total"]
            interviews = row["interviews"] or 0
            rate = interviews / total

            if total >= 10 and rate == 0.0:
                self._recommendations.append(Recommendation(
                    category="company",
                    action="block",
                    target=company,
                    current_value=f"{total} apps, 0 interviews",
                    suggested_value="Add to blocklist",
                    confidence=0.95,
                    evidence=f"Zero interview rate after {total} applications",
                    impact_estimate="Save time on low-yield companies",
                ))
            elif total >= 5 and rate >= 0.3:
                self._recommendations.append(Recommendation(
                    category="company",
                    action="boost",
                    target=company,
                    current_value=f"{total} apps, {rate:.0%} interview rate",
                    suggested_value="Prioritize new openings",
                    confidence=min(rate, 0.9),
                    evidence=f"Strong interview rate: {interviews}/{total}",
                    impact_estimate="Focus on high-yield companies",
                ))

        self._stats["companies_analyzed"] = len(rows)

    # ------------------------------------------------------------------
    # 6. Screening Cache Performance
    # ------------------------------------------------------------------

    def _analyze_screening_cache(self) -> None:
        """Report screening cache hit rates and correction rates."""
        db_path = str(DATA_DIR / "screening_semantic_cache.db")
        if not Path(db_path).exists():
            return

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(
                "SELECT COUNT(*) FROM screening_semantic_cache"
            ).fetchone()[0]
            usage = conn.execute(
                "SELECT SUM(times_used) as uses, SUM(success_count) as successes, SUM(correction_count) as corrections FROM screening_semantic_cache"
            ).fetchone()

        if total == 0:
            return

        uses = usage["uses"] or 0
        successes = usage["successes"] or 0
        corrections = usage["corrections"] or 0

        total_uses = uses + successes + corrections
        if total_uses == 0:
            return

        correction_rate = corrections / total_uses if total_uses > 0 else 0
        if correction_rate > 0.2:
            self._recommendations.append(Recommendation(
                category="screening",
                action="review_cache",
                target="semantic_cache",
                current_value=f"{correction_rate:.0%} correction rate",
                suggested_value="Prune low-success entries",
                confidence=min(correction_rate, 0.9),
                evidence=f"{corrections} corrections out of {total_uses} cache uses",
                impact_estimate="Better screening answer quality",
            ))

        self._stats["screening_cache_entries"] = total
        self._stats["screening_cache_correction_rate"] = round(correction_rate, 3)

    # ------------------------------------------------------------------
    # Application / Approval
    # ------------------------------------------------------------------

    def apply_recommendation(self, rec: Recommendation) -> dict:
        """Apply a single recommendation. Returns result status."""
        try:
            if rec.category == "gate" and rec.action in ("raise_threshold", "lower_threshold"):
                return self._apply_gate_threshold(rec)
            if rec.category == "cv" and "threshold" in rec.action:
                return self._apply_cv_threshold(rec)
            if rec.category == "profile" and rec.action == "update_profile":
                return self._apply_profile_update(rec)
            if rec.category == "company" and rec.action == "block":
                return self._apply_company_block(rec)
            return {"applied": False, "reason": "not_implemented"}
        except Exception as exc:
            logger.warning("Failed to apply recommendation: %s", exc)
            return {"applied": False, "reason": str(exc)}

    def _apply_gate_threshold(self, rec: Recommendation) -> dict:
        """Apply a gate threshold change."""
        # Store in a config override table or emit signal
        from shared.optimization import get_optimization_engine
        engine = get_optimization_engine()
        engine.emit(
            signal_type="adaptation",
            source_loop="weekly_optimizer",
            domain=rec.target,
            payload={
                "param": "gate_threshold",
                "old_value": rec.current_value,
                "new_value": rec.suggested_value,
                "reason": rec.evidence,
            },
        )
        return {"applied": True, "type": "gate_threshold_override"}

    def _apply_cv_threshold(self, rec: Recommendation) -> dict:
        from jobpulse.cv_templates.scrutiny_calibrator import ScrutinyCalibrator
        # The calibrator auto-learns; we just note the recommendation
        return {"applied": True, "type": "cv_threshold_noted"}

    def _apply_profile_update(self, rec: Recommendation) -> dict:
        return {"applied": True, "type": "profile_update_suggested", "manual": True}

    def _apply_company_block(self, rec: Recommendation) -> dict:
        try:
            from jobpulse.company_blocklist import BlocklistCache
            bl = BlocklistCache()
            bl.block(rec.target, reason=rec.evidence)
            return {"applied": True, "type": "company_blocked"}
        except Exception as exc:
            return {"applied": False, "reason": str(exc)}
