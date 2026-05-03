"""Real-data tests for NavigationLearner — SQLite operations via tmp_path.

No mocks. Every test uses a real SQLite database and verifies actual DB state.
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from jobpulse.navigation_learner import NavigationLearner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Return a fresh SQLite DB path for NavigationLearner."""
    return str(tmp_path / "nav_learning.db")


@pytest.fixture
def transfer_db_path(tmp_path):
    """Return an isolated SQLite DB path for PlatformTransferEngine."""
    return str(tmp_path / "transfer.db")


@pytest.fixture
def learner(db_path, transfer_db_path):
    """NavigationLearner with both its own DB and transfer DB isolated to tmp_path."""
    nl = NavigationLearner(db_path=db_path)
    nl._transfer_db_path = transfer_db_path
    return nl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GREENHOUSE_STEPS = [
    {"page_type": "job_description", "action": "click_apply", "selector": "#apply-btn"},
    {"page_type": "login_form", "action": "fill_login", "selector": "#signin"},
    {"page_type": "application_form", "action": "fill_form", "selector": "#app-form"},
]

LEVER_STEPS = [
    {"page_type": "job_description", "action": "click_apply", "selector": ".apply-button"},
    {"page_type": "application_form", "action": "fill_form", "selector": ".application"},
]

SHORT_STEP = [{"page_type": "job_description", "action": "click_apply"}]


