"""Tests for BasePlatformStrategy ABC and registry."""
import pytest
from jobpulse.ats_adapters.strategy import (
    BasePlatformStrategy,
    get_strategy,
    register_strategy,
    _STRATEGY_REGISTRY,
)


def test_get_strategy_returns_generic_for_unknown():
    strategy = get_strategy("nonexistent_platform")
    assert strategy.name == "generic"


def test_get_strategy_returns_generic_for_none():
    strategy = get_strategy(None)
    assert strategy.name == "generic"


def test_register_strategy_decorator():
    @register_strategy
    class _TestStrategy(BasePlatformStrategy):
        name = "_test_dummy"

        def detect(self, url: str) -> bool:
            return "_test_" in url

    try:
        result = get_strategy("_test_dummy")
        assert result.name == "_test_dummy"
        assert result.detect("https://_test_example.com")
    finally:
        _STRATEGY_REGISTRY.pop("_test_dummy", None)


def test_base_strategy_defaults():
    @register_strategy
    class _DefaultsStrategy(BasePlatformStrategy):
        name = "_test_defaults"
        def detect(self, url): return False

    try:
        s = get_strategy("_test_defaults")
        assert s.min_page_time == 5.0
        assert s.max_form_pages == 20
        assert s.extra_label_mappings() == {}
        assert s.next_page_selectors() == []
        assert s.apply_button_selectors() == []
        assert s.submit_selectors() == []
        assert s.field_fill_overrides() == {}
        # screening answers come from ScreeningPipeline at runtime — strategies
        # MUST NOT define a hardcoded `screening_defaults` method (PII policy).
        assert not hasattr(s, "screening_defaults")
    finally:
        _STRATEGY_REGISTRY.pop("_test_defaults", None)


def test_base_strategy_form_container_hint_returns_none():
    strategy = get_strategy("generic")
    assert strategy.form_container_hint() is None


def test_base_strategy_expected_field_range_default():
    strategy = get_strategy("generic")
    assert strategy.expected_field_range() == (1, 30)


def test_linkedin_strategy_form_container_hint():
    strategy = get_strategy("linkedin")
    assert strategy.form_container_hint() == ".jobs-easy-apply-modal"


def test_linkedin_strategy_expected_field_range():
    strategy = get_strategy("linkedin")
    assert strategy.expected_field_range() == (3, 10)


def test_workday_strategy_expected_field_range():
    strategy = get_strategy("workday")
    assert strategy.expected_field_range() == (3, 20)


def test_greenhouse_strategy_form_container_hint():
    strategy = get_strategy("greenhouse")
    assert strategy.form_container_hint() == "#application"


def test_greenhouse_strategy_expected_field_range():
    strategy = get_strategy("greenhouse")
    assert strategy.expected_field_range() == (3, 15)
