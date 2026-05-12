"""GotchasDB stores per-domain widget patterns."""
import sqlite3


def test_widget_pattern_columns_exist(tmp_path):
    from jobpulse.form_engine.gotchas import GotchasDB
    db = GotchasDB(db_path=str(tmp_path / "g.db"))
    cols = [r[1] for r in sqlite3.connect(db.db_path).execute(
        "PRAGMA table_info(widget_patterns)"
    )]
    for required in ("id", "domain", "label", "selector", "widget_type",
                     "ancestor_classes", "aria_label", "captured_at",
                     "fix_count"):
        assert required in cols, f"missing column: {required}"


def test_record_widget_pattern_inserts_row(tmp_path):
    from jobpulse.form_engine.gotchas import GotchasDB
    db = GotchasDB(db_path=str(tmp_path / "g.db"))
    db.record_widget_pattern(
        domain="welovealfa.com",
        label="Do you require visa sponsorship in the United Kingdom?",
        selector="div[data-q='visa-sponsorship'] button[role='button']",
        widget_type="custom_select",
        ancestor_classes="styles-module-scss-module__visa-q",
        aria_label="",
    )
    rows = list(sqlite3.connect(db.db_path).execute(
        "SELECT label, widget_type, fix_count FROM widget_patterns"
    ))
    assert len(rows) == 1
    assert rows[0][0] == "Do you require visa sponsorship in the United Kingdom?"
    assert rows[0][1] == "custom_select"
    assert rows[0][2] == 1


def test_record_widget_pattern_increments_fix_count_on_duplicate(tmp_path):
    from jobpulse.form_engine.gotchas import GotchasDB
    db = GotchasDB(db_path=str(tmp_path / "g.db"))
    db.record_widget_pattern(
        domain="welovealfa.com", label="Visa?", selector="#visa",
        widget_type="select", ancestor_classes="", aria_label="",
    )
    db.record_widget_pattern(
        domain="welovealfa.com", label="Visa?", selector="#visa",
        widget_type="select", ancestor_classes="", aria_label="",
    )
    rows = list(sqlite3.connect(db.db_path).execute(
        "SELECT fix_count FROM widget_patterns "
        "WHERE domain='welovealfa.com' AND label='Visa?'"
    ))
    assert len(rows) == 1
    assert rows[0][0] == 2


def test_get_widget_patterns_for_domain_returns_list(tmp_path):
    from jobpulse.form_engine.gotchas import GotchasDB
    db = GotchasDB(db_path=str(tmp_path / "g.db"))
    db.record_widget_pattern(
        domain="welovealfa.com", label="A", selector="#a",
        widget_type="text", ancestor_classes="", aria_label="",
    )
    db.record_widget_pattern(
        domain="welovealfa.com", label="B", selector="#b",
        widget_type="select", ancestor_classes="", aria_label="",
    )
    db.record_widget_pattern(
        domain="other.com", label="X", selector="#x",
        widget_type="text", ancestor_classes="", aria_label="",
    )
    patterns = db.get_widget_patterns("welovealfa.com")
    assert len(patterns) == 2
    assert {p["label"] for p in patterns} == {"A", "B"}
