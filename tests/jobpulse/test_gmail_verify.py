"""Tests for GmailVerifier — verification email polling + link extraction."""

import pytest
from unittest.mock import MagicMock
from jobpulse.gmail_verify import extract_verification_link, GmailVerifier


def test_extract_link_verify_pattern():
    html = '''<a href="https://greenhouse.io/verify?token=abc123">Verify Email</a>
              <a href="https://greenhouse.io/unsubscribe">Unsubscribe</a>'''
    link = extract_verification_link(html, "greenhouse.io")
    assert link is not None
    assert "verify" in link
    assert "token=abc123" in link


def test_extract_link_confirm_pattern():
    html = '<a href="https://workday.com/confirm-email/xyz">Confirm your account</a>'
    link = extract_verification_link(html, "workday.com")
    assert "confirm-email" in link


def test_extract_link_activate_pattern():
    html = '<a href="https://lever.co/activate/token123">Activate Account</a>'
    link = extract_verification_link(html, "lever.co")
    assert "activate" in link


def test_extract_link_no_match():
    html = '<a href="https://example.com/about">About Us</a>'
    link = extract_verification_link(html, "example.com")
    assert link is None


def test_extract_link_filters_unsubscribe():
    html = '''<a href="https://example.com/verify?t=1">Verify</a>
              <a href="https://example.com/unsubscribe">Unsubscribe</a>'''
    link = extract_verification_link(html, "example.com")
    assert "verify" in link
    assert "unsubscribe" not in link


def test_verifier_exponential_polling():
    """Verify polling uses exponential backoff intervals."""
    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {"messages": []}

    verifier = GmailVerifier(service=mock_service)
    link = verifier.wait_for_verification("example.com", timeout_s=3, initial_interval_s=0.5)
    assert link is None
    call_count = mock_service.users().messages().list().execute.call_count
    assert call_count >= 2


def test_verifier_finds_email():
    import base64
    mock_service = MagicMock()

    mock_service.users().messages().list().execute.side_effect = [
        {"messages": []},
        {"messages": [{"id": "msg1"}]},
    ]

    html = '<a href="https://greenhouse.io/verify?token=abc123">Verify</a>'
    b64_html = base64.urlsafe_b64encode(html.encode()).decode()
    msg_data = {
        "payload": {
            "headers": [{"name": "From", "value": "noreply@greenhouse.io"}],
            "body": {"data": ""},
            "parts": [{"mimeType": "text/html", "body": {"data": b64_html}}],
        }
    }
    mock_service.users().messages().get().execute.return_value = msg_data

    verifier = GmailVerifier(service=mock_service)
    link = verifier.wait_for_verification("greenhouse.io", timeout_s=10, initial_interval_s=0.1)
    assert link is not None
    assert "verify" in link


def test_verifier_timeout():
    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {"messages": []}

    verifier = GmailVerifier(service=mock_service)
    link = verifier.wait_for_verification("example.com", timeout_s=1, initial_interval_s=0.3)
    assert link is None
