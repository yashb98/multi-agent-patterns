"""Tests for DomainClassifier Pass 1 (embedding similarity)."""

import json
from pathlib import Path
import pytest

from research_journal.domain_filter import DomainClassifier
from jobpulse.papers.models import Paper

FIX = Path(__file__).parent.parent.parent / "research_journal" / "anchors"


@pytest.fixture
def classifier(monkeypatch):
    # Forward hook for future caching — env var unused until Task 9
    monkeypatch.setenv("JOURNAL_DOMAIN_CACHE", "0")
    return DomainClassifier(anchors_path=FIX / "anchor_sets.json")


def _paper(title: str, abstract: str = "test abstract") -> Paper:
    return Paper(
        arxiv_id="0000.00000", title=title, authors=["X"],
        abstract=abstract, categories=["cs.CL"],
        pdf_url="", arxiv_url="", published_at="2026-01-01",
    )


def test_loads_anchors_from_fixture(classifier):
    assert len(classifier.anchors_core) == 25
    assert len(classifier.anchors_tangent) == 10
    assert len(classifier.anchors_out) == 10


def test_pass1_obvious_core(classifier, monkeypatch):
    # Fake is anchor-aware: returns high sim only when BOTH text AND anchors match.
    # Without anchor-awareness all three sim_core/sim_out/sim_tangent would tie at 0.85,
    # and the strict sim_core > sim_out branch would never fire.
    def fake_max_cosine(text, anchors):
        is_core_anchor = any("RLHF" in a or "DPO" in a for a in anchors)
        if is_core_anchor and ("RLHF" in text or "DPO" in text):
            return 0.85
        return 0.10
    monkeypatch.setattr(classifier, "_max_cosine", fake_max_cosine)
    tag, conf, reason = classifier._pass1(_paper("DPO for LLM alignment"))
    assert tag == "core"
    assert conf >= 0.65


def test_pass1_obvious_out(classifier, monkeypatch):
    # Anchor-aware: only out anchors contain "molecular dynamics simulation"
    def fake_max_cosine(text, anchors):
        is_out_anchor = any("molecular" in a.lower() for a in anchors)
        if is_out_anchor and "molecular" in text.lower():
            return 0.85
        return 0.10
    monkeypatch.setattr(classifier, "_max_cosine", fake_max_cosine)
    tag, conf, reason = classifier._pass1(_paper("Molecular dynamics simulation"))
    assert tag == "out"


def test_pass1_borderline_returns_none(classifier, monkeypatch):
    """Confidence between 0.55 and 0.65 falls through to Pass 2."""
    monkeypatch.setattr(classifier, "_max_cosine", lambda t, a: 0.60)
    tag, conf, reason = classifier._pass1(_paper("borderline"))
    assert tag is None


def test_max_cosine_pair_uses_real_embedder(monkeypatch, classifier):
    """Verify _max_cosine_pair calls embed_text and computes cosine correctly.

    Patches embed_text to return controlled vectors instead of monkeypatching
    the higher-level _max_cosine method.
    """
    import numpy as np

    def fake_embed(text):
        if "alpha" in text:
            return [1.0, 0.0, 0.0]
        if "beta" in text:
            return [1.0, 0.0, 0.0]   # identical → cos=1.0
        return [0.0, 1.0, 0.0]       # orthogonal → cos=0.0

    monkeypatch.setattr("shared.memory_layer._embedder.embed_text", fake_embed)
    score = classifier._max_cosine_pair("alpha thing", "beta thing")
    assert score > 0.99   # unit vectors aligned

    score_orth = classifier._max_cosine_pair("alpha thing", "gamma thing")
    assert abs(score_orth) < 0.01   # orthogonal
