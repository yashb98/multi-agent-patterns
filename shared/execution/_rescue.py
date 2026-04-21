"""Rescue Agent — LLM-powered fallback for unknown forms.

Vision analysis + cross-domain transfer for unrecognized ATS platforms.
Budget-capped: max 3 rescue attempts per domain per day.
"""

from __future__ import annotations

from shared.execution._event_store import EventStore
from shared.logging_config import get_logger

logger = get_logger(__name__)


class RescueAgent:
    def __init__(self, event_store: EventStore, max_rescues_per_domain: int = 3):
        self._store = event_store
        self._max_rescues = max_rescues_per_domain

    def can_rescue(self, domain: str) -> bool:
        recent = self._store.query(
            event_types=["form.rescue_used"],
            limit=100,
        )
        domain_count = sum(
            1 for e in recent
            if e.get("payload", {}).get("domain") == domain
        )
        return domain_count < self._max_rescues

    def analyze_page(
        self,
        screenshot_b64: str,
        dom_summary: str,
        event_history: list[dict],
    ) -> dict:
        return self._llm_analyze_page(screenshot_b64, dom_summary, event_history)

    def _llm_analyze_page(
        self,
        screenshot_b64: str,
        dom_summary: str,
        event_history: list[dict],
    ) -> dict:
        from shared.agents import get_llm, smart_llm_call
        import json

        prompt = (
            "Analyze this form page and identify all fillable fields.\n\n"
            f"DOM structure:\n{dom_summary[:3000]}\n\n"
            f"Previous attempts: {len(event_history)} events\n\n"
            "Return JSON with:\n"
            '{"fields": [{"label": str, "selector": str, "type": str, "confidence": float}], '
            '"risk": "low"|"medium"|"high"}'
        )
        try:
            llm = get_llm()
            response = smart_llm_call(llm, prompt)
            return json.loads(response)
        except Exception as e:
            logger.error("Rescue LLM analysis failed: %s", e)
            return {"fields": [], "risk": "high", "error": str(e)}

    def find_similar_forms(self, dom_signature: str, limit: int = 5) -> list[dict]:
        return self._store.query(
            event_types=["form.page_filled"],
            limit=limit,
        )
