"""Real end-to-end adaptation chain tests — no mocks, real SQLite via tmp_path.

Tests the 3 mandatory self-adaptation chains with actual DB operations:

Chain 1: Correction -> Rule -> Consumption
    CorrectionCapture records a diff -> AgentRulesDB creates a learned rule ->
    NativeFormFiller can query and consume that rule.

Chain 2: Strategy Reflection -> TrajectoryStore + ExperienceMemory
    Deterministic heuristic extraction from field trajectories ->
    ExperienceMemory receives high-quality experiences.

Chain 3: Optimization Signal Flow
    OptimizationEngine.emit() stores a signal in SQLite ->
    SignalBus.query() retrieves it -> before/after learning actions tracked.

ALL tests use real SQLite via tmp_path. NO mocks. NO monkeypatching of
business logic. Only DB path redirection via constructor args.
"""

import json
import sqlite3

import pytest

from jobpulse.agent_rules import AgentRulesDB
from jobpulse.correction_capture import CorrectionCapture
from shared.experiential_learning import (
    Experience,
    ExperienceMemory,
    reset_shared_experience_memory,
)
from shared.optimization._engine import OptimizationEngine
from shared.optimization._signals import SignalBus


# ======================================================================
# Chain 1: Correction -> Rule -> Consumption
# ======================================================================


