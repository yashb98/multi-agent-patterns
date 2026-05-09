"""Verification engine — composite badge of 5 checks."""

from __future__ import annotations

from typing import Optional

from shared.external_verifiers import semantic_scholar_lookup, PEER_REVIEWED_VENUES
from shared.logging_config import get_logger

logger = get_logger(__name__)


def check_peer_reviewed(arxiv_id: str) -> tuple[Optional[bool], str]:
    """Returns (True/False/None, reason). None = S2 unavailable."""
    data = semantic_scholar_lookup(arxiv_id)
    if data is None:
        return None, "Semantic Scholar unavailable"
    if data.get("is_peer_reviewed"):
        return True, f"venue: {data.get('venue', 'unknown')}"
    venue = (data.get("venue") or "").lower()
    if any(v in venue for v in PEER_REVIEWED_VENUES):
        return True, f"venue: {data.get('venue')}"
    return False, f"venue '{data.get('venue', 'arXiv')}' not in PEER_REVIEWED_VENUES"
