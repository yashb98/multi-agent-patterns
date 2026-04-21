# Adversarial Evaluation (shared/adversarial/)

Lightweight adversarial eval framework — Pillar 6 of 6.

## Core Components
- **GoldenSuite** (`_golden_suite.py`): 35 hand-crafted adversarial cases across 4 categories (score manipulation, cross-agent injection, prompt injection, boundary violations).
- **BaselineTracker** (`_baseline_tracker.py`): SQLite append-only store. Records eval scores, detects regressions (>10% drop from median of last 3 baselines).
- **InjectionTester** (`_injection_tester.py`): Runs golden cases against Pillar 5 governance primitives (ScoreValidator, OutputSanitizer, prompt_defense).
- **EvalRunner** (`_eval_runner.py`): Orchestrates full pipeline. Quick mode (~2s) or full mode (~10s).

## Usage
```python
from shared.adversarial import EvalRunner

runner = EvalRunner()
report = runner.run(quick=False)
print(f"Passed: {report.passed}/{report.total}")
```

CLI: `python -m shared.adversarial` or `python -m shared.adversarial --quick`

## Rules
- Golden cases are code, not config — add new cases directly in _golden_suite.py
- BaselineTracker uses data/eval_baselines.db — tests MUST use tmp_path
- InjectionTester validates governance primitives, not end-to-end LLM resilience
- EvalRunner emits eval.adversarial_completed events to the event store