class TestCorrectionToRuleChain:
    """Verify the full chain: CorrectionCapture -> AgentRulesDB -> query."""

    def test_correction_recorded_in_db(self, tmp_path):
        """CorrectionCapture.record_corrections writes real rows to SQLite."""
        db_path = str(tmp_path / "field_corrections.db")
        cc = CorrectionCapture(db_path=db_path)

        agent_mapping = {
            "Visa Status": "No",
            "First Name": "Yash",
        }
        final_mapping = {
            "Visa Status": "Graduate Visa",
            "First Name": "Yash",
        }

        result = cc.record_corrections(
            domain="greenhouse.io",
            platform="greenhouse",
            agent_mapping=agent_mapping,
            final_mapping=final_mapping,
        )

        assert len(result["corrections"]) == 1
        assert result["unchanged"] == 1
        assert result["corrections"][0]["field"] == "Visa Status"
        assert result["corrections"][0]["agent"] == "No"
        assert result["corrections"][0]["user"] == "Graduate Visa"

        # Verify actual DB row
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM field_corrections").fetchall()
        conn.close()

        assert len(rows) == 1
        row = rows[0]
        assert row["domain"] == "greenhouse.io"
        assert row["platform"] == "greenhouse"
        assert row["field_label"] == "visa status"  # normalized to lowercase
        assert row["agent_value"] == "No"
        assert row["user_value"] == "Graduate Visa"

    def test_correction_count_query(self, tmp_path):
        """CorrectionCapture.get_correction_count returns accurate counts."""
        db_path = str(tmp_path / "field_corrections.db")
        cc = CorrectionCapture(db_path=db_path)

        # Record 3 corrections for the same field
        for i in range(3):
            cc.record_corrections(
                domain="greenhouse.io",
                platform="greenhouse",
                agent_mapping={"Salary": f"old_{i}"},
                final_mapping={"Salary": f"new_{i}"},
            )

        assert cc.get_correction_count("Salary") == 3
        assert cc.get_correction_count("salary") == 3  # case-insensitive
        assert cc.get_correction_count("NonExistent") == 0

    def test_correction_feeds_agent_rules(self, tmp_path):
        """CorrectionCapture correction -> AgentRulesDB creates an override rule."""
        corrections_db = str(tmp_path / "field_corrections.db")
        rules_db = str(tmp_path / "agent_rules.db")

        cc = CorrectionCapture(db_path=corrections_db)
        rules = AgentRulesDB(db_path=rules_db)

        # Step 1: Record a correction
        result = cc.record_corrections(
            domain="greenhouse.io",
            platform="greenhouse",
            agent_mapping={"Visa Status": "No"},
            final_mapping={"Visa Status": "Graduate Visa"},
        )
        assert len(result["corrections"]) == 1

        # Step 2: Feed correction into AgentRulesDB
        correction = result["corrections"][0]
        rule_result = rules.auto_generate_from_correction(
            field_label=correction["field"],
            agent_value=correction["agent"],
            user_value=correction["user"],
            domain="greenhouse.io",
            platform="greenhouse",
        )

        assert rule_result["rule_id"] is not None
        assert rule_result["action"] == "override_answer"

        # Step 3: Verify DB row directly
        conn = sqlite3.connect(rules_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM agent_rules").fetchall()
        conn.close()

        assert len(rows) == 1
        row = rows[0]
        assert row["rule_type"] == "correction_override"
        assert row["source"] == "correction_capture"
        assert row["category"] == "Visa Status"
        assert row["pattern"] == "greenhouse.io"
        assert row["value"] == "Graduate Visa"
        assert row["sample_count"] == 1

    def test_rule_consumed_via_get_field_overrides(self, tmp_path):
        """AgentRulesDB.get_field_overrides returns learned corrections for form filling."""
        rules_db = str(tmp_path / "agent_rules.db")
        rules = AgentRulesDB(db_path=rules_db)

        # Create a correction-based rule
        rules.auto_generate_from_correction(
            field_label="Visa Status",
            agent_value="No",
            user_value="Graduate Visa",
            domain="greenhouse.io",
            platform="greenhouse",
        )

        # Consume the rule (as NativeFormFiller would)
        overrides = rules.get_field_overrides(domain="greenhouse.io")

        assert "Visa Status" in overrides
        override = overrides["Visa Status"]
        assert override["value"] == "Graduate Visa"
        assert override["action"] == "override_answer"
        assert override["confidence"] > 0

        # Verify times_applied was incremented
        conn = sqlite3.connect(rules_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT times_applied FROM agent_rules WHERE category = ?",
            ("Visa Status",),
        ).fetchone()
        conn.close()
        assert row["times_applied"] == 1

    def test_repeated_corrections_escalate(self, tmp_path):
        """3+ corrections for the same field escalates to 'escalate' action."""
        rules_db = str(tmp_path / "agent_rules.db")
        rules = AgentRulesDB(db_path=rules_db)

        # Feed 3 corrections for the same field+domain
        for i in range(3):
            result = rules.auto_generate_from_correction(
                field_label="Salary",
                agent_value=f"wrong_{i}",
                user_value=f"correct_{i}",
                domain="workday.com",
                platform="workday",
            )

        # After 3 corrections, action should be 'escalate'
        assert result["action"] == "escalate"

        # Verify the field shows up in escalation fields
        escalation_fields = rules.get_escalation_fields()
        assert "Salary" in escalation_fields

    def test_full_chain_correction_to_consumption(self, tmp_path):
        """End-to-end: record correction -> create rule -> query override -> verify DB state."""
        corrections_db = str(tmp_path / "field_corrections.db")
        rules_db = str(tmp_path / "agent_rules.db")

        cc = CorrectionCapture(db_path=corrections_db)
        rules = AgentRulesDB(db_path=rules_db)

        # Record correction
        cc.record_corrections(
            domain="lever.co",
            platform="lever",
            agent_mapping={"Notice Period": "2 weeks", "City": "London"},
            final_mapping={"Notice Period": "1 month", "City": "London"},
        )

        # Feed into rules
        rules.auto_generate_from_correction(
            field_label="Notice Period",
            agent_value="2 weeks",
            user_value="1 month",
            domain="lever.co",
            platform="lever",
        )

        # Query overrides for consumption
        overrides = rules.get_field_overrides(domain="lever.co")
        assert "Notice Period" in overrides
        assert overrides["Notice Period"]["value"] == "1 month"

        # Verify both DBs have the correct data
        corr_conn = sqlite3.connect(corrections_db)
        corr_count = corr_conn.execute(
            "SELECT COUNT(*) FROM field_corrections"
        ).fetchone()[0]
        corr_conn.close()
        assert corr_count == 1

        rules_conn = sqlite3.connect(rules_db)
        rules_conn.row_factory = sqlite3.Row
        rule_rows = rules_conn.execute(
            "SELECT * FROM agent_rules WHERE active = 1"
        ).fetchall()
        rules_conn.close()
        assert len(rule_rows) == 1
        assert rule_rows[0]["value"] == "1 month"


# ======================================================================
# Chain 2: Strategy Reflection -> TrajectoryStore + ExperienceMemory
# ======================================================================


class TestStrategyReflectionChain:
    """Verify deterministic heuristic extraction and ExperienceMemory storage."""

    def test_deterministic_heuristic_extraction_from_corrections(self):
        """extract_deterministic_heuristics finds correction-based heuristics."""
        from jobpulse.strategy_reflector import extract_deterministic_heuristics
        from jobpulse.trajectory_store import FieldTrajectory

        trajectories = [
            FieldTrajectory(
                job_id="job_001",
                domain="greenhouse.io",
                page_index=0,
                field_label="Visa Status",
                field_type="select",
                strategy="pattern_match",
                value_filled="No",
                confidence=0.8,
                time_ms=200,
                corrected=True,
                corrected_value="Graduate Visa",
            ),
            FieldTrajectory(
                job_id="job_001",
                domain="greenhouse.io",
                page_index=0,
                field_label="First Name",
                field_type="text",
                strategy="profile_store",
                value_filled="Test",
                confidence=0.99,
                time_ms=50,
                corrected=False,
            ),
        ]

        heuristics = extract_deterministic_heuristics(trajectories)
        assert len(heuristics) >= 1

        # Find the correction heuristic
        correction_h = [h for h in heuristics if h["source"] == "correction"]
        assert len(correction_h) == 1
        assert "Visa Status" in correction_h[0]["trigger"]
        assert "Graduate Visa" in correction_h[0]["action"]
        assert correction_h[0]["confidence"] == 0.95

    def test_strategy_distribution_heuristics(self):
        """extract_deterministic_heuristics flags unreliable strategies."""
        from jobpulse.strategy_reflector import extract_deterministic_heuristics
        from jobpulse.trajectory_store import FieldTrajectory

        # 4 fields using 'llm_tier3' strategy, 3 of which were corrected
        trajectories = [
            FieldTrajectory(
                job_id="job_002",
                domain="workday.com",
                page_index=0,
                field_label=f"Field_{i}",
                field_type="text",
                strategy="llm_tier3",
                value_filled=f"v_{i}",
                confidence=0.5,
                time_ms=1000,
                corrected=(i < 3),  # 3 out of 4 corrected = 75%
                corrected_value=f"cv_{i}" if i < 3 else "",
            )
            for i in range(4)
        ]

        heuristics = extract_deterministic_heuristics(trajectories)
        dist_h = [h for h in heuristics if h["source"] == "strategy_distribution"]
        assert len(dist_h) >= 1
        assert "llm_tier3" in dist_h[0]["trigger"]
        assert "avoid" in dist_h[0]["action"].lower()

    def test_experience_memory_stores_and_retrieves(self, tmp_path):
        """ExperienceMemory.add() persists to SQLite, retrieve() returns it."""
        db_path = str(tmp_path / "experience_memory.db")
        em = ExperienceMemory(max_size=20, db_path=db_path)

        exp = Experience(
            task_description="job_application:greenhouse.io:greenhouse",
            successful_pattern=(
                "Domain: greenhouse.io | Platform: greenhouse\n"
                "Fields: 10 total, 8 pattern, 1 LLM, 0 corrected\n"
                "Heuristics:\n  - visa field -> use Graduate Visa"
            ),
            score=9.0,
            domain="job_application",
        )
        em.add(exp)

        # Verify in-memory
        assert len(em) == 1

        # Verify SQLite persistence
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM experiences").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["score"] == 9.0
        assert "greenhouse.io" in rows[0]["task_description"]

        # Verify retrieval
        retrieved = em.retrieve("job_application", n=3)
        assert len(retrieved) == 1
        assert retrieved[0].score == 9.0
        assert "greenhouse.io" in retrieved[0].task_description

        em.close()

    def test_experience_memory_evicts_lowest(self, tmp_path):
        """ExperienceMemory evicts lowest-scored entries when exceeding max_size."""
        db_path = str(tmp_path / "experience_memory.db")
        em = ExperienceMemory(max_size=3, db_path=db_path)

        # Add 4 experiences (max_size=3), lowest should be evicted
        for i, score in enumerate([5.0, 9.0, 7.0, 8.5]):
            em.add(Experience(
                task_description=f"task_{i}",
                successful_pattern=f"pattern_{i}",
                score=score,
                domain="test",
            ))

        assert len(em) == 3

        # Verify the lowest-scored (5.0) was evicted
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT score FROM experiences ORDER BY score DESC"
        ).fetchall()
        conn.close()
        scores = [r[0] for r in rows]
        assert 5.0 not in scores
        assert len(scores) == 3

        em.close()

    def test_experience_memory_format_for_prompt(self, tmp_path):
        """ExperienceMemory.format_for_prompt returns injectable context."""
        db_path = str(tmp_path / "experience_memory.db")
        em = ExperienceMemory(max_size=20, db_path=db_path)

        em.add(Experience(
            task_description="job_application:lever.co",
            successful_pattern="Click Apply, fill top-to-bottom, ArrowDown for selects",
            score=8.5,
            domain="job_application",
        ))

        prompt_ctx = em.format_for_prompt("job_application", n=3)
        assert "Learned Patterns" in prompt_ctx
        assert "lever.co" in prompt_ctx
        assert "ArrowDown" in prompt_ctx

        em.close()

    @pytest.mark.slow
    def test_reflect_on_application_deterministic_path(self, tmp_path):
        """reflect_on_application runs deterministic Pass 1 without LLM."""
        from jobpulse.strategy_reflector import reflect_on_application
        from jobpulse.trajectory_store import TrajectoryStore

        ts_db = str(tmp_path / "trajectory.db")
        ts = TrajectoryStore(db_path=ts_db)

        job_id = "test_reflect_001"
        domain = "greenhouse.io"

        # Record field trajectories via log_field (the real API)
        ts.log_field(
            job_id=job_id, domain=domain,
            field_label="Visa Status", strategy="pattern_match",
            value_filled="No", page_index=0, field_type="select",
            confidence=0.8, time_ms=200,
        )
        ts.log_field(
            job_id=job_id, domain=domain,
            field_label="First Name", strategy="profile_store",
            value_filled="Test", page_index=0, field_type="text",
            confidence=0.99, time_ms=50,
        )
        ts.log_field(
            job_id=job_id, domain=domain,
            field_label="Email", strategy="profile_store",
            value_filled="test@example.com", page_index=0, field_type="email",
            confidence=0.99, time_ms=40,
        )

        # Mark Visa Status as corrected (simulates user override)
        ts.mark_corrected(job_id, domain, "Visa Status", "Graduate Visa")

        job_context = {
            "platform": "greenhouse",
            "url": f"https://{domain}/jobs/123",
            "company": "TestCorp",
            "title": "Data Analyst",
        }

        # llm_threshold=999 forces deterministic-only path (no LLM call)
        strategy = reflect_on_application(
            ts, job_id, job_context, llm_threshold=999,
        )

        assert strategy.domain == domain
        assert strategy.fields_total >= 1

        # Verify heuristics were extracted and saved
        heuristics = json.loads(strategy.heuristics)
        assert len(heuristics) >= 1

        # Verify strategy is persisted in DB
        conn = sqlite3.connect(ts_db)
        conn.row_factory = sqlite3.Row
        strat_rows = conn.execute(
            "SELECT * FROM application_strategies WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        conn.close()
        assert len(strat_rows) >= 1


# ======================================================================
# Chain 3: Optimization Signal Flow
# ======================================================================


class TestOptimizationSignalFlow:
    """Verify signal emission, storage, querying, and learning action tracking."""

    def test_emit_stores_signal_in_sqlite(self, tmp_path):
        """OptimizationEngine.emit() writes a signal row to the signals table."""
        db_path = str(tmp_path / "optimization.db")
        engine = OptimizationEngine(db_path=db_path)

        engine.emit(
            signal_type="failure",
            source_loop="form_fill",
            domain="greenhouse.io",
            agent_name="native_form_filler",
            payload={"field": "salary", "error": "readonly"},
            session_id="test_session_001",
        )

        # Verify via direct SQLite query
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM signals").fetchall()
        conn.close()

        assert len(rows) == 1
        row = rows[0]
        assert row["signal_type"] == "failure"
        assert row["source_loop"] == "form_fill"
        assert row["domain"] == "greenhouse.io"
        assert row["agent_name"] == "native_form_filler"
        payload = json.loads(row["payload"])
        assert payload["field"] == "salary"

    def test_signal_bus_query_retrieves_by_domain(self, tmp_path):
        """SignalBus.query() filters signals by domain correctly."""
        db_path = str(tmp_path / "signals.db")
        bus = SignalBus(db_path=db_path)

        # Emit signals for different domains
        from shared.optimization._signals import LearningSignal

        for domain in ["greenhouse.io", "workday.com", "greenhouse.io"]:
            bus.emit(LearningSignal(
                signal_type="correction",
                source_loop="correction_capture",
                domain=domain,
                agent_name="form_filler",
                severity="info",
                payload={"field": "test"},
                session_id=f"sess_{domain}",
            ))

        gh_signals = bus.query(domain="greenhouse.io")
        assert len(gh_signals) == 2

        wd_signals = bus.query(domain="workday.com")
        assert len(wd_signals) == 1

        all_signals = bus.query()
        assert len(all_signals) == 3

    def test_signal_bus_query_by_type(self, tmp_path):
        """SignalBus.query() filters by signal_type."""
        db_path = str(tmp_path / "signals.db")
        bus = SignalBus(db_path=db_path)

        from shared.optimization._signals import LearningSignal

        bus.emit(LearningSignal(
            signal_type="correction",
            source_loop="cc",
            domain="test",
            agent_name="a",
            severity="info",
            payload={},
            session_id="s1",
        ))
        bus.emit(LearningSignal(
            signal_type="failure",
            source_loop="ff",
            domain="test",
            agent_name="a",
            severity="warning",
            payload={},
            session_id="s2",
        ))
        bus.emit(LearningSignal(
            signal_type="success",
            source_loop="sr",
            domain="test",
            agent_name="a",
            severity="info",
            payload={},
            session_id="s3",
        ))

        corrections = bus.query(signal_type="correction")
        assert len(corrections) == 1
        assert corrections[0].signal_type == "correction"

        failures = bus.query(signal_type="failure")
        assert len(failures) == 1

    def test_signal_count(self, tmp_path):
        """SignalBus.count() returns correct totals."""
        db_path = str(tmp_path / "signals.db")
        bus = SignalBus(db_path=db_path)

        from shared.optimization._signals import LearningSignal

        for i in range(5):
            bus.emit(LearningSignal(
                signal_type="success",
                source_loop="test",
                domain="greenhouse.io" if i < 3 else "lever.co",
                agent_name="test",
                severity="info",
                payload={},
                session_id=f"s_{i}",
            ))

        assert bus.count() == 5
        assert bus.count(domain="greenhouse.io") == 3
        assert bus.count(domain="lever.co") == 2

    def test_before_after_learning_action(self, tmp_path):
        """OptimizationEngine tracks before/after metrics for learning actions."""
        db_path = str(tmp_path / "optimization.db")
        engine = OptimizationEngine(db_path=db_path)

        # Record before-state
        action_id = engine.before_learning_action(
            loop_name="correction_capture",
            domain="greenhouse.io",
            metrics={"correction_rate": 0.3, "fields_filled": 10},
        )
        assert action_id  # non-empty UUID

        # Record after-state
        result = engine.after_learning_action(
            action_id,
            metrics={"correction_rate": 0.1, "fields_filled": 12},
        )

        assert "improvement" in result or "regression" in result

        # Verify DB has the row with both before and after
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM learning_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["loop_name"] == "correction_capture"
        assert row["domain"] == "greenhouse.io"
        before = json.loads(row["before_metrics"])
        after = json.loads(row["after_metrics"])
        assert before["correction_rate"] == 0.3
        assert after["correction_rate"] == 0.1

    def test_multiple_signals_from_different_sources(self, tmp_path):
        """Multiple learning loops emit signals; all land in the same DB."""
        db_path = str(tmp_path / "optimization.db")
        engine = OptimizationEngine(db_path=db_path)

        # CorrectionCapture signal
        engine.emit(
            signal_type="correction",
            source_loop="correction_capture",
            domain="greenhouse.io",
            agent_name="form_filler",
            payload={"field": "visa", "old_value": "No", "new_value": "Graduate Visa"},
            session_id="cc_001",
        )

        # Strategy reflector signal
        engine.emit(
            signal_type="success",
            source_loop="strategy_reflector",
            domain="greenhouse.io",
            agent_name="strategy_reflector",
            payload={"heuristics_extracted": 3, "fields_total": 10},
            session_id="sr_001",
        )

        # AgentRulesDB adaptation signal
        engine.emit(
            signal_type="adaptation",
            source_loop="agent_rules",
            domain="visa status",
            agent_name="agent_rules",
            payload={"param": "blocker_avoidance", "old_value": "", "new_value": "sponsorship"},
            session_id="ar_001",
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT signal_type, source_loop FROM signals ORDER BY timestamp"
        ).fetchall()
        conn.close()

        assert len(rows) == 3
        sources = {r["source_loop"] for r in rows}
        assert sources == {"correction_capture", "strategy_reflector", "agent_rules"}

    def test_engine_report(self, tmp_path):
        """OptimizationEngine.get_report() reflects actual signal counts."""
        db_path = str(tmp_path / "optimization.db")
        engine = OptimizationEngine(db_path=db_path)

        for i in range(3):
            engine.emit(
                signal_type="failure",
                source_loop="form_fill",
                domain="workday.com",
                agent_name="filler",
                payload={"attempt": i},
                session_id=f"sess_{i}",
            )

        report = engine.get_report(domain="workday.com")
        assert report["signal_count"] == 3
        assert report["domain"] == "workday.com"

    def test_disabled_engine_is_noop(self, tmp_path, monkeypatch):
        """OptimizationEngine with OPTIMIZATION_ENABLED=false emits nothing."""
        monkeypatch.setenv("OPTIMIZATION_ENABLED", "false")
        db_path = str(tmp_path / "optimization.db")
        engine = OptimizationEngine(db_path=db_path)

        engine.emit(
            signal_type="failure",
            source_loop="test",
            domain="test",
            payload={},
            session_id="s1",
        )

        # No signals table should exist (or should be empty)
        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signals'"
        ).fetchall()
        if tables:
            count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            assert count == 0
        conn.close()


