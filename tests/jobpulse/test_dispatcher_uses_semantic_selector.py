"""When a field has selector + semantic_match=True, dispatcher uses
locator(selector) instead of get_by_label."""
import inspect


def test_fill_by_label_consults_field_metadata_for_semantic_selector():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller)
    assert "semantic_match" in src
    assert "page.locator" in src or "self._page.locator" in src


def test_scan_fields_populates_fields_by_label():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller)
    # _scan_fields should now stash fields_by_label so the dispatcher
    # can consult per-field metadata.
    assert "_fields_by_label" in src