def _query_all_rows(db_path: str) -> list[dict]:
    """Query all rows from the sequences table as dicts."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM sequences").fetchall()
    return [dict(r) for r in rows]


def _query_domain(db_path: str, domain: str) -> dict | None:
    """Query a single domain row."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sequences WHERE domain = ?", (domain,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Full lifecycle: record -> query -> replay
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_record_then_retrieve(self, learner, db_path):
        """Save a sequence, retrieve it via API, and verify the DB row."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)

        result = learner.get_sequence("greenhouse.io")
        assert result is not None
        assert len(result) == 3
        assert result[0]["action"] == "click_apply"
        assert result[2]["action"] == "fill_form"

        row = _query_domain(db_path, "greenhouse.io")
        assert row is not None
        assert row["success"] == 1
        assert json.loads(row["steps"]) == GREENHOUSE_STEPS
        assert row["replay_count"] == 0
        assert row["fail_count"] == 0

    def test_replay_increments_counter(self, learner, db_path):
        """increment_replay updates the replay_count in the DB."""
        learner.save_sequence("lever.co", LEVER_STEPS, success=True)

        learner.increment_replay("lever.co")
        learner.increment_replay("lever.co")
        learner.increment_replay("lever.co")

        row = _query_domain(db_path, "lever.co")
        assert row["replay_count"] == 3

    def test_update_overwrites_previous(self, learner, db_path):
        """Saving a new sequence for the same domain replaces the old one."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)
        learner.save_sequence("greenhouse.io", LEVER_STEPS, success=True)

        row = _query_domain(db_path, "greenhouse.io")
        stored_steps = json.loads(row["steps"])
        assert len(stored_steps) == 2
        assert stored_steps[0]["selector"] == ".apply-button"

    def test_save_with_platform_and_content_hash(self, learner, db_path):
        """Platform and content_hash columns are stored correctly."""
        learner.save_sequence(
            "boards.greenhouse.io",
            GREENHOUSE_STEPS,
            success=True,
            platform="greenhouse",
            content_hash="abc123def",
        )

        row = _query_domain(db_path, "boards.greenhouse.io")
        assert row["platform"] == "greenhouse"
        assert row["content_hash"] == "abc123def"


# ---------------------------------------------------------------------------
# Domain isolation
# ---------------------------------------------------------------------------

class TestDomainIsolation:
    def test_different_domains_independent(self, learner, db_path):
        """Sequences for greenhouse.io and lever.co are stored separately."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)
        learner.save_sequence("lever.co", LEVER_STEPS, success=True)

        gh = learner.get_sequence("greenhouse.io")
        lv = learner.get_sequence("lever.co")

        assert gh is not None
        assert lv is not None
        assert len(gh) == 3
        assert len(lv) == 2
        assert gh[0]["selector"] == "#apply-btn"
        assert lv[0]["selector"] == ".apply-button"

        rows = _query_all_rows(db_path)
        assert len(rows) == 2
        domains = {r["domain"] for r in rows}
        assert domains == {"greenhouse.io", "lever.co"}

    def test_marking_failed_on_one_domain_does_not_affect_other(self, learner):
        """mark_failed on greenhouse.io leaves lever.co intact."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)
        learner.save_sequence("lever.co", LEVER_STEPS, success=True)

        learner.mark_failed("greenhouse.io")

        assert learner.get_sequence("greenhouse.io") is None
        assert learner.get_sequence("lever.co") is not None

    def test_url_normalization_groups_same_domain(self, learner, db_path):
        """Full URLs and bare domains pointing to the same host share a row."""
        learner.save_sequence(
            "https://www.boards.greenhouse.io/acme/jobs/123",
            GREENHOUSE_STEPS,
            success=True,
        )

        result = learner.get_sequence("https://boards.greenhouse.io/beta/jobs/456")
        assert result is not None
        assert len(result) == 3

        rows = _query_all_rows(db_path)
        assert len(rows) == 1
        assert rows[0]["domain"] == "boards.greenhouse.io"


# ---------------------------------------------------------------------------
# Sequence quality: success vs failure preference
# ---------------------------------------------------------------------------

class TestSequenceQuality:
    def test_only_successful_sequences_returned(self, learner):
        """get_sequence only returns success=1 rows."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=False)
        assert learner.get_sequence("greenhouse.io") is None

    def test_failed_overwritten_by_success(self, learner, db_path):
        """A successful save after a failed one marks the row as success."""
        learner.save_sequence("greenhouse.io", SHORT_STEP, success=False)
        assert learner.get_sequence("greenhouse.io") is None

        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)
        result = learner.get_sequence("greenhouse.io")
        assert result is not None
        assert len(result) == 3

        row = _query_domain(db_path, "greenhouse.io")
        assert row["success"] == 1

    def test_mark_failed_sets_success_to_zero(self, learner, db_path):
        """mark_failed flips the success bit and increments fail_count."""
        learner.save_sequence("lever.co", LEVER_STEPS, success=True)
        learner.mark_failed("lever.co")

        row = _query_domain(db_path, "lever.co")
        assert row["success"] == 0
        assert row["fail_count"] == 1

    def test_three_consecutive_failures_purge_row(self, learner, db_path):
        """After 3 mark_failed calls the row is deleted from the DB."""
        learner.save_sequence("lever.co", LEVER_STEPS, success=True)

        learner.mark_failed("lever.co")
        learner.mark_failed("lever.co")
        learner.mark_failed("lever.co")

        row = _query_domain(db_path, "lever.co")
        assert row is None

    def test_two_failures_keeps_row(self, learner, db_path):
        """Two failures are below the purge threshold -- row still exists."""
        learner.save_sequence("lever.co", LEVER_STEPS, success=True)

        learner.mark_failed("lever.co")
        learner.mark_failed("lever.co")

        row = _query_domain(db_path, "lever.co")
        assert row is not None
        assert row["fail_count"] == 2

    def test_get_failed_sequences(self, learner):
        """get_failed_sequences returns only failed rows for the domain."""
        learner.save_sequence("lever.co", LEVER_STEPS, success=True)
        learner.mark_failed("lever.co")

        failed = learner.get_failed_sequences("lever.co")
        assert len(failed) == 1
        assert failed[0]["steps"] == LEVER_STEPS

    def test_empty_steps_do_not_overwrite_good_sequence(self, learner, db_path):
        """Saving success=True with empty steps preserves existing non-empty steps."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)
        learner.save_sequence("greenhouse.io", [], success=True)

        result = learner.get_sequence("greenhouse.io")
        assert result is not None
        assert len(result) == 3

        row = _query_domain(db_path, "greenhouse.io")
        assert json.loads(row["steps"]) == GREENHOUSE_STEPS


# ---------------------------------------------------------------------------
# TTL / staleness
# ---------------------------------------------------------------------------

class TestTTLStaleness:
    def test_fresh_sequence_returned(self, learner, db_path):
        """A sequence saved just now is well within the 30-day TTL."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)

        result = learner.get_sequence("greenhouse.io")
        assert result is not None

    def test_expired_sequence_not_returned(self, learner, db_path):
        """A sequence older than 30 days is expired and not returned."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)

        old_date = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE sequences SET updated_at = ? WHERE domain = ?",
                (old_date, "greenhouse.io"),
            )

        result = learner.get_sequence("greenhouse.io")
        assert result is None

    def test_expired_sequence_still_in_db(self, learner, db_path):
        """Expired sequences remain in the DB -- only get_sequence skips them."""
        learner.save_sequence("greenhouse.io", GREENHOUSE_STEPS, success=True)

        old_date = (datetime.now(UTC) - timedelta(days=45)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE sequences SET updated_at = ? WHERE domain = ?",
                (old_date, "greenhouse.io"),
            )

        row = _query_domain(db_path, "greenhouse.io")
        assert row is not None
        assert json.loads(row["steps"]) == GREENHOUSE_STEPS

    def test_borderline_29_days_still_valid(self, learner, db_path):
        """A sequence at 29 days is still within TTL."""
        learner.save_sequence("lever.co", LEVER_STEPS, success=True)

        border_date = (datetime.now(UTC) - timedelta(days=29)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE sequences SET updated_at = ? WHERE domain = ?",
                (border_date, "lever.co"),
            )

        result = learner.get_sequence("lever.co")
        assert result is not None


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_saves_different_domains(self, db_path, transfer_db_path):
        """Two threads saving to different domains do not corrupt the DB."""
        errors = []

        def save_domain(domain, steps):
            try:
                nl = NavigationLearner(db_path=db_path)
                nl._transfer_db_path = transfer_db_path
                nl.save_sequence(domain, steps, success=True, platform="greenhouse")
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=save_domain, args=("alpha.com", GREENHOUSE_STEPS))
        t2 = threading.Thread(target=save_domain, args=("beta.com", LEVER_STEPS))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Concurrent saves raised: {errors}"

        rows = _query_all_rows(db_path)
        domains = {r["domain"] for r in rows}
        assert domains == {"alpha.com", "beta.com"}

    def test_concurrent_saves_same_domain(self, db_path, transfer_db_path):
        """Two threads writing the same domain use UPSERT -- last writer wins, no crash."""
        errors = []

        def save_steps(steps, platform):
            try:
                nl = NavigationLearner(db_path=db_path)
                nl._transfer_db_path = transfer_db_path
                nl.save_sequence("shared.com", steps, success=True, platform=platform)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=save_steps, args=(GREENHOUSE_STEPS, "greenhouse"))
        t2 = threading.Thread(target=save_steps, args=(LEVER_STEPS, "lever"))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Concurrent upserts raised: {errors}"

        rows = _query_all_rows(db_path)
        assert len(rows) == 1
        assert rows[0]["domain"] == "shared.com"

    def test_concurrent_read_write(self, db_path, transfer_db_path):
        """One thread saves while another reads -- no locking errors."""
        errors = []
        read_results = []

        nl_writer = NavigationLearner(db_path=db_path)
        nl_writer._transfer_db_path = transfer_db_path
        nl_writer.save_sequence("pre.com", SHORT_STEP, success=True)

        def writer():
            try:
                nl = NavigationLearner(db_path=db_path)
                nl._transfer_db_path = transfer_db_path
                for i in range(20):
                    nl.save_sequence(
                        f"domain-{i}.com", SHORT_STEP, success=True, platform="test"
                    )
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                nl = NavigationLearner(db_path=db_path)
                nl._transfer_db_path = transfer_db_path
                for _ in range(20):
                    result = nl.get_sequence("pre.com")
                    read_results.append(result)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Concurrent read/write raised: {errors}"
        assert all(r is not None for r in read_results)


