"""Post-application strategy reflection and heuristic extraction.

Two-pass architecture (arXiv 2603.24639 — Experiential Reflective Learning):
    Pass 1: Deterministic — statistical pattern extraction from trajectories (free)
    Pass 2: LLM reflection — edge cases where deterministic pass finds < 2 heuristics (~$0.002)

Extracted heuristics feed into:
    - TrajectoryStore.heuristics table (domain/platform-scoped)
    - ExperienceMemory (shared GRPO engine) for cross-domain learning
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

from shared.agents import get_llm, smart_llm_call
from shared.logging_config import get_logger

from jobpulse.trajectory_store import _is_sensitive_field

if TYPE_CHECKING:
    from jobpulse.trajectory_store import (
        ApplicationStrategy,
        FieldTrajectory,
        Heuristic,
        TrajectoryStore,
    )

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Pass 1: Deterministic heuristic extraction (free, instant)
# ------------------------------------------------------------------


def _extract_correction_heuristics(
    trajectories: list[FieldTrajectory],
) -> list[dict]:
    """Extract heuristics from user corrections — highest signal."""
    heuristics = []
    for t in trajectories:
        if not t.corrected or not t.corrected_value:
            continue
        heuristics.append({
            "trigger": f"field '{t.field_label}' ({t.field_type}) on {t.domain}",
            "action": f"use '{t.corrected_value}' instead of strategy '{t.strategy}' which gave '{t.value_filled}'",
            "confidence": 0.95,
            "source": "correction",
        })
    return heuristics


def _extract_strategy_distribution_heuristics(
    trajectories: list[FieldTrajectory],
) -> list[dict]:
    """Extract heuristics from which strategies succeed most."""
    if len(trajectories) < 3:
        return []

    strategy_counts = Counter(t.strategy for t in trajectories)
    corrected_by_strategy = Counter(
        t.strategy for t in trajectories if t.corrected
    )

    heuristics = []
    for strategy, count in strategy_counts.items():
        if count < 2:
            continue
        correction_rate = corrected_by_strategy.get(strategy, 0) / count
        if correction_rate > 0.5:
            heuristics.append({
                "trigger": f"strategy '{strategy}' on {trajectories[0].domain}",
                "action": f"avoid '{strategy}' — corrected {correction_rate:.0%} of the time, prefer alternative tier",
                "confidence": min(0.9, 0.5 + count * 0.05),
                "source": "strategy_distribution",
            })
        elif correction_rate == 0 and count >= 3:
            heuristics.append({
                "trigger": f"strategy '{strategy}' on {trajectories[0].domain}",
                "action": f"'{strategy}' is reliable here — {count} uses, 0 corrections",
                "confidence": min(0.9, 0.5 + count * 0.05),
                "source": "strategy_distribution",
            })

    return heuristics


def _extract_slow_field_heuristics(
    trajectories: list[FieldTrajectory],
    threshold_ms: int = 5000,
) -> list[dict]:
    """Flag fields that consistently take too long — candidate for caching."""
    slow = [t for t in trajectories if t.time_ms > threshold_ms]
    if not slow:
        return []

    heuristics = []
    field_counts = Counter(t.field_label for t in slow)
    for field, count in field_counts.most_common(3):
        if count >= 2:
            strategies = [t.strategy for t in slow if t.field_label == field]
            heuristics.append({
                "trigger": f"field '{field}' takes >{threshold_ms}ms on {trajectories[0].domain}",
                "action": f"pre-cache or pattern-match this field (strategies used: {', '.join(set(strategies))})",
                "confidence": 0.7,
                "source": "slow_field",
            })

    return heuristics


def extract_deterministic_heuristics(
    trajectories: list[FieldTrajectory],
) -> list[dict]:
    """Pass 1: All deterministic (statistical) heuristic extractors."""
    results = []
    results.extend(_extract_correction_heuristics(trajectories))
    results.extend(_extract_strategy_distribution_heuristics(trajectories))
    results.extend(_extract_slow_field_heuristics(trajectories))
    return results


# ------------------------------------------------------------------
# Pass 2: LLM reflection (edge cases only, ~$0.002)
# ------------------------------------------------------------------


def _build_reflection_prompt(
    strategy: ApplicationStrategy,
    trajectories: list[FieldTrajectory],
) -> str:
    """Build the reflection prompt from trajectory data."""
    field_summary = []
    for t in trajectories[:30]:
        status = "CORRECTED" if t.corrected else "ok"
        sensitive = _is_sensitive_field(t.field_label)
        val_display = "[sensitive]" if sensitive else t.value_filled[:40]
        corrected_display = ""
        if t.corrected:
            corrected_display = f", corrected_to='[sensitive]'" if sensitive else f", corrected_to='{t.corrected_value[:30]}'"
        field_summary.append(
            f"  {t.field_label} ({t.field_type}): strategy={t.strategy}, "
            f"confidence={t.confidence:.2f}, time={t.time_ms}ms, status={status}"
            + corrected_display
        )

    fields_text = "\n".join(field_summary) if field_summary else "  (no fields recorded)"

    return f"""You are analyzing a completed job application to extract reusable heuristics.

