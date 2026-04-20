"""Tests for form_interaction_log — step-by-step form replay."""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from jobpulse.form_interaction_log import FormInteractionLog


@pytest.fixture
def log(tmp_path):
    return FormInteractionLog(db_path=str(tmp_path / "interactions.db"))


def test_log_step_and_replay(log):
    log.log_step("s1", "example.com", "generic", page_num=1, step_order=1,
                 step_type="fill", target_label="Name", value="Yash", method="deterministic")
    log.log_step("s1", "example.com", "generic", page_num=1, step_order=2,
                 step_type="fill", target_label="Email", value="y@test.com", method="cache")
    log.log_step("s1", "example.com", "generic", page_num=1, step_order=3,
                 step_type="navigate", target_label="Next", method="click")

    replay = log.get_replay("example.com")
    assert len(replay) == 3
    assert replay[0]["target_label"] == "Name"
    assert replay[1]["target_label"] == "Email"
    assert replay[2]["step_type"] == "navigate"


def test_log_page_structure_and_retrieve(log):
    log.log_page_structure("example.com", "generic", 1, "Contact Info",
                           ["Name", "Email", "Phone"], ["text", "text", "text"],
                           has_file_upload=False, nav_buttons=["Next"])
    log.log_page_structure("example.com", "generic", 2, "Resume",
                           ["Resume", "Cover Letter"], ["file", "file"],
                           has_file_upload=True, nav_buttons=["Back", "Next"])

    pages = log.get_page_structure("example.com")
    assert len(pages) == 2
    assert pages[0]["page_title"] == "Contact Info"
    assert pages[0]["field_labels"] == ["Name", "Email", "Phone"]
    assert pages[1]["has_file_upload"] == 1
    assert pages[1]["nav_buttons"] == ["Back", "Next"]


def test_get_form_flow(log):
    log.log_page_structure("test.com", "linkedin", 1, "Contact", [], [], nav_buttons=["Next"])
    log.log_page_structure("test.com", "linkedin", 2, "Resume", [], [], nav_buttons=["Back", "Next"])
    log.log_page_structure("test.com", "linkedin", 3, "Review", [], [], nav_buttons=["Back", "Submit"])

    flow = log.get_form_flow("test.com")
    assert len(flow) == 3
    assert flow[0]["page_title"] == "Contact"
    assert flow[2]["nav_buttons"] == ["Back", "Submit"]


def test_replay_returns_latest_session(log):
    log.log_step("old", "example.com", step_order=1, step_type="fill", value="old_val")
    log.log_step("new", "example.com", step_order=1, step_type="fill", value="new_val")

    replay = log.get_replay("example.com")
    assert len(replay) == 1
    assert replay[0]["value"] == "new_val"


def test_domain_normalization(log):
    log.log_step("s1", "https://www.example.com/jobs/123", step_order=1, step_type="fill", value="test")
    replay = log.get_replay("example.com")
    assert len(replay) == 1


def test_page_structure_upsert(log):
    log.log_page_structure("test.com", "generic", 1, "Old Title", ["A"], ["text"])
    log.log_page_structure("test.com", "generic", 1, "New Title", ["A", "B"], ["text", "text"])

    pages = log.get_page_structure("test.com")
    assert len(pages) == 1
    assert pages[0]["page_title"] == "New Title"
    assert len(pages[0]["field_labels"]) == 2


def test_empty_domain_returns_empty(log):
    assert log.get_replay("nonexistent.com") == []
    assert log.get_page_structure("nonexistent.com") == []
    assert log.get_form_flow("nonexistent.com") == []


def test_get_stats(log):
    log.log_step("s1", "a.com", step_order=1, step_type="fill")
    log.log_step("s1", "a.com", step_order=2, step_type="navigate")
    log.log_step("s2", "b.com", step_order=1, step_type="fill")

    stats = log.get_stats()
    assert stats["total_steps"] == 3
    assert stats["total_sessions"] == 2
    assert stats["total_domains"] == 2


def test_correction_logging(log):
    log.log_step("s1", "test.com", step_order=1, step_type="fill",
                 target_label="Languages", value="Python, SQL", method="llm")
    log.log_step("s1", "test.com", step_order=2, step_type="correct",
                 target_label="Languages", value="Python, SQL, Java, Kotlin",
                 was_corrected=True, original_value="Python, SQL", method="user_override")

    replay = log.get_replay("test.com")
    assert len(replay) == 2
    assert replay[1]["was_corrected"] == 1
    assert replay[1]["original_value"] == "Python, SQL"
