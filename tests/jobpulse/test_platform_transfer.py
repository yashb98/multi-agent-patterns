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


class TestSimilarityMatrix:
    def _seed_two_domains(self, db_path: str) -> None:
        exp_db = FormExperienceDB(db_path=db_path)
        exp_db.record(domain="boards.greenhouse.io/acme", platform="greenhouse", adapter="native",
            pages_filled=3, field_types=["text", "email", "tel", "file"], screening_questions=[], time_seconds=45.0, success=True)
        exp_db.store_timing("boards.greenhouse.io/acme", hydration_ms=800, fill_ms=2000, transition_ms=500)
        exp_db.store_container("boards.greenhouse.io/acme", "#application")
        exp_db.record_fill_technique("boards.greenhouse.io/acme", "email", "email", "type_text")
        exp_db.record_fill_technique("boards.greenhouse.io/acme", "phone", "tel", "type_text")

        exp_db.record(domain="boards.greenhouse.io/beta", platform="greenhouse", adapter="native",
            pages_filled=3, field_types=["text", "email", "tel", "file", "select"], screening_questions=[], time_seconds=50.0, success=True)
        exp_db.store_timing("boards.greenhouse.io/beta", hydration_ms=900, fill_ms=2200, transition_ms=600)
        exp_db.store_container("boards.greenhouse.io/beta", "#application .form")
        exp_db.record_fill_technique("boards.greenhouse.io/beta", "email", "email", "type_text")
        exp_db.record_fill_technique("boards.greenhouse.io/beta", "salary", "text", "select_option")

    def test_recompute_populates_similarity(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        self._seed_two_domains(db)
        engine = PlatformTransferEngine(db_path=db)
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM platform_similarity").fetchall()
        conn.close()
        assert len(rows) > 0

    def test_recompute_stores_all_signal_types(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        self._seed_two_domains(db)
        engine = PlatformTransferEngine(db_path=db)
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")
        conn = sqlite3.connect(db)
        signal_types = {r[0] for r in conn.execute("SELECT DISTINCT signal_type FROM platform_similarity").fetchall()}
        conn.close()
        assert len(signal_types) >= 5

    def test_recompute_incremental_only_new_domain(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        self._seed_two_domains(db)
        engine = PlatformTransferEngine(db_path=db)
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")
        conn = sqlite3.connect(db)
        count_before = conn.execute("SELECT COUNT(*) FROM platform_similarity").fetchone()[0]
        conn.close()
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")
        conn = sqlite3.connect(db)
        count_after = conn.execute("SELECT COUNT(*) FROM platform_similarity").fetchone()[0]
        conn.close()
        assert count_after == count_before

    def test_similarity_values_in_range(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        self._seed_two_domains(db)
        engine = PlatformTransferEngine(db_path=db)
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT similarity FROM platform_similarity").fetchall()
        conn.close()
        for (sim,) in rows:
            assert 0.0 <= sim <= 1.0


class TestThompsonSampling:
    def _seed_similarity_and_outcomes(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        now = "2026-04-29T00:00:00+00:00"
        conn.execute("INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "acme.greenhouse.io", "timing_profile", 0.9, 5, now))
        conn.execute("INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "beta.greenhouse.io", "timing_profile", 0.5, 3, now))
        conn.execute(
            "INSERT INTO transfer_outcomes (target_domain, donor_domain, signal_type, alpha, beta_param, transfer_count, success_count, last_outcome, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "acme.greenhouse.io", "timing_profile", 10.0, 2.0, 12, 10, "success", now, now))
        conn.commit()
        conn.close()

    def test_get_transfer_data_selects_donor(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        self._seed_similarity_and_outcomes(db)
        result = engine.get_transfer_data("new.greenhouse.io", "timing_profile")
        assert result is not None
        assert result["donor_domain"] in ("acme.greenhouse.io", "beta.greenhouse.io")
        assert result["signal_type"] == "timing_profile"
        assert result["_transfer"] is True
        assert 0.0 < result["similarity"] <= 1.0

    def test_get_transfer_data_prefers_strong_donor(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        self._seed_similarity_and_outcomes(db)
        acme_count = 0
        for _ in range(100):
            result = engine.get_transfer_data("new.greenhouse.io", "timing_profile")
            if result and result["donor_domain"] == "acme.greenhouse.io":
                acme_count += 1
        assert acme_count > 60

    def test_get_transfer_data_returns_none_below_threshold(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        conn = sqlite3.connect(db)
        now = "2026-04-29T00:00:00+00:00"
        conn.execute("INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("target.io", "donor.io", "timing_profile", 0.1, 2, now))
        conn.commit()
        conn.close()
        result = engine.get_transfer_data("target.io", "timing_profile", min_similarity=0.3)
        assert result is None

    def test_get_transfer_data_no_donors(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        result = engine.get_transfer_data("unknown.io", "timing_profile")
        assert result is None

    def test_record_outcome_creates_entry(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        engine.record_outcome("target.io", "donor.io", "timing_profile", success=True)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT alpha, beta_param, transfer_count, success_count, last_outcome FROM transfer_outcomes WHERE target_domain = ? AND donor_domain = ? AND signal_type = ?",
            ("target.io", "donor.io", "timing_profile")).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 2.0   # α = 1 + 1
        assert row[1] == 1.0   # β = 1
        assert row[2] == 1
        assert row[3] == 1
        assert row[4] == "success"

    def test_record_outcome_updates_existing(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        engine.record_outcome("t.io", "d.io", "field_types", success=True)
        engine.record_outcome("t.io", "d.io", "field_types", success=False)
        engine.record_outcome("t.io", "d.io", "field_types", success=True)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT alpha, beta_param, transfer_count, success_count FROM transfer_outcomes WHERE target_domain = 't.io' AND donor_domain = 'd.io'").fetchone()
        conn.close()
        assert row[0] == 3.0   # α = 1 + 2
        assert row[1] == 2.0   # β = 1 + 1
        assert row[2] == 3
        assert row[3] == 2

    def test_record_outcome_emits_optimization_signal(self, tmp_path, monkeypatch):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        emitted = []
        class FakeEngine:
            def emit(self, **kwargs):
                emitted.append(kwargs)
        monkeypatch.setattr("jobpulse.platform_transfer.get_optimization_engine", lambda: FakeEngine())
        engine.record_outcome("t.io", "d.io", "timing_profile", success=True)
        assert len(emitted) == 1
        assert emitted[0]["signal_type"] == "transfer"
