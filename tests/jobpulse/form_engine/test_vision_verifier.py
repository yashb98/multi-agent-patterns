"""Mechanics tests for vision_verifier (Slice S26).

These are structural tests — they exercise tier mapping, kill-switch
short-circuit, vision-unavailable fallback, and the correction routing
plumbing. They are NOT a substitute for live evidence (rule 1) — the
live ≥95% read-accuracy gate is verified by an apply_job dry-run on a
real ATS URL with `JOBPULSE_TEST_MODE=0`.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobpulse.form_engine import vision_verifier as vv
from shared import semantic_decisions


@pytest.fixture(autouse=True)
def _isolate_decisions_db(tmp_path, monkeypatch):
    """Route semantic_decisions writes to a tmp DB; reset cached state.
    Also disable artifact saving so tests don't pollute data/audits/."""
    db_path = tmp_path / "decisions.db"
    semantic_decisions.set_decisions_db_path(db_path)
    semantic_decisions.set_test_mode(False)
    monkeypatch.setenv("JOBPULSE_TEST_MODE", "0")
    monkeypatch.setenv("VISION_VERIFIER_SAVE_ARTIFACTS", "0")
    # S26-follow-up-L: the production verifier now has a fallback vendor
    # behind ``VISION_VERIFIER_FALLBACK_MODEL`` (default qwen3-vl:4b on
    # local Ollama). Tests mock the primary OpenAI client, but the
    # fallback path constructs a SEPARATE client which would either hit
    # real Ollama or raise — neither is appropriate in unit tests.
    # Disable the fallback so tests only exercise the primary path,
    # which is what they assert against.
    monkeypatch.setenv("VISION_VERIFIER_FALLBACK_MODEL", "none")
    # Re-read the module-level constants so the override takes effect
    # for this test (vision_verifier.py reads them at import time).
    import jobpulse.form_engine.vision_verifier as vv
    vv._FALLBACK_MODEL = "none"
    yield
    semantic_decisions.set_test_mode(None)


@pytest.fixture
def _enable_verifier(monkeypatch):
    monkeypatch.setenv("VISION_VERIFICATION_ENABLED", "true")


def _fake_page_with_screenshot() -> MagicMock:
    page = MagicMock()
    # locator(...).first → has count() async and is_visible() async and screenshot() async
    locator = MagicMock()
    locator.count = AsyncMock(return_value=0)
    locator.is_visible = AsyncMock(return_value=False)
    locator.screenshot = AsyncMock(return_value=b"x")
    sub_locator = MagicMock()
    sub_locator.first = locator
    page.locator = MagicMock(return_value=sub_locator)
    page.screenshot = AsyncMock(return_value=b"fakepng")
    return page


def _vision_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = json.dumps(payload)
    response.usage = MagicMock()
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 30
    return response


def test_killswitch_off_returns_empty(monkeypatch):
    monkeypatch.delenv("VISION_VERIFICATION_ENABLED", raising=False)
    page = _fake_page_with_screenshot()
    result = asyncio.run(
        vv.verify_form_page(
            page, {"Email": "a@b.com"},
            page_url="https://x.example/y", platform="greenhouse",
        )
    )
    assert result.verdicts == []
    assert result.mismatches == 0
    assert result.cost_usd == 0.0


def test_empty_mapping_returns_empty(_enable_verifier):
    page = _fake_page_with_screenshot()
    result = asyncio.run(
        vv.verify_form_page(
            page, {},
            page_url="https://x.example/y", platform="greenhouse",
        )
    )
    assert result.verdicts == []


