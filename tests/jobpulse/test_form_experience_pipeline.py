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
