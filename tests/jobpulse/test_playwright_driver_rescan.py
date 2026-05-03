"""PlaywrightDriver.rescan_after_fill() — DOM read-back + validation error scan.

Removed 2026-05-03: 4 tests built on AsyncMock(page) + patched
scan_validation_errors (Category B per project policy). Real rescan behavior
is exercised in `tests/jobpulse/integration/test_pipeline_live.py` against
real Chrome via CDP.
"""
