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
