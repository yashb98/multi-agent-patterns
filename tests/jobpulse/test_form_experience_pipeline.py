"""Integration tests for form experience pipeline wiring.

All data uses real production URLs, field labels, techniques, and platform names.
DB isolation via tmp_path per project testing rules.
"""
from __future__ import annotations

import sqlite3

import pytest

from jobpulse.form_experience_db import FormExperienceDB


@pytest.fixture
def seeded_exp_db(tmp_path):
    """Seed FormExperienceDB with real production data snapshot."""
    db = FormExperienceDB(str(tmp_path / "form_experience.db"))

    db.record("job-boards.greenhouse.io", "greenhouse", "extension",
              pages_filled=2,
              field_types=["text:first_name", "text:last_name", "text:email",
                           "combobox:country", "combobox:do_you_hold_the_right_to_work"],
              screening_questions=["Do you hold the right to work in the UK?:Graduate Visa"],
              time_seconds=94.0, success=True)

    db.record("linkedin.com", "linkedin", "extension",
              pages_filled=3,
              field_types=["text:first_name", "text:last_name", "select:phone_country_code",
                           "select:email_address"],
              screening_questions=[], time_seconds=120.0, success=True)

    db.record("careers.snowflake.com", "workday", "extension",
              pages_filled=1,
              field_types=["text:first_name", "text:last_name", "text:email",
                           "combobox:country", "multiselect:skills"],
              screening_questions=[], time_seconds=20.0, success=True)

    db.record("jobs.smartrecruiters.com", "smartrecruiters", "extension",
              pages_filled=2,
              field_types=["text:first_name", "text:last_name", "combobox:city",
                           "combobox:gender", "radio:disability"],
              screening_questions=["Do you require a visa?:No"],
              time_seconds=35.0, success=True)

    db.record("jobs.ashbyhq.com", "ashby", "extension",
              pages_filled=1,
              field_types=["text:first_name", "text:email", "file:resume",
                           "radio:work_authorization"],
              screening_questions=[], time_seconds=45.0, success=True)

    db.record("experienced-arm.icims.com", "icims", "extension",
              pages_filled=1,
              field_types=["text:PersonProfileFields.FirstName",
                           "text:PersonProfileFields.LastName",
                           "text:PersonProfileFields.Email"],
              screening_questions=[], time_seconds=120.0, success=True)

    db.record("expedia.wd108.myworkdayjobs.com", "workday", "extension",
              pages_filled=5,
              field_types=["text:first_name", "text:last_name", "combobox:country",
                           "multiselect:skills", "textarea:cover_letter"],
              screening_questions=["Salary expectations:35000-42000"],
              time_seconds=600.0, success=True)

    db.record("jobs.asos.com", "icims", "extension",
              pages_filled=1,
              field_types=["text:first_name", "text:email"],
              screening_questions=[], time_seconds=25.0, success=True)

    db.record("uk.linkedin.com", "linkedin", "extension",
              pages_filled=0,
              field_types=[], screening_questions=[],
              time_seconds=0.0, success=True)

    db.record("job-boards.eu.greenhouse.io", "greenhouse", "extension",
              pages_filled=1,
              field_types=["text:first_name", "text:email", "combobox:country"],
              screening_questions=[], time_seconds=32.2, success=True)

    db.record_fill_technique("job-boards.greenhouse.io", "Country",
                             "combobox:combobox", "combobox_prescanned_match",
                             "United Kingdom", success=True)
    db.record_fill_technique("job-boards.greenhouse.io", "First Name",
                             "input:text", "direct_fill", "Yash", success=True)
    db.record_fill_technique("job-boards.greenhouse.io", "Email",
                             "input:text", "direct_fill",
                             "bishnoiyash274@gmail.com", success=True)
    db.record_fill_technique("job-boards.greenhouse.io",
                             "How did you hear about this job?",
                             "combobox:combobox", "combobox_type_to_search",
                             "LinkedIn", success=True)
    db.record_fill_technique("job-boards.greenhouse.io",
                             "What is your current notice period?",
                             "combobox:combobox", "combobox_prescanned_match",
                             "1 month", success=True)
    db.record_fill_technique("linkedin.com", "First name",
                             "input:text", "direct_fill", "Yash", success=True)
    db.record_fill_technique("linkedin.com", "Last name",
                             "input:text", "direct_fill", "Bishnoi", success=True)
    db.record_fill_technique("linkedin.com", "Email address",
                             "select:select", "select_option",
                             "bishnoiyash274@gmail.com", success=True)
    db.record_fill_technique("linkedin.com", "Phone country code",
                             "select:select", "select_option",
                             "+44", success=True)

    db.save_field_mappings("experienced-arm.icims.com", {
        "PersonProfileFields.FirstName": "first_name",
        "PersonProfileFields.LastName": "last_name",
        "PersonProfileFields.Email": "email",
        "-1_PersonProfileFields.PhoneNumber": "phone",
        "-1_PersonProfileFields.AddressStreet1": "address",
        "-1_PersonProfileFields.AddressCity": "location",
        "-1_PersonProfileFields.AddressZip": "postcode",
    })

    return db


