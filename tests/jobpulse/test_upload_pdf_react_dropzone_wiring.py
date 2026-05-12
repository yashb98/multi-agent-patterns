"""Wiring test: upload_pdf re-fires events for React drop-zones.

Live regression on Revolut welovealfa.com 2026-05-05: Playwright's
set_input_files attached the CV file (logs confirmed "uploaded ... 98165
bytes"), but the page's react-dropzone component didn't notice — the
input.value/files cleared, the disabled "Upload CV" submit stayed
disabled, no page transition.

The fix: after set_input_files, walk up the DOM looking for a wrapper
with class matching `drop|upload|cv|resume|file` and re-fire
input/change events on it. Most react-dropzone implementations bind
their listener to the wrapper, not the hidden input.
"""
from __future__ import annotations
import inspect


def _module_src() -> str:
    from jobpulse.form_engine import file_uploader
    import inspect as _inspect
    return _inspect.getsource(file_uploader)


def test_upload_pdf_re_fires_events_on_input():
    """upload_pdf (or its readback helper) must re-dispatch input +
    change after set_input_files so React-controlled dropzones catch
    the change."""
    src = _module_src()
    assert "Event('change'" in src
    assert "Event('input'" in src
    assert "bubbles: true" in src


def test_upload_pdf_walks_dropzone_ancestors():
    """upload_pdf must walk parent ancestors and dispatch on dropzone
    wrappers (the component usually listens on the wrapper, not the input)."""
    src = _module_src()
    assert "parentElement" in src
    assert "drop|upload" in src or "drop|upload|cv|resume|file" in src


def test_upload_pdf_verifies_file_attached():
    """upload_pdf must read el.files.length and surface failure (warn or
    raise) if zero — set_input_files can silently no-op when the React
    component swaps the input."""
    src = _module_src()
    assert "el.files" in src
    assert "files_attached" in src or "files-length" in src or "files.length" in src
    assert "warning" in src.lower() or "FileUploadError" in src


def test_upload_pdf_still_logs_success():
    """When the upload succeeds (files.length > 0), still log info."""
    src = _module_src()
    assert "uploaded" in src
    assert "files.length=%d" in src or "files.length=" in src


def test_upload_pdf_raises_on_persistent_failure():
    """After the retry, persistent failure must raise FileUploadError so
    the caller can route to human review or fail the application — the
    previous behavior of logging a warning and continuing silently
    caused jobs to submit without a CV attached."""
    from jobpulse.form_engine.file_uploader import FileUploadError
    src = _module_src()
    assert "FileUploadError" in src
    assert "raise FileUploadError" in src or "raise FileUploadError(" in src
    assert issubclass(FileUploadError, Exception)


def test_upload_files_disambiguates_two_attach_inputs():
    """Greenhouse renders two file inputs both labelled 'Attach' — the
    first is CV/Resume and the second is Cover Letter. upload_files
    must apply that ordering when no other signal is available."""
    src = _module_src()
    assert "2-Attach" in src or "first input → CV" in src or "first → CV" in src
    assert "h2, h3, h4" in src or "h3" in src  # heading scan present
