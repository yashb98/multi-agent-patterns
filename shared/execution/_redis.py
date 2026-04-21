"""Optional Redis client with graceful degradation.

When Redis is unavailable, all operations silently no-op.
The system works without Redis — it just loses real-time push
and fast cached projections.
"""

from __future__ import annotations

import json

from shared.logging_config import get_logger

logger = get_logger(__name__)

try:
    import redis
except ImportError:
    redis = None  # type: ignore[assignment]


class RedisClient:
    """Redis wrapper that degrades gracefully when unavailable."""

    def __init__(self, url: str = "redis://localhost:6379"):
        self.url = url
        self._conn: redis.Redis | None = None  # type: ignore[union-attr]
        self._available = False
        self._connect()

    def _connect(self) -> None:
        if redis is None:
            logger.info("Redis package not installed — running without Redis")
            return
        try:
            self._conn = redis.from_url(self.url, decode_responses=True)
            self._conn.ping()
            self._available = True
            logger.info("Redis connected at %s", self.url)
        except Exception as e:
            self._available = False
            self._conn = None
            logger.info("Redis unavailable (%s) — degrading gracefully", e)

    def is_available(self) -> bool:
        return self._available

    def publish(self, channel: str, data: dict) -> None:
        if not self._available or self._conn is None:
            return
        try:
            self._conn.publish(channel, json.dumps(data))
        except Exception as e:
            logger.debug("Redis publish failed: %s", e)
            self._available = False

    def hset(self, key: str, mapping: dict) -> None:
        if not self._available or self._conn is None:
            return
        try:
            self._conn.hset(
                key,
                mapping={
                    k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                    for k, v in mapping.items()
                },
            )
        except Exception as e:
            logger.debug("Redis hset failed: %s", e)
            self._available = False

    def hget(self, key: str) -> dict | None:
        if not self._available or self._conn is None:
            return None
        try:
            raw = self._conn.hgetall(key)
            if not raw:
                return None
            result = {}
            for k, v in raw.items():
                try:
                    result[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    result[k] = v
            return result
        except Exception as e:
            logger.debug("Redis hget failed: %s", e)
            self._available = False
            return None

    def set_with_ttl(self, key: str, value: str, ttl_seconds: int) -> None:
        if not self._available or self._conn is None:
            return
        try:
            self._conn.set(key, value, ex=ttl_seconds)
        except Exception as e:
            logger.debug("Redis set failed: %s", e)
            self._available = False

    def get(self, key: str) -> str | None:
        if not self._available or self._conn is None:
            return None
        try:
            return self._conn.get(key)
        except Exception as e:
            logger.debug("Redis get failed: %s", e)
            self._available = False
            return None

    def incr(self, key: str) -> int | None:
        if not self._available or self._conn is None:
            return None
        try:
            return self._conn.incr(key)
        except Exception as e:
            logger.debug("Redis incr failed: %s", e)
            self._available = False
            return None
