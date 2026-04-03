"""Tests for ApplicationOrchestrator — full lifecycle controller."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from jobpulse.application_orchestrator import ApplicationOrchestrator


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.navigate = AsyncMock()
    b.fill = AsyncMock()
    b.click = AsyncMock()
    b.upload = AsyncMock()
    b.get_snapshot = AsyncMock()
    b.screenshot = AsyncMock(return_value=b"screenshot")
    b.select_option = AsyncMock()
    b.check = AsyncMock()
    return b


@pytest.fixture
def orchestrator(bridge, tmp_path):
    from jobpulse.account_manager import AccountManager
    from jobpulse.navigation_learner import NavigationLearner

    return ApplicationOrchestrator(
        bridge=bridge,
        account_manager=AccountManager(db_path=str(tmp_path / "acc.db")),
        gmail_verifier=MagicMock(),
        navigation_learner=NavigationLearner(db_path=str(tmp_path / "nav.db")),
    )


def test_orchestrator_has_required_methods(orchestrator):
    assert hasattr(orchestrator, "apply")
    assert hasattr(orchestrator, "_navigate_to_form")
    assert hasattr(orchestrator, "_handle_signup")
    assert hasattr(orchestrator, "_handle_login")
    assert hasattr(orchestrator, "_handle_email_verification")
    assert hasattr(orchestrator, "_fill_application")
