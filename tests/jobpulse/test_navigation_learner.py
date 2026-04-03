"""Tests for NavigationLearner — per-domain sequence replay."""

import pytest
from jobpulse.navigation_learner import NavigationLearner


@pytest.fixture
def learner(tmp_path):
    return NavigationLearner(db_path=str(tmp_path / "nav_learning.db"))


def test_no_sequence_initially(learner):
    assert learner.get_sequence("careers.acme.com") is None


def test_save_and_retrieve(learner):
    steps = [
        {"page_type": "job_description", "action": "click_apply", "selector": "#apply"},
        {"page_type": "login_form", "action": "fill_login", "selector": "#signin"},
        {"page_type": "application_form", "action": "fill_form", "selector": ""},
    ]
    learner.save_sequence("careers.acme.com", steps, success=True)
    result = learner.get_sequence("careers.acme.com")
    assert result is not None
    assert len(result) == 3
    assert result[0]["action"] == "click_apply"


def test_only_returns_successful_sequences(learner):
    steps = [{"page_type": "job_description", "action": "click_apply", "selector": "#apply"}]
    learner.save_sequence("careers.acme.com", steps, success=False)
    assert learner.get_sequence("careers.acme.com") is None


def test_domain_normalization(learner):
    steps = [{"page_type": "login_form", "action": "fill_login", "selector": "#login"}]
    learner.save_sequence("https://careers.acme.com/jobs/123", steps, success=True)
    result = learner.get_sequence("https://careers.acme.com/other")
    assert result is not None


def test_overwrite_with_newer(learner):
    steps_old = [{"page_type": "job_description", "action": "click_apply", "selector": "#old"}]
    steps_new = [{"page_type": "login_form", "action": "fill_login", "selector": "#new"}]
    learner.save_sequence("acme.com", steps_old, success=True)
    learner.save_sequence("acme.com", steps_new, success=True)
    result = learner.get_sequence("acme.com")
    assert result[0]["selector"] == "#new"


def test_mark_sequence_failed(learner):
    steps = [{"page_type": "job_description", "action": "click_apply", "selector": "#apply"}]
    learner.save_sequence("acme.com", steps, success=True)
    learner.mark_failed("acme.com")
    assert learner.get_sequence("acme.com") is None


def test_get_stats(learner):
    steps = [{"page_type": "login_form", "action": "fill_login", "selector": "#login"}]
    learner.save_sequence("acme.com", steps, success=True)
    learner.save_sequence("beta.com", steps, success=True)
    learner.save_sequence("gamma.com", steps, success=False)
    stats = learner.get_stats()
    assert stats["total_domains"] == 3
    assert stats["successful_domains"] == 2
