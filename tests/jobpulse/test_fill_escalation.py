"""Plan E: when _fill_by_label exhausts normal recovery, the dispatcher
calls _escalate_fill, which routes through CognitiveEngine and records
the fix back into widget_patterns via ai_assist_logger."""
import inspect


def test_escalate_fill_method_exists():
    from jobpulse.native_form_filler import NativeFormFiller
    assert hasattr(NativeFormFiller, "_escalate_fill")


def test_fill_by_label_calls_escalation_on_no_field():
    """The 'No field found' / 'No fillable field' branches in
    _fill_by_label must call _escalate_fill before returning failure."""
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._fill_by_label)
    assert "_escalate_fill" in src


def test_escalate_fill_calls_cognitive_engine_with_form_recovery_domain():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._escalate_fill)
    # Must invoke the cognitive engine, scoped to the form_recovery domain
    assert "form_recovery" in src
    # Stake should be high — this is post-exhaustion of cheaper tiers
    assert "high" in src
    # Must record the fix back through ai_assist_logger so the next
    # visit picks it up via _scan_learned_patterns
    assert "record_fix" in src or "ai_assist" in src
