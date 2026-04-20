"""Google API retry — exponential backoff for transient Google API failures.

Wraps Google API calls with retry logic for:
- 429 Too Many Requests
- 500/502/503/504 Server errors
- Connection / timeout errors

Usage:
    from shared.google_retry import call_google_api_with_retry
    results = call_google_api_with_retry(
        lambda: service.events().list(calendarId="primary", ...).execute()
    )
"""

import random
import time

from shared.logging_config import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
BASE_DELAY = 2.0
MAX_DELAY = 30.0


def is_retryable_google_error(error) -> bool:
    """Determine if a Google API error is transient and worth retrying."""
    try:
        from googleapiclient.errors import HttpError
        if isinstance(error, HttpError):
            return error.resp.status in {429, 500, 502, 503, 504}
    except ImportError:
        pass

    error_str = str(error).lower()
    retryable_patterns = [
        "rate limit", "rate_limit", "too many requests",
        "timeout", "timed out",
        "connection", "connect",
        "server error", "internal server",
        "service unavailable", "bad gateway",
        "overloaded", "temporary",
    ]
    return any(p in error_str for p in retryable_patterns)


def call_google_api_with_retry(api_call, max_retries: int = MAX_RETRIES):
    """Execute a Google API call with exponential backoff on transient failures.

    Args:
        api_call: Callable that performs the API call (use lambda for inline calls).
        max_retries: Maximum number of retry attempts.

    Returns:
        The result of api_call().

    Raises:
        The last exception if all retries are exhausted or error is not retryable.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return api_call()
        except Exception as e:
            last_error = e
            if attempt >= max_retries or not is_retryable_google_error(e):
                raise

            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            delay = delay * (0.5 + random.random())  # 50%-150% jitter
            logger.warning(
                "Google API call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                attempt + 1, max_retries + 1, str(e)[:120], delay,
            )
            time.sleep(delay)

    raise last_error  # Safety net — should not reach here
