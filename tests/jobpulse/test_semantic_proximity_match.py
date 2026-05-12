"""match_question_to_widget finds the nearest interactive element."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from jobpulse.form_engine.semantic_scanner import Question


@pytest.mark.asyncio
async def test_matches_widget_within_400px_below_question():
    from jobpulse.form_engine.semantic_scanner import match_question_to_widget

    q = Question(text="Do you require visa sponsorship?",
                 y=300, dom_path="div.q > p")

    page = MagicMock()
    page.evaluate = AsyncMock(return_value={
        "matched": True,
        "y": 360,
        "tag": "BUTTON",
        "role": "button",
        "aria_haspopup": "listbox",
        "aria_pressed": None,
        "aria_checked": None,
        "selector": "div[data-q='visa'] button",
        "ancestor_classes": "visa-q",
        "match_kind": "proximity",
        "distance_px": 60,
    })

    widget = await match_question_to_widget(q, page)
    assert widget is not None
    assert widget["selector"] == "div[data-q='visa'] button"
    assert widget["distance_px"] == 60


@pytest.mark.asyncio
async def test_returns_none_when_no_widget_within_proximity():
    from jobpulse.form_engine.semantic_scanner import match_question_to_widget

    q = Question(text="Stale question?", y=100, dom_path="div > p")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value={"matched": False})
    widget = await match_question_to_widget(q, page)
    assert widget is None


@pytest.mark.asyncio
async def test_prefers_ancestor_match_over_pixel_proximity():
    from jobpulse.form_engine.semantic_scanner import match_question_to_widget

    q = Question(text="Are you OK with on-call?", y=200, dom_path="fieldset.qa > p")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value={
        "matched": True,
        "y": 350,
        "tag": "BUTTON",
        "role": "switch",
        "aria_haspopup": "",
        "aria_checked": "false",
        "selector": "fieldset.qa button",
        "ancestor_classes": "qa",
        "match_kind": "ancestor",
        "distance_px": 0,
    })
    widget = await match_question_to_widget(q, page)
    assert widget["match_kind"] == "ancestor"
