"""Tests for GotchasDB engine tagging."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from jobpulse.form_engine.gotchas import GotchasDB


def test_store_and_lookup_with_engine(tmp_path):
    db = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    db.store("greenhouse.io", "#email", "fill fails", "use type()", engine="extension")
    db.store("greenhouse.io", "#email", "fill fails", "use el.fill()", engine="playwright")

    ext = db.lookup("greenhouse.io", "#email", engine="extension")
    pw = db.lookup("greenhouse.io", "#email", engine="playwright")

    assert ext is not None
    assert ext["solution"] == "use type()"
    assert pw is not None
    assert pw["solution"] == "use el.fill()"


def test_lookup_domain_filters_by_engine(tmp_path):
    db = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    db.store("lever.co", "#name", "p1", "s1", engine="extension")
    db.store("lever.co", "#phone", "p2", "s2", engine="extension")
    db.store("lever.co", "#name", "p1", "s3", engine="playwright")

    ext = db.lookup_domain("lever.co", engine="extension")
    pw = db.lookup_domain("lever.co", engine="playwright")

    assert len(ext) == 2
    assert len(pw) == 1


def test_record_usage_per_engine(tmp_path):
    db = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    db.store("d.com", "#f", "p", "s", engine="extension")
    db.store("d.com", "#f", "p", "s2", engine="playwright")

    db.record_usage("d.com", "#f", engine="extension")
    db.record_usage("d.com", "#f", engine="extension")

    ext = db.lookup("d.com", "#f", engine="extension")
    pw = db.lookup("d.com", "#f", engine="playwright")
    assert ext["times_used"] == 2
    assert pw["times_used"] == 0


def test_default_engine_is_extension(tmp_path):
    db = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    db.store("d.com", "#x", "problem", "solution")
    result = db.lookup("d.com", "#x")
    assert result is not None
    assert result["engine"] == "extension"


def test_store_upsert_same_engine(tmp_path):
    db = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    db.store("d.com", "#x", "old problem", "old solution", engine="playwright")
    db.store("d.com", "#x", "new problem", "new solution", engine="playwright")
    result = db.lookup("d.com", "#x", engine="playwright")
    assert result["problem"] == "new problem"
    assert result["solution"] == "new solution"