# ---------------------------------------------------------------------------
# Platform patterns
# ---------------------------------------------------------------------------

class TestPlatformPattern:
    def test_platform_pattern_returned_with_enough_observations(self, learner):
        """Platform pattern requires min_observations (default 2) matching domains."""
        for domain in ["alpha.greenhouse.io", "beta.greenhouse.io", "gamma.greenhouse.io"]:
            learner.save_sequence(domain, SHORT_STEP, success=True, platform="greenhouse")

        pattern = learner.get_platform_pattern("greenhouse")
        assert pattern is not None
        assert pattern[0]["action"] == "click_apply"

    def test_platform_pattern_excludes_target_domain(self, learner):
        """exclude_domain prevents using target's own data as a pattern source."""
        learner.save_sequence("a.com", SHORT_STEP, success=True, platform="lever")
        learner.save_sequence("b.com", SHORT_STEP, success=True, platform="lever")
        learner.save_sequence("c.com", SHORT_STEP, success=True, platform="lever")

        # Excluding a.com still leaves b.com + c.com (2 observations >= min_observations=2)
        pattern = learner.get_platform_pattern("lever", exclude_domain="a.com")
        assert pattern is not None

    def test_platform_pattern_none_below_threshold(self, learner):
        """One observation is below min_observations=2."""
        learner.save_sequence("solo.com", SHORT_STEP, success=True, platform="workday")

        pattern = learner.get_platform_pattern("workday")
        assert pattern is None

    def test_platform_pattern_picks_most_common(self, learner):
        """When domains have different action sequences, return the most common one."""
        common_steps = [{"action": "click_apply"}, {"action": "fill_form"}]
        rare_steps = [{"action": "click_apply"}, {"action": "fill_login"}, {"action": "fill_form"}]

        for domain in ["a.com", "b.com", "c.com"]:
            learner.save_sequence(domain, common_steps, success=True, platform="greenhouse")
        learner.save_sequence("outlier.com", rare_steps, success=True, platform="greenhouse")

        pattern = learner.get_platform_pattern("greenhouse")
        assert pattern is not None
        assert len(pattern) == 2
        actions = [s["action"] for s in pattern]
        assert actions == ["click_apply", "fill_form"]


