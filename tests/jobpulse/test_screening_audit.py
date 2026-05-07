"""S4 audit guards for screening pipeline blockers.

Tests B-1, B-2, B-3, B-4 from `/tmp/audit-screening_pipeline.md`.
Each test reproduces a confirmed runtime bug; fixes ship in the same
commit so the test passes after the fix.
"""

from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# B-1: COMMON_ANSWERS regex collisions on "currently based in the UK?" /
# variants. The two-bug collision returns the user's salary or "No"
# instead of routing to the V2 intent classifier (LOCATION_CURRENT /
# WILLING_RELOCATE / WORK_AUTH_YES_NO depending on phrasing).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_legacy_tier(monkeypatch):
    # Pin to legacy tier so this test exercises the regex dict — V2 is
    # tested elsewhere (test_screening_v2.py).
    monkeypatch.setenv("JOBPULSE_TEST_MODE", "1")


def test_b1_currently_based_uk_does_not_match_current_base_salary():
    """`current.*base` must not steal "currently based" location queries."""
    from jobpulse.screening_answers import try_instant_answer

    answer = try_instant_answer("Are you currently based in the UK?")
    # Pre-fix: returns the user's salary like "22000". After fix the
    # regex no longer matches and the function returns None (instant
    # tier miss → V2 / LLM tier handles it).
    assert answer != "22000"
    assert not (answer or "").isdigit(), (
        f"`current.*base` regex should not match location/residency questions; "
        f"got {answer!r}"
    )


def test_b1_uk_residency_questions_do_not_hardcode_no():
    """The L199 `based.*in.*uk|...` block must not auto-answer 'No'.

    The user IS based in the UK; the legitimate "are you a permanent UK
    resident / citizen / settled status holder" cases are already
    covered by L127 (`british.*citizen|...|settled.*status`). The L199
    pattern over-matches and produces wrong answers for plain location
    questions.
    """
    from jobpulse.screening_answers import try_instant_answer

    for q in (
        "Do you live in the UK?",
        "Are you a UK resident?",
        "Do you reside in the United Kingdom?",
    ):
        answer = try_instant_answer(q)
        assert answer != "No", (
            f"Question {q!r} should not be auto-answered 'No' via the "
            f"residency regex; got {answer!r}"
        )


# ---------------------------------------------------------------------------
# B-2: operator precedence in WILLING_RELOCATE short-circuit.
# ---------------------------------------------------------------------------


def test_b2_willing_relocate_does_not_short_circuit_with_empty_profile():
    """`my_loc=""` must not trigger the 'already in same area' branch."""
    from jobpulse.screening_pipeline import ScreeningPipeline
    from jobpulse.screening_intent import ScreeningIntent

    pipeline = ScreeningPipeline(profile={})  # empty profile → my_loc==""
    result = pipeline._resolve_intent_from_profile(
        ScreeningIntent.WILLING_RELOCATE, {"location": "London"},
    )
    # Pre-fix: returns 'No' because `"" in "london"` is True.
    # After fix: profile has no `willing_to_relocate` either, so
    # mapping lookup returns None.
    assert result != "No", (
        "WILLING_RELOCATE with empty profile location should not short-circuit "
        f"to 'No'; got {result!r}"
    )


def test_b2_willing_relocate_real_geo_overlap_still_returns_no():
    """The legitimate same-area short-circuit must still work after fix."""
    from jobpulse.screening_pipeline import ScreeningPipeline
    from jobpulse.screening_intent import ScreeningIntent

    pipeline = ScreeningPipeline(profile={"location": "London, UK"})
    result = pipeline._resolve_intent_from_profile(
        ScreeningIntent.WILLING_RELOCATE, {"location": "London"},
    )
    # Job is in London, user is in London — short-circuit applies.
    assert result == "No"


# ---------------------------------------------------------------------------
# B-3: missing _get_qdrant_client export breaks
# cross_platform_field_transfer's vector store init.
# ---------------------------------------------------------------------------


def test_b3_get_qdrant_client_export_exists():
    """`_get_qdrant_client` must be importable from screening_semantic_cache.

    Otherwise `cross_platform_field_transfer._init_vector_stores`
    silently fails the import (caught by a bare except) and leaves
    `self._qdrant = None`, killing cross-platform vector lookups.
    """
    from jobpulse.screening_semantic_cache import _get_qdrant_client  # noqa: F401


def test_b3_cross_platform_transfer_uses_canonical_imports():
    """`_init_vector_stores` must call importable modules: the canonical
    `shared.semantic_utils._get_embedder` and the
    `screening_semantic_cache._get_qdrant_client` accessor (added in
    B-3 fix). Pre-fix it imported a nonexistent `shared.embeddings`
    module → `embedder=None`.
    """
    # Both canonical accessors exist
    from shared.semantic_utils import _get_embedder
    from jobpulse.screening_semantic_cache import _get_qdrant_client
    assert callable(_get_embedder)
    assert callable(_get_qdrant_client)

    # Walk the AST of `_init_vector_stores` body and inspect actual
    # import statements (not docstring / comments).
    import ast, inspect
    from jobpulse import cross_platform_field_transfer

    src = inspect.getsource(
        cross_platform_field_transfer.CrossPlatformFieldTransfer._init_vector_stores
    )
    tree = ast.parse(src.strip())
    import_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            import_modules.append(node.module)

    assert "shared.embeddings" not in import_modules, (
        f"_init_vector_stores still imports `shared.embeddings`; "
        f"actual imports={import_modules}"
    )
    assert "shared.semantic_utils" in import_modules
    assert "jobpulse.screening_semantic_cache" in import_modules


# ---------------------------------------------------------------------------
# B-4: feedback_loop passes intent=None to extractor.observe when
# classifier failed to init, silently dropping the observation.
# ---------------------------------------------------------------------------


def test_b4_feedback_loop_intent_none_is_remapped(monkeypatch, tmp_path):
    """When classifier is None, intent must default to UNKNOWN, not None."""
    monkeypatch.setattr(
        "jobpulse.config.DATA_DIR", tmp_path,
    )

    from unittest.mock import MagicMock

    from jobpulse.screening_feedback_loop import ScreeningFeedbackLoop
    from jobpulse.screening_intent import ScreeningIntent

    fake_extractor = MagicMock()
    fake_cache = MagicMock()
    fake_aligner = MagicMock()
    fake_classifier_for_init = MagicMock()  # passed to skip auto-init

    loop = ScreeningFeedbackLoop(
        cache=fake_cache,
        classifier=fake_classifier_for_init,
        aligner=fake_aligner,
        extractor=fake_extractor,
    )
    # Now overwrite to simulate post-init "classifier failed during use"
    # (matches the production scenario where embed() throws after init).
    loop._classifier = None

    loop.learn_from_correction(
        question="What is your current salary?",
        agent_answer="20000",
        user_answer="35000",
        field_options=None,
        field_type="text",
    )

    # Verify extractor.observe was actually called and that the intent
    # passed in was the UNKNOWN sentinel — not None (which would crash
    # PatternExtractor.observe → silent log.debug).
    assert fake_extractor.observe.called, "observe() must be called even with no classifier"
    for call in fake_extractor.observe.call_args_list:
        intent = call.kwargs.get("intent", call.args[2] if len(call.args) > 2 else None)
        assert intent is not None, (
            "intent must default to UNKNOWN when classifier is None; got None"
        )
        assert intent == ScreeningIntent.UNKNOWN, (
            f"intent must default to UNKNOWN, got {intent!r}"
        )
