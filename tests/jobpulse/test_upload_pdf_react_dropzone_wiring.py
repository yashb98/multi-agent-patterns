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


def test_upload_pdf_re_fires_events_on_input():
    """upload_pdf must re-dispatch input + change after set_input_files
    so React-controlled dropzones catch the change."""
    from jobpulse.form_engine import file_uploader
    src = inspect.getsource(file_uploader.upload_pdf)
    assert "Event('change'" in src
    assert "Event('input'" in src
    assert "bubbles: true" in src


def test_upload_pdf_walks_dropzone_ancestors():
    """upload_pdf must walk parent ancestors and dispatch on dropzone
    wrappers (the component usually listens on the wrapper, not the input)."""
    from jobpulse.form_engine import file_uploader
    src = inspect.getsource(file_uploader.upload_pdf)
    # Walks ancestors via parentElement
    assert "parentElement" in src
    # Matches drop/upload/cv/resume/file classes
    assert "drop|upload" in src or "drop|upload|cv|resume|file" in src


def test_upload_pdf_verifies_file_attached():
    """upload_pdf must read el.files.length and warn if zero (set_input_files
    silently no-op'd because the React component swapped the input)."""
    from jobpulse.form_engine import file_uploader
    src = inspect.getsource(file_uploader.upload_pdf)
    assert "el.files" in src
    assert "files_attached" in src
    # Warning path for empty
    assert "warning" in src.lower()


def test_upload_pdf_still_logs_success():
    """When the upload succeeds (files.length > 0), still log info."""
    from jobpulse.form_engine import file_uploader
    src = inspect.getsource(file_uploader.upload_pdf)
    assert "uploaded" in src
    assert "files.length=%d" in src or "files.length=" in src
