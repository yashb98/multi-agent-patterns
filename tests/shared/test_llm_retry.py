"""Tests for shared/llm_retry.py — retry logic with jittered backoff."""

from unittest.mock import patch

import pytest

from shared.llm_retry import is_retryable_error, retry_with_backoff


class TestIsRetryableError:
    def test_rate_limit_pattern(self):
        assert is_retryable_error(Exception("rate limit exceeded"))

    def test_timeout_pattern(self):
        assert is_retryable_error(Exception("Request timed out"))

    def test_status_code_429(self):
        assert is_retryable_error(Exception("HTTP 429"))

    def test_status_code_503(self):
        assert is_retryable_error(Exception("503 Service Unavailable"))

    def test_non_retryable(self):
        assert not is_retryable_error(Exception("invalid api key"))


class TestRetryWithBackoff:
    def test_success_no_retry(self):
        result = retry_with_backoff(lambda: "ok")
        assert result == "ok"

    def test_retries_on_transient_error(self):
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("rate limit exceeded")
            return "recovered"

        with patch("shared.llm_retry.time.sleep"):
            result = retry_with_backoff(flaky, max_retries=3, base_delay=1.0)
        assert result == "recovered"
        assert call_count == 3

    def test_raises_after_max_retries(self):
        with patch("shared.llm_retry.time.sleep"):
            with pytest.raises(Exception, match="rate limit"):
                retry_with_backoff(
                    lambda: (_ for _ in ()).throw(Exception("rate limit")),
                    max_retries=2,
                    base_delay=0.01,
                )

    def test_non_retryable_error_raises_immediately(self):
        call_count = 0

        def bad():
            nonlocal call_count
            call_count += 1
            raise Exception("invalid api key")

        with pytest.raises(Exception, match="invalid api key"):
            retry_with_backoff(bad, max_retries=3)
        assert call_count == 1


class TestJitter:
    def test_jitter_produces_varying_delays(self):
        """Jitter should produce different delays across runs."""
        delays = []

        def capture_delay(d):
            delays.append(d)

        call_count = 0

        def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("rate limit exceeded")
            return "ok"

        with patch("shared.llm_retry.time.sleep", side_effect=capture_delay):
            retry_with_backoff(fail_twice, max_retries=3, base_delay=10.0)

        # Two retries should have produced two delays
        assert len(delays) == 2
        # With base_delay=10 and jitter, delays should differ (extremely unlikely to be equal)
        # We just verify they are positive numbers
        assert all(d > 0 for d in delays)

    def test_jitter_stays_within_bounds(self):
        """Jitter must keep delays between 0.5x and 1.5x of the base exponential delay."""
        delays = []

        def capture_delay(d):
            delays.append(d)

        base_delay = 4.0
        backoff_factor = 2.0
        max_delay = 100.0
        num_samples = 200

        for _ in range(num_samples):
            call_count = 0

            def fail_once():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("rate limit exceeded")
                return "ok"

            with patch("shared.llm_retry.time.sleep", side_effect=capture_delay):
                retry_with_backoff(
                    fail_once,
                    max_retries=1,
                    base_delay=base_delay,
                    max_delay=max_delay,
                    backoff_factor=backoff_factor,
                )

        # attempt=0: base_delay * backoff^0 = 4.0, jitter range [2.0, 6.0)
        expected_base = min(base_delay * (backoff_factor ** 0), max_delay)
        low = expected_base * 0.5
        high = expected_base * 1.5
        for d in delays:
            assert low <= d < high, f"Delay {d} outside [{low}, {high})"

    def test_jitter_not_deterministic(self):
        """Multiple retries with same params should not all produce identical delays."""
        delays = []

        def capture_delay(d):
            delays.append(d)

        for _ in range(20):
            call_count = 0

            def fail_once():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("rate limit exceeded")
                return "ok"

            with patch("shared.llm_retry.time.sleep", side_effect=capture_delay):
                retry_with_backoff(fail_once, max_retries=1, base_delay=10.0)

        # With 20 samples and jitter, we should see at least 2 distinct values
        assert len(set(delays)) >= 2, f"All delays identical: {delays[0]}"
