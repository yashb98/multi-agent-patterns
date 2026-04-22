"""Streaming LLM output — real-time token-by-token generation.

Provides streaming wrappers around LangChain LLMs with:
- Retry-aware streaming (retries on transient failures)
- Pluggable callbacks (terminal, WebSocket, file, custom)
- Graceful fallback to non-streaming invoke() if stream fails
- Full response accumulation for downstream state updates

Usage:
    from shared.streaming import stream_llm, TerminalStreamCallback

    # Stream to terminal with real-time output
    response = stream_llm(llm, messages, callback=TerminalStreamCallback())

    # Stream with custom callback
    response = stream_llm(llm, messages, callback=my_callback)

    # Silent streaming (just accumulate, no output)
    response = stream_llm(llm, messages)
"""

import os
import sys
import threading
from typing import Protocol, runtime_checkable

from shared.cost_tracker import record_llm_usage
from shared.logging_config import get_logger
from shared.llm_retry import is_retryable_error, MAX_RETRIES, BASE_DELAY, BACKOFF_FACTOR, MAX_DELAY

logger = get_logger(__name__)

# ─── GLOBAL STREAMING TOGGLE ─────────────────────────────────
# Enable via: STREAM_LLM_OUTPUT=1 env var or set_streaming_enabled(True)

_streaming_state = threading.local()


def is_streaming_enabled() -> bool:
    """Check if streaming is enabled for the current thread."""
    thread_val = getattr(_streaming_state, "enabled", None)
    if thread_val is not None:
        return thread_val
    return os.environ.get("STREAM_LLM_OUTPUT", "").lower() in ("1", "true", "yes")


def set_streaming_enabled(enabled: bool) -> None:
    """Enable or disable streaming for the current thread."""
    _streaming_state.enabled = enabled


def get_active_callback() -> "StreamCallback | None":
    """Get the active stream callback for the current thread, if any."""
    return getattr(_streaming_state, "callback", None)


def set_active_callback(callback: "StreamCallback | None") -> None:
    """Set the active stream callback for the current thread."""
    _streaming_state.callback = callback


# ─── CALLBACK PROTOCOL ────────────────────────────────────────

@runtime_checkable
class StreamCallback(Protocol):
    """Protocol for streaming output consumers."""

    def on_token(self, token: str) -> None:
        """Called for each token chunk received."""
        ...

    def on_complete(self, full_text: str) -> None:
        """Called when streaming finishes successfully."""
        ...

    def on_error(self, error: Exception) -> None:
        """Called when streaming fails (before retry or final failure)."""
        ...


# ─── BUILT-IN CALLBACKS ──────────────────────────────────────

class TerminalStreamCallback:
    """Streams tokens to stdout in real-time."""

    def __init__(self, prefix: str = "", color: str = ""):
        self.prefix = prefix
        self._started = False
        self.color = color

    def on_token(self, token: str) -> None:
        if not self._started:
            if self.prefix:
                sys.stdout.write(f"\n{self.prefix}")
            self._started = True
        sys.stdout.write(token)
        sys.stdout.flush()

    def on_complete(self, full_text: str) -> None:
        if self._started:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def on_error(self, error: Exception) -> None:
        logger.warning(
            "Stream error: %s",
            str(error)[:100],
            extra={"error_type": type(error).__name__},
        )


class AccumulatorCallback:
    """Silent callback that just accumulates the full response."""

    def __init__(self):
        self.text = ""

    def on_token(self, token: str) -> None:
        self.text += token

    def on_complete(self, full_text: str) -> None:
        pass

    def on_error(self, error: Exception) -> None:
        pass


class MultiCallback:
    """Fan-out to multiple callbacks."""

    def __init__(self, *callbacks: StreamCallback):
        self.callbacks = list(callbacks)

    def on_token(self, token: str) -> None:
        for cb in self.callbacks:
            cb.on_token(token)

    def on_complete(self, full_text: str) -> None:
        for cb in self.callbacks:
            cb.on_complete(full_text)

    def on_error(self, error: Exception) -> None:
        for cb in self.callbacks:
            cb.on_error(error)


# ─── STREAMING CORE ──────────────────────────────────────────

