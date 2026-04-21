"""Eval runner — orchestrate adversarial evaluation pipeline."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from shared.adversarial._baseline_tracker import BaselineTracker
from shared.adversarial._golden_suite import load_golden_suite
from shared.adversarial._injection_tester import InjectionTester, TestResult
from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class EvalReport:
    timestamp: str
    total: int
    passed: int
    failed: int
    regressions: list = field(default_factory=list)
    details: list[TestResult] = field(default_factory=list)
    duration_s: float = 0.0


class EvalRunner:
    def __init__(self, baseline_db_path: str = "data/eval_baselines.db"):
        self._tracker = BaselineTracker(db_path=baseline_db_path)
        self._tester = InjectionTester()

    def run(self, quick: bool = False) -> EvalReport:
        start = time.monotonic()
        cases = load_golden_suite()
        all_results: list[TestResult] = []

        score_cases = [c for c in cases if c.category == "score_manipulation"]
        boundary_score_cases = [c for c in cases if c.category == "boundary" and "review" in c.input]
        all_results.extend(self._tester.test_score_integrity(score_cases + boundary_score_cases))

        if not quick:
            injection_cases = [c for c in cases if c.category == "cross_agent_injection"]
            all_results.extend(self._tester.test_output_sanitization(injection_cases))

            prompt_cases = [c for c in cases if c.category == "prompt_injection"]
            all_results.extend(self._tester.test_prompt_input_defense(prompt_cases))

        passed = sum(1 for r in all_results if r.passed)
        failed = len(all_results) - passed
        duration = time.monotonic() - start

        pass_rate = passed / len(all_results) if all_results else 0.0
        scores = {"pass_rate": pass_rate, "total": float(len(all_results)), "passed": float(passed)}
        self._tracker.record("adversarial", scores)
        regressions = self._tracker.detect_regressions("adversarial", scores)

        try:
            from shared.execution import emit
            emit("eval:adversarial", "eval.adversarial_completed", {
                "total": len(all_results),
                "passed": passed,
                "failed": failed,
                "quick": quick,
            })
        except Exception:
            pass

        report = EvalReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total=len(all_results),
            passed=passed,
            failed=failed,
            regressions=regressions,
            details=all_results,
            duration_s=duration,
        )

        if failed:
            logger.warning("Adversarial eval: %d/%d FAILED", failed, len(all_results))
            for r in all_results:
                if not r.passed:
                    logger.warning("  FAIL %s: %s", r.case_id, r.notes)
        else:
            logger.info("Adversarial eval: %d/%d passed in %.2fs", passed, len(all_results), duration)

        return report
