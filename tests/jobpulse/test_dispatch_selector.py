"""Tests for :mod:`jobpulse.dispatch` — the DispatchStrategy selector.

Phase 1, item 2. Previously every caller (telegram/webhook/slack/discord/
multi_bot) carried its own ``USE_SWARM = os.getenv("JOBPULSE_SWARM", ...)``
+ conditional import. That duplication is gone — the selector now lives
in one place and the strategy is an enum callers can pass explicitly.

These tests pin down:

- ``default_strategy()`` reads ``JOBPULSE_SWARM`` with the same truthy
  spellings the old code accepted (true/1/yes/on → SWARM, else FLAT).
- ``dispatch(cmd)`` with no strategy uses the resolved default.
- ``dispatch(cmd, strategy=...)`` overrides the env entirely — tests and
  benchmarks no longer need to monkeypatch env vars to force a strategy.
- The selector delegates to the correct underlying module without
  re-implementing any dispatch logic.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jobpulse.command_router import Intent, ParsedCommand
from jobpulse.dispatch import DispatchStrategy, default_strategy, dispatch


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("JOBPULSE_SWARM", raising=False)
    yield


@pytest.fixture
def cmd():
    return ParsedCommand(intent=Intent.HELP, raw="help", args="")


# ─── default_strategy() env parsing ──

@pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes", "on", " true "])
def test_env_truthy_selects_swarm(monkeypatch, val):
    monkeypatch.setenv("JOBPULSE_SWARM", val)
    assert default_strategy() is DispatchStrategy.SWARM


@pytest.mark.parametrize("val", ["false", "0", "no", "off", "nonsense", ""])
def test_env_falsy_selects_flat(monkeypatch, val):
    monkeypatch.setenv("JOBPULSE_SWARM", val)
    assert default_strategy() is DispatchStrategy.FLAT


def test_env_unset_defaults_to_swarm(monkeypatch):
    monkeypatch.delenv("JOBPULSE_SWARM", raising=False)
    assert default_strategy() is DispatchStrategy.SWARM


# ─── dispatch() delegation ──

def test_dispatch_with_swarm_calls_swarm_module(cmd):
    with patch("jobpulse.swarm_dispatcher.dispatch", return_value="swarm-result") as m:
        out = dispatch(cmd, strategy=DispatchStrategy.SWARM)
    assert out == "swarm-result"
    m.assert_called_once_with(cmd)


def test_dispatch_with_flat_calls_flat_module(cmd):
    with patch("jobpulse.dispatcher.dispatch", return_value="flat-result") as m:
        out = dispatch(cmd, strategy=DispatchStrategy.FLAT)
    assert out == "flat-result"
    m.assert_called_once_with(cmd)


def test_dispatch_default_uses_env_strategy(cmd, monkeypatch):
    monkeypatch.setenv("JOBPULSE_SWARM", "false")
    with (
        patch("jobpulse.dispatcher.dispatch", return_value="flat") as flat_mock,
        patch("jobpulse.swarm_dispatcher.dispatch", return_value="swarm") as swarm_mock,
    ):
        out = dispatch(cmd)
    assert out == "flat"
    flat_mock.assert_called_once_with(cmd)
    swarm_mock.assert_not_called()


def test_dispatch_explicit_override_beats_env(cmd, monkeypatch):
    """Explicit strategy= wins over JOBPULSE_SWARM so tests can force a path."""
    monkeypatch.setenv("JOBPULSE_SWARM", "true")
    with patch("jobpulse.dispatcher.dispatch", return_value="forced-flat") as m:
        out = dispatch(cmd, strategy=DispatchStrategy.FLAT)
    assert out == "forced-flat"
    m.assert_called_once()


# ─── enum contract ──

def test_dispatch_strategy_has_exactly_two_variants():
    """Guard against accidentally exporting a placeholder third strategy."""
    assert set(DispatchStrategy) == {DispatchStrategy.FLAT, DispatchStrategy.SWARM}


def test_dispatch_strategy_is_str_enum():
    """Values are strings so log lines / env persistence stay readable."""
    assert DispatchStrategy.FLAT.value == "flat"
    assert DispatchStrategy.SWARM.value == "swarm"