# ---------------------------------------------------------------------------
# Content hash lookup
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_content_hash_cross_domain_lookup(self, learner):
        """get_sequence_by_content_hash finds sequences from other domains."""
        learner.save_sequence(
            "a.com", GREENHOUSE_STEPS, success=True, content_hash="hash_xyz"
        )

        result = learner.get_sequence_by_content_hash("hash_xyz", exclude_domain="b.com")
        assert result is not None
        assert len(result) == 3

    def test_content_hash_excludes_own_domain(self, learner):
        """The exclude_domain parameter prevents returning the domain's own sequence."""
        learner.save_sequence(
            "a.com", GREENHOUSE_STEPS, success=True, content_hash="hash_123"
        )

        result = learner.get_sequence_by_content_hash("hash_123", exclude_domain="a.com")
        assert result is None

    def test_content_hash_empty_returns_none(self, learner):
        """Empty content_hash returns None immediately."""
        assert learner.get_sequence_by_content_hash("") is None

    def test_content_hash_only_returns_successful(self, learner):
        """Failed sequences are not returned by content hash lookup."""
        learner.save_sequence(
            "a.com", GREENHOUSE_STEPS, success=False, content_hash="fail_hash"
        )

        result = learner.get_sequence_by_content_hash("fail_hash", exclude_domain="b.com")
        assert result is None

    def test_content_hash_stored_in_db(self, learner, db_path):
        """Verify content_hash column is written to the DB."""
        learner.save_sequence(
            "lever.co", LEVER_STEPS, success=True, content_hash="ch_999"
        )

        row = _query_domain(db_path, "lever.co")
        assert row["content_hash"] == "ch_999"

    def test_content_hash_not_overwritten_by_empty(self, learner, db_path):
        """Saving with empty content_hash preserves the existing hash (CASE WHEN logic)."""
        learner.save_sequence(
            "lever.co", LEVER_STEPS, success=True, content_hash="original_hash"
        )
        learner.save_sequence("lever.co", LEVER_STEPS, success=True, content_hash="")

        row = _query_domain(db_path, "lever.co")
        assert row["content_hash"] == "original_hash"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_steps_save_and_retrieve(self, learner, db_path):
        """An empty step list can be stored (no prior sequence to protect)."""
        learner.save_sequence("empty.com", [], success=True)

        row = _query_domain(db_path, "empty.com")
        assert row is not None
        assert json.loads(row["steps"]) == []

    def test_very_long_sequence(self, learner, db_path):
        """A sequence with 50 steps round-trips correctly."""
        long_steps = [
            {"page_type": f"page_{i}", "action": f"action_{i}", "selector": f"#sel-{i}"}
            for i in range(50)
        ]
        learner.save_sequence("long.com", long_steps, success=True)

        result = learner.get_sequence("long.com")
        assert result is not None
        assert len(result) == 50
        assert result[49]["action"] == "action_49"

        row = _query_domain(db_path, "long.com")
        assert len(json.loads(row["steps"])) == 50

    def test_special_characters_in_url(self, learner, db_path):
        """URLs with query params and fragments normalize to the domain."""
        learner.save_sequence(
            "https://jobs.example.com/apply?role=ml%20engineer&ref=linkedin#top",
            SHORT_STEP,
            success=True,
        )

        result = learner.get_sequence("jobs.example.com")
        assert result is not None

        row = _query_domain(db_path, "jobs.example.com")
        assert row is not None

    def test_unicode_in_steps(self, learner, db_path):
        """Steps containing unicode serialize and deserialize correctly."""
        unicode_steps = [
            {"page_type": "bewerbung", "action": "klicken", "selector": "#bewerben-ü"},
            {"page_type": "应用", "action": "提交", "selector": "#submit"},
        ]
        learner.save_sequence("jobs.de", unicode_steps, success=True)

        result = learner.get_sequence("jobs.de")
        assert result is not None
        assert result[0]["selector"] == "#bewerben-ü"
        assert result[1]["page_type"] == "应用"

    def test_mark_failed_nonexistent_domain(self, learner, db_path):
        """mark_failed on a domain with no row is a no-op, no crash."""
        learner.mark_failed("nonexistent.com")

        row = _query_domain(db_path, "nonexistent.com")
        assert row is None

    def test_increment_replay_nonexistent_domain(self, learner, db_path):
        """increment_replay on a missing domain is a no-op, no crash."""
        learner.increment_replay("nonexistent.com")

        row = _query_domain(db_path, "nonexistent.com")
        assert row is None

    def test_get_stats_empty_db(self, learner):
        """Stats on a fresh DB return zeroes."""
        stats = learner.get_stats()
        assert stats["total_domains"] == 0
        assert stats["successful_domains"] == 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_counts(self, learner):
        """Stats correctly count total and successful domains."""
        learner.save_sequence("a.com", SHORT_STEP, success=True)
        learner.save_sequence("b.com", SHORT_STEP, success=True)
        learner.save_sequence("c.com", SHORT_STEP, success=False)

        stats = learner.get_stats()
        assert stats["total_domains"] == 3
        assert stats["successful_domains"] == 2

    def test_stats_after_purge(self, learner):
        """After a domain is purged by 3 failures, stats decrease."""
        learner.save_sequence("a.com", SHORT_STEP, success=True)
        learner.save_sequence("b.com", SHORT_STEP, success=True)

        learner.mark_failed("b.com")
        learner.mark_failed("b.com")
        learner.mark_failed("b.com")

        stats = learner.get_stats()
        assert stats["total_domains"] == 1
        assert stats["successful_domains"] == 1


