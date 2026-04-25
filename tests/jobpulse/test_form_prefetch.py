# tests/jobpulse/test_form_prefetch.py
"""Tests for form_prefetch — pre-apply knowledge aggregation."""
import pytest

from jobpulse.form_prefetch import prefetch_form_hints, get_prefetch_stats


@pytest.fixture
def db_paths(tmp_path):
    return {
        "form_exp_db": str(tmp_path / "form_exp.db"),
        "interaction_db": str(tmp_path / "interactions.db"),
        "nav_db": str(tmp_path / "nav.db"),
    }


def test_unknown_domain_returns_empty_hints(db_paths):
    hints = prefetch_form_hints("https://unknown-domain.com/apply", **db_paths)
    assert hints is not None
    assert hints.known_domain is False
    assert hints.expected_pages == 0
    assert hints.field_types == []
    assert hints.screening_questions == []
    assert hints.page_structures == []
    assert hints.nav_steps is None
    assert hints.apply_count == 0


def test_known_domain_aggregates_all_sources(db_paths):
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.form_interaction_log import FormInteractionLog
    from jobpulse.navigation_learner import NavigationLearner

    exp_db = FormExperienceDB(db_path=db_paths["form_exp_db"])
    exp_db.record(
        domain="boards.greenhouse.io",
        platform="greenhouse",
        adapter="extension",
        pages_filled=3,
        field_types=["text", "select", "file"],
        screening_questions=["Require sponsorship?", "Salary?"],
        time_seconds=45.0,
        success=True,
    )

    int_log = FormInteractionLog(db_path=db_paths["interaction_db"])
    int_log.log_page_structure(
        "boards.greenhouse.io", "greenhouse", 1, "Contact",
        ["Name", "Email", "Phone"], ["text", "text", "text"],
        nav_buttons=["Next"],
    )
    int_log.log_page_structure(
        "boards.greenhouse.io", "greenhouse", 2, "Resume",
        ["Resume", "Cover Letter"], ["file", "file"],
        has_file_upload=True, nav_buttons=["Back", "Submit"],
    )

    nav = NavigationLearner(db_path=db_paths["nav_db"])
    nav.save_sequence("boards.greenhouse.io", [
        {"type": "click", "selector": "#apply-btn"},
        {"type": "wait", "selector": "#form"},
    ], success=True)

    hints = prefetch_form_hints(
        "https://boards.greenhouse.io/company/jobs/123", **db_paths
    )

    assert hints.known_domain is True
    assert hints.platform == "greenhouse"
    assert hints.expected_pages == 3
    assert hints.field_types == ["text", "select", "file"]
    assert hints.screening_questions == ["Require sponsorship?", "Salary?"]
    assert len(hints.page_structures) == 2
    assert hints.page_structures[0]["page_title"] == "Contact"
    assert hints.page_structures[1]["has_file_upload"] == 1
    assert hints.nav_steps is not None
    assert len(hints.nav_steps) == 2
    assert hints.apply_count == 1
    assert hints.avg_time_seconds == pytest.approx(45.0)
    assert hints.has_file_upload is True


def test_partial_data_still_returns_hints(db_paths):
    from jobpulse.form_experience_db import FormExperienceDB

    exp_db = FormExperienceDB(db_path=db_paths["form_exp_db"])
    exp_db.record(
        domain="jobs.lever.co",
        platform="lever",
        adapter="extension",
        pages_filled=2,
        field_types=["text", "file"],
        screening_questions=[],
        time_seconds=30.0,
        success=True,
    )

    hints = prefetch_form_hints("https://jobs.lever.co/company/abc", **db_paths)

    assert hints.known_domain is True
    assert hints.platform == "lever"
    assert hints.expected_pages == 2
    assert hints.page_structures == []
    assert hints.nav_steps is None


def test_to_dict_serialization(db_paths):
    hints = prefetch_form_hints("https://unknown.com/apply", **db_paths)
    d = hints.to_dict()
    assert isinstance(d, dict)
    assert d["known_domain"] is False
    assert d["expected_pages"] == 0
    assert d["nav_steps"] is None


def test_has_file_upload_derived_from_page_structures(db_paths):
    from jobpulse.form_interaction_log import FormInteractionLog

    int_log = FormInteractionLog(db_path=db_paths["interaction_db"])
    int_log.log_page_structure(
        "example.com", "generic", 1, "Contact",
        ["Name"], ["text"], has_file_upload=False,
    )
    int_log.log_page_structure(
        "example.com", "generic", 2, "Resume",
        ["Resume"], ["file"], has_file_upload=True,
    )

    hints = prefetch_form_hints("https://example.com/apply", **db_paths)
    assert hints.has_file_upload is True


def test_no_file_upload_when_not_present(db_paths):
    from jobpulse.form_interaction_log import FormInteractionLog

    int_log = FormInteractionLog(db_path=db_paths["interaction_db"])
    int_log.log_page_structure(
        "nofile.com", "generic", 1, "Info",
        ["Name", "Email"], ["text", "text"], has_file_upload=False,
    )

    hints = prefetch_form_hints("https://nofile.com/apply", **db_paths)
    assert hints.has_file_upload is False


