# Navigator Verification Hardening — Follow-ups

This file accumulates observations from real-world runs of the navigator verification hardening branch.

## Verification status (post Task 13 smoke test)

- Tests: 46/46 passing across 9 test files
- Imports: all new symbols resolve
- Trigger paths: all expected grep matches present
- Real-data dry-run: pending (requires live JOB_AUTOPILOT_AUTO_SUBMIT=false run on a real ATS URL — out of scope for this CI-only smoke test)

## Known limitations

- The pre-existing test `tests/jobpulse/test_nav_action_executor.py::TestOverlayDismissal::test_dismisses_overlays_before_filling` was repaired in Task 2 by adjusting the mock fixture to return `count=0` for standard close-button names.
- The vision-DOM agreement gate (Task 12) only fires for actions with `confidence < 0.7`, excluding `done`, `abort`, and `wait_human`. Adjust threshold based on real-data observations.
- The `expected_outcome="fields_filled"` branch in `_check_expected_outcome` returns `None` (deferred to ExecutorResult). If callers need a synchronous result, extend the method to consult `ctx.executor_result.fills_verified` against the count of `action.field_fills`.

## Future enhancements (deferred from review feedback)

- Force-click retry block in `_phase_act` still calls `_detect_ghost_click` directly rather than through `_verify_action`. Unifying would require restructuring the retry to construct its own `post_snapshot` dict from the freshly fetched snapshot. Tracked as follow-up.
- `_safe_input_value` is called twice on the persistent-failure path (once via `_verify_fill`, once at line 200 to capture actual). Could be reduced to one call by changing `_verify_fill` to return `Optional[str]`.
- `session_id` for emitted optimization signals has 1-second resolution. Multiple failures in the same second share a session_id — informational only, no schema constraint, but worth noting if future analytics aggregate by session.

## Real-data smoke test placeholder

When you run a live dry-run application against a real ATS URL with this branch, look for these log markers to confirm wiring:

| Marker | Source | Expected when |
|---|---|---|
| `Filled X (verified)` | action_executor.py | every successful fill |
| `Filled X (verified after retry)` | action_executor.py | first read-back failed, second succeeded |
| `Fill mismatch for 'X'` | action_executor.py | both read-backs failed |
| `ACT: ghost click detected` | _navigator.py _phase_act | click registered but page didn't change |
| `Invalidated cached reasoning for ghost-click page` | _navigator.py | post-Task-10 cache invalidation |
| `Reflection produced: X` | _navigator.py | post-Task-11 re-grounding |
| `Auth login: ghost click detected` | _auth.py | ghost click on login page |
| `ACT: expected_outcome 'X' not met` | _navigator.py | declared outcome violated |
| `Vision-DOM disagreement: reasoner=X vision=Y` | _navigator.py | low-confidence + vision disagrees |
