"""semantic_decisions helper module — audit-slice S3 (dimension H1).

Pre-S3 every PASS in ``docs/audits/2026-05-10-semantic-audit-verified.md``
relied on log mining (``grep`` over ``logs/live_e2e/run_final_*.log``).
The audit's H1 dimension calls this out explicitly as a global GAP
(see TP-9 caveat: "only wraps DB lookups, not LLM decisions or
option-alignment decisions. A separate ``data/semantic_decisions.db``
does NOT exist").

These tests cover the helper module in isolation. Wiring tests for
the three call sites that consume the API live alongside their
respective screening tests.
"""

import os
import time

import pytest

import shared.semantic_decisions as sd
from shared.semantic_decisions import (
    Decision,
    record_decision,
    query_decisions,
    set_decisions_db_path,
    set_test_mode,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Each test gets its own SQLite file under tmp_path, and test-mode
    is forced OFF so writes actually land (the env-var test-mode is
    designed for production unit tests that should NOT pollute the
    real DB; here we explicitly want the writer)."""
    # Reset internal schema flag before each test (set_decisions_db_path
    # already does this via the global; explicit here for clarity).
    sd._schema_initialised = False
    set_test_mode(False)
    set_decisions_db_path(str(tmp_path / "sd.db"))
    yield
    # Reset to default behaviour for the next test
    set_test_mode(None)


# ── Schema + basic write ─────────────────────────────────────────────


class TestSchema:
    def test_record_decision_writes_a_row(self, tmp_path):
        row_id = record_decision(
            agent_name="screening_pipeline",
            call_site="_llm_answer:free_text",
            decision_type="llm_call",
            mechanism="llm",
            tier_reached="cognitive_l1",
            input_value="Will you require visa sponsorship?",
            output_value="No, I have Graduate Visa.",
            confidence=0.85,
            profile_state_hash="profhash12345678",
            jd_context_hash="jdhash9abcdef01",
            field_label="visa_sponsorship",
            elapsed_ms=147.0,
        )
        assert row_id > 0

    def test_query_returns_written_decision(self):
        record_decision(
            agent_name="OptionAligner",
            call_site="align_answer",
            decision_type="option_align",
            mechanism="embedding",
            tier_reached="embedding_similarity",
            input_value="No",
            output_value="No, I do not have a disability...",
            confidence=0.53,
        )
        results = query_decisions(agent_name="OptionAligner")
        assert len(results) == 1
        d = results[0]
        assert isinstance(d, Decision)
        assert d.call_site == "align_answer"
        assert d.decision_type == "option_align"
        assert d.mechanism == "embedding"
        assert d.confidence == pytest.approx(0.53)

    def test_input_hash_stable_across_records(self):
        """Same input → same hash. Enables replay: 'find every decision
        for input X across all profile/JD contexts'."""
        record_decision(
            agent_name="a", call_site="x", decision_type="llm_call",
            mechanism="llm", tier_reached="t", input_value="hello world",
        )
        record_decision(
            agent_name="b", call_site="y", decision_type="option_align",
            mechanism="embedding", tier_reached="t", input_value="hello world",
        )
        rows = query_decisions(limit=10)
        hashes = {r.input_hash for r in rows}
        assert len(hashes) == 1, (
            f"Same input should produce one hash, got {hashes}"
        )

    def test_repr_truncation(self):
        """A 5000-char input must not write 5000 chars to the DB."""
        long_input = "x" * 5000
        record_decision(
            agent_name="a", call_site="x", decision_type="llm_call",
            mechanism="llm", tier_reached="t", input_value=long_input,
        )
        rows = query_decisions()
        assert len(rows[0].input_repr) <= 310  # 300 + repr quotes + ...


class TestQueryFilters:
    def _seed(self):
        record_decision(
            agent_name="screening_pipeline", call_site="_llm_answer",
            decision_type="llm_call", mechanism="llm",
            tier_reached="cognitive", input_value="q1",
            profile_state_hash="p1", jd_context_hash="j1",
        )
        record_decision(
            agent_name="screening_pipeline", call_site="_llm_answer",
            decision_type="llm_call", mechanism="llm",
            tier_reached="cognitive", input_value="q2",
            profile_state_hash="p2", jd_context_hash="j1",
        )
        record_decision(
            agent_name="OptionAligner", call_site="align_answer",
            decision_type="option_align", mechanism="embedding",
            tier_reached="emb_sim", input_value="No",
        )

    def test_filter_by_agent_name(self):
        self._seed()
        rows = query_decisions(agent_name="OptionAligner")
        assert len(rows) == 1
        assert rows[0].agent_name == "OptionAligner"

    def test_filter_by_decision_type(self):
        self._seed()
        rows = query_decisions(decision_type="llm_call")
        assert len(rows) == 2
        assert all(r.decision_type == "llm_call" for r in rows)

    def test_filter_by_profile_jd_hashes(self):
        """SG1 audit replay: find every decision for a given (profile, JD)
        pair to verify per-context correctness."""
        self._seed()
        rows = query_decisions(profile_state_hash="p1", jd_context_hash="j1")
        assert len(rows) == 1
        assert rows[0].input_repr == "'q1'"


class TestEnumValidation:
    def test_unknown_decision_type_warns_but_stores(self, caplog):
        """Unknown enum values must produce a WARN log line so a
        reviewer notices the new vocabulary but the write still
        succeeds — audit log integrity over enum strictness."""
        import logging
        with caplog.at_level(logging.WARNING):
            row_id = record_decision(
                agent_name="a", call_site="x",
                decision_type="brand_new_type",
                mechanism="llm", tier_reached="t",
                input_value="hi",
            )
        assert row_id > 0
        assert any("decision_type" in r.message for r in caplog.records)

    def test_unknown_mechanism_warns_but_stores(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            row_id = record_decision(
                agent_name="a", call_site="x",
                decision_type="llm_call",
                mechanism="brand_new_mechanism",
                tier_reached="t",
                input_value="hi",
            )
        assert row_id > 0
        assert any("mechanism" in r.message for r in caplog.records)


class TestTestModeShortCircuit:
    def test_test_mode_blocks_writes(self, tmp_path):
        """JOBPULSE_TEST_MODE=1 must short-circuit writes so unit tests
        don't pollute the production DB. The conftest already sets it
        for the test suite; this test asserts the contract directly."""
        sd._schema_initialised = False
        set_decisions_db_path(str(tmp_path / "tm.db"))
        set_test_mode(True)
        row_id = record_decision(
            agent_name="a", call_site="x", decision_type="llm_call",
            mechanism="llm", tier_reached="t", input_value="hi",
        )
        assert row_id == -1
        # Query also short-circuits
        assert query_decisions(agent_name="a") == []
        set_test_mode(False)


class TestPipelineNeverBreaks:
    """semantic_decisions writes are best-effort — failure must never
    break the apply pipeline. The wrapped call must return normally
    even when SQLite is unwritable."""

    def test_write_failure_returns_minus_one_does_not_raise(self, tmp_path):
        """Point at an unwritable path (file exists as a directory) and
        confirm the function logs at debug and returns -1, doesn't
        propagate the exception to the caller."""
        # Make the "DB" actually be a directory — SQLite open will fail.
        bad = tmp_path / "bad.db"
        bad.mkdir()
        sd._schema_initialised = False
        sd._db_path = bad  # bypass set_decisions_db_path's mkdir
        set_test_mode(False)
        row_id = record_decision(
            agent_name="a", call_site="x", decision_type="llm_call",
            mechanism="llm", tier_reached="t", input_value="hi",
        )
        assert row_id == -1  # no exception propagated
