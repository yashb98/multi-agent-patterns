from jobpulse.cover_letter_agent import _cover_letter_prompt_profile, build_cover_letter_prompt
from jobpulse.native_form_filler import (
    _profile_prompt_json,
    _screening_prompt_background,
    _screening_prompt_profile,
)
from jobpulse.screening_answers import _screening_profile_summary, _screening_prompt_profile as screening_profile
from shared.pii import audit_prompt_for_unwrapped_pii


def test_cover_letter_prompt_wraps_profile_fields():
    prompt = build_cover_letter_prompt(
        company="Example Corp",
        role="Data Scientist",
        jd_text="Build models and communicate insights.",
        matched_skills=["Python", "SQL"],
        matched_projects=["Churn prediction"],
    )
    leaks = audit_prompt_for_unwrapped_pii(prompt, _cover_letter_prompt_profile(), "cover_letter")
    assert leaks == []


def test_native_form_profile_json_wraps_profile_fields():
    profile = {"first_name": "Yash", "last_name": "Bishnoi", "email": "yash@example.com"}
    prompt_profile = _profile_prompt_json(profile)
    leaks = audit_prompt_for_unwrapped_pii(prompt_profile, profile, "applicant.profile")
    assert leaks == []


def test_native_form_screening_background_wraps_profile_fields():
    profile = _screening_prompt_profile()
    background = _screening_prompt_background(profile)
    leaks = audit_prompt_for_unwrapped_pii(background, profile, "applicant")
    assert leaks == []


def test_screening_answers_summary_wraps_profile_fields():
    profile = screening_profile()
    summary = _screening_profile_summary(profile)
    leaks = audit_prompt_for_unwrapped_pii(summary, profile, "screening")
    assert leaks == []
