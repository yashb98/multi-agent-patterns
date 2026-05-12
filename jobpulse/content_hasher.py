"""Structural content hashing for cross-domain form matching.

Computes a fingerprint from a page's field labels and types (structure),
ignoring values, selectors, and options (instance data). Used by PRAXIS
procedural memory for cross-domain generalization.
"""
from __future__ import annotations

import hashlib
import json


def compute_content_hash(fields: list[dict]) -> str:
    """Compute a 16-char hex hash from sorted field (label, type) pairs.

    Order-independent. Ignores values, selectors, options — only structural.
    """
    structural = sorted(
        (f.get("label", "").lower().strip(), f.get("type", "text"))
        for f in fields
    )
    raw = json.dumps(structural, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
