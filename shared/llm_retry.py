"""LLM Retry — exponential backoff for transient LLM failures.

Wraps LLM calls with retry logic for:
- 429 Too Many Requests (rate limit)
- 500/502/503 Server errors
- Timeout errors
- Connection errors

Integrates with both OpenAI client and LangChain ChatOpenAI.
"""

import time
from functools import wraps

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 2.0  # seconds
MAX_DELAY = 30.0  # seconds
BACKOFF_FACTOR = 2.0

# HTTP status codes that are retryable
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def is_retryable_error(error: Exception) -> bool:
    """Determine if an error is transient and worth retrying."""
    error_str = str(error).lower()

    # OpenAI-specific errors
    try:
        from openai import RateLimitError, APITimeoutError, APIConnectionError, InternalServerError
        if isinstance(error, (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)):
            return True
    except ImportError:
        pass

    # HTTP status code in error message
    for code in RETRYABLE_STATUS_CODES:
        if str(code) in error_str:
            return True

    # Common transient error patterns
    retryable_patterns = [
        "rate limit", "rate_limit", "too many requests",
        "timeout", "timed out",
        "connection", "connect",
        "server error", "internal server",
        "service unavailable", "bad gateway",
        "overloaded",
    ]
    return any(p in error_str for p in retryable_patterns)


def retry_with_backoff(
    fn,
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY,
    max_delay: float = MAX_DELAY,
    backoff_factor: float = BACKOFF_FACTOR,
):
    """Execute a function with exponential backoff on transient failures.

    Args:
        fn: Callable to execute (no args — use lambda or partial)
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        backoff_factor: Multiplier for each retry

    Returns:
        The result of fn()

    Raises:
        The last exception if all retries are exhausted
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt >= max_retries or not is_retryable_error(e):
                raise

            delay = min(base_delay * (backoff_factor ** attempt), max_delay)
            logger.warning(
                "LLM call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                attempt + 1, max_retries + 1, str(e)[:100], delay,
            )
            time.sleep(delay)

    raise last_error  # Should never reach here, but safety net


def resilient_llm_call(llm, messages, **kwargs):
    """Call a LangChain LLM with automatic retry on transient failures.

    Usage:
        from shared.llm_retry import resilient_llm_call
        response = resilient_llm_call(llm, [SystemMessage(...), HumanMessage(...)])
    """
    return retry_with_backoff(lambda: llm.invoke(messages, **kwargs))


def resilient_openai_call(client_create_fn, **kwargs):
    """Call OpenAI client.chat.completions.create with automatic retry.

    Usage:
        from shared.llm_retry import resilient_openai_call
        response = resilient_openai_call(client.chat.completions.create, model=..., messages=...)
    """
    return retry_with_backoff(lambda: client_create_fn(**kwargs))
