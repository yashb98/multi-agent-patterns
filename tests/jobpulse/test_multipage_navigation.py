"""Tests for multi-page navigation — Next button finder, progress, stuck detection."""

import pytest
from jobpulse.state_machines import find_next_button, detect_progress, is_page_stuck


def test_find_next_button_standard():
    buttons = [
        {"text": "Cancel", "enabled": True, "selector": "#cancel"},
        {"text": "Next", "enabled": True, "selector": "#next"},
    ]
    result = find_next_button(buttons)
    assert result is not None
    assert result["selector"] == "#next"


def test_find_next_button_continue():
    buttons = [{"text": "Continue", "enabled": True, "selector": "#cont"}]
    assert find_next_button(buttons)["selector"] == "#cont"


def test_find_next_button_save_and_continue():
    buttons = [{"text": "Save and Continue", "enabled": True, "selector": "#save"}]
    assert find_next_button(buttons)["selector"] == "#save"


def test_find_next_button_skips_submit():
    """find_next_button skips Submit — that's find_submit_button's job."""
    buttons = [
        {"text": "Next", "enabled": True, "selector": "#next"},
        {"text": "Submit Application", "enabled": True, "selector": "#submit"},
    ]
    assert find_next_button(buttons)["selector"] == "#next"


def test_find_next_button_review_over_next():
    buttons = [
        {"text": "Next", "enabled": True, "selector": "#next"},
        {"text": "Review", "enabled": True, "selector": "#review"},
    ]
    assert find_next_button(buttons)["selector"] == "#review"


def test_find_next_button_disabled_skipped():
    buttons = [{"text": "Next", "enabled": False, "selector": "#next"}]
    assert find_next_button(buttons) is None


def test_find_next_button_none():
    buttons = [{"text": "Cancel", "enabled": True, "selector": "#cancel"}]
    assert find_next_button(buttons) is None


def test_find_next_button_proceed():
    buttons = [{"text": "Proceed", "enabled": True, "selector": "#proceed"}]
    assert find_next_button(buttons)["selector"] == "#proceed"


def test_detect_progress_step_of():
    assert detect_progress("Step 2 of 5 — Contact Information") == (2, 5)


def test_detect_progress_page_slash():
    assert detect_progress("Page 3 / 4") == (3, 4)


def test_detect_progress_bare_numbers():
    assert detect_progress("2 of 6") == (2, 6)


def test_detect_progress_none():
    assert detect_progress("Please fill in your details") is None


def test_detect_progress_invalid_range():
    assert detect_progress("Step 0 of 5") is None


def test_is_page_stuck_same():
    prev = {"page_text_preview": "Please enter your contact information and phone number details"}
    curr = {"page_text_preview": "Please enter your contact information and phone number details"}
    assert is_page_stuck(prev, curr) is True


def test_is_page_stuck_different():
    prev = {"page_text_preview": "Please enter your contact information and phone number details"}
    curr = {"page_text_preview": "Upload your resume and cover letter for this engineering position"}
    assert is_page_stuck(prev, curr) is False


def test_is_page_stuck_short_text_not_stuck():
    prev = {"page_text_preview": "Hi"}
    curr = {"page_text_preview": "Hi"}
    assert is_page_stuck(prev, curr) is False
