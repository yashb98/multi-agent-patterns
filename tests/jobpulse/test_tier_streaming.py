"""Tests for tier-aware streaming — FieldAnswer metadata validation."""

from __future__ import annotations

from jobpulse.ext_models import FieldAnswer


def test_tier_name_display():
    """FieldAnswer tier names are human-readable."""
    fa = FieldAnswer(answer="Yes", tier=1, confidence=1.0, tier_name="pattern")
    assert fa.tier_name == "pattern"

    fa2 = FieldAnswer(answer="No", tier=3, confidence=0.75, tier_name="nano")
    assert fa2.tier_name == "nano"

    fa3 = FieldAnswer(answer="...", tier=5, confidence=0.6, tier_name="vision")
    assert fa3.tier_name == "vision"


def test_confidence_thresholds():
    """Low-confidence answers are flagged."""
    high = FieldAnswer(answer="Yes", tier=1, confidence=1.0, tier_name="pattern")
    low = FieldAnswer(answer="Maybe", tier=4, confidence=0.3, tier_name="llm")
    assert high.confidence > 0.5
    assert low.confidence <= 0.5


def test_all_tier_names():
    """Every tier has a descriptive name."""
    tiers = {
        1: "pattern",
        2: "semantic_cache",
        3: "nano",
        4: "llm",
        5: "vision",
    }
    for tier_num, name in tiers.items():
        fa = FieldAnswer(answer="test", tier=tier_num, confidence=0.5, tier_name=name)
        assert fa.tier_name == name
        assert fa.tier == tier_num
