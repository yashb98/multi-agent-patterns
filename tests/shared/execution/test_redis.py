"""Tests for Redis client with graceful degradation."""

import pytest
from unittest.mock import patch, MagicMock


class TestRedisClient:
    def test_creates_with_defaults(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:6379")
        assert client.url == "redis://localhost:6379"

    def test_is_available_returns_false_when_no_redis(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:9999")
        assert client.is_available() is False

    def test_publish_silent_on_failure(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:9999")
        client.publish("channel:test", {"event": "data"})

    def test_hset_silent_on_failure(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:9999")
        client.hset("key", {"field": "value"})

    def test_hget_returns_none_on_failure(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:9999")
        assert client.hget("key") is None

    @patch("shared.execution._redis.redis")
    def test_publish_calls_redis_when_available(self, mock_redis_mod):
        mock_conn = MagicMock()
        mock_conn.ping.return_value = True
        mock_redis_mod.from_url.return_value = mock_conn
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:6379")
        client._conn = mock_conn
        client._available = True
        client.publish("ch:test", {"x": 1})
        mock_conn.publish.assert_called_once()

    @patch("shared.execution._redis.redis")
    def test_hset_calls_redis_when_available(self, mock_redis_mod):
        mock_conn = MagicMock()
        mock_conn.ping.return_value = True
        mock_redis_mod.from_url.return_value = mock_conn
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:6379")
        client._conn = mock_conn
        client._available = True
        client.hset("projection:scan:1", {"state": "running"})
        mock_conn.hset.assert_called_once()
