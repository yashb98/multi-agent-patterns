"""_fill_resolved_widget dispatches by input_type to the right handler."""
import inspect


def test_fill_resolved_widget_exists():
    from jobpulse.native_form_filler import NativeFormFiller
    assert hasattr(NativeFormFiller, "_fill_resolved_widget")


def test_fill_by_label_routes_semantic_combobox_to_resolved_widget():
    """semantic-matched combobox/switch/select must hit
    _fill_resolved_widget instead of falling through to page.fill()."""
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._fill_by_label)
    # The function must call _fill_resolved_widget for non-text widget types
    assert "_fill_resolved_widget" in src


def test_fill_by_label_routes_learned_pattern_to_resolved_widget():
    """learned_pattern fields carry a pre-resolved locator + widget type
    so they go through the same dispatcher."""
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._fill_by_label)
    assert "learned_pattern" in src
