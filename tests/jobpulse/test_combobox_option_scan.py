"""F2: pre-fill option scanning for combobox-class fields.

Live regression on Revolut welovealfa.com: screening pipeline received
field={'label': 'Do you require visa sponsorship?', 'type': 'combobox',
'options': []}, generated answer='Yes'. Real options were
['Yes - I require sponsorship', 'No - I do not require sponsorship'].
Token-overlap match misses the wrapped phrasings.

Fix: open every closed combobox briefly during scan_fields, capture
options via [role=option], close, attach to the field dict so the
LLM sees the actual offered set when generating the answer.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_scan_combobox_options_opens_and_reads_options():
    from jobpulse.form_engine.field_scanner import _scan_combobox_options

    page = MagicMock()
    page.locator = MagicMock(return_value=MagicMock(
        first=MagicMock(
            count=AsyncMock(return_value=1),
            click=AsyncMock(return_value=None),
            is_visible=AsyncMock(return_value=True),
        ),
    ))
    page.evaluate = AsyncMock(return_value=[
        "Yes - I require sponsorship",
        "No - I do not require sponsorship",
    ])
    page.keyboard = MagicMock(press=AsyncMock(return_value=None))

    out = await _scan_combobox_options(
        page,
        selector="div[data-q='visa'] button",
    )
    assert out == [
        "Yes - I require sponsorship",
        "No - I do not require sponsorship",
    ]


@pytest.mark.asyncio
async def test_scan_combobox_options_returns_empty_on_no_open():
    from jobpulse.form_engine.field_scanner import _scan_combobox_options

    page = MagicMock()
    page.locator = MagicMock(return_value=MagicMock(
        first=MagicMock(count=AsyncMock(return_value=0)),
    ))

    out = await _scan_combobox_options(page, selector="#missing")
    assert out == []


@pytest.mark.asyncio
async def test_populate_combobox_options_fills_empty_options_only():
    """Fields with options already populated should be skipped."""
    from jobpulse.form_engine.field_scanner import _populate_combobox_options

    fields = [
        {"label": "Visa?", "type": "combobox", "selector": "#visa"},
        {"label": "Country?", "type": "select",
         "options": ["UK", "US"], "selector": "#country"},
        {"label": "Email", "type": "text", "selector": "#email"},
    ]
    page = MagicMock()
    page.url = "https://example.com/apply"

    with patch(
        "jobpulse.form_engine.field_scanner._scan_combobox_options",
        AsyncMock(return_value=["Yes", "No"]),
    ) as mock_scan:
        await _populate_combobox_options(page, fields)

    # Visa was empty + combobox → scanned
    assert fields[0]["options"] == ["Yes", "No"]
    # Country had options → NOT re-scanned
    assert fields[1]["options"] == ["UK", "US"]
    # Email is text → not eligible
    assert "options" not in fields[2] or fields[2].get("options") in (None, [])
    # _scan_combobox_options called exactly once
    assert mock_scan.call_count == 1


@pytest.mark.asyncio
async def test_populate_combobox_options_caches_per_url_label():
    """Two scan_fields calls on the same URL+label should not re-open
    the combobox twice."""
    from jobpulse.form_engine.field_scanner import (
        _populate_combobox_options, _COMBOBOX_OPTION_CACHE,
    )
    _COMBOBOX_OPTION_CACHE.clear()

    page = MagicMock()
    page.url = "https://example.com/apply"

    with patch(
        "jobpulse.form_engine.field_scanner._scan_combobox_options",
        AsyncMock(return_value=["A", "B"]),
    ) as mock_scan:
        fields1 = [{"label": "X", "type": "combobox", "selector": "#x"}]
        await _populate_combobox_options(page, fields1)

        fields2 = [{"label": "X", "type": "combobox", "selector": "#x"}]
        await _populate_combobox_options(page, fields2)

    assert mock_scan.call_count == 1
    assert fields2[0]["options"] == ["A", "B"]
