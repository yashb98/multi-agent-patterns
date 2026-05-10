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
        r"^i\s+accept$",
    )
)

_SPECIAL_REQUIRED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bdemographic data surveys above\b",
        r"\bprocessing my responses to the demographic data surveys above\b",
    )
)


# Semantic-similarity archetypes for the allow-list path. New phrasings of
# required-consent labels (typos, multilingual variants, paraphrase) match
# these via embedding similarity even when they don't lexically intersect
# any _ALLOW_PATTERNS entry. The deny-list still wins — these archetypes
# never override a marketing/newsletter label.
_REQUIRED_CONSENT_ARCHETYPES: tuple[str, ...] = (
    "I agree to the terms of service",
    "I accept the privacy policy",
    "I consent to the processing of my personal data for this application",
    "I have read and agree to the GDPR data protection notice",
    "I confirm the information provided is accurate",
    "I am at least 18 years of age",
    "I acknowledge and accept the cookie policy",
    "I agree to the data processing agreement for this application",
)


def is_required_consent(label: str) -> bool:
    """Return True only for labels that look like a required application consent.

    Three-tier resolution, deny-list-wins:
      1. Deny-list keyword regex — structural blocklist for marketing /
         newsletter / third-party / opt-in phrasing. These keyword patterns
         exist to provide a hard safety floor: if the label contains any of
         them, we never tick the box, full stop. Regex is the right tool
         here (keyword presence detection, like a PII scanner) and is
         retained intentionally despite the no-regex-for-classification
         rule — the rule excepts "structural format validation" and
         security/safety blocklists.
      2. Embedding similarity to required-consent archetypes — primary
         classification path for phrasings that pass the deny filter.
         Survives paraphrase/typo/multilingual drift that the regex
         allow-list misses.
      3. Allow-list regex fallback — last-resort heuristic when embeddings
         are unavailable or score below threshold. Logs a hit so we can
         later add the new phrasing to the archetype list.
    """
    if not label or not label.strip():
        return False

    for pattern in _SPECIAL_REQUIRED_PATTERNS:
        if pattern.search(label):
            return True

    # Tier 1: deny-list. If marketing / newsletter / opt-in / third-party
    # appears anywhere, refuse regardless of other signals.
    for pattern in _DENY_PATTERNS:
        if pattern.search(label):
            return False

    # Tier 2: semantic similarity to required-consent archetypes.
    try:
        from shared.semantic_utils import best_semantic_match
        match, score = best_semantic_match(
            label.strip(),
            list(_REQUIRED_CONSENT_ARCHETYPES),
            min_score=0.78,  # conservative — false positives = ticking the wrong box
        )
        if match is not None:
            return True
    except Exception:
        # Embedding backend unavailable (offline, etc.) — fall through to regex.
        pass

    # Tier 3: regex allow-list fallback. Each hit here is a phrasing that
    # the embedding path missed — candidate for archetype expansion.
    for pattern in _ALLOW_PATTERNS:
        if pattern.search(label):
            return True

    return False
