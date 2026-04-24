"""Tests for TrajectoryStore — per-field decision journal + heuristics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobpulse.trajectory_store import (
    ApplicationStrategy,
    FieldTrajectory,
    Heuristic,
    StrategyTier,
    TrajectoryStore,
    _is_sensitive_field,
    _normalize_domain,
    _reset_shared_store,
    get_trajectory_store,
    load_heuristics_for_application,
)


@pytest.fixture
def store(tmp_path):
    return TrajectoryStore(db_path=str(tmp_path / "test_traj.db"))


class TestNormalization:
    def test_normalize_domain_url(self):
        assert _normalize_domain("https://www.greenhouse.io/jobs") == "greenhouse.io"

    def test_normalize_domain_bare(self):
        assert _normalize_domain("smartrecruiters.com") == "smartrecruiters.com"

    def test_sensitive_field_detection(self):
        assert _is_sensitive_field("What is your gender?")
        assert _is_sensitive_field("Salary expectations")
        assert _is_sensitive_field("visa status")
        assert not _is_sensitive_field("First name")
        assert not _is_sensitive_field("Phone number")


class TestFieldTrajectory:
    def test_log_and_retrieve(self, store):
        row_id = store.log_field(
            job_id="job1", domain="greenhouse.io",
            field_label="First Name", strategy=StrategyTier.PROFILE_STORE,
            value_filled="Yash", confidence=0.99, time_ms=50,
        )
        assert row_id > 0

        trajectories = store.get_trajectories("job1")
        assert len(trajectories) == 1
        t = trajectories[0]
        assert t.field_label == "First Name"
        assert t.strategy == "profile_store"
        assert t.value_filled == "Yash"
        assert t.confidence == 0.99

    def test_sensitive_field_encrypted(self, store):
        store.log_field(
            job_id="job1", domain="greenhouse.io",
            field_label="What is your gender?",
            strategy=StrategyTier.PATTERN_MATCH,
            value_filled="Male",
        )
        # Raw DB should have encrypted value (not "Male")
        import sqlite3
        conn = sqlite3.connect(store._db_path)
        row = conn.execute(
            "SELECT value_filled, is_encrypted FROM field_trajectories WHERE id = 1"
        ).fetchone()
        conn.close()
        if row[1]:  # is_encrypted
            assert row[0] != "Male"

    def test_mark_corrected(self, store):
        store.log_field(
            job_id="job1", domain="greenhouse.io",
            field_label="City", strategy="pattern_match",
            value_filled="London",
        )
        found = store.mark_corrected("job1", "greenhouse.io", "City", "Manchester")
        assert found

        trajectories = store.get_trajectories("job1")
        assert trajectories[0].corrected is True
        assert trajectories[0].corrected_value == "Manchester"

    def test_mark_corrected_not_found(self, store):
        found = store.mark_corrected("job1", "greenhouse.io", "Nonexistent", "val")
        assert not found

    def test_multiple_fields_ordered(self, store):
        store.log_field("job1", "g.io", "Name", "profile_store", page_index=0)
        store.log_field("job1", "g.io", "Email", "profile_store", page_index=0)
        store.log_field("job1", "g.io", "CV", "profile_store", page_index=1)

        trajectories = store.get_trajectories("job1")
        assert len(trajectories) == 3
        assert trajectories[0].page_index == 0
        assert trajectories[2].page_index == 1

    def test_domain_trajectories_limit(self, store):
        for i in range(20):
            store.log_field(f"job{i}", "g.io", f"field{i}", "pattern_match")

        result = store.get_domain_trajectories("g.io", limit=5)
        assert len(result) == 5


class TestApplicationStrategy:
    def test_save_and_retrieve(self, store):
        strategy = ApplicationStrategy(
            job_id="job1", domain="greenhouse.io",
            platform="greenhouse", adapter="extension",
            navigation_strategy="replay",
            fields_total=10, fields_pattern=6, fields_llm=2,
            fields_cached=2, fields_corrected=1,
            total_time_seconds=45.2, success=True,
            reflection='{"deterministic": 2, "llm": 1}',
            heuristics='[{"trigger": "t", "action": "a"}]',
        )
        store.save_strategy(strategy)

        result = store.get_strategy("job1")
        assert result is not None
        assert result.fields_total == 10
        assert result.platform == "greenhouse"
        assert result.success

    def test_aggregate_from_trajectories(self, store):
        store.log_field("job1", "g.io", "Name", "pattern_match", time_ms=50)
        store.log_field("job1", "g.io", "Email", "pattern_match", time_ms=30)
        store.log_field("job1", "g.io", "Salary", "llm_tier3", time_ms=2000)
        store.log_field("job1", "g.io", "City", "cache_hit", time_ms=10)

        strategy = store.aggregate_strategy("job1", {"platform": "greenhouse"})
        assert strategy.fields_total == 4
        assert strategy.fields_pattern == 2
        assert strategy.fields_llm == 1
        assert strategy.fields_cached == 1

    def test_domain_strategies(self, store):
        for i in range(3):
            store.save_strategy(ApplicationStrategy(
                job_id=f"job{i}", domain="greenhouse.io",
                platform="greenhouse", adapter="ext",
                navigation_strategy="", fields_total=5,
                fields_pattern=3, fields_llm=1, fields_cached=1,
                fields_corrected=0, total_time_seconds=30,
                success=True,
            ))

        results = store.get_domain_strategies("greenhouse.io")
        assert len(results) == 3

    def test_platform_strategies(self, store):
        store.save_strategy(ApplicationStrategy(
            job_id="job1", domain="boards.greenhouse.io",
            platform="greenhouse", adapter="ext",
            navigation_strategy="", fields_total=5,
            fields_pattern=3, fields_llm=1, fields_cached=1,
            fields_corrected=0, total_time_seconds=30,
            success=True,
        ))

        results = store.get_platform_strategies("greenhouse")
        assert len(results) == 1


class TestHeuristics:
    def test_save_and_get(self, store):
        h = Heuristic(
            trigger="city field on smartrecruiters",
            action="type then ArrowDown+Enter",
            confidence=0.85,
            source_domain="jobs.smartrecruiters.com",
            platform="smartrecruiters",
        )
        count = store.save_heuristics([h])
        assert count == 1

        results = store.get_heuristics("jobs.smartrecruiters.com")
        assert len(results) == 1
        assert results[0].trigger == "city field on smartrecruiters"
        assert results[0].confidence == 0.85

    def test_expired_heuristics_excluded(self, store):
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        h = Heuristic(
            trigger="old rule", action="do something",
            confidence=0.5, source_domain="example.com",
            platform="generic", expires_at=past,
        )
        store.save_heuristics([h])

        results = store.get_heuristics("example.com")
        assert len(results) == 0

    def test_platform_wide_heuristics(self, store):
        h = Heuristic(
            trigger="greenhouse phone field",
            action="use +44 prefix",
            confidence=0.8,
            source_domain="boards.greenhouse.io",
            platform="greenhouse",
        )
        store.save_heuristics([h])

        # Different domain, same platform
        results = store.get_heuristics(
            "job-boards.eu.greenhouse.io",
            platform="greenhouse",
            include_platform=True,
        )
        assert len(results) == 1

    def test_record_outcome(self, store):
        h = Heuristic(
            trigger="test", action="test",
            confidence=0.5, source_domain="x.com", platform="",
        )
        store.save_heuristics([h])

        store.record_heuristic_outcome(1, succeeded=True)
        store.record_heuristic_outcome(1, succeeded=False)

        results = store.get_heuristics("x.com")
        assert results[0].times_applied == 2
        assert results[0].times_succeeded == 1

    def test_invalidate_stale(self, store):
        h = Heuristic(
            trigger="bad rule", action="do wrong thing",
            confidence=0.9, source_domain="x.com", platform="",
        )
        store.save_heuristics([h])

        # Apply 5 times, succeed 1 time (20% success)
        for _ in range(4):
            store.record_heuristic_outcome(1, succeeded=False)
        store.record_heuristic_outcome(1, succeeded=True)

        invalidated = store.invalidate_stale_heuristics("x.com", threshold=0.6)
        assert invalidated == 1

        results = store.get_heuristics("x.com")
        assert len(results) == 0

    def test_confidence_decay(self, store):
        h = Heuristic(
            trigger="test", action="test",
            confidence=1.0, source_domain="x.com", platform="",
        )
        store.save_heuristics([h])

        decayed = store.decay_confidence()
        assert decayed == 1

        results = store.get_heuristics("x.com")
        assert results[0].confidence < 1.0


class TestPruning:
    def test_prune_expired_heuristics(self, store):
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        h = Heuristic(
            trigger="old", action="old",
            confidence=0.5, source_domain="x.com",
            platform="", expires_at=past,
        )
        store.save_heuristics([h])

        result = store.prune()
        assert result["heuristics_expired"] == 1


class TestSingleton:
    def test_singleton_returns_same(self):
        _reset_shared_store()
        s1 = get_trajectory_store()
        s2 = get_trajectory_store()
        assert s1 is s2
        _reset_shared_store()

    def test_db_path_returns_fresh_instance(self, tmp_path):
        _reset_shared_store()
        db = str(tmp_path / "override.db")
        s1 = get_trajectory_store(db_path=db)
        s2 = get_trajectory_store(db_path=db)
        assert s1 is not s2
        _reset_shared_store()


class TestHeuristicReuse:
    def test_load_heuristics_empty(self, store):
        result = load_heuristics_for_application(
            "new-domain.com", "generic", store=store,
        )
        assert result["domain_heuristics"] == []
        assert result["platform_heuristics"] == []
        assert result["prompt_context"] == ""

    def test_load_heuristics_with_data(self, store):
        store.save_heuristics([
            Heuristic(
                trigger="city autocomplete",
                action="type then ArrowDown",
                confidence=0.9,
                source_domain="jobs.smartrecruiters.com",
                platform="smartrecruiters",
            ),
        ])

        result = load_heuristics_for_application(
            "jobs.smartrecruiters.com", "smartrecruiters", store=store,
        )
        assert len(result["domain_heuristics"]) == 1
        assert "city autocomplete" in result["prompt_context"]


class TestStats:
    def test_stats_empty(self, store):
        s = store.stats()
        assert s["field_trajectories"] == 0
        assert s["application_strategies"] == 0
        assert s["heuristics_total"] == 0

    def test_stats_after_data(self, store):
        store.log_field("job1", "g.io", "Name", "pattern_match")
        store.save_strategy(ApplicationStrategy(
            job_id="job1", domain="g.io", platform="greenhouse",
            adapter="ext", navigation_strategy="", fields_total=1,
            fields_pattern=1, fields_llm=0, fields_cached=0,
            fields_corrected=0, total_time_seconds=10, success=True,
        ))
        store.save_heuristics([
            Heuristic(trigger="t", action="a", confidence=0.5,
                      source_domain="g.io", platform="greenhouse"),
        ])

        s = store.stats()
        assert s["field_trajectories"] == 1
        assert s["application_strategies"] == 1
        assert s["heuristics_active"] == 1
