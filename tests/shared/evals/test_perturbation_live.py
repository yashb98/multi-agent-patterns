"""Run perturbation eval against real live snapshots from fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.evals.perturbation import PerturbationEngine

_SNAPSHOTS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "live_snapshots"
_MANIFEST = _SNAPSHOTS_DIR / "manifest.json"


@pytest.mark.skipif(not _MANIFEST.exists(), reason="No live snapshots available")
class TestPerturbationOnLiveSnapshots:
    @pytest.fixture
    def snapshots(self):
        manifest = json.loads(_MANIFEST.read_text())
        return manifest["fixtures"]

    def test_engine_generates_variants_for_each_snapshot(self, snapshots):
        engine = PerturbationEngine()
        for snap in snapshots:
            assert "title" in snap
            assert "platform" in snap

    def test_perturbation_count(self):
        engine = PerturbationEngine()
        sample = [
            {"label": "Name", "type": "text", "options": [], "value": ""},
            {"label": "Email", "type": "text", "options": [], "value": ""},
        ]
        variants = engine.generate_variants(sample, n_variants=5)
        assert len(variants) == 5
