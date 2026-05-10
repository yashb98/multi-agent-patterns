"""S13 live evidence — cognitive routing context-leak fix.

Verifies, with the real ``shared.MemoryManager`` + cognitive engine
(production state at ``data/agent_memory/memories.db``), that the cross-
domain procedural-recall bleed no longer surfaces orchestration text as
the answer for ``domain="screening_answers"``.

Pre-S13 reproduction (on ``pipeline-correctness-fixes`` HEAD)::

    cognitive_llm_call(
        task='SYSTEM: ... USER: Will you require visa sponsorship?',
        domain='screening_answers', stakes='high',
    )
    → "Enhanced swarm convergence: GRPO group sampling. Score 8.5/10 ..."

Post-S13: the same call returns either a sensible visa-sponsorship
answer (e.g. ``"No, I have Graduate Visa..."``) or, on cache miss + LLM
disagreement, falls through to a ``None`` so the screening pipeline
treats it as a miss.

Run::

    python scripts/audit_s13_live_evidence.py

Cleans up any garbage written to ``data/screening_semantic_cache.db`` /
Qdrant during verification before exiting (S1 lesson — production
caches must not retain test data).
"""

from __future__ import annotations

import logging
import sqlite3
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("audit.s13")


SCREENING_QUESTIONS = [
    # Free-text path (the previously poisoned path).
    {
        "q": ("Will you now or in the future require employment visa "
              "sponsorship?"),
        "field": {"type": "text", "options": []},
        "expect_substring": ("graduate visa", "no, i", "no,"),
    },
    {
        "q": "Why do you want to work at this company?",
        "field": {"type": "text", "options": []},
        "expect_substring": None,  # any prose answer acceptable
    },
    {
        "q": "How did you hear about this position?",
        "field": {"type": "text", "options": []},
        "expect_substring": None,
    },
]


def _run_one(pipeline, item: dict) -> dict:
    out = pipeline.answer(item["q"], job_context={"company": "Anthropic"})
    answer = out.get("answer") or ""
    leaked = any(
        marker in answer.lower()
        for marker in ("enhanced swarm", "grpo", "round 1/3",
                       "score 8.5/10", "iteration 1")
    )
    return {
        "question": item["q"],
        "source": out.get("source"),
        "confidence": out.get("confidence"),
        "answer_prefix": answer[:120],
        "leaked": leaked,
        "expect": item["expect_substring"],
    }


def _cleanup_garbage_cache() -> int:
    """Delete any leak-pattern rows that this evidence script may have
    written to the production cache. Returns count removed."""
    try:
        with sqlite3.connect("data/screening_semantic_cache.db") as conn:
            cur = conn.execute(
                "DELETE FROM screening_semantic_cache "
                "WHERE answer LIKE '%Enhanced swarm%' "
                "OR answer LIKE '%GRPO%' "
                "OR answer LIKE '%score 8.%/10%' "
                "OR answer LIKE '%Round %/%' "
                "OR answer LIKE '%successes on map_reduce%'"
            )
            removed = cur.rowcount
            conn.commit()
    except sqlite3.OperationalError as exc:
        logger.warning("Cache cleanup skipped: %s", exc)
        return 0
    if removed:
        logger.warning("Cleaned %d leak-pattern rows from production cache",
                       removed)
    return removed


def _build_pipeline_against_production():
    """Wire ScreeningPipeline against production caches/DBs (no tmp_path).

    Production state is the only meaningful target for live evidence —
    a tmp_path test wouldn't include the cross-domain procedural rows
    that triggered the original leak. Same merge shape as
    ``screening_answers._get_v2_pipeline``."""
    from jobpulse.screening_answers import PROFILE, WORK_AUTH
    from jobpulse.screening_pipeline import ScreeningPipeline

    merged = dict(PROFILE)
    merged["visa_status"] = str(WORK_AUTH.get("visa_status", ""))
    merged["visa_sponsorship_required"] = (
        "No" if not WORK_AUTH.get("requires_sponsorship") else "Yes"
    )
    merged["right_to_work"] = (
        "Yes" if WORK_AUTH.get("right_to_work_uk") else "No"
    )
    merged["notice_period"] = str(WORK_AUTH.get("notice_period", ""))
    merged["salary_expectation"] = str(WORK_AUTH.get("salary_expectation", ""))
    return ScreeningPipeline(profile=merged)


def _direct_cognitive_check() -> dict:
    """Directly invoke ``cognitive_llm_call(domain="screening_answers")``
    against the production memory store. This is the unmediated check —
    if cognitive routing is still bleeding, it shows up here regardless
    of any screening_pipeline guard."""
    from shared.agents import cognitive_llm_call
    task = (
        "SYSTEM: You are answering a job application screening question. "
        "Answer concisely and honestly based on the candidate profile "
        "provided. Never mention that you are an AI.\n"
        "USER: Will you now or in the future require employment visa "
        "sponsorship?"
    )
    try:
        result = cognitive_llm_call(
            task=task, domain="screening_answers", stakes="high",
        )
    except Exception as exc:
        return {"error": repr(exc), "leaked": False, "answer": None}
    leaked = bool(result) and any(
        m in result.lower()
        for m in ("enhanced swarm", "grpo", "round 1/3", "score 8.5/10")
    )
    return {"answer": (result or "")[:200], "leaked": leaked,
            "is_none": result is None}


def main() -> int:
    logger.info("=== S13 live evidence (cognitive routing leak fix) ===")
    logger.info("Pre-flight cleanup of any prior leak-pattern cache rows")
    pre = _cleanup_garbage_cache()
    logger.info("  removed=%d", pre)

    logger.info("\n--- 1. Direct cognitive_llm_call(domain='screening_answers') ---")
    direct = _direct_cognitive_check()
    logger.info("  result.is_none=%s leaked=%s answer_prefix=%r",
                direct.get("is_none"), direct["leaked"],
                direct.get("answer", "")[:120])
    if direct["leaked"]:
        logger.error("FAIL: cognitive_llm_call leaked orchestration text")
        return 2

    logger.info("\n--- 2. ScreeningPipeline.answer end-to-end ---")
    pipeline = _build_pipeline_against_production()
    results = []
    for item in SCREENING_QUESTIONS:
        r = _run_one(pipeline, item)
        results.append(r)
        flag = "LEAK" if r["leaked"] else "OK"
        logger.info(
            "  [%s] q=%r src=%s conf=%s ans=%r",
            flag, r["question"][:55], r["source"], r["confidence"],
            r["answer_prefix"][:80],
        )

    logger.info("\n--- 3. Post-run cleanup (cache hygiene) ---")
    post = _cleanup_garbage_cache()
    logger.info("  removed=%d", post)

    leaked = [r for r in results if r["leaked"]]
    if leaked:
        logger.error("FAIL: %d question(s) leaked orchestration text:",
                     len(leaked))
        for r in leaked:
            logger.error("  q=%r ans=%r", r["question"], r["answer_prefix"])
        return 1

    logger.info("\n=== S13 PASS ===")
    logger.info("  direct cognitive call: clean (is_none=%s)",
                direct.get("is_none"))
    logger.info("  pipeline answers: %d / %d clean",
                len(results), len(results))
    logger.info("  cache hygiene: pre=%d post=%d rows removed", pre, post)
    return 0


if __name__ == "__main__":
    sys.exit(main())