# ======================================================================
# Cross-chain: Correction -> AgentRulesDB -> OptimizationEngine signal
# ======================================================================


class TestCrossChainSignalPropagation:
    """Verify that AgentRulesDB operations emit signals to OptimizationEngine."""

    def test_blocker_rule_emits_adaptation_signal(self, tmp_path, monkeypatch):
        """AgentRulesDB.auto_generate_from_blocker emits to OptimizationEngine."""
        opt_db = str(tmp_path / "optimization.db")
        rules_db = str(tmp_path / "agent_rules.db")

        engine = OptimizationEngine(db_path=opt_db)
        # Redirect the shared engine so AgentRulesDB's import finds our test engine
        import shared.optimization._engine as engine_mod
        original = engine_mod._shared_engine
        engine_mod._shared_engine = engine

        try:
            rules = AgentRulesDB(db_path=rules_db)
            rules.auto_generate_from_blocker(
                category="geo-restriction",
                pattern="US only",
                count=5,
                total=20,
            )
        finally:
            engine_mod._shared_engine = original

        # Verify the adaptation signal landed in the optimization DB
        conn = sqlite3.connect(opt_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signals WHERE source_loop = 'agent_rules'"
        ).fetchall()
        conn.close()

        assert len(rows) >= 1
        assert rows[0]["signal_type"] == "adaptation"
        payload = json.loads(rows[0]["payload"])
        assert payload["param"] == "blocker_avoidance"

    def test_correction_rule_emits_adaptation_signal(self, tmp_path):
        """AgentRulesDB.auto_generate_from_correction emits to OptimizationEngine."""
        opt_db = str(tmp_path / "optimization.db")
        rules_db = str(tmp_path / "agent_rules.db")

        engine = OptimizationEngine(db_path=opt_db)
        import shared.optimization._engine as engine_mod
        original = engine_mod._shared_engine
        engine_mod._shared_engine = engine

        try:
            rules = AgentRulesDB(db_path=rules_db)
            rules.auto_generate_from_correction(
                field_label="Salary",
                agent_value="50000",
                user_value="35000",
                domain="lever.co",
                platform="lever",
            )
        finally:
            engine_mod._shared_engine = original

        conn = sqlite3.connect(opt_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signals WHERE source_loop = 'agent_rules'"
        ).fetchall()
        conn.close()

        assert len(rows) >= 1
        payload = json.loads(rows[0]["payload"])
        assert payload["field"] == "Salary"
        assert payload["old_value"] == "50000"
        assert payload["new_value"] == "35000"
