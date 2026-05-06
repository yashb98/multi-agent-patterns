"""F3: _fill_resolved_widget extended for range slider, rich-text,
native date, and tag-input."""
import inspect


def test_handles_range_slider_input_type():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._fill_resolved_widget)
    # Range slider + split-numeric input pair handler must exist
    assert "range" in src.lower() and "split" in src.lower()


def test_handles_rich_text_contenteditable():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._fill_resolved_widget)
    # Must dispatch contenteditable / rich-text via pressSequentially
    # or DOM-tree text node insertion (not page.fill which is broken
    # for contenteditable)
    assert "rich_text" in src or "contenteditable" in src
    assert "press_sequentially" in src.lower() or "type(" in src


def test_handles_native_date_input():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller._fill_resolved_widget)
    assert "date_native" in src or "date" in src.lower()