def stream_llm(
    llm,
    messages,
    callback: StreamCallback | None = None,
    max_retries: int = MAX_RETRIES,
    **kwargs,
) -> str:
    """Stream LLM response with retry and callback support.

    Args:
        llm: LangChain LLM instance (must support .stream())
        messages: List of messages to send
        callback: Optional StreamCallback for real-time output
        max_retries: Maximum retry attempts on transient failures
        **kwargs: Additional kwargs passed to llm.stream()

    Returns:
        Full accumulated response text

    Falls back to non-streaming invoke() if streaming is not supported.
    """
    import time

    cb = callback or AccumulatorCallback()
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return _do_stream(llm, messages, cb, **kwargs)
        except Exception as e:
            last_error = e
            cb.on_error(e)

            if attempt >= max_retries or not is_retryable_error(e):
                # Final attempt or non-retryable — try fallback invoke
                logger.warning(
                    "Streaming failed after %d attempts, falling back to invoke()",
                    attempt + 1,
                    extra={"attempts": attempt + 1},
                )
                return _fallback_invoke(llm, messages, cb, **kwargs)

            delay = min(BASE_DELAY * (BACKOFF_FACTOR ** attempt), MAX_DELAY)
            logger.warning(
                "Stream failed (attempt %d/%d): %s. Retrying in %.1fs...",
                attempt + 1, max_retries + 1, str(e)[:100], delay,
                extra={
                    "attempt": attempt + 1,
                    "max_retries": max_retries + 1,
                    "delay_seconds": round(delay, 3),
                    "error_type": type(e).__name__,
                },
            )
            time.sleep(delay)

    # Should not reach here
    return _fallback_invoke(llm, messages, cb, **kwargs)


def _do_stream(llm, messages, cb: StreamCallback, **kwargs) -> str:
    """Execute streaming and accumulate result."""
    accumulated = []

    for chunk in llm.stream(messages, **kwargs):
        # LangChain chunk types vary — extract content
        if hasattr(chunk, "content"):
            token = chunk.content
        elif isinstance(chunk, str):
            token = chunk
        else:
            token = str(chunk)

        if token:
            accumulated.append(token)
            cb.on_token(token)

    full_text = "".join(accumulated)
    cb.on_complete(full_text)
    return full_text


def _fallback_invoke(llm, messages, cb: StreamCallback, **kwargs) -> str:
    """Non-streaming fallback when .stream() fails."""
    from shared.llm_retry import resilient_llm_call

    response = resilient_llm_call(llm, messages, **kwargs)
    full_text = response.content if hasattr(response, "content") else str(response)
    cb.on_complete(full_text)
    return full_text


# ─── SMART CALL (AUTO-SWITCH) ────────────────────────────────

def smart_llm_call(llm, messages, **kwargs):
    """Call LLM with automatic streaming/non-streaming selection.

    If streaming is enabled (STREAM_LLM_OUTPUT=1 or set_streaming_enabled(True)):
        - Uses stream_llm() with the active callback
        - Returns a mock response object with .content for compatibility

    If streaming is disabled (default):
        - Uses resilient_llm_call() (standard non-streaming path)
        - Returns the normal LangChain response

    This is a drop-in replacement for resilient_llm_call() that adds
    streaming when enabled, with zero changes to calling code.
    """
    if is_streaming_enabled():
        callback = get_active_callback() or TerminalStreamCallback()
        text = stream_llm(llm, messages, callback=callback, **kwargs)
        estimated_tokens = max(len(text) // 4, 1)
        usage = {
            "input_tokens": sum(len(m.content) // 4 for m in messages if hasattr(m, "content")),
            "output_tokens": estimated_tokens,
            "total_tokens": sum(len(m.content) // 4 for m in messages if hasattr(m, "content")) + estimated_tokens,
        }
        response = _StreamResponse(text, usage_metadata=usage)
        try:
            record_llm_usage(
                response,
                agent_name="unknown",
                messages=messages,
                model_hint=getattr(llm, "model_name", None),
                operation="stream",
            )
        except Exception as exc:
            logger.debug("Streaming telemetry skipped: %s", exc)
        return response
    else:
        from shared.llm_retry import resilient_llm_call
        return resilient_llm_call(llm, messages, **kwargs)


class _StreamResponse:
    """Minimal response wrapper for streaming compatibility."""

    def __init__(self, content: str, usage_metadata: dict | None = None):
        self.content = content
        self.usage_metadata = usage_metadata or {}
        self.response_metadata = {}
