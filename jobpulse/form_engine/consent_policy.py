"""Consent-checkbox policy: decide which checkboxes the agent may auto-tick.

Historical bug: a broad substring allow-list (``agree|consent|terms|privacy|
accept|acknowledge``) auto-checked GDPR-regulated marketing opt-ins such as
"I consent to receive marketing emails" or "I agree to share my profile with
third-party partners". Ticking those on a user's behalf is a legal exposure
we don't get to opt out of.

This module centralizes the policy:

    is_required_consent(label)  # True only if the label is a *required*
                                # application consent that the user must
                                # accept to submit (terms of service /
                                # privacy policy / data processing for this
                                # application). Marketing, third-party
                                # sharing, newsletters, research panels, and
                                # anything else opt-in is NEVER ticked.

Both ``NativeFormFiller._check_consent`` and
``form_engine.checkbox_filler._is_consent_checkbox`` delegate here so the
policy lives in exactly one place.
"""

from __future__ import annotations

import re


# Deny-list wins. If any of these phrases appear in the label, we never tick
# the box regardless of whether allow-list terms are also present — a label
# like "I consent to receive marketing emails" matches both and must be
# rejected.
_DENY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bmarketing\b",
        r"\bnewsletter(s)?\b",
        r"\bpromotion(s|al)?\b",
        r"\bsubscribe\b",
        r"\bsubscription\b",
        r"\bopt[\s\-]?in\b",
        r"\bthird[\s\-]?part(y|ies)\b",
        r"\bpartners?\b",
        r"\baffiliate(s|d)?\b",
        r"\badvertising\b",
        r"\badvertis(e|er|ers|ing)\b",
        r"\bsurvey(s)?\b",
        r"\bresearch panel\b",
        r"\btalent (community|network|pool)\b",
        r"\bfuture (job |role |position |opportunit)",
        r"\bsimilar (job|role|position|opportunit)",
        r"\bother (job|role|position|opportunit)",
        r"\bsms\b",
        r"\btext message",
        r"\bwhatsapp\b",
        r"\bemail (me|us|updates|alerts|communications?)\b",
        # Sales / commercial communications
        r"\bsales (team|emails|communications?)\b",
        r"\bcommercial (emails?|communications?)\b",
    )
)

# Allow-list: only required application consents. Must be a *phrase* or word
# boundary match so "consent to marketing" doesn't sneak through the deny
# filter only to hit "consent" here. We intentionally keep this short —
# unknown labels are rejected by default.
_ALLOW_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(terms(\s+(of|and))?\s+(use|service|conditions))\b",
        r"\bterms\s*(&|and)\s*conditions\b",
        r"\b(privacy\s+(policy|notice|statement))\b",
        r"\b(data\s+(processing|protection)\s+(agreement|policy|notice))\b",
        r"\b(gdpr)\b",
        r"\b(acknowledge(d)?\s+(and\s+)?(accept(ed)?|agree(d)?)\s+(the|to\s+the)?)",
        r"\b(i\s+)?agree\s+to\s+the\s+(terms|privacy|conditions|policy)\b",
        r"\b(i\s+)?accept\s+the\s+(terms|privacy|conditions|policy)\b",
        r"\b(i\s+)?consent\s+to\s+the\s+(terms|privacy|processing\s+of\s+my)\b",
        r"\b(i\s+)?have\s+read\s+(and\s+)?(agree|accept|understood)",
        r"\b(cookie\s+policy)\b",
        r"\b(i\s+am\s+(at\s+least\s+)?18)\b",
        r"\b(confirm.{0,40}\b(accuracy|true|correct))\b",
        r"\b(information\s+(is|provided).{0,30}\b(accurate|true|correct))\b",
    )
)


def is_required_consent(label: str) -> bool:
    """Return True only for labels that look like a required application consent.

    The function is conservative by design: anything ambiguous returns False
    so the checkbox is left unchecked and the user can handle it manually
    (or the correction-capture flow learns that it should have been ticked).

    Rules:
    1. Empty / whitespace-only label → False.
    2. Any deny-list pattern matches → False (even if allow-list also matches).
    3. Any allow-list pattern matches → True.
    4. Default → False.
    """
    if not label or not label.strip():
        return False

    for pattern in _DENY_PATTERNS:
        if pattern.search(label):
            return False

    for pattern in _ALLOW_PATTERNS:
        if pattern.search(label):
            return True

    return False