def test_blank_value_is_skipped_not_sent_to_vision(_enable_verifier):
    """Outcome 6: skipped fields are recorded as skipped_no_expected_value
    and not hallucinated into vision verdicts."""
    page = _fake_page_with_screenshot()
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock()
        # ALL claim values are blank → no vision call expected.
        result = asyncio.run(
            vv.verify_form_page(
                page, {"Empty 1": "", "Empty 2": "   "},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    assert mock_client.return_value.chat.completions.create.call_count == 0
    assert len(result.verdicts) == 2
    assert all(v.tier_reached == "skipped_no_expected_value" for v in result.verdicts)


def test_passed_and_mismatch_tier_mapping(_enable_verifier):
    page = _fake_page_with_screenshot()
    vision_payload = {
        "verdicts": [
            {
                "label": "Email",
                "observed_value": "yash@example.com",
                "matches_claim": True,
                "contradicts_help_text": False,
                "reason": "matches",
            },
            {
                "label": "AI Policy",
                "observed_value": "No",
                "matches_claim": False,
                "contradicts_help_text": True,
                "reason": "form requires Yes, filler entered No",
            },
        ]
    }
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock(
            return_value=_vision_response(vision_payload)
        )
        result = asyncio.run(
            vv.verify_form_page(
                page,
                {"Email": "yash@example.com", "AI Policy": "No"},
                page_url="https://job-boards.greenhouse.io/x", platform="greenhouse",
            )
        )
    tiers = {v.label: v.tier_reached for v in result.verdicts}
    assert tiers == {"Email": "passed", "AI Policy": "mismatch_detected"}
    assert result.mismatches == 1


def test_vision_unparseable_returns_unavailable(_enable_verifier):
    page = _fake_page_with_screenshot()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock()]
    bad_response.choices[0].message = MagicMock()
    bad_response.choices[0].message.content = "not json at all"
    bad_response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock(return_value=bad_response)
        result = asyncio.run(
            vv.verify_form_page(
                page, {"Email": "a@b.com"},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    assert result.vision_unavailable is True
    assert all(v.tier_reached == "vision_unavailable" for v in result.verdicts)


def test_vision_client_raises_returns_unavailable(_enable_verifier):
    page = _fake_page_with_screenshot()
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock(
            side_effect=RuntimeError("boom")
        )
        result = asyncio.run(
            vv.verify_form_page(
                page, {"Email": "a@b.com"},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    assert result.vision_unavailable is True


def test_correction_succeeds_and_routes_learning(_enable_verifier):
    """Mismatch + correction_enabled + successful fill → correction_succeeded
    AND the fix routes through ai_assist_logger (cache invalidation)."""
    page = _fake_page_with_screenshot()

    # First vision call: verdicts say mismatch. Second vision call: proposes corrected_value.
    verdict_response = _vision_response({
        "verdicts": [{
            "label": "AI Policy",
            "observed_value": "No",
            "matches_claim": False,
            "contradicts_help_text": True,
            "reason": "claim is No but help-text requires Yes",
        }]
    })
    correction_response = _vision_response({"corrected_value": "Yes"})

    fill_callback = AsyncMock(return_value={"success": True, "value_verified": True})

    learn_called = {"hits": 0}

    def _fake_learn(**kwargs):
        learn_called["hits"] += 1
        learn_called["kwargs"] = kwargs

    with patch.object(vv, "get_openai_client") as mock_client, \
         patch.object(vv, "_learn_correction", side_effect=_fake_learn):
        mock_client.return_value.chat.completions.create = MagicMock(
            side_effect=[verdict_response, correction_response]
        )
        result = asyncio.run(
            vv.verify_form_page(
                page,
                {"AI Policy": "No"},
                page_url="https://job-boards.greenhouse.io/x",
                platform="greenhouse",
                correction_enabled=True,
                fill_callback=fill_callback,
            )
        )

    assert result.corrections_applied == 1
    assert result.corrections_failed == 0
    assert result.verdicts[0].tier_reached == "correction_succeeded"
    assert result.verdicts[0].observed_value == "Yes"
    fill_callback.assert_awaited_once_with("AI Policy", "Yes")
    assert learn_called["hits"] == 1
    assert learn_called["kwargs"]["new_value"] == "Yes"


def test_correction_failed_when_refill_does_not_verify(_enable_verifier):
    page = _fake_page_with_screenshot()
    verdict_response = _vision_response({
        "verdicts": [{
            "label": "AI Policy",
            "observed_value": "No",
            "matches_claim": False,
            "contradicts_help_text": True,
            "reason": "mismatch",
        }]
    })
    correction_response = _vision_response({"corrected_value": "Yes"})
    fill_callback = AsyncMock(return_value={"success": False, "error": "click failed"})
    with patch.object(vv, "get_openai_client") as mock_client, \
         patch.object(vv, "_learn_correction") as mock_learn:
        mock_client.return_value.chat.completions.create = MagicMock(
            side_effect=[verdict_response, correction_response]
        )
        result = asyncio.run(
            vv.verify_form_page(
                page, {"AI Policy": "No"},
                page_url="https://job-boards.greenhouse.io/x",
                platform="greenhouse",
                correction_enabled=True,
                fill_callback=fill_callback,
            )
        )
    assert result.corrections_applied == 0
    assert result.corrections_failed == 1
    assert result.verdicts[0].tier_reached == "correction_failed"
    mock_learn.assert_not_called()


def test_compression_real_png_to_webp():
    """Compress a real PNG and confirm it round-trips as WebP smaller than raw."""
    import io
    from PIL import Image

    img = Image.new("RGB", (800, 600), color=(255, 255, 255))
    buf = io.BytesIO(); img.save(buf, "PNG"); raw = buf.getvalue()
    compressed = vv._compress_for_vision(raw)
    assert compressed != raw
    assert vv._mime_for(compressed) == "image/webp"
    # Lossless WebP of a near-blank image should be substantially smaller than raw PNG.
    assert len(compressed) < len(raw)


def test_compression_resizes_oversized_image(monkeypatch):
    """Image larger than the Kimi 4K rec gets resized while staying WebP."""
    import io
    from PIL import Image

    monkeypatch.setattr(vv, "_MAX_LONG_EDGE", 1024)
    img = Image.new("RGB", (4000, 100), color=(200, 200, 200))
    buf = io.BytesIO(); img.save(buf, "PNG"); raw = buf.getvalue()
    compressed = vv._compress_for_vision(raw)
    assert vv._mime_for(compressed) == "image/webp"
    out = Image.open(io.BytesIO(compressed))
    assert max(out.size) <= 1024


def test_mime_detection():
    import io
    from PIL import Image

    img = Image.new("RGB", (10, 10))
    png = io.BytesIO(); img.save(png, "PNG")
    jpg = io.BytesIO(); img.save(jpg, "JPEG")
    webp = io.BytesIO(); img.save(webp, "WEBP")
    assert vv._mime_for(png.getvalue()) == "image/png"
    assert vv._mime_for(jpg.getvalue()) == "image/jpeg"
    assert vv._mime_for(webp.getvalue()) == "image/webp"
    assert vv._mime_for(b"unknown") == "image/png"  # fallback


def test_primary_failure_no_fallback_returns_unavailable(_enable_verifier, monkeypatch):
    """S26-follow-up-L tightening: ONE primary attempt, no retry. When
    fallback is disabled (test default), a primary 429 / timeout
    surfaces as vision_unavailable without burning a second attempt on
    the same provider — retry is now provided by the fallback vendor,
    not by a same-provider re-roll."""
    monkeypatch.setattr(vv.asyncio, "sleep", AsyncMock(return_value=None))
    page = _fake_page_with_screenshot()

    create_mock = MagicMock(side_effect=RuntimeError("Error code: 429 - overloaded"))
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = create_mock
        result = asyncio.run(
            vv.verify_form_page(
                page, {"Email": "a@b.com"},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    assert create_mock.call_count == 1
    assert result.vision_unavailable is True


def test_primary_success_returns_content(_enable_verifier):
    """Happy-path: primary returns content on the single attempt — no
    backoff sleep, no second call. Replaces the pre-L retry-success
    test (the retry no longer exists)."""
    page = _fake_page_with_screenshot()
    payload = {
        "verdicts": [{
            "label": "Email",
            "observed_value": "yash@example.com",
            "matches_claim": True,
            "contradicts_help_text": False,
            "reason": "ok",
        }]
    }
    create_mock = MagicMock(return_value=_vision_response(payload))
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = create_mock
        result = asyncio.run(
            vv.verify_form_page(
                page, {"Email": "yash@example.com"},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    assert create_mock.call_count == 1
    assert result.vision_unavailable is False
    assert result.verdicts[0].tier_reached == "passed"


def test_non_transient_error_does_not_retry(_enable_verifier, monkeypatch):
    """Auth failures, invalid params etc. should NOT burn the retry budget."""
    monkeypatch.setattr(vv.asyncio, "sleep", AsyncMock(return_value=None))
    page = _fake_page_with_screenshot()

    create_mock = MagicMock(side_effect=ValueError("invalid model name"))
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = create_mock
        result = asyncio.run(
            vv.verify_form_page(
                page, {"Email": "a@b.com"},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    assert create_mock.call_count == 1
    assert result.vision_unavailable is True


def test_single_shot_call_count_tall_screenshot(_enable_verifier):
    """S26-follow-up-K invariant — even a tall screenshot produces ONE
    kimi call (the old verifier chunked vertically; the new one crops
    per-field into a composite or falls back to single-shot whole-page).
    """
    import io
    from PIL import Image

    img = Image.new("RGB", (1000, 6000), color=(255, 255, 255))
    buf = io.BytesIO(); img.save(buf, "PNG"); raw = buf.getvalue()

    page = _fake_page_with_screenshot()
    page.screenshot = AsyncMock(return_value=raw)

    response = _vision_response({
        "verdicts": [
            {
                "label": "Email",
                "observed_value": "yash@example.com",
                "matches_claim": True,
                "contradicts_help_text": False,
                "reason": "ok",
            },
            {
                "label": "AI Policy",
                "observed_value": "No",
                "matches_claim": False,
                "contradicts_help_text": True,
                "reason": "form requires Yes",
            },
        ]
    })
    with patch.object(vv, "get_openai_client") as mock_client:
        create_mock = MagicMock(return_value=response)
        mock_client.return_value.chat.completions.create = create_mock
        result = asyncio.run(
            vv.verify_form_page(
                page,
                {"Email": "yash@example.com", "AI Policy": "Yes"},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    assert create_mock.call_count == 1
    tiers = {v.label: v.tier_reached for v in result.verdicts}
    assert tiers == {"Email": "passed", "AI Policy": "mismatch_detected"}


def test_composite_built_when_field_bboxes_resolve(_enable_verifier, tmp_path):
    """When bbox extraction succeeds the verifier builds a composite WebP
    and sends ONE kimi call keyed by ordinal — Outcome 2 evidence."""
    import io
    from PIL import Image

    img = Image.new("RGB", (1000, 800), color=(255, 255, 255))
    buf = io.BytesIO(); img.save(buf, "PNG"); raw = buf.getvalue()

    page = _fake_page_with_screenshot()
    page.screenshot = AsyncMock(return_value=raw)

    # get_by_label returns a locator that resolves to a bbox.
    fake_locator = MagicMock()
    fake_locator.count = AsyncMock(return_value=1)
    fake_locator.evaluate = AsyncMock(
        return_value={"x": 100.0, "y": 100.0, "width": 200.0, "height": 30.0},
    )
    fake_locator.first = fake_locator
    page.get_by_label = MagicMock(return_value=fake_locator)
    page.get_by_placeholder = MagicMock(return_value=fake_locator)
    page.get_by_role = MagicMock(return_value=fake_locator)

    response = _vision_response({
        "verdicts": [
            {
                "ordinal": 1,
                "label": "Email",
                "observed_value": "yash@example.com",
                "matches_claim": True,
                "contradicts_help_text": False,
                "reason": "ordinal 01",
            },
        ]
    })
    with patch.object(vv, "get_openai_client") as mock_client:
        create_mock = MagicMock(return_value=response)
        mock_client.return_value.chat.completions.create = create_mock
        result = asyncio.run(
            vv.verify_form_page(
                page,
                {"Email": "yash@example.com"},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    assert create_mock.call_count == 1
    # The image MIME sent to vision is WebP (composite path) not PNG (fallback)
    sent_args = create_mock.call_args.kwargs["messages"][0]["content"]
    image_url = sent_args[1]["image_url"]["url"]
    assert image_url.startswith("data:image/webp"), \
        "composite path should send WebP"
    assert result.verdicts[0].tier_reached == "passed"


def test_decision_rows_written(_enable_verifier):
    page = _fake_page_with_screenshot()
    payload = {
        "verdicts": [
            {
                "label": "Email",
                "observed_value": "yash@example.com",
                "matches_claim": True,
                "contradicts_help_text": False,
                "reason": "ok",
            }
        ]
    }
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock(
            return_value=_vision_response(payload)
        )
        asyncio.run(
            vv.verify_form_page(
                page, {"Email": "yash@example.com"},
                page_url="https://x.example/y", platform="greenhouse",
            )
        )
    rows = semantic_decisions.query_decisions(
        decision_type="vision_verification", limit=10,
    )
    assert len(rows) == 1
    assert rows[0].field_label == "Email"
    assert rows[0].tier_reached == "passed"
    assert rows[0].mechanism == "llm"
