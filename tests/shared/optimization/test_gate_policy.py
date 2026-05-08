"""Tests for gate policy threshold adaptation."""

from __future__ import annotations

import pytest

from shared.optimization._gate_policy import GatePolicy, ThresholdSuggestion


class TestGatePolicy:
    def test_insufficient_data_returns_none(self, tmp_path):
        policy = GatePolicy(db_path=str(tmp_path / "apps.db"))
        suggestions = policy.suggest_thresholds("ml_engineer")
        assert suggestions == []

    def test_discover_domains_empty_db(self, tmp_path):
        policy = GatePolicy(db_path=str(tmp_path / "apps.db"))
        domains = policy._discover_domains()
        assert domains == []

    def test_format_report_empty(self):
        policy = GatePolicy()
        report = policy.format_report([])
        assert "No threshold adjustments" in report

    def test_format_report_with_suggestions(self):
        policy = GatePolicy()
        suggestions = [
            ThresholdSuggestion(
                gate_name="gate3_competitiveness",
                current_value=75,
                suggested_value=70,
                domain="ml_engineer",
                evidence="30% of rejected jobs got interviews",
                confidence=0.7,
                sample_size=25,
            )
        ]
        report = policy.format_report(suggestions)
        assert "gate3_competitiveness" in report
        assert "ml_engineer" in report
        assert "70" in report

    def test_module_does_not_import_from_jobpulse(self):
        """S10 audit M-B: shared/ MUST NOT depend on jobpulse/ (Principle 1).
        DATA_DIR was previously sourced from jobpulse.config; verify the
        module now resolves it through shared.paths."""
        import shared.optimization._gate_policy as gp
        from shared.paths import DATA_DIR as SHARED_DATA_DIR

        # The module-level _DEFAULT_DB must be derived from shared.paths.
        assert gp._DEFAULT_DB.startswith(str(SHARED_DATA_DIR))
        # And there must not be a jobpulse symbol leaking via the module
        # globals — tightens the rule against re-introducing the import.
        assert "jobpulse" not in repr(gp.__dict__.get("DATA_DIR", ""))
