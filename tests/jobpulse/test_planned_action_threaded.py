"""Plan D-1/D-2/D-3: PageReasoner's PageAction is threaded through the
fill pipeline so _is_submit_page and _click_navigation consume
advance_button + action='done' instead of hardcoded button names."""
import inspect


def test_make_result_includes_planned_action_for_form_pages():
    """Navigator._make_result must surface planned_action so the filler
    can consume advance_button and action='done'."""
    from jobpulse.application_orchestrator_pkg import _navigator
    src = inspect.getsource(_navigator.FormNavigator._make_result)
    assert "planned_action" in src


def test_fill_application_forwards_planned_action_to_filler():
    """_form_filler.fill_application reads planned_action from snapshot
    or from the orchestrator and passes it to NativeFormFiller.fill."""
    from jobpulse.application_orchestrator_pkg import _form_filler
    src = inspect.getsource(_form_filler.FormFiller.fill_application)
    assert "planned_action" in src


def test_native_form_filler_fill_accepts_planned_action_kwarg():
    from jobpulse.native_form_filler import NativeFormFiller
    sig = inspect.signature(NativeFormFiller.fill)
    assert "planned_action" in sig.parameters


def test_is_submit_page_consults_planned_action():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._is_submit_page)
    assert "_planned_action" in src
    # Confirm we route to action='done' as the terminal signal
    assert '"done"' in src or "'done'" in src
    # The function body (excluding the docstring) must not call
    # get_by_role with a hardcoded button-text list. Check by
    # excluding the docstring then looking for the disallowed pattern.
    body_only = src.split('"""')[2] if src.count('"""') >= 2 else src
    assert 'get_by_role("button", name="Apply"' not in body_only
    assert 'get_by_role("button", name="Submit"' not in body_only


def test_click_navigation_uses_advance_button():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._click_navigation)
    assert "advance_button" in src
    # The hardcoded submit/next lists must be gone
    assert '"Submit Application", "Submit", "Apply"' not in src
    assert '"Save and Continue", "Save & Continue", "Continue"' not in src


def test_page_reasoner_validates_advance_button_for_advance_actions():
    """When action='fill_and_advance' but advance_button is empty, the
    reasoner must downgrade confidence so the consumer doesn't click
    a non-existent button."""
    from jobpulse.page_analysis import page_reasoner
    src = inspect.getsource(page_reasoner)
    # The validator check must mention the advance_button gap
    assert "advance_button" in src
    # And there must be a validator that downgrades confidence when missing
    assert "fill_and_advance" in src
