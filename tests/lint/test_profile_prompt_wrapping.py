from pathlib import Path


ROOT = Path(__file__).resolve().parents[2] / "jobpulse"
PII_MARKERS = ("assert_prompt_has_wrapped_pii", "wrap_pii_value", "pii_json")
PROFILE_MARKERS = ("PROFILE[", "WORK_AUTH[", "APPLICANT_PROFILE")
PROMPT_MARKERS = (
    'messages=[{"role": "user", "content": prompt}]',
    "HumanMessage(content=prompt)",
    "prompt = (",
    'prompt = f"""',
)


def test_profile_backed_prompts_use_pii_wrapping():
    violations: list[str] = []

    for path in ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if not any(marker in source for marker in PROFILE_MARKERS):
            continue
        if not any(marker in source for marker in PROMPT_MARKERS):
            continue
        if not any(marker in source for marker in PII_MARKERS):
            violations.append(str(path.relative_to(ROOT.parent)))

    assert not violations, (
        "Profile-backed prompt builders must wrap/audit PII fields: "
        + ", ".join(violations)
    )
