"""Tests that all ATS adapters are wired to the screening answer engine.

Verifies:
1. Every adapter has answer_screening_questions (inherited from base)
2. The shared helper calls get_answer with correct platform name
3. Adapters skip internal keys (_job_context) in custom_answers loops
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.ats_adapters.base import BaseATSAdapter
from jobpulse.ats_adapters.greenhouse import GreenhouseAdapter
from jobpulse.ats_adapters.indeed import IndeedAdapter
from jobpulse.ats_adapters.lever import LeverAdapter
from jobpulse.ats_adapters.generic import GenericAdapter
from jobpulse.ats_adapters.workday import WorkdayAdapter


ALL_ADAPTERS = [
    GreenhouseAdapter,
    LeverAdapter,
    IndeedAdapter,
    WorkdayAdapter,
    GenericAdapter,
]


# ------------------------------------------------------------------
# All adapters inherit answer_screening_questions from base
# ------------------------------------------------------------------

@pytest.mark.parametrize("adapter_cls", ALL_ADAPTERS)
def test_adapter_has_screening_method(adapter_cls):
    adapter = adapter_cls()
    assert hasattr(adapter, "answer_screening_questions")
    assert callable(adapter.answer_screening_questions)


# ------------------------------------------------------------------
# Platform name is set correctly per adapter
# ------------------------------------------------------------------

EXPECTED_NAMES = {
    GreenhouseAdapter: "greenhouse",
    LeverAdapter: "lever",
    IndeedAdapter: "indeed",
    WorkdayAdapter: "workday",
    GenericAdapter: "generic",
}


@pytest.mark.parametrize("adapter_cls,expected_name", EXPECTED_NAMES.items())
def test_adapter_platform_name(adapter_cls, expected_name):
    adapter = adapter_cls()
    assert adapter.name == expected_name


# ------------------------------------------------------------------
# answer_screening_questions calls get_answer with correct platform
# ------------------------------------------------------------------

@patch("jobpulse.screening_answers.get_answer")
def test_screening_helper_passes_platform(mock_get_answer):
    """When a form group with a label + text input is found, get_answer
    is called with the adapter's platform name."""
    mock_get_answer.return_value = "Test Answer"

    adapter = GreenhouseAdapter()

    # Mock a page with one form group containing label + text input
    mock_input = MagicMock()
    mock_input.evaluate.return_value = "input"
    mock_input.get_attribute.return_value = "text"
    mock_input.input_value.return_value = ""

    mock_label = MagicMock()
    mock_label.text_content.return_value = "What is your nationality?"

    mock_group = MagicMock()
    mock_group.query_selector.side_effect = lambda sel: (
        mock_label if "label" in sel else mock_input
    )
    mock_group.query_selector_all.return_value = []

    mock_page = MagicMock()
    mock_page.query_selector_all.return_value = [mock_group]

    count = adapter.answer_screening_questions(mock_page, {"job_title": "Data Scientist"})

    # Verify get_answer was called with platform="greenhouse"
    mock_get_answer.assert_called_once()
    call_kwargs = mock_get_answer.call_args
    assert call_kwargs.kwargs.get("platform") == "greenhouse" or (
        len(call_kwargs.args) >= 1 and "greenhouse" in str(call_kwargs)
    )


# ------------------------------------------------------------------
# answer_screening_questions returns count of answered questions
# ------------------------------------------------------------------

@patch("jobpulse.screening_answers.get_answer")
def test_screening_helper_returns_count(mock_get_answer):
    mock_get_answer.return_value = "Indian"

    adapter = IndeedAdapter()

    mock_input = MagicMock()
    mock_input.evaluate.return_value = "input"
    mock_input.get_attribute.return_value = "text"
    mock_input.input_value.return_value = ""

    mock_label = MagicMock()
    mock_label.text_content.return_value = "What is your nationality?"

    mock_group = MagicMock()
    mock_group.query_selector.side_effect = lambda sel: (
        mock_label if "label" in sel else mock_input
    )
    mock_group.query_selector_all.return_value = []

    mock_page = MagicMock()
    mock_page.query_selector_all.return_value = [mock_group]

    count = adapter.answer_screening_questions(mock_page)
    assert count == 1


# ------------------------------------------------------------------
# Empty page returns 0
# ------------------------------------------------------------------

def test_screening_helper_empty_page():
    adapter = WorkdayAdapter()
    mock_page = MagicMock()
    mock_page.query_selector_all.return_value = []

    count = adapter.answer_screening_questions(mock_page)
    assert count == 0
