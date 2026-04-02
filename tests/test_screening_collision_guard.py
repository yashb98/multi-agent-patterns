"""Collision guard — ensures no regex pattern matches questions from other categories.

Every category has must_match (positive) and must_not_match (negative) examples.
If a pattern matches a question from another category, this test fails —
catching regressions like the ethnicity/city collision (2026-04-01).
"""

from __future__ import annotations

import re

import pytest

from jobpulse.screening_answers import COMMON_ANSWERS

# Map each category to example questions it MUST match
POSITIVE_EXAMPLES: dict[str, list[str]] = {
    # Work authorization
    "right_to_work_type": [
        "What is your Right to Work Type?",
        "What type of visa do you currently hold?",
        "What is your work permit type?",
    ],
    "authorization": [
        "Are you authorized to work in the UK?",
        "Do you have the right to work in the United Kingdom?",
        "Are you legally allowed to work in the UK?",
        "Are you eligible to work in the UK?",
    ],
    "sponsorship": [
        "Do you require visa sponsorship?",
        "Will you need sponsorship to work in the UK?",
        "Do you now or in the future require sponsorship?",
    ],
    "visa_status": [
        "What is your current visa status?",
        "What is your immigration status?",
    ],
    # Salary
    "current_salary": [
        "What is your current salary?",
        "What is your present salary?",
    ],
    "expected_salary": [
        "What is your expected salary?",
        "What are your salary expectations?",
        "Desired compensation?",
        "What is your target salary?",
    ],
    # Employment
    "currently_employed": [
        "Are you currently employed?",
        "What is your current employment status?",
    ],
    "current_title": [
        "What is your current job title?",
        "What is your current role?",
    ],
    "current_employer": [
        "Who is your current employer?",
        "What company do you work for?",
    ],
    # Location
    "location": [
        "What is your current location?",
        "Where are you based?",
        "What city do you live in?",
        "Where are you currently located?",
    ],
    # Education
    "education_level": [
        "What is your highest level of education?",
        "What is your highest qualification?",
    ],
    "degree_subject": [
        "What is your degree subject?",
        "What is your field of study?",
    ],
    # Diversity — critical collision zone
    "gender": [
        "What is your gender?",
        "Please indicate your gender identity",
        "What is your gender identity?",
    ],
    "orientation": [
        "What is your sexual orientation?",
        "Please indicate your sexual orientation",
    ],
    "ethnicity": [
        "What is your ethnicity?",
        "What is your ethnic background?",
        "Please select your racial background",
    ],
    "disability": [
        "Do you have a disability?",
        "Do you consider yourself disabled?",
        "Do you have a long-term health condition?",
    ],
    "religion": [
        "What is your religion?",
        "What is your religion or belief?",
    ],
    "marital_status": [
        "What is your marital status?",
        "What is your relationship status?",
    ],
    "pronouns": [
        "What are your preferred pronouns?",
        "Please indicate your pronouns",
    ],
    "nationality": [
        "What is your nationality?",
        "What is your country of citizenship?",
    ],
    # Other
    "driving": [
        "Do you have a valid UK driving licence?",
        "Do you have a valid driver's license?",
    ],
    "background_check": [
        "Are you willing to undergo a background check?",
        "Do you have any unspent criminal convictions?",
        "Are you willing to undergo a DBS check?",
    ],
    "security_clearance": [
        "What level of security clearance do you hold?",
        "Do you hold SC or DV clearance?",
    ],
    "management": [
        "Do you have management experience?",
        "Do you have leadership experience?",
    ],
    "direct_reports": [
        "How many direct reports have you managed?",
        "How many people have you managed?",
    ],
    "uk_resident": [
        "Are you based in the UK?",
        "Are you a UK resident?",
    ],
    "consent": [
        "I consent to having my data processed for recruitment purposes",
        "Do you agree to the privacy policy?",
    ],
    "how_hear": [
        "How did you hear about this job?",
        "How did you find this position?",
    ],
    "referral": [
        "Were you referred by an employee?",
        "Do you have a referral code?",
    ],
}


def _find_matching_pattern(question: str) -> str | None:
    """Return the first COMMON_ANSWERS pattern that matches the question, or None."""
    for pattern in COMMON_ANSWERS:
        if re.search(pattern, question, re.IGNORECASE):
            return pattern
    return None


class TestPositiveMatches:
    """Every example question must match at least one pattern."""

    @pytest.mark.parametrize(
        "category,question",
        [
            (cat, q)
            for cat, questions in POSITIVE_EXAMPLES.items()
            for q in questions
        ],
    )
    def test_question_matches_a_pattern(self, category, question):
        pattern = _find_matching_pattern(question)
        assert pattern is not None, (
            f"Category '{category}': question '{question}' matched NO pattern in COMMON_ANSWERS"
        )


class TestNoCrossCollisions:
    """No question from category X should match a pattern that belongs to category Y.

    This catches bugs like 'ethnicity' matching the location pattern via 'city' substring.
    """

    # Build a map: pattern -> category (first match wins based on dict order)
    PATTERN_TO_CATEGORY: dict[str, str] = {}
    for _cat, _questions in POSITIVE_EXAMPLES.items():
        for _q in _questions:
            _pat = _find_matching_pattern(_q)
            if _pat and _pat not in PATTERN_TO_CATEGORY:
                PATTERN_TO_CATEGORY[_pat] = _cat

    # Critical cross-collision pairs to check
    COLLISION_PAIRS = [
        # (question, must NOT match this category)
        ("What is your ethnicity?", "location"),
        ("What is your ethnic background?", "location"),
        ("What is your sexual orientation?", "gender"),
        ("What is your nationality?", "location"),
        ("What is your current salary?", "expected_salary"),
        ("What is your Right to Work Type?", "authorization"),
        ("Do you have a disability?", "background_check"),
        ("What is your religion?", "location"),
        ("What is your marital status?", "location"),
        ("Are you based in the UK?", "location"),
    ]

    @pytest.mark.parametrize("question,forbidden_category", COLLISION_PAIRS)
    def test_no_cross_collision(self, question, forbidden_category):
        pattern = _find_matching_pattern(question)
        if pattern is None:
            return  # No match at all — not a collision
        matched_cat = self.PATTERN_TO_CATEGORY.get(pattern, "unknown")
        assert matched_cat != forbidden_category, (
            f"COLLISION: '{question}' matched pattern for '{forbidden_category}' "
            f"category (pattern: {pattern})"
        )


class TestPatternOrdering:
    """Specific patterns must match before general ones."""

    ORDERING_TESTS = [
        # (question, expected_answer_substring)
        ("What is your Right to Work Type?", "Graduate Visa"),
        ("What is your current salary?", "22000"),
        ("How many direct reports have you managed?", "8"),
    ]

    @pytest.mark.parametrize("question,expected_substr", ORDERING_TESTS)
    def test_specific_before_general(self, question, expected_substr):
        # Walk patterns in order — first match should contain expected
        for pattern, answer in COMMON_ANSWERS.items():
            if re.search(pattern, question, re.IGNORECASE):
                assert answer is not None, f"First match for '{question}' was LLM (None)"
                assert expected_substr in str(answer), (
                    f"'{question}' first matched pattern '{pattern}' -> '{answer}', "
                    f"expected '{expected_substr}'"
                )
                break
