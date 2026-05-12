"""scan_fields must call vision_augment when the result is sparse."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_scan_fields_invokes_vision_augment_on_sparse_application_form():
    from jobpulse.form_engine.field_scanner import scan_fields

    page = MagicMock()
    page.url = "https://welovealfa.com/.../complete-profile"

    sparse = [{"label": f"f{i}", "type": "text", "value": ""} for i in range(5)]

    augment_extras = [
        {"label": "Visa sponsorship?", "type": "select",
         "options": ["Yes", "No"], "vision_only": True, "value": ""},
    ]

    with patch("jobpulse.form_engine.field_scanner._run_all_strategies_parallel",
               AsyncMock(return_value={"dom_query": sparse})), \
         patch("jobpulse.form_engine.field_scanner._maybe_augment_with_vision",
               AsyncMock(return_value=augment_extras)) as mock_augment:
        out = await scan_fields(page)

    assert mock_augment.awaited
    assert any(f.get("vision_only") for f in out)
    assert any(f["label"] == "Visa sponsorship?" for f in out)


@pytest.mark.asyncio
async def test_scan_fields_skips_vision_when_dense():
    """24 scanner fields → augment helper returns []."""
    from jobpulse.form_engine.field_scanner import scan_fields

    page = MagicMock()
    page.url = "https://example.com/.../apply"
    dense = [{"label": f"f{i}", "type": "text", "value": ""} for i in range(24)]

    with patch("jobpulse.form_engine.field_scanner._run_all_strategies_parallel",
               AsyncMock(return_value={"dom_query": dense})), \
         patch("jobpulse.form_engine.field_scanner._maybe_augment_with_vision",
               AsyncMock(return_value=[])) as mock_augment:
        out = await scan_fields(page)

    # No vision_only fields appended
    assert not any(f.get("vision_only") for f in out)


@pytest.mark.asyncio
async def test_maybe_augment_with_vision_returns_empty_when_predicate_false():
    """Helper short-circuits via should_force_vision before calling LLM."""
    from jobpulse.form_engine.field_scanner import _maybe_augment_with_vision

    page = MagicMock()
    dense = [{"label": f"f{i}", "type": "text"} for i in range(20)]

    with patch("jobpulse.form_engine.vision_gate.vision_augment_scan",
               AsyncMock(return_value=[{"label": "X", "vision_only": True}])) as mock_aug:
        result = await _maybe_augment_with_vision(
            page, dense, page_type_hint="application_form", confidence_hint=0.9,
        )
    assert result == []
    assert not mock_aug.called


@pytest.mark.asyncio
async def test_maybe_augment_with_vision_calls_when_sparse():
    from jobpulse.form_engine.field_scanner import _maybe_augment_with_vision

    page = MagicMock()
    sparse = [{"label": "x", "type": "text"}]

    extras = [{"label": "Visa?", "vision_only": True}]
    with patch("jobpulse.form_engine.vision_gate.vision_augment_scan",
               AsyncMock(return_value=extras)) as mock_aug:
        result = await _maybe_augment_with_vision(
            page, sparse, page_type_hint="application_form", confidence_hint=0.9,
        )
    assert result == extras
    assert mock_aug.called
