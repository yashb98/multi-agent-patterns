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

    def test_reflection_prompt_forbids_failed_action(self, tmp_path):
        """Regression: live runs showed reflection returning the SAME action
        that just failed (fill_and_advance → reflection → fill_and_advance).
        Prompt must explicitly forbid the prior action when it's known.
        """
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap = {
            "url": "https://reed.co.uk/login", "page_text_preview": "login",
            "dialog_text": "", "fields": [], "buttons": [],
        }
        captured_prompts: list[object] = []
        with patch("jobpulse.page_analysis.page_reasoner.smart_llm_call") as mock_call:
            response = MagicMock(content=json.dumps({
                "page_understanding": "session expired", "page_type": "session_expired",
                "action": "wait_human", "target_text": "", "field_fills": [],
                "advance_button": "", "overlays_to_dismiss": [],
                "reasoning": "credentials rejected → human", "confidence": 0.7,
                "expected_outcome": "page_unchanged",
            }))

            def capture_call(*args, **kwargs):
                captured_prompts.append(args[1])
                return response

            mock_call.side_effect = capture_call
            with patch("jobpulse.page_analysis.page_reasoner.get_llm",
                       return_value=MagicMock()):
                pr.reason_with_failure(
                    snap,
                    failure_context=(
                        "trigger=expected_outcome_violation | "
                        "expected=url_changes | action=fill_and_advance | "
                        "pre_url=https://reed.co.uk/login | "
                        "post_url=https://reed.co.uk/login | "
                        "ghost_click=False | expected_outcome_met=False"
                    ),
                )
        prompt_text = str(captured_prompts)
        # Must explicitly forbid the action that just failed.
        assert "DO NOT return action='fill_and_advance'" in prompt_text, (
            "Reflection prompt must forbid the failed action by name — "
            "without it the LLM keeps returning the same action that "
            "failed (observed live on Reed login + Workday signup)."
        )
        # Must enumerate concrete escalation alternatives.
        for alt in ("wait_human", "go_back", "dismiss_overlay", "abort"):
            assert alt in prompt_text, f"reflection prompt missing alternative: {alt}"

    def test_reflection_prompt_uses_generic_forbid_when_action_missing(self, tmp_path):
        """When the failure_context doesn't include action=X, the prompt
        falls back to a generic 'don't return the same action' clause.
        """
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap = {
            "url": "https://example.com/x", "page_text_preview": "x",
            "dialog_text": "", "fields": [], "buttons": [],
        }
        captured_prompts: list[object] = []
        with patch("jobpulse.page_analysis.page_reasoner.smart_llm_call") as mock_call:
            response = MagicMock(content=json.dumps({
                "page_understanding": "abort", "page_type": "unknown",
                "action": "abort", "target_text": "", "field_fills": [],
                "advance_button": "", "overlays_to_dismiss": [],
                "reasoning": "no signal", "confidence": 0.3,
                "expected_outcome": "unknown",
            }))

            def capture_call(*args, **kwargs):
                captured_prompts.append(args[1])
                return response

            mock_call.side_effect = capture_call
            with patch("jobpulse.page_analysis.page_reasoner.get_llm",
                       return_value=MagicMock()):
                pr.reason_with_failure(snap, failure_context="trigger=ghost_click")
        prompt_text = str(captured_prompts)
        # No action= in failure_context, so the generic clause should fire.
        assert "DO NOT return the same action" in prompt_text
        assert "DO NOT return action='" not in prompt_text
