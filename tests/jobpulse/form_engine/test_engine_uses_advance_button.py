"""F1-1: FormFillEngine consumes reasoner's advance_button + action='done'
just like NativeFormFiller does (Plan D)."""
import inspect


def test_fill_accepts_planned_action_kwarg():
    from jobpulse.form_engine.engine import FormFillEngine
    sig = inspect.signature(FormFillEngine.fill)
    assert "planned_action" in sig.parameters


def test_click_navigation_uses_advance_button():
    from jobpulse.form_engine.engine import FormFillEngine
    src = inspect.getsource(FormFillEngine._click_navigation)
    assert "advance_button" in src
    # The hardcoded submit/next button-name lists must be gone
    assert '"Submit Application", "Submit", "Apply"' not in src
    assert '"Save and Continue", "Save & Continue", "Continue"' not in src
    assert '["Submit", "Apply Now", "Continue"]' not in src


def test_click_navigation_consults_planned_action():
    from jobpulse.form_engine.engine import FormFillEngine
    src = inspect.getsource(FormFillEngine._click_navigation)
    assert "_planned_action" in src
    # Falls back to reasoner when planned_action absent
    assert "get_page_reasoner" in src
