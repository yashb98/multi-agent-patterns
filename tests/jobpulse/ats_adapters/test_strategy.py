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
        assert s.next_button_selectors() == []
        assert s.screening_defaults() == {}
        assert s.field_fill_overrides() == {}
    finally:
        _STRATEGY_REGISTRY.pop("_test_defaults", None)
