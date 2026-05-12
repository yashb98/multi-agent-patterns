"""Hard filter — drops papers without empirical results."""

from __future__ import annotations

import json as _json

from jobpulse.papers.models import Paper
from research_journal.models import PaperTypeClassification
from shared.logging_config import get_logger

logger = get_logger(__name__)


def classify_results(paper: Paper) -> PaperTypeClassification:
    if not paper.abstract or not paper.abstract.strip():
        return PaperTypeClassification(
            has_results=False, paper_type="research",
            reason="empty abstract — cannot assess", confidence=0.0,
        )
    try:
        result = _llm_classify(paper)
    except Exception as exc:
        logger.warning("results_filter LLM failed (%s); keeping paper with has_results=True", exc)
        return PaperTypeClassification(
            has_results=True, paper_type="research",
            reason=f"classifier failed: {exc}", confidence=0.0,
        )

    # Low-confidence: don't hard-drop. Mark has_results=True so paper survives the filter.
    if result.confidence < 0.6:
        return PaperTypeClassification(
            has_results=True, paper_type=result.paper_type,
            reason=f"low-confidence ({result.confidence:.2f}); kept by default — {result.reason}",
            confidence=result.confidence,
        )
    return result


def _llm_classify(paper: Paper) -> PaperTypeClassification:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt

    prompt = get_prompt("journal", "results_filter")
    call_params = prompt.render(title=paper.title, abstract=paper.abstract[:3000])
    task = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in call_params["messages"]
    )
    raw = cognitive_llm_call(
        task=task,
        domain="journal_results",
        stakes="low",
        fallback_messages=call_params["messages"],
        response_format={"type": "json_object"},
    ) or "{}"
    data = _json.loads(_strip_codefence(raw))
    return PaperTypeClassification(**data)


def _strip_codefence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    return s.strip()
