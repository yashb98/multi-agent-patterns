"""Wiring test: per-page live snapshots survive into final_mapping.

Live regression on 2026-05-05: Forge Holiday Group submission left
Indeed via the agent path. The user manually corrected two screening
fields (City: 'Chester HQ' → 'Dundee'; "Worked at Forge?": 'Forge Holiday
Group' → 'N/A'). `_capture_final_mapping_async` only scanned the live
review-module page — but Indeed's review page is read-only, so it
captured 0 inputs. The corrections never reached `confirm_application`,
`CorrectionCapture`, or `AgentRulesDB`.

Fix: NativeFormFiller now snapshots every visible field's value right
before clicking Next/Continue and stores into `_per_page_live_snapshots`.
`_capture_final_mapping_async` merges those snapshots into `final_mapping`
(oldest first, later writes win, then the review-page read on top).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_native_form_filler_initializes_per_page_snapshots_list():
    """NativeFormFiller.__init__ must set up _per_page_live_snapshots."""
    from jobpulse.native_form_filler import NativeFormFiller

    page = MagicMock()
    driver = SimpleNamespace(intelligence=None)
    filler = NativeFormFiller(page=page, driver=driver)

    assert hasattr(filler, "_per_page_live_snapshots")
    assert isinstance(filler._per_page_live_snapshots, list)
    assert filler._per_page_live_snapshots == []


@pytest.mark.asyncio
async def test_capture_final_mapping_merges_per_page_snapshots():
    """_capture_final_mapping_async must include per-page snapshot data."""
    from jobpulse.live_review_applicator import LiveReviewSession

    session = LiveReviewSession.__new__(LiveReviewSession)
    session._page = MagicMock()
    session._agent_mapping = {}
    session._final_mapping = {}
    session.pull_ai_assist_data = MagicMock(return_value=None)

    page = session._page
    page.get_by_role = MagicMock(return_value=MagicMock(all=AsyncMock(return_value=[])))
    page.locator = MagicMock(return_value=MagicMock(all=AsyncMock(return_value=[])))

    filler = SimpleNamespace(
        _get_accessible_name=AsyncMock(return_value=""),
        _per_page_live_snapshots=[
            {"City *": "Dundee", "Worked at Forge?": "N/A"},
            {"What is your age range?": "20 - 29 years"},
        ],
    )

    final = await session._capture_final_mapping_async(filler)

    assert final.get("City *") == "Dundee"
    assert final.get("Worked at Forge?") == "N/A"
    assert final.get("What is your age range?") == "20 - 29 years"


@pytest.mark.asyncio
async def test_later_per_page_snapshots_overwrite_earlier_for_same_label():
    """Layered merge: page-2 edit wins over page-1 fill on the same label."""
    from jobpulse.live_review_applicator import LiveReviewSession

    session = LiveReviewSession.__new__(LiveReviewSession)
    session._page = MagicMock()
    session._agent_mapping = {}
    session._final_mapping = {}
    session.pull_ai_assist_data = MagicMock(return_value=None)

    page = session._page
    page.get_by_role = MagicMock(return_value=MagicMock(all=AsyncMock(return_value=[])))
    page.locator = MagicMock(return_value=MagicMock(all=AsyncMock(return_value=[])))

    filler = SimpleNamespace(
        _get_accessible_name=AsyncMock(return_value=""),
        _per_page_live_snapshots=[
            {"City *": "Chester HQ"},   # agent's first wrong fill
            {"City *": "Dundee"},       # user-edited on a later page revisit
        ],
    )

    final = await session._capture_final_mapping_async(filler)
    assert final["City *"] == "Dundee"


def test_snapshot_method_exists_on_filler():
    """The fill loop hook expects _snapshot_live_form_state on the filler."""
    from jobpulse.native_form_filler import NativeFormFiller

    page = MagicMock()
    driver = SimpleNamespace(intelligence=None)
    filler = NativeFormFiller(page=page, driver=driver)

    assert hasattr(filler, "_snapshot_live_form_state")
    assert callable(filler._snapshot_live_form_state)
