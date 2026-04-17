"""Tone framework — bans corporate-speak, injects concrete proof points.

Post-processes screening answers and cover letter text.
"""

from __future__ import annotations

import re

from shared.logging_config import get_logger

logger = get_logger(__name__)

BANNED_PHRASES = [
    "passionate about",
    "results-oriented",
    "proven track record",
    "leveraged",
    "spearheaded",
    "facilitated",
    "synergies",
    "robust",
    "seamless",
    "cutting-edge",
    "innovative",
    "just checking in",
    "just following up",
    "touching base",
    "circling back",
    "i would love the opportunity",
    "in today's fast-paced world",
    "demonstrated ability to",
    "strong communicator",
    "self-starter",
    "team player",
    "go-getter",
    "think outside the box",
    "hit the ground running",
]

_BANNED_PATTERNS = [re.compile(re.escape(p), re.IGNORECASE) for p in BANNED_PHRASES]

_QUESTION_PATTERNS = {
    "why_this_role": re.compile(r"why.*(this|the) (role|position|job)", re.IGNORECASE),
    "why_this_company": re.compile(r"why.*(this|our|the) (company|org)", re.IGNORECASE),
    "relevant_experience": re.compile(r"(relevant|related).*(experience|background|work)", re.IGNORECASE),
    "good_fit": re.compile(r"(good|great|strong) fit|why should we", re.IGNORECASE),
    "how_heard": re.compile(r"how did you (hear|find|learn)", re.IGNORECASE),
    "additional_info": re.compile(r"additional.*(info|anything|share)", re.IGNORECASE),
}


def contains_banned_phrase(text: str) -> bool:
    """Check if text contains any banned corporate-speak phrases."""
    return any(p.search(text) for p in _BANNED_PATTERNS)


def classify_question_type(question: str) -> str:
    """Classify a screening question into a known type."""
    for qtype, pattern in _QUESTION_PATTERNS.items():
        if pattern.search(question):
            return qtype
    return "other"


def _remove_banned(text: str) -> str:
    """Remove banned phrases from text, replacing with empty string and cleaning whitespace."""
    for pattern in _BANNED_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"^\s*[,.]", "", text).strip()
    return text


def apply_tone(answer: str, question: str, listing) -> str:
    """Apply tone framework to a screening answer.

    Removes banned phrases. Returns cleaned answer.
    """
    if not answer:
        return answer

    result = _remove_banned(answer)

    if not result or len(result) < 10:
        return answer

    return result
