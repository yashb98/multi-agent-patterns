"""Circuit Breaker — prevents cascading failures from external service outages.

When an external service (DuckDuckGo, Semantic Scholar, etc.) fails repeatedly,
the circuit breaker trips OPEN and returns a fallback immediately instead of
waiting for timeouts. After a cooldown period, it allows a single probe request
to check if the service recovered.

States:
    CLOSED  → Normal operation, requests pass through
    OPEN    → Service is down, fail fast with fallback
    HALF_OPEN → Cooldown expired, allow one probe request

Usage:
    breaker = CircuitBreaker("duckduckgo", failure_threshold=3, cooldown_seconds=60)

    result = breaker.call(
        fn=lambda: ddgs.text(query, max_results=3),
        fallback={"source": None, "supports": False, "snippet": "Service unavailable"},
    )
"""

import time
import threading
from enum import Enum
from typing import Any, Callable, Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Thread-safe circuit breaker for external service calls."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        success_threshold: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.cooldown_seconds:
                    self._state = CircuitState.HALF_OPEN
                    logger.info("Circuit '%s' → HALF_OPEN (cooldown expired)", self.name)
            return self._state

    def call(
        self,
        fn: Callable,
        fallback: Any = None,
        fallback_fn: Optional[Callable] = None,
    ) -> Any:
        """Execute fn through the circuit breaker.

        Args:
            fn: The external service call to execute
            fallback: Static fallback value when circuit is OPEN
            fallback_fn: Dynamic fallback function when circuit is OPEN

        Returns:
            Result of fn() on success, or fallback when circuit is OPEN
        """
        current_state = self.state

        if current_state == CircuitState.OPEN:
            logger.debug("Circuit '%s' OPEN — returning fallback", self.name)
            if fallback_fn:
                return fallback_fn()
            return fallback

        try:
            result = fn()
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            if fallback_fn:
                return fallback_fn()
            return fallback

    def _on_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info("Circuit '%s' → CLOSED (service recovered)", self.name)
            else:
                self._failure_count = 0

    def _on_failure(self, error: Exception):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit '%s' → OPEN (probe failed: %s)", self.name, error)
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit '%s' → OPEN after %d failures (last: %s). "
                    "Cooldown: %ds",
                    self.name, self._failure_count, error, self.cooldown_seconds,
                )

    def record_failure(self):
        """Record a failure directly (without a callable)."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit '%s' → OPEN (probe failed)", self.name)
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit '%s' → OPEN after %d failures. Cooldown: %ds",
                    self.name, self._failure_count, self.cooldown_seconds,
                )

    def allow_request(self) -> bool:
        """Return True if the circuit allows a request to proceed."""
        return self.state != CircuitState.OPEN

    def reset(self):
        """Manually reset the circuit breaker to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0

    def __repr__(self):
        return f"CircuitBreaker('{self.name}', state={self.state.value.lower()}, failures={self._failure_count})"


# ─── SHARED BREAKER INSTANCES ──────────────────────────────────────
# Pre-configured breakers for services used by fact_checker and external_verifiers.

ddg_breaker = CircuitBreaker("duckduckgo", failure_threshold=3, cooldown_seconds=60)
s2_breaker = CircuitBreaker("semantic_scholar", failure_threshold=3, cooldown_seconds=120)
web_breaker = CircuitBreaker("web_search", failure_threshold=5, cooldown_seconds=90)

# ─── BREAKER REGISTRY ──────────────────────────────────────────────
_BREAKERS: dict[str, CircuitBreaker] = {
    "duckduckgo": ddg_breaker,
    "semantic_scholar": s2_breaker,
    "web_search": web_breaker,
    "openai": CircuitBreaker("openai", failure_threshold=5, cooldown_seconds=120),
    "notion": CircuitBreaker("notion", failure_threshold=5, cooldown_seconds=120),
    "linkedin": CircuitBreaker("linkedin", failure_threshold=3, cooldown_seconds=300),
}


def get_breaker(name: str) -> CircuitBreaker:
    """Return the named circuit breaker, creating a default one if not found."""
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(name)
    return _BREAKERS[name]
