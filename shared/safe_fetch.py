"""Safe HTTP fetch helpers with SSRF guardrails for agent code."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx


class UnsafeURLError(ValueError):
    """Raised when a URL fails SSRF safety checks."""


@dataclass(frozen=True)
class SafeFetchResult:
    url: str
    status_code: int
    content: bytes
    text: str
    content_type: str


def _reject_ip(ip_text: str) -> None:
    ip = ipaddress.ip_address(ip_text)
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UnsafeURLError(f"Unsafe target address: {ip_text}")


def _validate_public_target(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"Unsupported URL scheme: {parsed.scheme or '<missing>'}")
    if not parsed.hostname:
        raise UnsafeURLError("URL must include a hostname")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URL credentials are not allowed")
    if parsed.hostname.lower() == "localhost":
        raise UnsafeURLError("localhost is not allowed")

    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        default_port = 443 if parsed.scheme == "https" else 80
        infos = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or default_port,
            type=socket.SOCK_STREAM,
        )
        if not infos:
            raise UnsafeURLError(f"Could not resolve host: {parsed.hostname}")
        for info in infos:
            _reject_ip(info[4][0])
    else:
        _reject_ip(parsed.hostname)
    return parsed.scheme, parsed.hostname


def safe_fetch(
    url: str,
    *,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    headers: dict[str, str] | None = None,
) -> SafeFetchResult:
    """Fetch public HTTP(S) content while rejecting private-network targets."""

    current_url = url
    redirect_count = 0
    while True:
        _validate_public_target(current_url)
        response = httpx.get(
            current_url,
            timeout=timeout,
            follow_redirects=False,
            headers=headers,
        )
        if not follow_redirects or response.status_code not in {301, 302, 303, 307, 308}:
            break
        location = response.headers.get("location")
        if not location:
            break
        redirect_count += 1
        if redirect_count > max_redirects:
            raise UnsafeURLError(f"Too many redirects while fetching: {url}")
        current_url = urljoin(str(response.url), location)
        _validate_public_target(current_url)

    response.raise_for_status()
    content = response.content[:max_bytes]
    return SafeFetchResult(
        url=str(response.url),
        status_code=response.status_code,
        content=content,
        text=content.decode(response.encoding or "utf-8", errors="replace"),
        content_type=response.headers.get("content-type", ""),
    )


def safe_fetch_text(url: str, **kwargs) -> str:
    return safe_fetch(url, **kwargs).text


def safe_fetch_bytes(url: str, **kwargs) -> bytes:
    return safe_fetch(url, **kwargs).content
