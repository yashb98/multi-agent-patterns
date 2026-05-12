"""Tests for sso_auto_discovery — generic SSO button pattern detection.

Button text samples drawn from real provider documentation and ATS pages.
"""
from __future__ import annotations

import pytest
from jobpulse.sso_auto_discovery import detect_sso_button_patterns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _btn(text: str, selector: str = "#sso-btn") -> dict:
    return {"text": text, "enabled": True, "selector": selector}


# ---------------------------------------------------------------------------
# None / empty inputs
# ---------------------------------------------------------------------------

def test_none_input_returns_none():
    assert detect_sso_button_patterns(None) is None


def test_empty_list_returns_none():
    assert detect_sso_button_patterns([]) is None


def test_buttons_with_no_text_returns_none():
    assert detect_sso_button_patterns([{"text": "", "selector": "#x"}]) is None


# ---------------------------------------------------------------------------
# Known providers cause defer (return None)
# ---------------------------------------------------------------------------

def test_google_button_defers():
    result = detect_sso_button_patterns([_btn("Sign in with Google", "#g")])
    assert result is None


def test_linkedin_button_defers():
    result = detect_sso_button_patterns([_btn("Continue with LinkedIn", "#li")])
    assert result is None


def test_microsoft_button_defers():
    result = detect_sso_button_patterns([_btn("Sign in with Microsoft", "#ms")])
    assert result is None


def test_apple_button_defers():
    result = detect_sso_button_patterns([_btn("Continue with Apple", "#apple")])
    assert result is None


def test_mixed_known_and_generic_defers_due_to_known():
    # Google is present, so generic SSO should be deferred even if Okta also present
    buttons = [
        _btn("Sign in with Google", "#g"),
        _btn("Continue with Okta", "#okta"),
    ]
    assert detect_sso_button_patterns(buttons) is None


# ---------------------------------------------------------------------------
# Okta variants
# ---------------------------------------------------------------------------

def test_okta_continue_with():
    result = detect_sso_button_patterns([_btn("Continue with Okta", "#okta")])
    assert result is not None
    assert result["provider"] == "okta"
    assert result["selector"] == "#okta"


def test_okta_sign_in_with():
    result = detect_sso_button_patterns([_btn("Sign in with Okta", "#okta-signin")])
    assert result is not None
    assert result["provider"] == "okta"
    assert result["selector"] == "#okta-signin"


def test_okta_log_in_via():
    result = detect_sso_button_patterns([_btn("Log in via Okta", "#okta-login")])
    assert result is not None
    assert result["provider"] == "okta"


# ---------------------------------------------------------------------------
# Auth0 variants
# ---------------------------------------------------------------------------

def test_auth0_sign_in_with():
    result = detect_sso_button_patterns([_btn("Sign in with Auth0", "#auth0")])
    assert result is not None
    assert result["provider"] == "auth0"
    assert result["button_text"] == "Sign in with Auth0"


def test_auth0_continue_with():
    result = detect_sso_button_patterns([_btn("Continue with Auth0", "#auth0-btn")])
    assert result is not None
    assert result["provider"] == "auth0"


# ---------------------------------------------------------------------------
# WorkOS variants
# ---------------------------------------------------------------------------

def test_workos_continue_with():
    result = detect_sso_button_patterns([_btn("Continue with WorkOS", "#workos")])
    assert result is not None
    assert result["provider"] == "workos"


def test_workos_sign_in_with():
    result = detect_sso_button_patterns([_btn("Sign in with WorkOS", "#workos-btn")])
    assert result is not None
    assert result["provider"] == "workos"


# ---------------------------------------------------------------------------
# OneLogin variants
# ---------------------------------------------------------------------------

def test_onelogin_sign_in_with():
    result = detect_sso_button_patterns([_btn("Sign in with OneLogin", "#onelogin")])
    assert result is not None
    assert result["provider"] == "onelogin"


def test_onelogin_log_in_via():
    result = detect_sso_button_patterns([_btn("Log in via OneLogin", "#ol")])
    assert result is not None
    assert result["provider"] == "onelogin"


# ---------------------------------------------------------------------------
# Ping Identity
# ---------------------------------------------------------------------------

def test_ping_identity_detected():
    result = detect_sso_button_patterns([_btn("Sign in with Ping Identity", "#ping")])
    assert result is not None
    assert result["provider"] == "ping_identity"


# ---------------------------------------------------------------------------
# Generic SSO variants
# ---------------------------------------------------------------------------

def test_generic_sign_in_with_sso():
    result = detect_sso_button_patterns([_btn("Sign in with SSO", "#sso")])
    assert result is not None
    assert result["provider"] == "generic_sso"


def test_generic_continue_with_sso():
    result = detect_sso_button_patterns([_btn("Continue with SSO", "#csso")])
    assert result is not None
    assert result["provider"] == "generic_sso"


def test_generic_use_company_login():
    result = detect_sso_button_patterns([_btn("Use your company login", "#company")])
    assert result is not None
    assert result["provider"] == "generic_sso"


def test_generic_corporate_login():
    result = detect_sso_button_patterns([_btn("Corporate login", "#corp")])
    assert result is not None
    assert result["provider"] == "generic_sso"


def test_generic_enterprise_login():
    result = detect_sso_button_patterns([_btn("Enterprise login", "#ent")])
    assert result is not None
    assert result["provider"] == "generic_sso"


def test_generic_enterprise_sso():
    result = detect_sso_button_patterns([_btn("Enterprise SSO", "#esign")])
    assert result is not None
    assert result["provider"] == "generic_sso"


# ---------------------------------------------------------------------------
# Return contract: provider + button_text + selector always present
# ---------------------------------------------------------------------------

def test_return_dict_has_required_keys():
    result = detect_sso_button_patterns([_btn("Continue with Okta", "#okta-42")])
    assert result is not None
    assert "provider" in result
    assert "button_text" in result
    assert "selector" in result
    assert result["selector"] == "#okta-42"


# ---------------------------------------------------------------------------
# First-match priority: returns the first matching button
# ---------------------------------------------------------------------------

def test_first_matching_button_returned():
    buttons = [
        _btn("Continue with Okta", "#okta-first"),
        _btn("Sign in with Auth0", "#auth0-second"),
    ]
    result = detect_sso_button_patterns(buttons)
    assert result is not None
    assert result["provider"] == "okta"
    assert result["selector"] == "#okta-first"


# ---------------------------------------------------------------------------
# Non-SSO buttons return None
# ---------------------------------------------------------------------------

def test_plain_sign_in_returns_none():
    result = detect_sso_button_patterns([_btn("Sign in", "#signin"), _btn("Create Account", "#ca")])
    assert result is None


def test_email_password_buttons_return_none():
    result = detect_sso_button_patterns([
        _btn("Continue with email", "#email"),
        _btn("Sign up", "#signup"),
    ])
    assert result is None