## Application Summary
- Domain: {strategy.domain}
- Platform: {strategy.platform}
- Success: {strategy.success}
- Fields: {strategy.fields_total} total, {strategy.fields_pattern} pattern-matched, {strategy.fields_llm} LLM-generated, {strategy.fields_cached} cached, {strategy.fields_corrected} corrected
- Time: {strategy.total_time_seconds:.1f}s
- Navigation: {strategy.navigation_strategy or 'default'}

## Field-by-Field Trajectory
{fields_text}

## Task
Extract 2-3 reusable heuristics from this application. Each heuristic should be:
- Specific enough to apply on this domain/platform
- Actionable (tells the system what to DO differently next time)
- Based on evidence from the trajectory (corrections, slow fields, strategy failures)

Return ONLY a JSON array of objects with keys: trigger, action, confidence (0-1).
No explanation, no markdown fences.
Example: [{{"trigger": "city field on smartrecruiters", "action": "type text then ArrowDown+Enter for shadow DOM autocomplete", "confidence": 0.85}}]"""


def reflect_with_llm(
    strategy: ApplicationStrategy,
    trajectories: list[FieldTrajectory],
) -> list[dict]:
    """Pass 2: LLM reflection for edge cases. Returns parsed heuristics."""
    from shared.cost_tracker import track_llm_usage

    prompt = _build_reflection_prompt(strategy, trajectories)

    try:
        llm = get_llm(temperature=0.3, tier="mini")
        response = smart_llm_call(
            llm, prompt, agent_name="strategy_reflection",
        )

        raw = response.content if hasattr(response, "content") else str(response)
        raw = raw.strip()

        if hasattr(response, "_jobpulse_usage"):
            pass
        else:
            track_llm_usage(response, agent_name="strategy_reflection")

        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [
                h for h in parsed
                if isinstance(h, dict) and "trigger" in h and "action" in h
            ]
    except json.JSONDecodeError:
        logger.warning("strategy_reflector: LLM returned non-JSON reflection")
    except Exception as exc:
        logger.warning("strategy_reflector: LLM reflection failed: %s", exc)

    return []


# ------------------------------------------------------------------
# Main reflection pipeline
# ------------------------------------------------------------------


def reflect_on_application(
    store: TrajectoryStore,
    job_id: str,
    job_context: dict,
    *,
    llm_threshold: int = 2,
) -> ApplicationStrategy:
    """Full reflection pipeline for a completed application.

    1. Aggregate field trajectories → strategy summary
    2. Pass 1: Deterministic heuristic extraction (free)
    3. Pass 2: LLM reflection if Pass 1 found < llm_threshold heuristics
    4. Save strategy + heuristics to TrajectoryStore
    5. Feed high-quality heuristics to ExperienceMemory (GRPO)

    Returns the saved ApplicationStrategy.
    """
    from jobpulse.trajectory_store import Heuristic

    trajectories = store.get_trajectories(job_id)
    strategy = store.aggregate_strategy(job_id, job_context, trajectories=trajectories)

    # Pass 1: deterministic
    det_heuristics = extract_deterministic_heuristics(trajectories)
    logger.info(
        "strategy_reflector: Pass 1 extracted %d heuristics for %s",
        len(det_heuristics), strategy.domain,
    )

    # Pass 2: LLM reflection if deterministic found too few
    llm_heuristics = []
    if len(det_heuristics) < llm_threshold and len(trajectories) >= 3:
        llm_heuristics = reflect_with_llm(strategy, trajectories)
        logger.info(
            "strategy_reflector: Pass 2 (LLM) extracted %d heuristics for %s",
            len(llm_heuristics), strategy.domain,
        )

    all_heuristics = det_heuristics + llm_heuristics

    # Save strategy with heuristics
    strategy.reflection = json.dumps(
        {"deterministic": len(det_heuristics), "llm": len(llm_heuristics)},
    )
    strategy.heuristics = json.dumps(all_heuristics)
    store.save_strategy(strategy)

    # Save as typed heuristics with TTL
    typed = [
        Heuristic(
            trigger=h["trigger"],
            action=h["action"],
            confidence=h.get("confidence", 0.5),
            source_domain=strategy.domain,
            platform=strategy.platform,
        )
        for h in all_heuristics
    ]
    if typed:
        store.save_heuristics(typed)

    # Feed to ExperienceMemory (GRPO) for cross-domain learning
    _feed_experience_memory(strategy, all_heuristics)

    return strategy


def _feed_experience_memory(
    strategy: ApplicationStrategy,
    heuristics: list[dict],
) -> None:
    """Store high-quality strategies in the shared ExperienceMemory."""
    if not heuristics or not strategy.success:
        return

    try:
        from shared.experiential_learning import Experience, ExperienceMemory

        em = ExperienceMemory()
        score = _compute_strategy_score(strategy)
        if score < 6.0:
            return

        pattern = (
            f"Domain: {strategy.domain} | Platform: {strategy.platform}\n"
            f"Fields: {strategy.fields_total} total, "
            f"{strategy.fields_pattern} pattern, {strategy.fields_llm} LLM, "
            f"{strategy.fields_corrected} corrected\n"
            f"Heuristics:\n"
            + "\n".join(f"  - {h['trigger']} → {h['action']}" for h in heuristics[:5])
        )

        exp = Experience(
            task_description=f"job_application:{strategy.domain}:{strategy.platform}",
            successful_pattern=pattern,
            score=score,
            domain="job_application",
        )
        em.store(exp)
        logger.info(
            "strategy_reflector: stored experience (score=%.1f) for %s",
            score, strategy.domain,
        )
    except Exception as exc:
        logger.debug("strategy_reflector: ExperienceMemory feed failed: %s", exc)


def _compute_strategy_score(strategy: ApplicationStrategy) -> float:
    """Score a strategy 0-10 for ExperienceMemory quality ranking."""
    if not strategy.success:
        return 2.0

    score = 5.0

    # Bonus for high pattern-match ratio (efficient)
    if strategy.fields_total > 0:
        pattern_ratio = strategy.fields_pattern / strategy.fields_total
        score += pattern_ratio * 2.0

    # Penalty for corrections (agent got it wrong)
    if strategy.fields_total > 0:
        correction_ratio = strategy.fields_corrected / strategy.fields_total
        score -= correction_ratio * 3.0

    # Bonus for speed
    if strategy.total_time_seconds < 60:
        score += 1.0
    elif strategy.total_time_seconds > 300:
        score -= 0.5

    return max(0.0, min(10.0, score))