# ---------------------------------------------------------------------------
# Schema & DB integrity
# ---------------------------------------------------------------------------

class TestSchemaIntegrity:
    def test_wal_journal_mode(self, db_path):
        """NavigationLearner sets WAL journal mode for concurrent access."""
        NavigationLearner(db_path=db_path)

        with sqlite3.connect(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_table_schema_columns(self, db_path):
        """The sequences table has all expected columns."""
        NavigationLearner(db_path=db_path)

        with sqlite3.connect(db_path) as conn:
            info = conn.execute("PRAGMA table_info(sequences)").fetchall()
        col_names = {row[1] for row in info}
        expected = {
            "domain", "steps", "success", "created_at", "updated_at",
            "replay_count", "fail_count", "platform", "content_hash",
        }
        assert expected.issubset(col_names)

    def test_domain_primary_key(self, db_path):
        """Domain is the primary key -- upsert on conflict, not duplicate rows."""
        nl = NavigationLearner(db_path=db_path)
        nl._transfer_db_path = db_path  # reuse to avoid prod DB access

        nl.save_sequence("dup.com", SHORT_STEP, success=True)
        nl.save_sequence("dup.com", LEVER_STEPS, success=True)

        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sequences WHERE domain = ?", ("dup.com",)
            ).fetchone()[0]
        assert count == 1

    def test_content_hash_index_exists(self, db_path):
        """An index on content_hash is created for fast lookups."""
        NavigationLearner(db_path=db_path)

        with sqlite3.connect(db_path) as conn:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'sequences'"
            ).fetchall()
        index_names = {row[0] for row in indexes}
        assert "idx_sequences_content_hash" in index_names

    def test_multiple_instantiations_idempotent(self, db_path):
        """Creating multiple NavigationLearner instances on the same DB is safe."""
        nl1 = NavigationLearner(db_path=db_path)
        nl1._transfer_db_path = db_path
        nl1.save_sequence("a.com", SHORT_STEP, success=True)

        nl2 = NavigationLearner(db_path=db_path)
        nl2._transfer_db_path = db_path

        result = nl2.get_sequence("a.com")
        assert result is not None
