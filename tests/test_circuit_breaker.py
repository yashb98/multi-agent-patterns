"""Tests for shared/circuit_breaker.py — circuit breaker for external services."""

import time
import threading
from unittest.mock import MagicMock

import pytest

from shared.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    ddg_breaker,
    s2_breaker,
    web_breaker,
)


@pytest.fixture
def breaker():
    """Circuit breaker with low thresholds for fast testing."""
    return CircuitBreaker("test_service", failure_threshold=3, cooldown_seconds=0.2)


# ─── INITIAL STATE ────────────────────────────────────────────────


class TestInitialState:
    def test_starts_closed(self, breaker):
        assert breaker.state == CircuitState.CLOSED

    def test_name_stored(self, breaker):
        assert breaker.name == "test_service"

    def test_repr_contains_state(self, breaker):
        r = repr(breaker)
        assert "test_service" in r
        assert "closed" in r


# ─── SUCCESSFUL CALLS ─────────────────────────────────────────────


class TestSuccessfulCalls:
    def test_success_returns_result(self, breaker):
        result = breaker.call(fn=lambda: 42)
        assert result == 42

    def test_success_stays_closed(self, breaker):
        breaker.call(fn=lambda: "ok")
        breaker.call(fn=lambda: "ok")
        assert breaker.state == CircuitState.CLOSED

    def test_success_resets_failure_count(self, breaker):
        """A success after some failures should reset the counter."""
        failing = MagicMock(side_effect=[ValueError, ValueError, "ok"])
        breaker.call(fn=failing, fallback="fb")  # fail 1
        breaker.call(fn=failing, fallback="fb")  # fail 2
        breaker.call(fn=failing)                  # success — resets counter
        assert breaker.state == CircuitState.CLOSED
        assert breaker._failure_count == 0


# ─── FAILURE HANDLING ──────────────────────────────────────────────


class TestFailureHandling:
    def test_failure_returns_fallback(self, breaker):
        result = breaker.call(
            fn=lambda: (_ for _ in ()).throw(ConnectionError("down")),
            fallback="unavailable",
        )
        assert result == "unavailable"

    def test_failure_increments_counter(self, breaker):
        breaker.call(fn=self._failing_fn, fallback=None)
        assert breaker._failure_count == 1

    def test_below_threshold_stays_closed(self, breaker):
        for _ in range(2):  # threshold is 3
            breaker.call(fn=self._failing_fn, fallback=None)
        assert breaker.state == CircuitState.CLOSED

    def test_at_threshold_transitions_to_open(self, breaker):
        for _ in range(3):
            breaker.call(fn=self._failing_fn, fallback=None)
        assert breaker.state == CircuitState.OPEN

    @staticmethod
    def _failing_fn():
        raise ConnectionError("service down")


# ─── OPEN STATE ────────────────────────────────────────────────────


class TestOpenState:
    def test_open_returns_fallback_without_calling_fn(self, breaker):
        # Trip the breaker
        for _ in range(3):
            breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback=None)

        spy = MagicMock(return_value="should not be called")
        result = breaker.call(fn=spy, fallback="fast_fail")
        assert result == "fast_fail"
        spy.assert_not_called()

    def test_open_uses_fallback_fn(self, breaker):
        for _ in range(3):
            breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback=None)

        result = breaker.call(
            fn=lambda: "nope",
            fallback_fn=lambda: {"status": "degraded"},
        )
        assert result == {"status": "degraded"}


# ─── HALF_OPEN STATE ──────────────────────────────────────────────


class TestHalfOpen:
    def test_transitions_to_half_open_after_cooldown(self, breaker):
        for _ in range(3):
            breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback=None)
        assert breaker.state == CircuitState.OPEN

        # Wait for cooldown
        time.sleep(0.25)
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_success_transitions_to_closed(self, breaker):
        # Trip open
        for _ in range(3):
            breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback=None)

        time.sleep(0.25)
        assert breaker.state == CircuitState.HALF_OPEN

        # Successful probe
        result = breaker.call(fn=lambda: "recovered")
        assert result == "recovered"
        assert breaker.state == CircuitState.CLOSED

    def test_half_open_failure_transitions_back_to_open(self, breaker):
        # Trip open
        for _ in range(3):
            breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback=None)

        time.sleep(0.25)
        assert breaker.state == CircuitState.HALF_OPEN

        # Failed probe
        breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback="fb")
        assert breaker.state == CircuitState.OPEN


# ─── RESET ─────────────────────────────────────────────────────────


class TestReset:
    def test_manual_reset_to_closed(self, breaker):
        for _ in range(3):
            breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback=None)
        assert breaker.state == CircuitState.OPEN

        breaker.reset()
        assert breaker.state == CircuitState.CLOSED
        assert breaker._failure_count == 0


# ─── THREAD SAFETY ─────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_failures_trip_breaker(self):
        breaker = CircuitBreaker("concurrent", failure_threshold=5, cooldown_seconds=10)
        errors = []

        def fail_call():
            try:
                breaker.call(
                    fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                    fallback="fb",
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fail_call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # After 10 failures (threshold=5), breaker must be OPEN
        assert breaker.state == CircuitState.OPEN
        assert len(errors) == 0  # All failures handled via fallback

    def test_concurrent_mixed_calls(self):
        breaker = CircuitBreaker("mixed", failure_threshold=100, cooldown_seconds=10)
        results = []

        def success_call():
            r = breaker.call(fn=lambda: "ok")
            results.append(r)

        threads = [threading.Thread(target=success_call) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r == "ok" for r in results)
        assert breaker.state == CircuitState.CLOSED


# ─── PRE-CONFIGURED BREAKERS ──────────────────────────────────────


class TestPreConfiguredBreakers:
    def test_ddg_breaker_exists(self):
        assert ddg_breaker.name == "duckduckgo"
        assert ddg_breaker.failure_threshold == 3
        assert ddg_breaker.cooldown_seconds == 60

    def test_s2_breaker_exists(self):
        assert s2_breaker.name == "semantic_scholar"
        assert s2_breaker.failure_threshold == 3
        assert s2_breaker.cooldown_seconds == 120

    def test_web_breaker_exists(self):
        assert web_breaker.name == "web_search"
        assert web_breaker.failure_threshold == 5
        assert web_breaker.cooldown_seconds == 90

    def test_preconfigured_breakers_start_closed(self):
        # Reset them in case other tests modified state
        for b in (ddg_breaker, s2_breaker, web_breaker):
            b.reset()
            assert b.state == CircuitState.CLOSED


# ─── EDGE CASES ────────────────────────────────────────────────────


class TestEdgeCases:
    def test_fallback_none_by_default(self, breaker):
        result = breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError))
        assert result is None

    def test_success_threshold_greater_than_one(self):
        breaker = CircuitBreaker("multi", failure_threshold=2, cooldown_seconds=0.1, success_threshold=2)
        # Trip open
        breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback=None)
        breaker.call(fn=lambda: (_ for _ in ()).throw(RuntimeError), fallback=None)
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.15)
        assert breaker.state == CircuitState.HALF_OPEN

        # First success — still half_open (need 2)
        breaker.call(fn=lambda: "ok")
        assert breaker.state == CircuitState.HALF_OPEN

        # Second success — now closed
        breaker.call(fn=lambda: "ok")
        assert breaker.state == CircuitState.CLOSED
