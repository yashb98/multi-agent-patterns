"""Gmail verification email agent.

Polls Gmail inbox for verification/confirmation emails from ATS platforms,
extracts the verification link, and returns it for the orchestrator to navigate to.
Uses exponential backoff: 1s → 2s → 4s → 8s → 16s → 32s → capped at 32s.
"""
from __future__ import annotations

import base64
import contextlib
import re
import time
from html.parser import HTMLParser

from shared.logging_config import get_logger

logger = get_logger(__name__)

_VERIFY_PATTERNS = re.compile(
    r"(verify|confirm|activate|validate|registration|email.?confirm|complete.?signup)",
    re.IGNORECASE,
)
_ANTI_PATTERNS = re.compile(
    r"(unsubscribe|privacy|terms|help|support|faq|contact)", re.IGNORECASE
)


class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self._current_href = value
                    self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href:
            text = " ".join(self._current_text).strip()
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


def extract_verification_link(html_body: str, expected_domain: str) -> str | None:
    """Extract verification/confirmation link from HTML email body."""
    parser = _LinkExtractor()
    parser.feed(html_body)

    candidates: list[tuple[str, int]] = []
    for href, text in parser.links:
        if _ANTI_PATTERNS.search(href) or _ANTI_PATTERNS.search(text):
            continue
        score = 0
        if _VERIFY_PATTERNS.search(href):
            score += 3
        if _VERIFY_PATTERNS.search(text):
            score += 2
        if re.search(r"[?&](token|code|key|t|k)=", href):
            score += 2
        # Domain bonus only counts if there's already a keyword signal
        if score > 0:
            domain_root = expected_domain.split(".")[-2] if "." in expected_domain else expected_domain
            if domain_root in href.lower():
                score += 1
            candidates.append((href, score))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


class GmailVerifier:
    """Poll Gmail for verification emails with exponential backoff."""

    def __init__(self, service=None):
        self._service = service

    def _get_service(self):
        if self._service is not None:
            return self._service
        from jobpulse.gmail_agent import _get_gmail_service
        return _get_gmail_service()

    def wait_for_verification(
        self,
        from_domain: str,
        timeout_s: int = 120,
        initial_interval_s: float = 1.0,
        max_interval_s: float = 32.0,
    ) -> str | None:
        """Poll Gmail for a verification email. Exponential backoff: 1s → 2s → 4s → ... → 32s."""
        service = self._get_service()
        if not service:
            logger.warning("Gmail service unavailable — cannot verify email")
            return None

        query = f"from:{from_domain} newer_than:5m (verify OR confirm OR activate OR registration)"
        start = time.monotonic()
        interval = initial_interval_s

        while time.monotonic() - start < timeout_s:
            try:
                results = (
                    service.users().messages()
                    .list(userId="me", q=query, maxResults=5)
                    .execute()
                )
                for msg_ref in results.get("messages", []):
                    msg = (
                        service.users().messages()
                        .get(userId="me", id=msg_ref["id"], format="full")
                        .execute()
                    )
                    html_body = self._extract_html_body(msg)
                    if not html_body:
                        continue
                    link = extract_verification_link(html_body, from_domain)
                    if link:
                        logger.info("Found verification link from %s: %s", from_domain, link[:80])
                        with contextlib.suppress(Exception):
                            service.users().messages().modify(
                                userId="me", id=msg_ref["id"],
                                body={"removeLabelIds": ["UNREAD"]},
                            ).execute()
                        return link
            except Exception as exc:
                logger.warning("Gmail poll error: %s", exc)

            time.sleep(interval)
            interval = min(interval * 2, max_interval_s)

        logger.warning("Verification email timeout after %ds for %s", timeout_s, from_domain)
        return None

    @staticmethod
    def _extract_html_body(message: dict) -> str | None:
        payload = message.get("payload", {})
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        return None
