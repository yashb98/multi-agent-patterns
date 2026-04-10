"""Structured agent results — unified error handling across all dispatchers and agents.

DispatchError provides structured error context (category, retryability, partial results)
while still converting to user-friendly strings for Telegram display.

classify_error() maps exceptions to error categories for consistent handling.
"""

from __future__ import annotations


class DispatchError:
    """Structured error for agent/dispatcher failures.

    Fields follow the error-handling convention in .claude/rules/error-handling.md.
    """

    def __init__(
        self,
        error_category: str,
        message: str,
        is_retryable: bool = False,
        partial_results: str | None = None,
        agent_name: str = "",
        attempted_action: str = "",
    ):
        self.error_category = error_category
        self.message = message
        self.is_retryable = is_retryable
        self.partial_results = partial_results
        self.agent_name = agent_name
        self.attempted_action = attempted_action

    def to_dict(self) -> dict:
        return {
            "status": "error",
            "errorCategory": self.error_category,
            "message": self.message,
            "isRetryable": self.is_retryable,
            "partialResults": self.partial_results,
            "agentName": self.agent_name,
            "attemptedAction": self.attempted_action,
        }

    def to_user_message(self) -> str:
        """Format error for Telegram display."""
        retry_hint = " Try again in a moment." if self.is_retryable else ""
        partial = (
            f"\n\nPartial result:\n{self.partial_results}"
            if self.partial_results
            else ""
        )
        return f"⚠️ {self.agent_name} error ({self.error_category}): {self.message}{retry_hint}{partial}"

    def __str__(self) -> str:
        return self.to_user_message()


class Result:
    """
    Lightweight Result[T, DispatchError] type for agent return values.

    Agents that want typed success/failure wrapping use this instead of
    returning bare strings or raising exceptions.

    Usage::

        def my_agent(cmd) -> Result:
            try:
                data = do_work()
                return Result.ok(data)
            except Exception as e:
                cat, retry = classify_error(e)
                return Result.err(DispatchError(cat, str(e), retry,
                                               agent_name="my_agent"))

        # Caller:
        result = my_agent(cmd)
        if result.is_ok:
            send(result.value)
        else:
            send(result.error.to_user_message())

        # Or collapse to string in one call:
        send(result.unwrap())
    """

    def __init__(self, value: str | None, error: "DispatchError | None"):
        self._value = value
        self._error = error

    @classmethod
    def ok(cls, value: str) -> "Result":
        return cls(value=value, error=None)

    @classmethod
    def err(cls, error: "DispatchError") -> "Result":
        return cls(value=None, error=error)

    @property
    def is_ok(self) -> bool:
        return self._error is None

    @property
    def value(self) -> str:
        if self._error is not None:
            raise ValueError("Result is an error — check is_ok first")
        return self._value  # type: ignore[return-value]

    @property
    def error(self) -> "DispatchError":
        if self._error is None:
            raise ValueError("Result is ok — check is_ok first")
        return self._error

    def unwrap(self) -> str:
        """Return value string, or the formatted error message if failed."""
        return self._value if self._error is None else self._error.to_user_message()

    def __bool__(self) -> bool:
        return self.is_ok


def classify_error(e: Exception) -> tuple[str, bool]:
    """Classify an exception into (errorCategory, isRetryable).

    Categories: transient, permission, validation, business.
    """
    err_str = str(e).lower()
    err_type = type(e).__name__

    # Transient: timeouts, rate limits, connection errors
    if any(
        kw in err_str
        for kw in ("timeout", "timed out", "rate limit", "429", "503", "502")
    ):
        return "transient", True
    if any(
        kw in err_type
        for kw in ("Timeout", "ConnectionError", "ConnectionReset")
    ):
        return "transient", True

    # Permission: auth failures
    if any(
        kw in err_str
        for kw in ("401", "403", "unauthorized", "forbidden", "permission")
    ):
        return "permission", False

    # Validation: bad input
    if any(
        kw in err_str
        for kw in ("invalid", "missing", "required", "400", "validation")
    ):
        return "validation", False

    # Default: unknown, not retryable
    return "business", False