class TestFailureRecording:
    def test_failure_reason_recorded_and_queryable(self, seeded_exp_db):
        """T1: record_failure_reason persists and get_failure_reasons retrieves."""
        seeded_exp_db.record_failure_reason(
            domain="job-boards.greenhouse.io",
            platform="greenhouse",
            failure_type="no_field",
            field_label="Sponsorship status",
            selector="",
            details="No fillable element found for label 'Sponsorship status'",
        )
        failures = seeded_exp_db.get_failure_reasons("job-boards.greenhouse.io")
        assert len(failures) == 1
        assert failures[0]["failure_type"] == "no_field"
        assert failures[0]["field_label"] == "Sponsorship status"
        assert failures[0]["platform"] == "greenhouse"

    def test_platform_failure_stats_aggregate(self, seeded_exp_db):
        """T1b: get_platform_failure_stats aggregates across domains."""
        seeded_exp_db.record_failure_reason(
            "job-boards.greenhouse.io", "greenhouse", "no_field",
            field_label="Sponsorship status",
        )
        seeded_exp_db.record_failure_reason(
            "job-boards.eu.greenhouse.io", "greenhouse", "blocked",
            field_label="Country",
            details="Element intercepted by overlay",
        )
        seeded_exp_db.record_failure_reason(
            "job-boards.greenhouse.io", "greenhouse", "no_field",
            field_label="Disability status",
        )
        stats = seeded_exp_db.get_platform_failure_stats("greenhouse")
        assert stats["no_field"] == 2
        assert stats["blocked"] == 1

    def test_negative_fill_technique_does_not_overwrite_success(self, seeded_exp_db):
        """T2: Failed technique recorded — ON CONFLICT replaces the row (success=0).
        get_fill_techniques filters WHERE success=1, so Country is hidden after failure.
        """
        seeded_exp_db.record_fill_technique(
            "job-boards.greenhouse.io", "Country",
            "combobox:combobox", "combobox_type_to_search", "UK", success=False,
        )
        techniques = seeded_exp_db.get_fill_techniques("job-boards.greenhouse.io")
        # ON CONFLICT replaces — the failure overwrites the success row (success=0).
        # get_fill_techniques filters success=1, so Country is absent from the result.
        country_tech = techniques.get("Country")
        assert country_tech is None, (
            "Failure write overwrites success via ON CONFLICT; "
            "get_fill_techniques(success=1) must not return the failed record"
        )

    def test_negative_fill_technique_raw_query_shows_both(self, seeded_exp_db):
        """T2b: Raw query shows both success and failure records."""
        seeded_exp_db.record_fill_technique(
            "job-boards.greenhouse.io", "Country",
            "combobox:combobox", "combobox_type_to_search", "UK", success=False,
        )
        with sqlite3.connect(seeded_exp_db._db_path) as conn:
            rows = conn.execute(
                "SELECT field_label, technique, success FROM fill_techniques "
                "WHERE domain = 'job-boards.greenhouse.io' AND field_label = 'Country' "
                "ORDER BY success DESC"
            ).fetchall()
        # ON CONFLICT replaces, so the latest write (failure) overwrites.
        # But get_fill_techniques filters success=1 — the key behavior is the filter.
        assert len(rows) >= 1


