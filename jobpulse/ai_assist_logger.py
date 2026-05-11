"""AI Assist Logger — capture, persist, and learn from AI assistant interventions.

When Kimi, Claude, Codex, or any external AI agent fixes form fields directly in the
browser, this module records:
  1. What changed (field-level diffs)
  2. Why it changed (reasoning / strategy)
  3. Higher-level insights (gotchas, navigation tricks, platform quirks)

All captured data is fed back into the same learning pipelines that human corrections
use: CorrectionCapture, GotchasDB, FormExperienceDB, NavigationLearner, and the
optimization signal bus.

Typical flow from an AI assistant script:

    from jobpulse.ai_assist_logger import AIAssistLogger
    from jobpulse.live_review_applicator import get_active_review

    logger = AIAssistLogger()
    session = logger.start_session(
        agent_name="kimi",
        job_id=job["job_id"],
        domain="greenhouse.io",
        platform="greenhouse",
        original_mapping=agent_mapping,
    )

    # AI fixes fields directly in the browser via Playwright ...

    # Record individual fixes with reasoning
    logger.record_fix(
        session_id=session.session_id,
        field_label="Salary Expectation",
        old_value="",
        new_value="80000",
        reasoning="JD stated £70-85k; profile default was blank. "
                  "Filled midpoint to stay competitive.",
        fix_category="value_correction",
        confidence=0.95,
    )

    # Record a discovered strategy
    logger.record_strategy(
        session_id=session.session_id,
        domain="greenhouse.io",
        strategy_type="fill_technique",
        description="Greenhouse salary fields require clicking the label first "
                    "to activate the input container.",
        selector_pattern='[data-qa="salary-input"]',
        applicability_pattern="greenhouse.io/*",
    )

    # Finalize — automatically pushes to all learning pipelines
    logger.finalize_session(session_id)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "ai_assist_sessions.db")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

VALID_AGENTS = frozenset({"kimi", "claude", "codex", "openai", "custom", "vision_verifier"})
VALID_FIX_CATEGORIES = frozenset({
    "value_correction",
    "strategy",
    "gotcha",
    "navigation",
    "selector",
    "screening_answer",
    "consent_policy",
})
VALID_STRATEGY_TYPES = frozenset({
    "selector_override",
    "wait_adjustment",
    "fill_technique",
    "navigation_sequence",
    "platform_quirk",
    "label_mapping",
    "screening_pattern",
    "consent_rule",
})


@dataclass
class AIAssistSession:
    session_id: str
    agent_name: str
    job_id: str
    domain: str
    platform: str
    started_at: str = ""
    finalized_at: str = ""
    original_mapping: dict[str, str] = field(default_factory=dict)
    final_mapping: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    success: bool = False

    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.now(UTC).isoformat()


@dataclass
class AIAssistFix:
    session_id: str
    field_label: str
    old_value: str
    new_value: str
    reasoning: str = ""
    fix_category: str = "value_correction"
    confidence: float = 1.0
    created_at: str = ""
    applied_to_corrections: bool = False
    applied_to_gotchas: bool = False

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        if self.fix_category not in VALID_FIX_CATEGORIES:
            raise ValueError(
                f"Invalid fix_category '{self.fix_category}'. "
                f"Must be one of: {', '.join(sorted(VALID_FIX_CATEGORIES))}"
            )


@dataclass
class AIAssistStrategy:
    session_id: str
    domain: str
    strategy_type: str
    description: str
    selector_pattern: str = ""
    old_solution: str = ""
    new_solution: str = ""
    applicability_pattern: str = ""
    times_used: int = 0
    created_at: str = ""
    last_used_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        if self.strategy_type not in VALID_STRATEGY_TYPES:
            raise ValueError(
                f"Invalid strategy_type '{self.strategy_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_STRATEGY_TYPES))}"
            )


# ---------------------------------------------------------------------------
# Core logger
# ---------------------------------------------------------------------------


class AIAssistLogger:
    """Central logger for AI assistant form-fixing sessions.

    All fixes and strategies are persisted locally and can be pushed into the
    project's existing learning infrastructure (corrections, gotchas,
    optimization signals, form experience).
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._init_db()

    # -- DB lifecycle -------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL UNIQUE,
                    agent_name TEXT NOT NULL,
                    job_id TEXT,
                    domain TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finalized_at TEXT,
                    original_mapping TEXT NOT NULL DEFAULT '{}',
                    final_mapping TEXT NOT NULL DEFAULT '{}',
                    summary TEXT,
                    success INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_fixes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    old_value TEXT NOT NULL DEFAULT '',
                    new_value TEXT NOT NULL DEFAULT '',
                    reasoning TEXT,
                    fix_category TEXT NOT NULL DEFAULT 'value_correction',
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    applied_to_corrections INTEGER DEFAULT 0,
                    applied_to_gotchas INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_strategies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    strategy_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    selector_pattern TEXT,
                    old_solution TEXT,
                    new_solution TEXT,
                    applicability_pattern TEXT,
                    times_used INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT
                )
            """)
            # Indexes
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_fixes_session
                ON ai_fixes (session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_fixes_field
                ON ai_fixes (field_label)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_strategies_domain
                ON ai_strategies (domain)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_strategies_type
                ON ai_strategies (strategy_type)
            """)

    # -- Session management -------------------------------------------------

    def start_session(
        self,
        agent_name: str,
        *,
        job_id: str = "",
        domain: str = "",
        platform: str = "",
        original_mapping: dict[str, str] | None = None,
    ) -> AIAssistSession:
        """Begin a new AI assist session.

        Args:
            agent_name: One of 'kimi', 'claude', 'codex', 'openai', 'custom'.
            job_id: Optional job identifier for linking.
            domain: The domain being worked on (e.g., 'greenhouse.io').
            platform: The ATS platform key (e.g., 'greenhouse').
            original_mapping: The agent's original field→value mapping before
                the AI assistant touched anything.

        Returns:
            The created AIAssistSession.
        """
        if agent_name not in VALID_AGENTS:
            raise ValueError(
                f"Invalid agent_name '{agent_name}'. "
                f"Must be one of: {', '.join(sorted(VALID_AGENTS))}"
            )
        session = AIAssistSession(
            session_id=f"ai_{agent_name}_{uuid.uuid4().hex[:12]}",
            agent_name=agent_name,
            job_id=job_id,
            domain=domain,
            platform=platform,
            original_mapping=dict(original_mapping or {}),
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ai_sessions
                   (session_id, agent_name, job_id, domain, platform,
                    started_at, original_mapping)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.session_id,
                    session.agent_name,
                    session.job_id,
                    session.domain,
                    session.platform,
                    session.started_at,
                    json.dumps(session.original_mapping),
                ),
            )
        logger.info(
            "ai_assist_logger: started session %s (%s, domain=%s)",
            session.session_id,
            agent_name,
            domain,
        )
        return session

    def record_fix(
        self,
        session_id: str,
        field_label: str,
        old_value: str,
        new_value: str,
        *,
        reasoning: str = "",
        fix_category: str = "value_correction",
        confidence: float = 1.0,
        dom_signature: dict | None = None,
    ) -> AIAssistFix:
        """Record a single field fix made by the AI assistant.

        Args:
            session_id: The session returned by start_session().
            field_label: Exact label of the field that was changed.
            old_value: Value before the AI fix.
            new_value: Value after the AI fix.
            reasoning: Explanation of why this fix was needed.
            fix_category: Category of fix (see VALID_FIX_CATEGORIES).
            confidence: 0.0-1.0 confidence in the fix.
            dom_signature: Optional {selector, widget_type, ancestor_classes,
                aria_label} captured from the live page. When provided, the
                signature is stored in GotchasDB.widget_patterns keyed by
                domain so future visits learn from this fix.

        Returns:
            The created AIAssistFix record.
        """
        fix = AIAssistFix(
            session_id=session_id,
            field_label=field_label,
            old_value=old_value,
            new_value=new_value,
            reasoning=reasoning,
            fix_category=fix_category,
            confidence=confidence,
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ai_fixes
                   (session_id, field_label, old_value, new_value, reasoning,
                    fix_category, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fix.session_id,
                    fix.field_label,
                    fix.old_value,
                    fix.new_value,
                    fix.reasoning,
                    fix.fix_category,
                    fix.confidence,
                    fix.created_at,
                ),
            )
        logger.debug(
            "ai_assist_logger: recorded fix %s -> %s (%s)",
            field_label,
            fix_category,
            session_id,
        )

        if dom_signature:
            try:
                from jobpulse.form_engine.gotchas import GotchasDB
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT domain FROM ai_sessions WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                domain = row[0] if row else ""
                if domain:
                    GotchasDB().record_widget_pattern(
                        domain=domain,
                        label=field_label,
                        selector=dom_signature.get("selector", ""),
                        widget_type=dom_signature.get("widget_type", "unknown"),
                        ancestor_classes=dom_signature.get("ancestor_classes", ""),
                        aria_label=dom_signature.get("aria_label", ""),
                    )
            except Exception as exc:
                logger.debug("ai_assist: widget pattern capture failed: %s", exc)

        return fix

    def record_strategy(
        self,
        session_id: str,
        domain: str,
        strategy_type: str,
        description: str,
        *,
        selector_pattern: str = "",
        old_solution: str = "",
        new_solution: str = "",
        applicability_pattern: str = "",
    ) -> AIAssistStrategy:
        """Record a higher-level strategy or platform insight.

        Args:
            session_id: The active session ID.
            domain: The domain this strategy applies to.
            strategy_type: One of the VALID_STRATEGY_TYPES.
            description: Human-readable description of the strategy.
            selector_pattern: CSS selector or locator pattern affected.
            old_solution: What the agent used to do (wrong).
            new_solution: What the AI discovered works (right).
            applicability_pattern: URL pattern or condition for when this applies.

        Returns:
            The created AIAssistStrategy record.
        """
        strategy = AIAssistStrategy(
            session_id=session_id,
            domain=domain,
            strategy_type=strategy_type,
            description=description,
            selector_pattern=selector_pattern,
            old_solution=old_solution,
            new_solution=new_solution,
            applicability_pattern=applicability_pattern,
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ai_strategies
                   (session_id, domain, strategy_type, description,
                    selector_pattern, old_solution, new_solution,
                    applicability_pattern, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy.session_id,
                    strategy.domain,
                    strategy.strategy_type,
                    strategy.description,
                    strategy.selector_pattern,
                    strategy.old_solution,
                    strategy.new_solution,
                    strategy.applicability_pattern,
                    strategy.created_at,
                ),
            )
        logger.info(
            "ai_assist_logger: recorded strategy %s for %s (%s)",
            strategy_type,
            domain,
            session_id,
        )
        return strategy

    def finalize_session(
        self,
        session_id: str,
        *,
        final_mapping: dict[str, str] | None = None,
        summary: str = "",
        success: bool = True,
        push_to_learning: bool = True,
    ) -> dict[str, Any]:
        """Finalize a session and optionally push all fixes to learning pipelines.

        Args:
            session_id: The session to finalize.
            final_mapping: The complete field→value mapping after AI intervention.
            summary: Optional human-readable summary of the session.
            success: Whether the AI assist ultimately succeeded.
            push_to_learning: If True, automatically pushes fixes to
                CorrectionCapture, GotchasDB, FormExperienceDB, and the
                optimization signal bus.

        Returns:
            Dict with counts of what was processed.
        """
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """UPDATE ai_sessions
                   SET finalized_at = ?,
                       final_mapping = ?,
                       summary = ?,
                       success = ?
                   WHERE session_id = ?""",
                (
                    now,
                    json.dumps(dict(final_mapping or {})),
                    summary,
                    int(success),
                    session_id,
                ),
            )

        result: dict[str, Any] = {
            "session_id": session_id,
            "fixes_pushed": 0,
            "strategies_pushed": 0,
            "corrections_stored": 0,
            "gotchas_stored": 0,
            "signals_emitted": 0,
        }

        if push_to_learning:
            result["fixes_pushed"] = self._push_fixes_to_corrections(session_id)
            result["strategies_pushed"] = self._push_strategies_to_gotchas(session_id)
            result["signals_emitted"] = self._emit_optimization_signals(session_id)
            result["corrections_stored"] = result["fixes_pushed"]
            result["gotchas_stored"] = result["strategies_pushed"]

        logger.info(
            "ai_assist_logger: finalized session %s "
            "(fixes=%d, strategies=%d, success=%s)",
            session_id,
            result.get("fixes_pushed", 0),
            result.get("strategies_pushed", 0),
            success,
        )
        return result

    # -- Convenience: capture page delta ------------------------------------

    def capture_page_delta(
        self,
        session_id: str,
        original_mapping: dict[str, str],
        current_mapping: dict[str, str],
        *,
        auto_reasoning: str = "",
    ) -> list[AIAssistFix]:
        """Compare two mappings and auto-record fixes for every difference.

        This is the primary convenience method for AI assistants that have
        already mutated the browser state and just want to log what changed.

        Args:
            session_id: The active session ID.
            original_mapping: Mapping before AI touched the page.
            current_mapping: Mapping after AI finished.
            auto_reasoning: Default reasoning applied to all auto-detected diffs.
                Can be overridden per-field if the AI calls record_fix() manually
                for specific fields.

        Returns:
            List of AIAssistFix records that were created.
        """
        fixes: list[AIAssistFix] = []
        for field, old_val in original_mapping.items():
            new_val = current_mapping.get(field, old_val)
            if str(old_val).strip() != str(new_val).strip():
                fix = self.record_fix(
                    session_id=session_id,
                    field_label=field,
                    old_value=old_val,
                    new_value=new_val,
                    reasoning=auto_reasoning,
                )
                fixes.append(fix)
        return fixes

    # -- Query / retrieval --------------------------------------------------

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ai_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_fixes(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_fixes WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_strategies(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_strategies WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_strategies_for_domain(
        self, domain: str, strategy_type: str = ""
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if strategy_type:
                rows = conn.execute(
                    """SELECT * FROM ai_strategies
                       WHERE domain = ? AND strategy_type = ?
                       ORDER BY times_used DESC, created_at DESC""",
                    (domain, strategy_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM ai_strategies
                       WHERE domain = ?
                       ORDER BY times_used DESC, created_at DESC""",
                    (domain,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_fixes_for_field(
        self, field_label: str, domain: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return AI fixes for a field label, optionally filtered by domain."""
        label_norm = field_label.strip().lower()
        with self._connect() as conn:
            if domain:
                rows = conn.execute(
                    """SELECT f.*, s.domain, s.platform FROM ai_fixes f
                       JOIN ai_sessions s ON f.session_id = s.session_id
                       WHERE LOWER(f.field_label) = ? AND s.domain = ?
                       ORDER BY f.created_at DESC LIMIT ?""",
                    (label_norm, domain, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT f.*, s.domain, s.platform FROM ai_fixes f
                       JOIN ai_sessions s ON f.session_id = s.session_id
                       WHERE LOWER(f.field_label) = ?
                       ORDER BY f.created_at DESC LIMIT ?""",
                    (label_norm, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_summary(self, agent_name: str = "", days: int = 30) -> dict[str, Any]:
        """Return aggregate stats for AI assist sessions."""
        from datetime import timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            params: tuple = (cutoff,)
            agent_clause = ""
            if agent_name:
                agent_clause = " AND agent_name = ?"
                params = (cutoff, agent_name)

            session_count = conn.execute(
                f"SELECT COUNT(*) FROM ai_sessions WHERE started_at >= ?{agent_clause}",
                params,
            ).fetchone()[0]

            fix_count = conn.execute(
                f"""SELECT COUNT(*) FROM ai_fixes f
                    JOIN ai_sessions s ON f.session_id = s.session_id
                    WHERE s.started_at >= ?{agent_clause}""",
                params,
            ).fetchone()[0]

            strategy_count = conn.execute(
                f"""SELECT COUNT(*) FROM ai_strategies st
                    JOIN ai_sessions s ON st.session_id = s.session_id
                    WHERE s.started_at >= ?{agent_clause}""",
                params,
            ).fetchone()[0]

        return {
            "period_days": days,
            "agent_filter": agent_name or "all",
            "sessions": session_count,
            "fixes": fix_count,
            "strategies": strategy_count,
        }

    # -- Internal: push to learning pipelines -------------------------------

    def _push_fixes_to_corrections(self, session_id: str) -> int:
        """Push all value_correction fixes from this session to CorrectionCapture."""
        session = self.get_session(session_id)
        if not session:
            return 0

        fixes = self.get_fixes(session_id)
        value_fixes = [f for f in fixes if f.get("fix_category") == "value_correction"]
        if not value_fixes:
            return 0

        agent_mapping: dict[str, str] = {}
        final_mapping: dict[str, str] = {}
        try:
            agent_mapping = json.loads(session.get("original_mapping") or "{}")
            final_mapping = json.loads(session.get("final_mapping") or "{}")
        except json.JSONDecodeError:
            agent_mapping = {}
            final_mapping = {}

        # Emergency Claude/Kimi assists rarely pass original_mapping at start
        # nor final_mapping at finalize, so the session-level mappings stay "{}"
        # and parse fine to empty dicts. Reconstruct from the individual fixes
        # whenever EITHER mapping is empty — record_corrections({}, {}) writes
        # zero rows but the bridge would still report success, masking the bug.
        if not agent_mapping or not final_mapping:
            for f in fixes:
                agent_mapping.setdefault(f["field_label"], f.get("old_value", ""))
                final_mapping[f["field_label"]] = f.get("new_value", "")

        try:
            from jobpulse.correction_capture import CorrectionCapture

            cc = CorrectionCapture()
            cc.record_corrections(
                domain=session["domain"],
                platform=session["platform"],
                agent_mapping=agent_mapping,
                final_mapping=final_mapping,
                job_id=session.get("job_id", ""),
                source="ai_assist",
                agent_name=session["agent_name"],
            )
        except Exception as exc:
            logger.warning("ai_assist_logger: correction push failed: %s", exc)
            return 0

        # Auto-generate AgentRulesDB rules from each fix. Without this, only
        # confirm_application's rules generation runs — meaning emergency AI
        # assists never produce queryable agent rules and the same field
        # failures recur on the next form. Mirrors applicator.confirm_application
        # lines 545-557 so the two correction paths produce identical rules.
        try:
            from jobpulse.agent_rules import AgentRulesDB

            ar = AgentRulesDB()
            for fix in value_fixes:
                try:
                    ar.auto_generate_from_correction(
                        field_label=fix["field_label"],
                        agent_value=fix.get("old_value", ""),
                        user_value=fix.get("new_value", ""),
                        domain=session["domain"],
                        platform=session["platform"],
                    )
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("ai_assist_logger: agent rules push failed: %s", exc)

        # ALSO push each fix into the screening_semantic_cache (Qdrant) so the
        # V2 pipeline retrieves these answers on future questions. Without this
        # step, the agent's V2 cache misses on questions Claude has already
        # answered manually — every form is a partial cold-start. CorrectionCapture
        # records the diff for AgentRulesDB, but it doesn't populate the
        # semantic cache that try_screening_v2 reads. This bridge closes the
        # loop end-to-end.
        try:
            from jobpulse.screening_semantic_cache import get_screening_semantic_cache
            cache = get_screening_semantic_cache()
            for f in value_fixes:
                question = (f.get("field_label") or "").strip()
                answer = (f.get("new_value") or "").strip()
                if not question or not answer:
                    continue
                try:
                    cache.cache(
                        question=question,
                        intent="ai_assist",
                        answer=answer,
                        confidence=float(f.get("confidence") or 0.85),
                        selected_option=answer,
                        field_type="",  # unknown at this point — agent will re-read
                        field_options=None,
                    )
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("ai_assist_logger: semantic cache bridge skipped: %s", exc)

        # Mark as applied
        with self._connect() as conn:
            conn.execute(
                """UPDATE ai_fixes
                   SET applied_to_corrections = 1
                   WHERE session_id = ? AND fix_category = 'value_correction'""",
                (session_id,),
            )
        return len(value_fixes)

    def _push_strategies_to_gotchas(self, session_id: str) -> int:
        """Push applicable strategies to GotchasDB."""
        session = self.get_session(session_id)
        if not session:
            return 0

        strategies = self.get_strategies(session_id)
        if not strategies:
            return 0

        pushed = 0
        try:
            from jobpulse.form_engine.gotchas import GotchasDB

            gdb = GotchasDB()
            for st in strategies:
                selector = st.get("selector_pattern") or "*"
                problem = st.get("old_solution") or "AI-discovered issue"
                solution = st.get("new_solution") or st.get("description", "")
                if not solution:
                    continue
                gdb.store(
                    domain=st["domain"],
                    selector_pattern=selector,
                    problem=problem,
                    solution=solution,
                    engine=f"ai_{session['agent_name']}",
                )
                pushed += 1
        except Exception as exc:
            logger.warning("ai_assist_logger: gotcha push failed: %s", exc)
            return 0

        # Mark as applied
        with self._connect() as conn:
            conn.execute(
                """UPDATE ai_strategies
                   SET times_used = times_used + 1, last_used_at = ?
                   WHERE session_id = ?""",
                (datetime.now(UTC).isoformat(), session_id),
            )
        return pushed

    def _emit_optimization_signals(self, session_id: str) -> int:
        """Emit optimization signals for fixes and strategies."""
        session = self.get_session(session_id)
        if not session:
            return 0

        try:
            from shared.optimization import get_optimization_engine

            engine = get_optimization_engine()
        except Exception as exc:
            logger.debug("ai_assist_logger: optimization engine unavailable: %s", exc)
            return 0

        count = 0
        domain = session["domain"]
        agent = session["agent_name"]

        # Signal per fix
        fixes = self.get_fixes(session_id)
        for fix in fixes:
            engine.emit(
                signal_type="correction",
                source_loop="ai_assist_logger",
                domain=domain,
                agent_name=agent,
                payload={
                    "field": fix["field_label"],
                    "old_value": fix["old_value"],
                    "new_value": fix["new_value"],
                    "reasoning": fix["reasoning"],
                    "category": fix["fix_category"],
                    "confidence": fix["confidence"],
                    "session_id": session_id,
                },
                session_id=session_id,
                severity="info",
            )
            count += 1

        # Signal per strategy
        strategies = self.get_strategies(session_id)
        for st in strategies:
            engine.emit(
                signal_type="adaptation",
                source_loop="ai_assist_logger",
                domain=domain,
                agent_name=agent,
                payload={
                    "strategy_type": st["strategy_type"],
                    "description": st["description"],
                    "selector": st.get("selector_pattern", ""),
                    "applicability": st.get("applicability_pattern", ""),
                    "session_id": session_id,
                },
                session_id=session_id,
                severity="info",
            )
            count += 1

        return count


# ---------------------------------------------------------------------------
# Singleton convenience
# ---------------------------------------------------------------------------

_logger_instance: AIAssistLogger | None = None


def get_ai_assist_logger() -> AIAssistLogger:
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = AIAssistLogger()
    return _logger_instance
