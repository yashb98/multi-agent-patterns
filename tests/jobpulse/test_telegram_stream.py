"""Tests for Telegram application progress streaming."""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from jobpulse.telegram_stream import TelegramApplicationStream
from jobpulse.perplexity import CompanyResearch


@pytest.fixture
def stream():
    return TelegramApplicationStream()


@pytest.fixture
def company():
    return CompanyResearch(
        company="Acme AI",
        description="AI startup",
        industry="Technology",
        size="startup",
        tech_stack=["Python", "PyTorch"],
    )


@pytest.mark.asyncio
@patch("jobpulse.telegram_stream._send_telegram")
async def test_stream_start_sends_message(mock_send, stream, company):
    mock_send.return_value = 12345
    await stream.stream_start(
        job={"role": "ML Engineer", "company": "Acme AI"},
        company_research=company,
    )
    mock_send.assert_called_once()
    msg = mock_send.call_args[0][0]
    assert "Acme AI" in msg
    assert "ML Engineer" in msg
    assert stream._msg_id == 12345


@pytest.mark.asyncio
@patch("jobpulse.telegram_stream._edit_telegram")
async def test_stream_field_updates_message(mock_edit, stream):
    stream._msg_id = 12345
    await stream.stream_field(
        label="First Name", value="Yash", tier=1, confident=True
    )
    mock_edit.assert_called_once()
    args = mock_edit.call_args[0]
    assert args[0] == 12345
    assert "First Name" in args[1]
    assert "Pattern" in args[1]


@pytest.mark.asyncio
@patch("jobpulse.telegram_stream._edit_telegram")
async def test_stream_complete(mock_edit, stream):
    stream._msg_id = 12345
    stream._lines = ["line1"]
    await stream.stream_complete(success=True, gate_score=8.5)
    mock_edit.assert_called_once()
    text = mock_edit.call_args[0][1]
    assert "8.5" in text


def test_stream_format_tier_labels(stream):
    assert stream._tier_label(1) == "Pattern"
    assert stream._tier_label(2) == "Nano"
    assert stream._tier_label(3) == "LLM"
    assert stream._tier_label(4) == "Vision"