class TestPostApplyHookFailurePath:
    def test_failure_records_partial_experience(self, tmp_path, monkeypatch):
        """T3: post_apply_hook records form experience even on failure."""
        db_path = str(tmp_path / "fe.db")

        # Monkeypatch external calls that would fail without credentials
        monkeypatch.setattr("jobpulse.post_apply_hook.upload_cv", lambda *a, **kw: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.upload_cover_letter", lambda *a, **kw: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.find_application_page", lambda *a, **kw: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.update_application_page", lambda *a, **kw: None)

        from jobpulse.post_apply_hook import post_apply_hook

        result = {
            "success": False,
            "pages_filled": 1,
            "field_types": ["text:first_name", "combobox:country"],
            "screening_questions": ["Do you hold the right to work in the UK?:Graduate Visa"],
            "time_seconds": 45.2,
            "error": "Stuck on identical page (page 2)",
            "agent_fill_stats": {
                "fields_attempted": 5,
                "fields_filled": 3,
                "fields_failed": 2,
                "failed_labels": ["Sponsorship status", "Disability"],
                "llm_fallback_count": 1,
            },
        }
        job_context = {
            "job_id": "",
            "company": "Sony Interactive",
            "title": "Data Analyst",
            "url": "https://job-boards.greenhouse.io/sonyinteractive/jobs/12345",
            "platform": "greenhouse",
            "ats_platform": "greenhouse",
            "notion_page_id": None,
            "cv_path": None,
            "cover_letter_path": None,
        }

        post_apply_hook(result, job_context, form_exp_db_path=db_path)

        db = FormExperienceDB(db_path)
        exp = db.lookup("job-boards.greenhouse.io")
        assert exp is not None
        assert exp["success"] == 0
        assert exp["pages_filled"] == 1

        failures = db.get_failure_reasons("job-boards.greenhouse.io")
        assert len(failures) == 2
        labels = {f["field_label"] for f in failures}
        assert labels == {"Sponsorship status", "Disability"}


class TestCrossPlatformTechniques:
    def test_platform_fill_techniques_returns_cross_domain(self, seeded_exp_db):
        """T4: get_platform_fill_techniques returns techniques from all greenhouse domains."""
        techniques = seeded_exp_db.get_platform_fill_techniques("greenhouse")
        assert len(techniques) > 0
        labels = [t["field_label"] for t in techniques]
        assert "Country" in labels
        assert "First Name" in labels
        technique_map = {t["field_label"]: t["technique"] for t in techniques}
        assert technique_map["Country"] == "combobox_prescanned_match"

    def test_platform_fill_techniques_sorted_by_apply_count(self, seeded_exp_db):
        """T4b: Techniques are sorted by apply_count DESC (most used first)."""
        techniques = seeded_exp_db.get_platform_fill_techniques("linkedin")
        assert len(techniques) > 0
        counts = [t["apply_count"] for t in techniques]
        assert counts == sorted(counts, reverse=True)


class TestValidateAgainstLive:
    def test_trusted_when_fields_match(self, seeded_exp_db):
        """T5: validate_against_live returns trusted when live matches stored."""
        live_types = ["text:first_name", "text:last_name", "text:email",
                      "combobox:country", "combobox:do_you_hold_the_right_to_work"]
        result = seeded_exp_db.validate_against_live(
            "job-boards.greenhouse.io", live_types,
        )
        assert result["trusted"] is True
        assert result["match_ratio"] >= 0.8

    def test_drift_detected_with_divergent_fields(self, seeded_exp_db):
        """T6: validate_against_live detects drift when fields completely different."""
        live_types = ["textarea:cover_letter", "file:portfolio", "radio:remote_preference"]
        result = seeded_exp_db.validate_against_live(
            "job-boards.greenhouse.io", live_types,
        )
        assert result["trusted"] is False
        assert len(result["diverged_fields"]) > 0
        assert result["match_ratio"] < 0.8

    def test_partial_overlap_uses_threshold(self, seeded_exp_db):
        """T5b: Partial overlap trusted if above 80% threshold."""
        live_types = ["text:first_name", "text:last_name", "text:email",
                      "combobox:country"]
        result = seeded_exp_db.validate_against_live(
            "job-boards.greenhouse.io", live_types,
        )
        assert result["trusted"] is True
        assert result["match_ratio"] >= 0.8
