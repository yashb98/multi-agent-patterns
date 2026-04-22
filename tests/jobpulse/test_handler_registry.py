"""Tests for ``jobpulse.handler_registry`` caching semantics.

Phase 0, item 4. ``get_handler_map()`` used to rebuild the map on every
dispatch — 50 imports + dict construction per Telegram message. The new
implementation caches lazily on first call, with two explicit escape hatches:

- Under pytest (``PYTEST_CURRENT_TEST`` env var set), the cache is bypassed
  so module-level ``@patch("jobpulse.dispatcher._handle_*")`` decorators
  still take effect across dispatches.
- :func:`reset_handler_map` clears the cache on demand.

These tests exercise both paths explicitly (by controlling the env var)
because the pytest-bypass is WHAT keeps the rest of the dispatcher test
suite green.
"""

from __future__ import annotations

import os

import pytest

from jobpulse import handler_registry
from jobpulse.command_router import Intent
from jobpulse.handler_registry import (
    get_handler_map,
    get_handler_map_by_value,
    reset_handler_map,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_handler_map()
    yield
    reset_handler_map()


# ─── production-mode caching (env var removed for the test) ──

def test_cache_reuses_map_across_calls(monkeypatch):
    """With PYTEST_CURRENT_TEST removed, second call returns SAME dict."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    reset_handler_map()

    first = get_handler_map()
    second = get_handler_map()
    assert first is second


def test_by_value_cache_reuses_map_across_calls(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    reset_handler_map()

    first = get_handler_map_by_value()
    second = get_handler_map_by_value()
    assert first is second


def test_reset_handler_map_forces_rebuild(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    reset_handler_map()

    first = get_handler_map()
    reset_handler_map()
    second = get_handler_map()
    assert first is not second


# ─── pytest-mode bypass (the one that keeps dispatcher tests green) ──

def test_pytest_env_var_bypasses_cache():
    """The test suite relies on ``@patch("jobpulse.dispatcher._handle_*")``
    between ``dispatch()`` calls. If the registry were cached while pytest
    is running, those patches wouldn't be visible after the first call."""
    assert "PYTEST_CURRENT_TEST" in os.environ

    first = get_handler_map()
    second = get_handler_map()
    # Under pytest we rebuild every call so that `@patch(...)` on
    # ``jobpulse.dispatcher._handle_*`` is re-imported each time.
    assert first is not second


def test_cache_is_not_populated_while_under_pytest():
    """Confirm the internal cache slot stays None under pytest."""
    assert "PYTEST_CURRENT_TEST" in os.environ
    reset_handler_map()
    get_handler_map()
    assert handler_registry._HANDLER_MAP_CACHE is None


# ─── semantic sanity ─

def test_handler_map_covers_all_non_terminal_intents():
    """Every Intent except UNKNOWN/STOP must have a handler."""
    m = get_handler_map()
    missing = (set(Intent) - {Intent.UNKNOWN, Intent.STOP}) - set(m.keys())
    assert missing == set(), f"intents missing handlers: {missing}"


def test_by_value_map_matches_intent_map():
    """get_handler_map_by_value should key by intent.value strings with the
    same handler functions as get_handler_map."""
    by_intent = get_handler_map()
    by_value = get_handler_map_by_value()
    assert set(by_value.keys()) == {i.value for i in by_intent.keys()}
    for intent, handler in by_intent.items():
        assert by_value[intent.value] is handler
