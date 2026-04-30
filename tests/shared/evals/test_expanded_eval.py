"""Tests for the 5 new domain-specific eval flow handlers."""

from __future__ import annotations

import pytest

from shared.evals._agent_eval import CanonicalFlowCase, _run_case


# ---------------------------------------------------------------------------
# screening_answer
# ---------------------------------------------------------------------------


def _screening_case(question: str) -> CanonicalFlowCase:
    return CanonicalFlowCase(
        case_id="screen-test",
        flow="screening_answer",
        input={"question": question},
        expected={},
    )


def test_screening_answer_returns_dict_with_intent_key():
    case = _screening_case("Do you require visa sponsorship?")
    result = _run_case(case)
    assert isinstance(result, dict)
    assert "intent" in result
    assert "confidence" in result


def test_screening_answer_intent_is_string():
    case = _screening_case("What is your notice period?")
    result = _run_case(case)
    assert isinstance(result["intent"], str)


def test_screening_answer_confidence_is_float():
    case = _screening_case("Are you willing to relocate?")
    result = _run_case(case)
    assert isinstance(result["confidence"], float)


def test_screening_answer_empty_question_returns_unknown():
    case = _screening_case("")
    result = _run_case(case)
    assert result["intent"] == "unknown"


def test_screening_answer_valid_intent_value():
    """Intent value must be one of the known ScreeningIntent enum values."""
    from jobpulse.screening_intent import ScreeningIntent

    valid_values = {i.value for i in ScreeningIntent}
    case = _screening_case("What is your current salary?")
    result = _run_case(case)
    assert result["intent"] in valid_values


# ---------------------------------------------------------------------------
# field_mapping
# ---------------------------------------------------------------------------


def _field_case(
    desired: str,
    options: list[str],
    label: str = "",
    numeric: float | None = None,
) -> CanonicalFlowCase:
    inp: dict = {"desired_value": desired, "available_options": options}
    if label:
        inp["field_label"] = label
    if numeric is not None:
        inp["numeric_value"] = numeric
    return CanonicalFlowCase(
        case_id="field-test",
        flow="field_mapping",
        input=inp,
        expected={},
    )


def test_field_mapping_exact_match():
    case = _field_case("Yes", ["Yes", "No"])
    result = _run_case(case)
    assert result["matched_option"] == "Yes"


def test_field_mapping_exact_match_case_insensitive():
    case = _field_case("yes", ["Yes", "No"])
    result = _run_case(case)
    assert result["matched_option"] == "Yes"


def test_field_mapping_alias_male_to_man():
    case = _field_case("Male", ["Man", "Woman", "Prefer not to say"])
    result = _run_case(case)
    assert result["matched_option"] == "Man"


def test_field_mapping_alias_female_to_woman():
    case = _field_case("Female", ["Man", "Woman", "Non-binary"])
    result = _run_case(case)
    assert result["matched_option"] == "Woman"


def test_field_mapping_numeric_range():
    # Numeric range matches X-Y patterns; 3 falls within "2-5 years"
    case = _field_case("3", ["0-1 years", "2-5 years", "6-10 years"], numeric=3.0)
    result = _run_case(case)
    assert result["matched_option"] == "2-5 years"


def test_field_mapping_no_match_returns_none():
    case = _field_case("purple elephant", ["Red", "Blue", "Green"])
    result = _run_case(case)
    assert result["matched_option"] is None


def test_field_mapping_empty_options_returns_none():
    case = _field_case("Yes", [])
    result = _run_case(case)
    assert result["matched_option"] is None


def test_field_mapping_token_overlap():
    case = _field_case("United Kingdom", ["United States", "United Kingdom", "Canada"])
    result = _run_case(case)
    assert result["matched_option"] == "United Kingdom"


# ---------------------------------------------------------------------------
# fill_failure_class
# ---------------------------------------------------------------------------


def _fail_case(error_message: str) -> CanonicalFlowCase:
    return CanonicalFlowCase(
        case_id="fail-test",
        flow="fill_failure_class",
        input={"error_message": error_message},
        expected={},
    )


def test_fill_failure_no_field():
    result = _run_case(_fail_case("Element not found on page"))
    assert result["failure_class"] == "no_field"


