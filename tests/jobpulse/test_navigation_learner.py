"""Tests for NavigationLearner — per-domain sequence replay."""

import pytest
from datetime import UTC, datetime
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


def test_platform_nav_pattern_fallback(tmp_path):
    """When no domain sequence exists, return the most common platform pattern."""
    learner = NavigationLearner(db_path=str(tmp_path / "nav.db"))

    for domain in ["acme.com", "beta.com", "gamma.com"]:
        learner.save_sequence(domain, [
            {"page_type": "job_description", "action": "click_apply"},
        ], success=True, platform="greenhouse")

    assert learner.get_sequence("newcompany.com") is None

    pattern = learner.get_platform_pattern("greenhouse", exclude_domain="newcompany.com")
    assert pattern is not None
    assert len(pattern) == 1
    assert pattern[0]["action"] == "click_apply"


def test_platform_nav_pattern_needs_minimum_observations(tmp_path):
    """Platform pattern requires >=3 successful domains to be trustworthy."""
    learner = NavigationLearner(db_path=str(tmp_path / "nav.db"))

    learner.save_sequence("acme.com", [
        {"page_type": "login_form", "action": "fill_login"},
    ], success=True, platform="greenhouse")

    pattern = learner.get_platform_pattern("greenhouse")
    assert pattern is None


def test_empty_steps_do_not_overwrite_existing(tmp_path):
    """Empty steps must not overwrite a non-empty learned sequence."""
    learner = NavigationLearner(db_path=str(tmp_path / "nav.db"))
    good = [{"page_type": "login_form", "action": "fill_login"}, {"page_type": "job_description", "action": "click_apply"}]
    learner.save_sequence("acme.com", good, success=True)
    learner.save_sequence("acme.com", [], success=True)
    result = learner.get_sequence("acme.com")
    assert result is not None
    assert len(result) == 2


def test_save_sequence_with_platform(tmp_path):
    """save_sequence stores platform and get_platform_pattern uses it."""
    import sqlite3
    learner = NavigationLearner(db_path=str(tmp_path / "nav.db"))
    learner.save_sequence("acme.com", [{"action": "click_apply"}], success=True, platform="lever")

    with sqlite3.connect(str(tmp_path / "nav.db")) as conn:
        row = conn.execute("SELECT platform FROM sequences WHERE domain = ?", ("acme.com",)).fetchone()
    assert row[0] == "lever"


def test_ttl_expired_sequence_not_returned(learner):
    """Sequences older than 30 days are not returned."""
    from datetime import timedelta
    import sqlite3

    steps = [{"page_type": "job_description", "action": "click_apply"}]
    learner.save_sequence("acme.com", steps, success=True)

    old_date = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with sqlite3.connect(learner._db_path) as conn:
        conn.execute("UPDATE sequences SET updated_at = ? WHERE domain = ?", (old_date, "acme.com"))

    assert learner.get_sequence("acme.com") is None


def test_consecutive_failures_purge_sequence(learner):
    """3 consecutive mark_failed() calls delete the sequence."""
    steps = [{"page_type": "login_form", "action": "fill_login"}]
    learner.save_sequence("acme.com", steps, success=True)

    learner.mark_failed("acme.com")
    learner.mark_failed("acme.com")
    learner.mark_failed("acme.com")

    import sqlite3
    with sqlite3.connect(learner._db_path) as conn:
        row = conn.execute("SELECT * FROM sequences WHERE domain = ?", ("acme.com",)).fetchone()
    assert row is None


def test_platform_pattern_with_two_observations(tmp_path):
    """Platform pattern works with 2 observations (lowered from 3)."""
    learner = NavigationLearner(db_path=str(tmp_path / "nav.db"))
    for domain in ["a.com", "b.com"]:
        learner.save_sequence(domain, [
            {"page_type": "job_description", "action": "click_apply"},
        ], success=True, platform="lever")

    pattern = learner.get_platform_pattern("lever")
    assert pattern is not None
    assert pattern[0]["action"] == "click_apply"


@pytest.mark.asyncio
async def test_redirect_loop_detected():
    """Navigator aborts when same (domain, page_type) appears 3 times."""
    from unittest.mock import AsyncMock, MagicMock
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator, MAX_NAVIGATION_STEPS
    from jobpulse.form_models import PageType
    from jobpulse.page_analyzer import PageAnalyzer

    # Build a minimal orchestrator mock
    orch = MagicMock()
    orch.cookie_dismisser = MagicMock()
    orch.cookie_dismisser.dismiss = AsyncMock(return_value=False)

    mock_learner = MagicMock()
    mock_learner.get_sequence = MagicMock(return_value=None)
    mock_learner.get_platform_pattern = MagicMock(return_value=None)
    orch.learner = mock_learner

    # Create a real PageAnalyzer with mock bridge — we'll mock _dom_detect instead
    mock_bridge = AsyncMock()
    orch.analyzer = PageAnalyzer(mock_bridge)

    orch.sso = MagicMock()
    orch.sso.detect_sso = MagicMock(return_value=None)

    auth = AsyncMock()
    nav = FormNavigator(orch, auth)

    login_snap = {
        "url": "https://ats.example.com/login",
        "buttons": [{"text": "Sign in", "enabled": True}],
        "fields": [
            {"input_type": "email", "label": "Email", "current_value": ""},
            {"input_type": "password", "label": "Password", "current_value": ""},
        ],
        "page_text_preview": "",
        "has_file_inputs": False,
    }

    # Alternate between login and some other page that re-triggers login
    call_count = 0
    async def mock_get_snapshot(force_refresh=False):
        nonlocal call_count
        call_count += 1
        return login_snap

    orch.driver = AsyncMock()
    orch.driver.navigate = AsyncMock()
    orch.driver.get_snapshot = mock_get_snapshot
    orch.driver.click = AsyncMock()
    orch.driver.page = None
    orch.driver.wait_for_apply = AsyncMock(side_effect=AttributeError)

    auth.handle_login = AsyncMock(return_value=login_snap)

    steps = []
    result = await nav.navigate_to_form("https://ats.example.com/jobs/123", "generic", steps)
    # Should have aborted due to loop detection (login appearing 3 times)
    assert result["page_type"] in (PageType.LOGIN_FORM, PageType.UNKNOWN)
    # Without loop detection: 10 full steps → 12 get_snapshot calls.
    # With loop detection at threshold=3: aborts at step 3 → ≤8 calls.
    assert call_count <= 8, f"Expected loop abort within 8 get_snapshot calls, got {call_count}"
