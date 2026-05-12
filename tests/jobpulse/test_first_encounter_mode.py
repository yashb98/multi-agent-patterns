"""First-encounter mode forces dry_run=True for never-seen domains."""
from unittest.mock import patch, MagicMock
import pytest


class TestFirstEncounterMode:
    def test_helper_function_exists(self):
        """is_first_encounter must be importable from applicator."""
        from jobpulse.applicator import is_first_encounter
        assert callable(is_first_encounter)

    def test_known_domain_returns_false(self, monkeypatch):
        from jobpulse.applicator import is_first_encounter

        fake_db = MagicMock()
        fake_db.lookup = MagicMock(return_value={"adapter": "greenhouse", "apply_count": 5})

        with patch("jobpulse.form_experience_db.FormExperienceDB", return_value=fake_db):
            assert is_first_encounter("https://www.greenhouse.io/jobs/1") is False

    def test_unknown_domain_returns_true(self, monkeypatch):
        from jobpulse.applicator import is_first_encounter

        fake_db = MagicMock()
        fake_db.lookup = MagicMock(return_value=None)

        with patch("jobpulse.form_experience_db.FormExperienceDB", return_value=fake_db):
            assert is_first_encounter("https://newcorp.example.com/apply") is True

    def test_db_error_returns_true(self, monkeypatch):
        """If FormExperienceDB lookup raises, treat as first encounter (safer)."""
        from jobpulse.applicator import is_first_encounter

        fake_db = MagicMock()
        fake_db.lookup = MagicMock(side_effect=RuntimeError("db unavailable"))

        with patch("jobpulse.form_experience_db.FormExperienceDB", return_value=fake_db):
            assert is_first_encounter("https://example.com/job/1") is True

    def test_empty_url_returns_true(self):
        """Empty URL is treated as first encounter (defensive)."""
        from jobpulse.applicator import is_first_encounter
        assert is_first_encounter("") is True
        assert is_first_encounter(None) is True
