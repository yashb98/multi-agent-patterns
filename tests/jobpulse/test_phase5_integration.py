"""Phase 5 external application engine integration tests.

Removed 2026-05-03: 6 tests built on `bridge = AsyncMock()` (Category B —
mocks the entire Playwright driver) plus extensive `patch(get_page_reasoner)`
to substitute deterministic PageAction sequences. End-to-end Phase 5 flow
(direct form, JD→form, CAPTCHA wall, SSO Google, signup→verify→login→apply,
cookie banner) is exercised against real Chrome via CDP in
`tests/jobpulse/integration/test_pipeline_live.py`.
"""
