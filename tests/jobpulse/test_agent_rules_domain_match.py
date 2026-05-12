"""Domain normalization must round-trip between write and read."""
import pytest
from jobpulse.agent_rules import AgentRulesDB, _normalize_domain


class TestNormalizeDomain:
    def test_strips_www(self):
        assert _normalize_domain("www.example.com") == "example.com"

    def test_lowercases(self):
        assert _normalize_domain("WWW.EXAMPLE.COM") == "example.com"

    def test_strips_scheme(self):
        assert _normalize_domain("https://example.com") == "example.com"
        assert _normalize_domain("http://www.example.com") == "example.com"

    def test_strips_path(self):
        assert _normalize_domain("https://example.com/jobs/123") == "example.com"

    def test_handles_empty(self):
        assert _normalize_domain("") == ""
        assert _normalize_domain(None) == ""

    def test_idempotent(self):
        once = _normalize_domain("https://www.Example.com/path")
        twice = _normalize_domain(once)
        assert once == twice == "example.com"


class TestRoundTrip:
    def test_write_then_read_matches(self, tmp_path):
        db = AgentRulesDB(db_path=str(tmp_path / "ar.db"))
        # Write with one canonical-looking form
        db.auto_generate_from_correction(
            field_label="Email",
            agent_value="old@x.com",
            user_value="new@x.com",
            domain="https://www.Greenhouse.io/job/1",
            platform="greenhouse",
        )
        # Read with a different canonical-looking form for the same domain
        overrides = db.get_field_overrides(domain="greenhouse.io")
        assert "Email" in overrides
        assert overrides["Email"]["value"] == "new@x.com"

    def test_times_applied_increments_on_read(self, tmp_path):
        db = AgentRulesDB(db_path=str(tmp_path / "ar.db"))
        db.auto_generate_from_correction(
            field_label="Phone",
            agent_value="",
            user_value="555-1234",
            domain="example.com",
            platform="generic",
        )
        # First read
        first = db.get_field_overrides(domain="EXAMPLE.com")
        assert "Phone" in first
        # Second read
        second = db.get_field_overrides(domain="https://example.com/path")
        assert "Phone" in second
        # Inspect times_applied — should be 2 after two reads
        import sqlite3
        with sqlite3.connect(db._db_path) as conn:
            row = conn.execute(
                "SELECT times_applied FROM agent_rules WHERE category = ?",
                ("Phone",),
            ).fetchone()
        assert row[0] == 2

    def test_migration_normalizes_legacy_rules(self, tmp_path):
        """Existing rules with non-canonical patterns should be normalized in place on init."""
        import sqlite3
        db_path = str(tmp_path / "legacy.db")
        # First, create the schema and insert a rule with a non-canonical pattern
        # by mocking what an older code path would have written.
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE agent_rules (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    action TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    times_applied INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            from datetime import datetime, UTC, timedelta
            now = datetime.now(UTC).isoformat()
            future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            conn.executemany(
                """INSERT INTO agent_rules
                   (rule_type, source, category, pattern, action, value,
                    confidence, sample_count, active, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    ("correction_override", "correction_capture", "Email",
                     "https://www.Example.com/path", "override_answer",
                     "x@y.com", 0.5, 1, 1, now, future),
                    ("correction_override", "user_correction", "Phone",
                     "WWW.GREENHOUSE.io", "override_answer",
                     "555", 0.6, 1, 1, now, future),
                    ("correction_override", "user_feedback", "Skills",
                     "lever.co/jobs", "override_answer",
                     "Python", 0.7, 1, 1, now, future),
                ],
            )

        # Init the AgentRulesDB which should run the normalization migration
        db = AgentRulesDB(db_path=db_path)

        # All three rules should now have normalized patterns
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT category, pattern FROM agent_rules ORDER BY category"
            ).fetchall()
        patterns = dict(rows)
        assert patterns["Email"] == "example.com"
        assert patterns["Phone"] == "greenhouse.io"
        assert patterns["Skills"] == "lever.co"
