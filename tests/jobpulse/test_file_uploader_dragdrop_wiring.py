"""Wiring test: file_uploader does NOT skip hidden <input type='file'>
behind drag-and-drop zones.

Live regression on Revolut welovealfa.com 2026-05-05: the only CV upload
on the page was a hidden <input type='file'> whose <label> wrapped a
visible drop zone with text "Upload CV / or drag and drop your cv here".
upload_files() identifiers (label + id + name) contained "drag and drop"
which matched the skip filter, so the agent silently advanced without
uploading the CV.

Fix: drop the "drag and drop" skip condition. set_input_files() works on
hidden file inputs by design (Playwright accepts them regardless of
visibility). The "autofill" skip is preserved — that one targets
LinkedIn's profile-autofill widget, which is a real false-positive case.
"""
from __future__ import annotations
import inspect


def test_drag_and_drop_skip_removed():
    """The active skip condition must NOT match 'drag and drop' as a substring.

    We grep for the *executable* check, not the explanatory comment that
    documents the regression. The legacy form was:
        if "autofill" in identifiers or "drag and drop" in identifiers:
    The new form is:
        if "autofill" in identifiers:
    """
    from jobpulse.form_engine import file_uploader
    src = inspect.getsource(file_uploader.upload_files)
    # Non-comment lines only
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert '"drag and drop" in identifiers' not in code_only, (
        "drag-and-drop skip is back in the executable code — "
        "Revolut/welovealfa-style hidden file inputs will be silently "
        "bypassed again"
    )


def test_autofill_skip_preserved():
    """LinkedIn autofill skip should still be there — it targets a real
    false-positive case (profile JSON import, not CV)."""
    from jobpulse.form_engine import file_uploader
    src = inspect.getsource(file_uploader.upload_files)
    assert "autofill" in src, "autofill skip removed — LinkedIn profile-autofill widget will be selected as CV target"


def test_explanatory_comment_present():
    """Future readers must see why the change was made."""
    from jobpulse.form_engine import file_uploader
    src = inspect.getsource(file_uploader.upload_files)
    # Anchor the explanation on something stable
    assert "set_input_files" in src
    assert "hidden" in src.lower()
