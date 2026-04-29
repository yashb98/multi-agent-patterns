"""Tests for per-domain Gate 3 threshold adaptation."""

from __future__ import annotations

import pytest

from jobpulse.gate_threshold_adapter import GateThresholdAdapter, _resolve_family


class TestResolveFamily:
    def test_tech_family(self):
        assert _resolve_family("software_engineer") == "tech"
        assert _resolve_family("devops") == "tech"

    def test_ml_family(self):
        assert _resolve_family("ml_engineer") == "ml_ai"
        assert _resolve_family("data_scientist") == "ml_ai"

    def test_quant_family(self):
        assert _resolve_family("quantitative_trading") == "quant"

    def test_unknown_defaults(self):
        assert _resolve_family("astronaut") == "default"


class TestGateThresholdAdapter:
    def test_default_with_no_data(self, tmp_path):
        adapter = GateThresholdAdapter(db_path=str(tmp_path / "gate.db"))
        assert adapter.get_threshold_for("software_engineer") == 0.65

    def test_default_with_insufficient_samples(self, tmp_path):
        adapter = GateThresholdAdapter(db_path=str(tmp_path / "gate.db"))
        for _ in range(3):
            adapter.record_outcome("software_engineer", 0.7, got_interview=True)
        # 3 < MIN_SAMPLES (5) → still default
        assert adapter.get_threshold_for("software_engineer") == 0.65

    def test_threshold_lowers_for_high_success_domain(self, tmp_path):
        adapter = GateThresholdAdapter(db_path=str(tmp_path / "gate.db"))
        # Domain where even low-quality JDs lead to interviews
        for _ in range(10):
            adapter.record_outcome("quantitative_trading", 0.5, got_interview=True)
        threshold = adapter.get_threshold_for("quantitative_trading")
        # Should learn lower threshold since even 0.5 quality works
        assert threshold < 0.65

    def test_threshold_raises_for_low_success_domain(self, tmp_path):
        adapter = GateThresholdAdapter(db_path=str(tmp_path / "gate.db"))
        # Domain where only very high quality JDs lead to interviews
        for _ in range(10):
            adapter.record_outcome("ux_designer", 0.9, got_interview=True)
            adapter.record_outcome("ux_designer", 0.5, got_interview=False)
        threshold = adapter.get_threshold_for("ux_designer")
        # Should learn threshold above the bad-quality cluster (0.5) but
        # below the good-quality cluster (0.9) — around 0.51
        assert threshold > 0.50
        assert threshold < 0.90

    def test_max_deviation_clamp(self, tmp_path):
        adapter = GateThresholdAdapter(db_path=str(tmp_path / "gate.db"))
        # Extreme case — should be clamped
        for _ in range(20):
            adapter.record_outcome("backend", 0.3, got_interview=True)
        threshold = adapter.get_threshold_for("backend")
        # Default is 0.65, max deviation 0.25 → min 0.40
        assert threshold >= 0.40

    def test_stats(self, tmp_path):
        adapter = GateThresholdAdapter(db_path=str(tmp_path / "gate.db"))
        adapter.record_outcome("ml_engineer", 0.8, got_interview=True)
        adapter.record_outcome("ml_engineer", 0.6, got_interview=False)
        stats = adapter.get_domain_stats("ml_engineer")
        assert stats["family"] == "ml_ai"
        assert stats["samples"] == 2
        assert stats["interviews"] == 1
        assert stats["interview_rate"] == 0.5

    def test_cross_domain_isolation(self, tmp_path):
        adapter = GateThresholdAdapter(db_path=str(tmp_path / "gate.db"))
        for _ in range(10):
            adapter.record_outcome("backend", 0.5, got_interview=True)
        # frontend is in same tech family — shares data
        threshold_backend = adapter.get_threshold_for("backend")
        threshold_frontend = adapter.get_threshold_for("frontend")
        assert threshold_backend == threshold_frontend
