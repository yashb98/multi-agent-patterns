"""F1-2: navigator's click_apply fallback consults the reasoner's
target_text instead of running a hardcoded button-text list."""
import inspect


def test_click_apply_uses_reasoner_target_text():
    from jobpulse.application_orchestrator_pkg import _navigator
    src = inspect.getsource(_navigator.FormNavigator.click_apply_button)
    # Must consult the reasoner for the apply button name
    assert "get_page_reasoner" in src
    assert "click_apply" in src
    assert "target_text" in src


def test_click_apply_no_hardcoded_button_list():
    from jobpulse.application_orchestrator_pkg import _navigator
    src = inspect.getsource(_navigator.FormNavigator.click_apply_button)
    # The four-item string list must be gone
    assert '"Apply now", "Apply for this job", "Start application", "Apply"' not in src
    assert "Start application" not in src or "reasoner" in src.lower()
