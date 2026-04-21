"""Adversarial Evaluation — Pillar 6.

Golden test suite, baseline tracking, injection testing, eval orchestration.
"""

from shared.adversarial._golden_suite import GoldenCase, load_golden_suite
from shared.adversarial._baseline_tracker import BaselineTracker, Regression
from shared.adversarial._injection_tester import InjectionTester, TestResult
from shared.adversarial._eval_runner import EvalRunner, EvalReport