def test_fill_failure_no_element():
    result = _run_case(_fail_case("no element matched selector"))
    assert result["failure_class"] == "no_field"


def test_fill_failure_readonly():
    result = _run_case(_fail_case("Field is readonly"))
    assert result["failure_class"] == "readonly"


def test_fill_failure_disabled():
    result = _run_case(_fail_case("Input is disabled"))
    assert result["failure_class"] == "readonly"


def test_fill_failure_blocked():
    result = _run_case(_fail_case("Click intercepted by overlay"))
    assert result["failure_class"] == "blocked"


def test_fill_failure_wrong_value():
    result = _run_case(_fail_case("Invalid value for field"))
    assert result["failure_class"] == "wrong_value"


def test_fill_failure_validation_error():
    result = _run_case(_fail_case("Validation error: format incorrect"))
    assert result["failure_class"] == "wrong_value"


def test_fill_failure_unknown():
    result = _run_case(_fail_case("Something unexpected happened"))
    assert result["failure_class"] == "unknown"


def test_fill_failure_empty_message():
    result = _run_case(_fail_case(""))
    assert result["failure_class"] == "unknown"


# ---------------------------------------------------------------------------
# platform_bypass
# ---------------------------------------------------------------------------


def _bypass_case(url: str) -> CanonicalFlowCase:
    return CanonicalFlowCase(
        case_id="bypass-test",
        flow="platform_bypass",
        input={"url": url},
        expected={},
    )


@pytest.mark.parametrize("url", [
    "https://www.indeed.com/jobs?q=data+analyst",
    "https://uk.indeed.com/jobs?q=engineer",
    "https://www.linkedin.com/jobs/view/12345",
    "https://www.totaljobs.com/jobs/data-analyst",
    "https://www.reed.co.uk/jobs/data-analyst",
    "https://www.glassdoor.com/Job/jobs.htm",
])
def test_platform_bypass_aggregators(url: str):
    result = _run_case(_bypass_case(url))
    assert result["is_aggregator"] is True


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/acme/jobs/123",
    "https://jobs.lever.co/acme/456",
    "https://acme.wd3.myworkdayjobs.com/jobs",
    "https://careers.smartrecruiters.com/acme",
    "https://acme.com/careers",
])
def test_platform_bypass_non_aggregators(url: str):
    result = _run_case(_bypass_case(url))
    assert result["is_aggregator"] is False


# ---------------------------------------------------------------------------
# page_classification
# ---------------------------------------------------------------------------


def _page_case(
    text: str = "",
    has_form: bool = False,
    has_submit: bool = False,
) -> CanonicalFlowCase:
    return CanonicalFlowCase(
        case_id="page-test",
        flow="page_classification",
        input={
            "text_content": text,
            "has_form_elements": has_form,
            "has_submit_button": has_submit,
        },
        expected={},
    )


def test_page_classification_application_form_with_form_and_submit():
    result = _run_case(_page_case(has_form=True, has_submit=True))
    assert result["page_type"] == "application_form"


def test_page_classification_application_form_apply_text_and_submit():
    result = _run_case(_page_case(text="Click apply to submit your application", has_submit=True))
    assert result["page_type"] == "application_form"


def test_page_classification_job_listing_job_description():
    result = _run_case(_page_case(text="Job Description: We are looking for a data analyst"))
    assert result["page_type"] == "job_listing"


def test_page_classification_job_listing_requirements():
    result = _run_case(_page_case(text="Requirements: 3+ years Python experience"))
    assert result["page_type"] == "job_listing"


def test_page_classification_login_sign_in():
    result = _run_case(_page_case(text="Sign in to your account to continue"))
    assert result["page_type"] == "login"


def test_page_classification_login_log_in():
    result = _run_case(_page_case(text="Log in with your email and password"))
    assert result["page_type"] == "login"


def test_page_classification_verification_wall_captcha():
    result = _run_case(_page_case(text="Please complete the captcha to continue"))
    assert result["page_type"] == "verification_wall"


def test_page_classification_verification_wall_verify():
    result = _run_case(_page_case(text="Please verify you are human"))
    assert result["page_type"] == "verification_wall"


def test_page_classification_unknown():
    result = _run_case(_page_case(text="Welcome to our website"))
    assert result["page_type"] == "unknown"
