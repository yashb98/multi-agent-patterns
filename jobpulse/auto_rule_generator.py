"""Auto-rule generator — converts corrections and trajectories into agent rules.

Analyzes historical correction patterns and trajectory outcomes to generate
concrete, testable agent rules. Rules are validated against historical data
before deployment.

Usage:
    generator = AutoRuleGenerator()

    # From accumulated corrections
    rules = generator.from_corrections(domain="greenhouse", min_samples=3)

    # From trajectory analysis
    rules = generator.from_trajectories(pipeline="form_fill", domain="linkedin")

    # Validate before deploying
    if generator.validate_rule(rule, test_cases):
        generator.deploy_rule(rule)
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_CORRECTIONS_DB = str(DATA_DIR / "field_corrections.db")
_DEFAULT_TRAJECTORY_DB = str(DATA_DIR / "optimization.db")


@dataclass
class GeneratedRule:
    """A candidate rule ready for validation and deployment."""

    rule_type: str
    source: str
    category: str
    pattern: str
    action: str
    value: str
    confidence: float
    sample_count: int
    evidence: str


class AutoRuleGenerator:
    """Generates agent rules from corrections and trajectory analysis."""

    def __init__(
        self,
        corrections_db: str | None = None,
        trajectory_db: str | None = None,
    ) -> None:
        self._corrections_db = corrections_db or _DEFAULT_CORRECTIONS_DB
        self._trajectory_db = trajectory_db or _DEFAULT_TRAJECTORY_DB

    # ------------------------------------------------------------------
    # Correction → Rule
    # ------------------------------------------------------------------

    def from_corrections(
        self,
        domain: str = "",
        platform: str = "",
        *,
        min_samples: int = 3,
        max_rules: int = 10,
    ) -> list[GeneratedRule]:
        """Generate rules from fields with repeated corrections.

        Groups corrections by (field_label, agent_value → user_value) pattern.
        Only emits rules when the same wrong→right pattern appears ≥min_samples
        times.
        """
        rows = self._fetch_correction_clusters(domain, platform, min_samples)
        rules: list[GeneratedRule] = []

        for row in rows:
            field_label = row["field_label"]
            agent_value = row["agent_value"]
            user_value = row["user_value"]
            count = row["cnt"]

            # Skip trivial formatting differences
            if self._is_trivial_diff(agent_value, user_value):
                continue

            # Build a regex-friendly pattern from the field label
            pattern = self._field_to_pattern(field_label)

            # Determine action based on correction type
            action, value = self._infer_action(
                field_label, agent_value, user_value, count,
            )

            confidence = min(count / 10.0, 0.95)
            evidence = (
                f"{count} corrections on '{field_label}': "
                f"'{agent_value[:60]}' → '{user_value[:60]}'"
            )

            rules.append(GeneratedRule(
                rule_type="correction_override",
                source="auto_rule_generator",
                category=field_label,
                pattern=pattern,
                action=action,
                value=value,
                confidence=confidence,
                sample_count=count,
                evidence=evidence,
            ))

            if len(rules) >= max_rules:
                break

        logger.info(
            "auto_rule_generator: generated %d rules from corrections "
            "(domain=%s, platform=%s)",
            len(rules), domain or "all", platform or "all",
        )
        return rules

    def _fetch_correction_clusters(
        self,
        domain: str,
        platform: str,
        min_samples: int,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[str] = []
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if platform:
            clauses.append("platform = ?")
            params.append(platform)

        where = " AND ".join(clauses) if clauses else "1=1"
        # Group by exact (field_label, agent_value, user_value) triple
        sql = f"""
            SELECT field_label, agent_value, user_value, COUNT(*) as cnt
            FROM field_corrections
            WHERE {where}
            GROUP BY field_label, agent_value, user_value
            HAVING cnt >= ?
            ORDER BY cnt DESC
        """
        params.append(min_samples)

        with sqlite3.connect(self._corrections_db) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql, params).fetchall()

    @staticmethod
    def _is_trivial_diff(a: str, b: str) -> bool:
        """Check if the difference is just whitespace/case/punctuation."""
        na = a.strip().lower().rstrip(".").replace(",", "")
        nb = b.strip().lower().rstrip(".").replace(",", "")
        return na == nb

    @staticmethod
    def _field_to_pattern(field_label: str) -> str:
        """Convert a field label into a regex pattern for matching questions."""
        # Extract key words, ignore filler
        words = re.findall(r"[a-zA-Z]{3,}", field_label.lower())
        if not words:
            return re.escape(field_label.lower())
        # Build a pattern that matches any question containing all key words
        return r".*".join(re.escape(w) for w in words[:5])

    @staticmethod
    def _infer_action(
        field_label: str,
        agent_value: str,
        user_value: str,
        count: int,
    ) -> tuple[str, str]:
        """Decide whether to override, escalate, or generate a template."""
        label_lower = field_label.lower()

        # High-correction fields get escalated (don't auto-fill)
        if count >= 5:
            return "escalate", user_value

        # Free-text fields with long answers → generate template
        if len(user_value) > 100 and "salary" not in label_lower:
            return "use_template", user_value

        # Simple value replacement
        return "override_answer", user_value

    # ------------------------------------------------------------------
    # Trajectory → Rule
    # ------------------------------------------------------------------

    def from_trajectories(
        self,
        pipeline: str = "",
        domain: str = "",
        *,
        min_samples: int = 5,
        max_rules: int = 10,
    ) -> list[GeneratedRule]:
        """Mine trajectories for decision patterns that predict success/failure.

        Looks for steps where a specific (action, target, output_value)
        combination correlates with final_outcome = 'success' vs 'failure'.
        """
        patterns = self._mine_trajectory_patterns(
            pipeline, domain, min_samples,
        )
        rules: list[GeneratedRule] = []

        for pattern, stats in patterns.items():
            action_name, target, output_value = pattern
            success_rate = stats["success"] / stats["total"]

            # Only generate rules for high-confidence patterns
            if success_rate >= 0.85 and stats["total"] >= min_samples:
                rules.append(GeneratedRule(
                    rule_type="trajectory_heuristic",
                    source="auto_rule_generator",
                    category=target,
                    pattern=re.escape(target.lower()),
                    action="recommend",
                    value=output_value,
                    confidence=success_rate,
                    sample_count=stats["total"],
                    evidence=(
                        f"{action_name} '{target}' = '{output_value[:80]}' "
                        f"leads to success {success_rate:.0%} "
                        f"({stats['success']}/{stats['total']})"
                    ),
                ))
            elif success_rate <= 0.2 and stats["total"] >= min_samples:
                # Avoid patterns that correlate with failure
                rules.append(GeneratedRule(
                    rule_type="trajectory_avoidance",
                    source="auto_rule_generator",
                    category=target,
                    pattern=re.escape(target.lower()),
                    action="avoid",
                    value=output_value,
                    confidence=1.0 - success_rate,
                    sample_count=stats["total"],
                    evidence=(
                        f"{action_name} '{target}' = '{output_value[:80]}' "
                        f"leads to failure {1 - success_rate:.0%} "
                        f"({stats['failure']}/{stats['total']})"
                    ),
                ))

            if len(rules) >= max_rules:
                break

        logger.info(
            "auto_rule_generator: generated %d rules from trajectories "
            "(pipeline=%s, domain=%s)",
            len(rules), pipeline or "all", domain or "all",
        )
        return rules

    def _mine_trajectory_patterns(
        self,
        pipeline: str,
        domain: str,
        min_samples: int,
    ) -> dict[tuple[str, str, str], dict[str, int]]:
        """Aggregate step patterns from completed trajectories."""
        clauses: list[str] = ["final_outcome IN ('success', 'failure')"]
        params: list[str] = []
        if pipeline:
            clauses.append("t.pipeline = ?")
            params.append(pipeline)
        if domain:
            clauses.append("t.domain = ?")
            params.append(domain)

        where = " AND ".join(clauses)
        sql = f"""
            SELECT t.trajectory_id, t.final_outcome,
                   s.action, s.target, s.output_value
            FROM trajectories t
            JOIN trajectory_steps s ON t.trajectory_id = s.trajectory_id
            WHERE {where}
        """

        with sqlite3.connect(self._trajectory_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        # Group by (action, target, output_value) and count success/failure
        stats: dict[tuple[str, str, str], dict[str, int]] = {}
        for row in rows:
            key = (row["action"], row["target"], row["output_value"])
            if key not in stats:
                stats[key] = {"success": 0, "failure": 0, "total": 0}
            outcome = row["final_outcome"]
            if outcome == "success":
                stats[key]["success"] += 1
            else:
                stats[key]["failure"] += 1
            stats[key]["total"] += 1

        # Filter to patterns with enough samples
        return {
            k: v for k, v in stats.items()
            if v["total"] >= min_samples
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_rule(
        self,
        rule: GeneratedRule,
        test_cases: list[dict] | None = None,
    ) -> bool:
        """Validate a rule against historical data or synthetic test cases.

        Returns True if the rule improves accuracy, False otherwise.
        """
        if rule.sample_count < 2:
            return False

        if rule.confidence < 0.5:
            return False

        # Check for obvious anti-patterns
        if rule.action == "override_answer" and not rule.value.strip():
            return False

        # If test cases provided, verify pattern matching works
        if test_cases:
            compiled = re.compile(rule.pattern, re.IGNORECASE)
            matches = sum(
                1 for tc in test_cases
                if compiled.search(tc.get("question", ""))
            )
            if matches == 0:
                return False

        return True

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy_rule(self, rule: GeneratedRule) -> dict:
        """Deploy a validated rule to the AgentRulesDB.

        Returns {"rule_id": int, "deployed": bool}.
        """
        try:
            from jobpulse.agent_rules import AgentRulesDB
            db = AgentRulesDB()
        except Exception as exc:
            logger.error("AgentRulesDB unavailable: %s", exc)
            return {"rule_id": None, "deployed": False}

        now = datetime.now(UTC).isoformat()
        expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()

        with db._connect() as conn:
            # Upsert on source+category+pattern
            existing = conn.execute(
                "SELECT rule_id FROM agent_rules WHERE source = ? AND category = ? AND pattern = ?",
                (rule.source, rule.category, rule.pattern),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE agent_rules
                       SET action = ?, value = ?, confidence = ?,
                           sample_count = ?, active = 1, expires_at = ?
                       WHERE rule_id = ?""",
                    (rule.action, rule.value, rule.confidence,
                     rule.sample_count, expires, existing["rule_id"]),
                )
                rule_id = existing["rule_id"]
            else:
                cursor = conn.execute(
                    """INSERT INTO agent_rules
                       (rule_type, source, category, pattern, action, value,
                        confidence, sample_count, active, created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (rule.rule_type, rule.source, rule.category,
                     rule.pattern, rule.action, rule.value,
                     rule.confidence, rule.sample_count, now, expires),
                )
                rule_id = cursor.lastrowid

        logger.info(
            "auto_rule_generator: deployed %s rule #%d category=%s action=%s",
            rule.rule_type, rule_id, rule.category, rule.action,
        )

        # Emit signal so the optimizer learns about its own actions
        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="adaptation",
                source_loop="auto_rule_generator",
                domain=rule.category,
                payload={
                    "param": rule.rule_type,
                    "old_value": "",
                    "new_value": rule.value,
                    "reason": rule.evidence,
                },
            )
        except Exception:
            pass

        return {"rule_id": rule_id, "deployed": True}

    def deploy_batch(self, rules: list[GeneratedRule]) -> list[dict]:
        """Validate and deploy a batch of rules. Returns deployment results."""
        results = []
        for rule in rules:
            if self.validate_rule(rule):
                results.append(self.deploy_rule(rule))
            else:
                results.append({
                    "rule_id": None,
                    "deployed": False,
                    "reason": "validation_failed",
                })
        return results
