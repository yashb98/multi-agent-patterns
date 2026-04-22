"""Tests for ``jobpulse.form_engine.consent_policy.is_required_consent``.

The bug this module fixes: a broad substring allow-list (``agree|consent|
terms|privacy|accept|acknowledge``) auto-ticked GDPR-regulated marketing
opt-ins on the user's behalf. This test file pins the deny-list semantics
so that regression is caught immediately.
"""

import pytest

from jobpulse.form_engine.consent_policy import is_required_consent


# ─── REQUIRED CONSENTS — must return True ──────────────────────

REQUIRED_CONSENT_LABELS = [
    "I agree to the terms and conditions",
    "I accept the terms of service",
    "I have read and agree to the privacy policy",
    "I agree to the Privacy Policy and Terms of Use",
    "I acknowledge and accept the terms",
    "I consent to the processing of my personal data for this application",
    "I have read the privacy notice",
    "I accept the cookie policy",
    "I agree to the data processing agreement",
    "GDPR consent",
    "I am at least 18 years old",
    "I confirm the information provided is accurate",
    "I confirm the information is true and correct",
]


@pytest.mark.parametrize("label", REQUIRED_CONSENT_LABELS)
def test_required_consents_are_ticked(label: str) -> None:
    assert is_required_consent(label) is True, (
        f"Required consent was rejected: {label!r}"
    )


# ─── MARKETING / OPT-IN — must return False ────────────────────
# This is the core safety regression set. If any of these come back True
# we're auto-ticking a GDPR opt-in again.

MARKETING_LABELS = [
    "I consent to receive marketing emails",
    "I agree to receive newsletters",
    "Subscribe me to the newsletter",
    "I would like to receive promotional offers",
    "I consent to share my profile with third parties",
    "I agree to share my data with partners for marketing purposes",
    "Send me job alerts via email",
    "Email me about similar job opportunities",
    "Contact me about future roles",
    "Add me to the talent community",
    "Join our talent network",
    "I agree to receive SMS messages",
    "I consent to text messages",
    "Send me WhatsApp updates",
    "I agree to participate in research surveys",
    "I opt-in to receive marketing communications",
    "I accept receiving commercial emails",
    "I agree to advertising communications",
    "Share my information with our affiliated companies",
    # The classic: terms-style phrasing bolted onto a marketing consent.
    "I agree to the marketing communications policy",
    "I consent to marketing under the privacy policy",
    "I agree to privacy policy AND to receive newsletters",
]


@pytest.mark.parametrize("label", MARKETING_LABELS)
def test_marketing_consents_are_never_ticked(label: str) -> None:
    assert is_required_consent(label) is False, (
        f"GDPR regression — marketing consent was auto-ticked: {label!r}"
    )


# ─── AMBIGUOUS / NON-CONSENT — must return False ───────────────

NON_CONSENT_LABELS = [
    "I have a disability",
    "Include a cover letter",
    "I am authorized to work in the US",
    "I require sponsorship",
    "",
    "   ",
    "Consent",  # bare word, no phrase — conservatively rejected
    "Accept",
    "Agree",
    "Privacy",
    "Terms",
]


@pytest.mark.parametrize("label", NON_CONSENT_LABELS)
def test_ambiguous_or_unrelated_labels_default_unchecked(label: str) -> None:
    assert is_required_consent(label) is False, (
        f"Ambiguous label was auto-ticked: {label!r}"
    )


# ─── LEGACY bug cases — explicitly guard against the old behavior ─

def test_old_substring_allowlist_bug_no_longer_ticks_marketing_consent() -> None:
    """Regression: the pre-fix code matched the substring "consent" in
    "I consent to receive marketing emails" and auto-ticked the box.
    """
    assert is_required_consent("I consent to receive marketing emails") is False


def test_old_substring_allowlist_bug_no_longer_ticks_newsletter_agreement() -> None:
    """Regression: the pre-fix code matched "agree" in "I agree to the
    newsletter" and auto-ticked it.
    """
    assert is_required_consent("I agree to receive the newsletter") is False


def test_deny_wins_over_allow() -> None:
    """Deny-list must beat allow-list even when both match the label."""
    # Contains "privacy policy" (allow) AND "marketing" (deny)
    label = "I agree to the privacy policy and to receive marketing emails"
    assert is_required_consent(label) is False


# ─── Case-insensitivity ─────────────────────────────────────────

def test_policy_is_case_insensitive() -> None:
    assert is_required_consent("I AGREE TO THE TERMS AND CONDITIONS") is True
    assert is_required_consent("i consent to receive MARKETING emails") is False
