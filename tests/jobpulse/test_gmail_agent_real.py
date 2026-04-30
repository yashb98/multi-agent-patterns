"""Tests for jobpulse/gmail_agent.py — pure logic, no OAuth needed."""

import pytest


class TestNormalizeCategory:
    def test_selected(self):
        from jobpulse.gmail_agent import _normalize_category

        assert _normalize_category("SELECTED_NEXT_ROUND") == "SELECTED_NEXT_ROUND"

    def test_selected_partial(self):
        from jobpulse.gmail_agent import _normalize_category

        assert _normalize_category("selected") == "SELECTED_NEXT_ROUND"

    def test_interview(self):
        from jobpulse.gmail_agent import _normalize_category

        assert _normalize_category("INTERVIEW_SCHEDULING") == "INTERVIEW_SCHEDULING"

    def test_interview_partial(self):
        from jobpulse.gmail_agent import _normalize_category

        assert _normalize_category("scheduling") == "INTERVIEW_SCHEDULING"

    def test_rejected(self):
        from jobpulse.gmail_agent import _normalize_category

        assert _normalize_category("REJECTED") == "REJECTED"

    def test_rejected_case_insensitive(self):
        from jobpulse.gmail_agent import _normalize_category

        assert _normalize_category("rejected") == "REJECTED"

    def test_unknown_returns_other(self):
        from jobpulse.gmail_agent import _normalize_category

        assert _normalize_category("something_weird") == "OTHER"

    def test_whitespace_stripped(self):
        from jobpulse.gmail_agent import _normalize_category

        assert _normalize_category("  REJECTED  ") == "REJECTED"


class TestScoreClassification:
    def test_valid_category_gets_high_score(self):
        from jobpulse.gmail_agent import _score_classification

        assert _score_classification("SELECTED_NEXT_ROUND") == 8.0

    def test_rejected_gets_high_score(self):
        from jobpulse.gmail_agent import _score_classification

        assert _score_classification("REJECTED") == 8.0

    def test_other_explicit_gets_high_score(self):
        from jobpulse.gmail_agent import _score_classification

        assert _score_classification("OTHER") == 8.0

    def test_invalid_gets_low_score(self):
        from jobpulse.gmail_agent import _score_classification

        assert _score_classification("gibberish") == 3.0


class TestExtractBody:
    def test_plain_text_body(self):
        from jobpulse.gmail_agent import _extract_body

        import base64

        encoded = base64.urlsafe_b64encode(b"Hello World").decode()
        payload = {"body": {"data": encoded}}
        body = _extract_body(payload)
        assert "Hello World" in body

    def test_multipart_body(self):
        from jobpulse.gmail_agent import _extract_body

        import base64

        plain_data = base64.urlsafe_b64encode(b"Plain text content").decode()
        html_data = base64.urlsafe_b64encode(b"<b>HTML</b>").decode()
        payload = {
            "parts": [
                {"mimeType": "text/plain", "body": {"data": plain_data}},
                {"mimeType": "text/html", "body": {"data": html_data}},
            ]
        }
        body = _extract_body(payload)
        assert "Plain text content" in body

    def test_nested_multipart(self):
        from jobpulse.gmail_agent import _extract_body

        import base64

        nested_data = base64.urlsafe_b64encode(b"Nested content").decode()
        payload = {
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": nested_data}},
                    ],
                }
            ]
        }
        body = _extract_body(payload)
        assert "Nested content" in body

    def test_empty_payload(self):
        from jobpulse.gmail_agent import _extract_body

        body = _extract_body({})
        assert body == ""

    def test_no_data_in_body(self):
        from jobpulse.gmail_agent import _extract_body

        body = _extract_body({"body": {}})
        assert body == ""
