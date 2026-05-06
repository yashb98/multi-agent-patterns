"""Subsystem-2 audit regression guards.

Two fixes ship together:
  - intent_healing._HEAL_PROMPT.format() must not raise KeyError on the
    literal '{"selector": ...}' braces (Path 3 of heal_locator was
    silently dead).
  - semantic_option_match must not fall through alias bidirectional
    substring containment for short (<3 char) aliases like "y", "n",
    "m", "f" — they leak into unrelated options ("y" → "yorkshire").
"""
from __future__ import annotations

import pytest

from jobpulse.form_engine.intent_healing import (
    FieldIntent,
    _HEAL_PROMPT,
    _call_llm_for_selector,
)
from jobpulse.form_engine.semantic_matcher import semantic_option_match


class TestHealPromptFormatNotBroken:
    def test_format_does_not_raise_keyerror(self):
        # Pre-fix: KeyError '"selector"' because the literal { in the
        # template was parsed as a format field. _call_llm_for_selector
        # caught the exception silently and returned None, so Path 3
        # never resolved a single locator in production.
        out = _HEAL_PROMPT.format(
            label="Country",
            role="combobox",
            field_type="select",
            neighborhood="Personal info",
            a11y_summary="  - First Name | textbox | text | id=fn",
        )
        # Literal braces preserved
        assert '{"selector": "<css>"}' in out
        assert '{"selector": null}' in out
        # Substitutions happened
        assert "label: Country" in out
        assert "role: combobox" in out

    def test_call_llm_does_not_silently_swallow_format_error(
        self, monkeypatch
    ):
        """Even if the LLM call itself fails, the format step must not
        raise — that's what dead-coded Path 3 in production."""
        from langchain_core.messages import HumanMessage  # noqa: F401

        # Stub get_llm + smart_llm_call to return a fake JSON response
        class _FakeResponse:
            content = '{"selector": "#country"}'

        def _fake_smart_llm_call(llm, messages):
            return _FakeResponse()

        def _fake_get_llm(*args, **kwargs):
            return object()

        monkeypatch.setattr(
            "shared.agents.get_llm", _fake_get_llm,
        )
        monkeypatch.setattr(
            "shared.agents.smart_llm_call", _fake_smart_llm_call,
        )
        intent = FieldIntent(
            label="Country", role="combobox", field_type="select",
        )
        result = _call_llm_for_selector(
            intent,
            [{"label": "Country", "role": "combobox", "input_type": "select", "id": "country"}],
        )
        assert result == "#country"


class TestSemanticMatcherShortAliasGuard:
    def test_short_alias_y_does_not_leak_into_unrelated(self):
        # "yes" → alias "y"; pre-fix returned "Yorkshire" via substring.
        result = semantic_option_match(
            "yes", ["Yorkshire", "Greenwich", "Confirmed"],
        )
        assert result != "Yorkshire", (
            f"short alias 'y' leaked into 'Yorkshire': got {result!r}"
        )

    def test_short_alias_n_does_not_leak_into_unrelated(self):
        # "no" → alias "n"; pre-fix returned "Not at this time" via
        # substring even when the user meant a literal decline.
        result = semantic_option_match(
            "no", ["Confirm", "Decline", "Maybe later"],
        )
        assert result != "Confirm", (
            f"short alias 'n' leaked into 'Confirm': got {result!r}"
        )

    def test_short_alias_m_does_not_leak_into_human(self):
        result = semantic_option_match(
            "male", ["human", "female", "unspecified"],
        )
        # 'male' has alias 'm' — must not match 'human' via substring
        assert result != "human"

    def test_long_alias_substring_still_works(self):
        # graduate visa → 'graduate route visa' alias must still match
        # via substring containment when no exact match exists.
        result = semantic_option_match(
            "graduate visa",
            ["Graduate route visa", "Tier 2", "Skilled Worker"],
        )
        assert result == "Graduate route visa"

    def test_exact_alias_match_still_returns(self):
        # "yes" → "true" alias; "true" is exactly an option
        result = semantic_option_match(
            "yes", ["true", "false", "maybe"],
        )
        assert result == "true"
