"""Integration test: apply_job with pre-populated form learning DBs."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def populated_dbs(tmp_path):
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.form_interaction_log import FormInteractionLog
    from jobpulse.navigation_learner import NavigationLearner

    exp_path = str(tmp_path / "form_exp.db")
    int_path = str(tmp_path / "interactions.db")
    nav_path = str(tmp_path / "nav.db")

    exp = FormExperienceDB(db_path=exp_path)
    exp.record("boards.greenhouse.io", "greenhouse", "extension",
               3, ["text", "select", "file"],
               ["Require sponsorship?", "Expected salary?"],
               45.0, True)

    log = FormInteractionLog(db_path=int_path)
    log.log_page_structure("boards.greenhouse.io", "greenhouse", 1, "Contact",
                           ["Name", "Email"], ["text", "text"],
                           nav_buttons=["Next"])
    log.log_page_structure("boards.greenhouse.io", "greenhouse", 2, "Resume",
                           ["Resume"], ["file"], has_file_upload=True,
                           nav_buttons=["Back", "Submit"])

    nav = NavigationLearner(db_path=nav_path)
    nav.save_sequence("boards.greenhouse.io",
                      [{"type": "click", "selector": "#apply"}], True)

    return {"form_exp_db": exp_path, "interaction_db": int_path, "nav_db": nav_path}


def test_full_cycle_hints_reach_adapter(populated_dbs):
    adapter = MagicMock()
    adapter.name = "mock"
    adapter.fill_and_submit.return_value = {
        "success": True, "pages_filled": 3,
        "field_types": ["text", "select", "file"],
        "screening_questions": ["Require sponsorship?", "Expected salary?"],
        "time_seconds": 42.0,
    }

    # Compute real hints BEFORE patching so we call the real function, not the mock.
    from jobpulse.form_prefetch import prefetch_form_hints, FormHints
    real_hints = prefetch_form_hints(
        "https://boards.greenhouse.io/co/jobs/1", **populated_dbs,
    )

    with patch("jobpulse.applicator.select_adapter", return_value=adapter), \
         patch("jobpulse.form_prefetch.prefetch_form_hints") as mock_pf:
        mock_pf.return_value = real_hints

        from jobpulse.applicator import apply_job
        result = apply_job(
            url="https://boards.greenhouse.io/co/jobs/1",
            ats_platform="greenhouse",
            cv_path=Path("/tmp/cv.pdf"),
            dry_run=True,
        )

    assert result["success"] is True
    call_kwargs = adapter.fill_and_submit.call_args
    answers = call_kwargs.kwargs.get("custom_answers") or call_kwargs[1].get("custom_answers", {})
    hints = answers.get("_form_hints", {})
    assert hints["known_domain"] is True
    assert hints["expected_pages"] == 3
    assert hints["has_file_upload"] is True
    assert len(hints["page_structures"]) == 2
    assert hints["nav_steps"] is not None
