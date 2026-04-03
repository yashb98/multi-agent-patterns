"""Tests for Phase 5 models: PageType, AccountInfo, NavigationStep."""

from jobpulse.ext_models import AccountInfo, NavigationStep, PageType


def test_page_type_values():
    assert PageType.JOB_DESCRIPTION == "job_description"
    assert PageType.LOGIN_FORM == "login_form"
    assert PageType.SIGNUP_FORM == "signup_form"
    assert PageType.EMAIL_VERIFICATION == "email_verification"
    assert PageType.APPLICATION_FORM == "application_form"
    assert PageType.CONFIRMATION == "confirmation"
    assert PageType.VERIFICATION_WALL == "verification_wall"
    assert PageType.UNKNOWN == "unknown"


def test_page_type_is_strenum():
    assert isinstance(PageType.LOGIN_FORM, str)
    assert PageType.LOGIN_FORM == "login_form"


def test_account_info_model():
    info = AccountInfo(
        domain="greenhouse.io",
        email="bishnoiyash274@gmail.com",
        verified=True,
    )
    assert info.domain == "greenhouse.io"
    assert info.verified is True
    assert info.created_at == ""
    assert info.last_login == ""


def test_navigation_step_model():
    step = NavigationStep(
        page_type="login_form",
        action="fill_login",
        selector="#signin",
        url="https://example.com/login",
    )
    assert step.page_type == "login_form"
    assert step.action == "fill_login"
    assert step.selector == "#signin"


def test_navigation_step_defaults():
    step = NavigationStep(page_type="click_apply", action="click")
    assert step.selector == ""
    assert step.url == ""
