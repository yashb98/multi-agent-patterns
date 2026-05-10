"""End-to-end wiring test for db_observability across the apply pipeline.

The relocation drop that motivated Item 14 is reproduced here:
``profile_store.screening_default('relocation')`` returns
``'Yes, within the UK'`` but the form has options ``['Yes', 'No']``,
so ``_align_screening_to_options`` rewrites it to ``'Yes'`` — and the
underlying profile-store lookup must show up as ``status='dropped'``
with reason ``option_misalignment`` in the observability DB.
"""

from __future__ import annotations

import sqlite3

import pytest

from shared import db_observability as obs


@pytest.fixture(autouse=True)
def _isolated_observability_db(tmp_path, monkeypatch):
    db = tmp_path / "obs.db"
    monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
    obs.set_test_mode(False)
    obs.set_observability_db_path(db)
    obs.flush_all()
    yield db
    obs.flush_all()
    obs.set_test_mode(None)


def _rows(db_path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM lookups ORDER BY id"))
    finally:
        conn.close()


def test_profile_store_relocation_drop_is_observed(
    tmp_path, monkeypatch, _isolated_observability_db,
):
    """The exact relocation scenario from the plan: a wrong-shape stored
    default gets aligned to a Yes/No option, and that lookup is logged
    as dropped with reason ``option_misalignment``."""

    profile_db = tmp_path / "user_profile.db"
    key_path = tmp_path / ".profile_key"
    monkeypatch.setattr(
        "shared.profile_store._DEFAULT_DB_PATH", profile_db,
    )
    monkeypatch.setattr(
        "shared.profile_store._DEFAULT_KEY_PATH", key_path,
    )

    from shared.profile_store import ProfileStore
    store = ProfileStore(db_path=profile_db, key_path=key_path)
    store.set_screening_default("relocation", "Yes, within the UK")

    assert store.screening_default("relocation") == "Yes, within the UK"

    from jobpulse.native_form_filler import _align_screening_to_options
    field = {
        "label": "Are you willing to relocate to the office in London?",
        "type": "select",
        "options": ["Yes", "No"],
    }
    aligned = _align_screening_to_options(
        store.screening_default("relocation"), field, label_for_log=field["label"],
    )
    # Whichever way the aligner resolves it (empty string when no mapping
    # exists, or "Yes" when the alias table has UK→Yes), the underlying
    # profile-store lookup is a drop because the stored value was
    # rewritten/discarded before reaching the form fill.
    assert aligned != "Yes, within the UK", (
        "OptionAligner should reject the wrong-shape stored default"
    )

    obs.flush_all()
    rows = _rows(_isolated_observability_db)

    relocation_rows = [
        r for r in rows
        if r["db_name"] == "user_profile"
        and r["table_name"] == "screening_defaults"
        and "Yes, within the UK" in (r["value_repr"] or "")
    ]
    assert relocation_rows, (
        f"Expected at least one screening_defaults lookup with value "
        f"'Yes, within the UK'. Got: {[dict(r) for r in rows]}"
    )

    dropped = [r for r in relocation_rows if r["status"] == "dropped"]
    assert dropped, (
        "Expected the wrong-shape relocation lookup to be marked dropped, "
        f"but got statuses: {[r['status'] for r in relocation_rows]}"
    )
    assert dropped[0]["drop_reason"] == "option_misalignment"
    assert dropped[0]["intended"] == "Yes, within the UK"
    # actual is "" when the aligner couldn't fit the value, or "Yes" when
    # the alias map covers UK→Yes — both count as a drop.
    assert dropped[0]["actual"] in ("", "Yes"), (
        f"unexpected actual={dropped[0]['actual']!r}"
    )


def test_aligned_unchanged_value_is_consumed(
    tmp_path, monkeypatch, _isolated_observability_db,
):
    """If the stored default fits the options as-is, the lookup is
    marked consumed, not dropped."""

    profile_db = tmp_path / "user_profile2.db"
    key_path = tmp_path / ".profile_key2"
    monkeypatch.setattr(
        "shared.profile_store._DEFAULT_DB_PATH", profile_db,
    )
    monkeypatch.setattr(
        "shared.profile_store._DEFAULT_KEY_PATH", key_path,
    )

    from shared.profile_store import ProfileStore
    store = ProfileStore(db_path=profile_db, key_path=key_path)
    store.set_screening_default("disability_status", "No")

    from jobpulse.native_form_filler import _align_screening_to_options
    field = {
        "label": "Disability Status",
        "type": "select",
        "options": ["Yes", "No", "I do not wish to answer"],
    }
    aligned = _align_screening_to_options(
        store.screening_default("disability_status"), field,
    )
    assert aligned == "No"

    obs.flush_all()
    rows = _rows(_isolated_observability_db)
    matching = [
        r for r in rows
        if r["db_name"] == "user_profile"
        and r["table_name"] == "screening_defaults"
        and "'No'" in (r["value_repr"] or "")
    ]
    assert matching, "Expected at least one matching consumed lookup"
    assert any(r["status"] == "consumed" for r in matching), (
        "Expected at least one consumed lookup for an aligned-unchanged value"
    )