class TestValidateHintsAgainstLive:
    def test_validates_matching_fields(self, db_paths):
        from jobpulse.form_experience_db import FormExperienceDB
        from jobpulse.form_prefetch import validate_hints_against_live

        exp_db = FormExperienceDB(db_path=db_paths["form_exp_db"])
        exp_db.record(
            domain="g.io", platform="greenhouse", adapter="ext",
            pages_filled=2, field_types=["text", "select", "upload"],
            screening_questions=[], time_seconds=30.0, success=True,
        )

        hints = prefetch_form_hints("https://g.io/apply", **db_paths)
        assert hints.known_domain is True

        validated = validate_hints_against_live(
            hints, ["text", "select", "upload"],
            url="https://g.io/apply",
            form_exp_db=db_paths["form_exp_db"],
        )
        assert validated.validated is True
        assert validated.known_domain is True
        assert validated.match_ratio == 1.0

    def test_invalidates_divergent_fields(self, db_paths):
        from jobpulse.form_experience_db import FormExperienceDB
        from jobpulse.form_prefetch import validate_hints_against_live

        exp_db = FormExperienceDB(db_path=db_paths["form_exp_db"])
        exp_db.record(
            domain="g.io", platform="greenhouse", adapter="ext",
            pages_filled=2, field_types=["text", "select", "upload"],
            screening_questions=[], time_seconds=30.0, success=True,
        )

        hints = prefetch_form_hints("https://g.io/apply", **db_paths)
        assert hints.known_domain is True

        validated = validate_hints_against_live(
            hints, ["checkbox", "radio", "textarea"],
            url="https://g.io/apply",
            form_exp_db=db_paths["form_exp_db"],
        )
        assert validated.validated is False
        assert validated.known_domain is False
        assert len(validated.diverged_fields) > 0

    def test_skips_validation_for_unknown_domain(self, db_paths):
        from jobpulse.form_prefetch import validate_hints_against_live

        hints = prefetch_form_hints("https://unknown.com/apply", **db_paths)
        validated = validate_hints_against_live(
            hints, ["text"], url="https://unknown.com/apply",
            form_exp_db=db_paths["form_exp_db"],
        )
        assert validated.known_domain is False
        assert validated.validated is False


class TestRiskFields:
    def test_default_risk_level_is_low(self, db_paths):
        hints = prefetch_form_hints("https://unknown.com/apply", **db_paths)
        assert hints.risk_level == "low"
        assert hints.scan_delay_range == (1.0, 5.0)
        assert hints.simulate_human is False

    def test_risk_fields_populated_for_known_platform(self, db_paths, monkeypatch):
        from jobpulse.form_experience_db import FormExperienceDB

        exp_db = FormExperienceDB(db_path=db_paths["form_exp_db"])
        exp_db.record(
            domain="boards.greenhouse.io", platform="greenhouse", adapter="ext",
            pages_filled=2, field_types=["text"], screening_questions=[],
            time_seconds=30.0, success=True,
        )

        from jobpulse import scan_learning
        monkeypatch.setattr(
            scan_learning.ScanLearningEngine, "get_adaptive_params",
            lambda self, platform: {
                "risk_level": "high",
                "delay_range": (5.0, 15.0),
                "simulate_human": True,
            },
        )

        hints = prefetch_form_hints(
            "https://boards.greenhouse.io/company/jobs/123", **db_paths
        )
        assert hints.risk_level == "high"
        assert hints.scan_delay_range == (5.0, 15.0)
        assert hints.simulate_human is True

    def test_to_dict_includes_risk_fields(self, db_paths):
        hints = prefetch_form_hints("https://unknown.com/apply", **db_paths)
        d = hints.to_dict()
        assert "risk_level" in d
        assert "simulate_human" in d


def test_platform_aggregate_returns_stats(tmp_path):
    from jobpulse.form_experience_db import FormExperienceDB

    db = FormExperienceDB(db_path=str(tmp_path / "exp.db"))
    for domain, fields, pages, time_s in [
        ("acme.com", ["text", "email", "tel", "file"], 2, 30.0),
        ("beta.com", ["text", "text", "email", "tel", "file", "select"], 2, 45.0),
        ("gamma.com", ["text", "email", "file", "select", "textarea"], 1, 25.0),
    ]:
        db.record(domain, "greenhouse", "playwright", pages, fields,
                  ["visa?", "salary?"], time_s, success=True)

    agg = db.get_platform_aggregate("greenhouse")
    assert agg is not None
    assert agg["observation_count"] == 3
    assert 1.0 <= agg["avg_pages"] <= 2.0
    assert 4.0 <= agg["avg_field_count"] <= 6.0
    assert 25.0 <= agg["avg_time_seconds"] <= 45.0
    assert "text" in agg["common_field_types"]
    assert "email" in agg["common_field_types"]
    assert "file" in agg["common_field_types"]


def test_platform_aggregate_excludes_failures(tmp_path):
    from jobpulse.form_experience_db import FormExperienceDB

    db = FormExperienceDB(db_path=str(tmp_path / "exp.db"))
    db.record("fail.com", "greenhouse", "playwright", 1, ["text"], [], 10.0, success=False)
    db.record("ok.com", "greenhouse", "playwright", 2, ["text", "email"], ["visa?"], 30.0, success=True)

    agg = db.get_platform_aggregate("greenhouse")
    assert agg["observation_count"] == 1
    assert agg["avg_pages"] == 2.0


def test_platform_aggregate_unknown_platform(tmp_path):
    from jobpulse.form_experience_db import FormExperienceDB

    db = FormExperienceDB(db_path=str(tmp_path / "exp.db"))
    assert db.get_platform_aggregate("nonexistent") is None


def test_platform_common_screening_questions(tmp_path):
    from jobpulse.form_experience_db import FormExperienceDB

    db = FormExperienceDB(db_path=str(tmp_path / "exp.db"))
    db.record("a.com", "lever", "pw", 1, ["text"], ["visa?", "salary?"], 20.0, True)
    db.record("b.com", "lever", "pw", 1, ["text"], ["visa?", "notice?"], 20.0, True)
    db.record("c.com", "lever", "pw", 1, ["text"], ["visa?"], 20.0, True)

    agg = db.get_platform_aggregate("lever")
    assert agg["common_screening_questions"][0][0] == "visa?"
    assert agg["common_screening_questions"][0][1] == 3
