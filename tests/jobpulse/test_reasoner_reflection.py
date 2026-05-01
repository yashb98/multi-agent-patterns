"""Tests for reason_with_failure — failure-driven re-grounding."""
from unittest.mock import patch, MagicMock
import json
from jobpulse.page_analysis.page_reasoner import PageReasoner


class TestReasonWithFailure:
    def test_failure_context_appears_in_prompt(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap = {
            "url": "https://example.com/login", "page_text_preview": "login",
            "dialog_text": "", "fields": [], "buttons": [],
        }
        captured_prompts = []
        with patch("jobpulse.page_analysis.page_reasoner.smart_llm_call") as mock_call:
            response = MagicMock(content=json.dumps({
                "page_understanding": "stuck on login",
                "page_type": "login_form",
                "action": "wait_human",
                "target_text": "",
                "field_fills": [], "advance_button": "",
                "overlays_to_dismiss": [],
                "reasoning": "previous fill bounced",
                "confidence": 0.4,
                "expected_outcome": "page_unchanged",
            }))
            def capture_call(*args, **kwargs):
                # Args: (llm, messages) — capture the messages
                captured_prompts.append(args[1])
                return response
            mock_call.side_effect = capture_call
            with patch("jobpulse.page_analysis.page_reasoner.get_llm",
                       return_value=MagicMock()):
                action = pr.reason_with_failure(
                    snap,
                    failure_context="ghost_click on advance_button=Sign in",
                )
        assert action.action == "wait_human"
        # The prompt sent to the LLM must contain the failure context
        all_text = str(captured_prompts)
        assert "ghost_click" in all_text

    def test_reflection_does_not_use_or_set_cache(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap = {
            "url": "https://example.com/x", "page_text_preview": "x",
            "dialog_text": "", "fields": [], "buttons": [],
        }
        # Pre-populate the cache
        from jobpulse.page_analysis.page_reasoner import PageAction
        cached = PageAction(
            page_understanding="cached", action="fill_form", target_text="",
            reasoning="cached", confidence=0.95, page_type="application_form",
        )
        key = pr._cache_key(snap["url"], snap["page_text_preview"], snap["dialog_text"],
                             snap["fields"], snap["buttons"])
        pr._set_cache(key, cached)

        with patch("jobpulse.page_analysis.page_reasoner.smart_llm_call") as mock_call:
            response = MagicMock(content=json.dumps({
                "page_understanding": "fresh", "page_type": "login_form",
                "action": "wait_human", "target_text": "", "field_fills": [],
                "advance_button": "", "overlays_to_dismiss": [],
                "reasoning": "fresh", "confidence": 0.4,
                "expected_outcome": "unknown",
            }))
            mock_call.return_value = response
            with patch("jobpulse.page_analysis.page_reasoner.get_llm",
                       return_value=MagicMock()):
                action = pr.reason_with_failure(snap, failure_context="test")
        # Reflection returned the fresh result, not the cached one
        assert action.page_understanding == "stuck on login" or action.page_understanding == "fresh"
        # Cache was NOT overwritten with the reflection result
        still_cached = pr._get_cached(key)
        assert still_cached is not None
        assert still_cached.page_understanding == "cached"
