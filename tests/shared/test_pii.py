import json

from shared.pii import (
    assert_prompt_has_wrapped_pii,
    audit_prompt_for_unwrapped_pii,
    pii_json,
    wrap_pii_value,
)


def test_wrap_pii_value_tags_scalar():
    assert wrap_pii_value("profile.email", "user@example.com") == (
        '<pii field="profile.email">user@example.com</pii>'
    )


def test_pii_json_wraps_nested_fields():
    payload = {"name": "Yash", "education": ["MSc", "MBA"]}
    rendered = json.loads(pii_json(payload, "applicant"))
    assert rendered["name"] == '<pii field="applicant.name">Yash</pii>'
    assert rendered["education"][0] == '<pii field="applicant.education[0]">MSc</pii>'


def test_audit_detects_unwrapped_values():
    prompt = "Name: Yash Bishnoi"
    leaks = audit_prompt_for_unwrapped_pii(prompt, {"name": "Yash Bishnoi"}, "profile")
    assert leaks == ["profile.name"]


def test_assert_prompt_accepts_wrapped_values():
    prompt = 'Name: <pii field="profile.name">Yash Bishnoi</pii>'
    assert_prompt_has_wrapped_pii(prompt, {"name": "Yash Bishnoi"}, "profile")
