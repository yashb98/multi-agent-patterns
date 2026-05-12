"""Predicate that decides whether to force vision augmentation."""
from jobpulse.form_engine.vision_gate import should_force_vision


def test_forces_vision_when_application_form_has_few_fields_at_high_confidence():
    assert should_force_vision(
        scanner_field_count=9,
        page_type="application_form",
        reasoner_confidence=0.90,
    ) is True


def test_skips_vision_when_scanner_count_is_dense():
    assert should_force_vision(
        scanner_field_count=25,
        page_type="application_form",
        reasoner_confidence=0.90,
    ) is False


def test_skips_vision_for_non_form_pages():
    assert should_force_vision(
        scanner_field_count=0,
        page_type="job_description",
        reasoner_confidence=1.0,
    ) is False


def test_skips_vision_when_reasoner_uncertain():
    assert should_force_vision(
        scanner_field_count=2,
        page_type="application_form",
        reasoner_confidence=0.5,
    ) is False


def test_threshold_at_10_fields():
    assert should_force_vision(10, "application_form", 0.9) is True
    assert should_force_vision(11, "application_form", 0.9) is False


def test_zero_scanner_fields_always_forces_when_form_confident():
    assert should_force_vision(0, "application_form", 0.85) is True
