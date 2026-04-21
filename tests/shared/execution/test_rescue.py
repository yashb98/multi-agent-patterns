import pytest
from unittest.mock import patch, MagicMock


class TestRescueAgent:
    def test_analyze_unknown_form_returns_field_map(self):
        from shared.execution._rescue import RescueAgent
        agent = RescueAgent(event_store=MagicMock())
        with patch.object(agent, "_llm_analyze_page") as mock_llm:
            mock_llm.return_value = {
                "fields": [
                    {"label": "Name", "selector": "#name", "type": "text", "confidence": 0.9},
                    {"label": "Email", "selector": "#email", "type": "email", "confidence": 0.85},
                ],
                "risk": "low",
            }
            result = agent.analyze_page(
                screenshot_b64="fake_base64",
                dom_summary="<form><input id='name'/><input id='email'/></form>",
                event_history=[],
            )
            assert len(result["fields"]) == 2
            assert result["risk"] == "low"

    def test_rescue_budget_cap(self):
        from shared.execution._rescue import RescueAgent
        store = MagicMock()
        store.query.return_value = [
            {"event_type": "form.rescue_used", "payload": {"domain": "x.com"}},
            {"event_type": "form.rescue_used", "payload": {"domain": "x.com"}},
            {"event_type": "form.rescue_used", "payload": {"domain": "x.com"}},
        ]
        agent = RescueAgent(event_store=store, max_rescues_per_domain=3)
        assert agent.can_rescue("x.com") is False

    def test_rescue_allowed_under_cap(self):
        from shared.execution._rescue import RescueAgent
        store = MagicMock()
        store.query.return_value = [
            {"event_type": "form.rescue_used", "payload": {"domain": "x.com"}},
        ]
        agent = RescueAgent(event_store=store, max_rescues_per_domain=3)
        assert agent.can_rescue("x.com") is True
