"""NavigationActionExecutor — overlay dismissal, field filling, advance, click.

Removed 2026-05-03: 8 tests that used `mock_page = AsyncMock()` to mock the
Playwright Page (Category B per project policy). Real executor behavior
against a real DOM is exercised in
`tests/jobpulse/integration/test_pipeline_live.py`. Pure verification logic
(ExecutorResult dataclass, signal emission against real OptimizationEngine)
lives in `tests/jobpulse/test_action_executor_verification.py`.
"""
