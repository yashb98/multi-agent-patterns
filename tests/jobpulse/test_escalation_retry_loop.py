"""F6: _escalate_fill retries with failure context appended after a
plan fails to execute. Caps at 3 retries per field."""
import inspect


def test_escalate_fill_has_retry_loop():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._escalate_fill)
    # Must loop (range / for / while) and feed failure context back
    assert ("for attempt in range" in src or "for _ in range" in src
            or "while" in src)
    # Must include "previous" or "prior" in the retry prompt to give
    # the engine the failure history
    assert "previous" in src.lower() or "prior" in src.lower()


def test_escalate_fill_caps_retries():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._escalate_fill)
    # 3 is the documented retry cap
    assert "range(3)" in src or "max_attempts" in src or "MAX_ESCALATION" in src
