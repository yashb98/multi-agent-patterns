"""Vision-augment gate for sparse-field scans on confident form pages.

When the reasoner is confident a page is an application_form (>=0.7) but the
DOM scanner returns suspiciously few fields, force the vision LLM to find
what the shape-based scanners missed. The predicate is pure and free; the
actual augment call (vision_augment_scan) is in the same module so callers
have a single import surface.
"""
from __future__ import annotations

# Threshold below which we treat a scan as suspicious. Tuned to the observed
# range (trivial CV-only pages 1-3, sparse screening 6-10, healthy 12-30).
SPARSE_FIELD_THRESHOLD = 10

# Confidence floor — below this, the existing vision_gate already runs vision.
HIGH_CONFIDENCE_FLOOR = 0.7


def should_force_vision(
    scanner_field_count: int,
    page_type: str,
    reasoner_confidence: float,
) -> bool:
    """True when the scanner result looks too sparse for a confident form."""
    if page_type != "application_form":
        return False
    if reasoner_confidence < HIGH_CONFIDENCE_FLOOR:
        return False
    return scanner_field_count <= SPARSE_FIELD_THRESHOLD
