"""Tests for PlatformTransferEngine."""
from __future__ import annotations

import json
import sqlite3

import pytest

from jobpulse.form_experience_db import FormExperienceDB
from jobpulse.platform_transfer import PlatformTransferEngine


class TestSchema:
    def test_creates_tables(self, tmp_path):
        db = str(tmp_path / "transfer.db")
        PlatformTransferEngine(db_path=db)
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "platform_similarity" in tables
        assert "transfer_outcomes" in tables
        conn.close()

    def test_idempotent_init(self, tmp_path):
        db = str(tmp_path / "transfer.db")
        PlatformTransferEngine(db_path=db)
        PlatformTransferEngine(db_path=db)  # No error on second init


class TestSimilarityMetrics:
    def test_cosine_similarity_identical(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        vec_a = {"text": 3, "email": 1, "tel": 1}
        vec_b = {"text": 3, "email": 1, "tel": 1}
        assert engine._cosine_similarity(vec_a, vec_b) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        vec_a = {"text": 1}
        vec_b = {"email": 1}
        assert engine._cosine_similarity(vec_a, vec_b) == pytest.approx(0.0)

    def test_cosine_similarity_partial_overlap(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        vec_a = {"text": 3, "email": 1}
        vec_b = {"text": 2, "tel": 1}
        result = engine._cosine_similarity(vec_a, vec_b)
        assert 0.0 < result < 1.0

    def test_cosine_similarity_empty(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._cosine_similarity({}, {}) == 0.0

    def test_jaccard_index(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._jaccard_index({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)

    def test_jaccard_index_identical(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._jaccard_index({"a", "b"}, {"a", "b"}) == pytest.approx(1.0)

    def test_jaccard_index_empty(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._jaccard_index(set(), set()) == 0.0

    def test_normalized_page_diff(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._normalized_page_diff(3, 3) == pytest.approx(1.0)
        assert engine._normalized_page_diff(3, 5) == pytest.approx(0.6)
        assert engine._normalized_page_diff(0, 0) == 0.0

    def test_normalized_levenshtein(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        seq_a = ["login", "fill_form", "submit"]
        seq_b = ["login", "fill_form", "submit"]
        assert engine._normalized_levenshtein(seq_a, seq_b) == pytest.approx(1.0)

    def test_normalized_levenshtein_different(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        seq_a = ["login", "fill_form", "submit"]
        seq_b = ["login", "review", "submit"]
        result = engine._normalized_levenshtein(seq_a, seq_b)
        assert 0.0 < result < 1.0

    def test_normalized_levenshtein_empty(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._normalized_levenshtein([], []) == 0.0

    def test_token_overlap(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._token_overlap("#app .form-container", "#app .form-wrapper") > 0.0
        assert engine._token_overlap("#app .form", "#app .form") == pytest.approx(1.0)
