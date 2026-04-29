from unittest.mock import Mock

import pytest

from shared.safe_fetch import UnsafeURLError, safe_fetch


def test_safe_fetch_blocks_localhost():
    with pytest.raises(UnsafeURLError, match="localhost"):
        safe_fetch("http://localhost:8080/admin")


def test_safe_fetch_blocks_private_ip():
    with pytest.raises(UnsafeURLError, match="Unsafe target address"):
        safe_fetch("http://10.0.0.5/metadata")


def test_safe_fetch_blocks_url_credentials():
    with pytest.raises(UnsafeURLError, match="credentials"):
        safe_fetch("https://user:pass@example.com/private")


def test_safe_fetch_allows_public_host(monkeypatch):
    monkeypatch.setattr(
        "shared.safe_fetch.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    response = Mock()
    response.content = b"hello world"
    response.encoding = "utf-8"
    response.status_code = 200
    response.url = "https://example.com/page"
    response.headers = {"content-type": "text/plain"}
    response.raise_for_status = Mock()

    monkeypatch.setattr("shared.safe_fetch.httpx.get", lambda *args, **kwargs: response)

    result = safe_fetch("https://example.com/page")
    assert result.text == "hello world"
    assert result.content_type == "text/plain"


def test_safe_fetch_revalidates_redirect_targets(monkeypatch):
    monkeypatch.setattr(
        "shared.safe_fetch.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    redirect = Mock()
    redirect.content = b""
    redirect.encoding = "utf-8"
    redirect.status_code = 302
    redirect.url = "https://example.com/start"
    redirect.headers = {"location": "http://127.0.0.1/internal"}
    redirect.raise_for_status = Mock()

    monkeypatch.setattr("shared.safe_fetch.httpx.get", lambda *args, **kwargs: redirect)

    with pytest.raises(UnsafeURLError, match="Unsafe target address"):
        safe_fetch("https://example.com/start")
