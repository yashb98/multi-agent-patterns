"""When confirm_application records a correction, the field's DOM
signature is captured into GotchasDB.widget_patterns."""
from pathlib import Path
from unittest.mock import MagicMock


def test_correction_capture_records_widget_pattern(tmp_path, monkeypatch):
    from jobpulse import applicator

    # Stub side effects so the test only exercises the widget-capture path
    monkeypatch.setattr(
        "jobpulse.applicator._record_agent_performance",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "jobpulse.post_apply_hook.post_apply_hook",
        lambda **kw: None,
    )

    fake_rate_limiter = MagicMock()
    fake_rate_limiter.record_application = MagicMock()
    monkeypatch.setattr(
        "jobpulse.rate_limiter.RateLimiter",
        lambda: fake_rate_limiter,
    )

    # Capture widget pattern records
    captured = []
    monkeypatch.setattr(
        "jobpulse.form_engine.gotchas.GotchasDB.record_widget_pattern",
        lambda self, **kw: captured.append(kw),
    )

    # Stub correction capture so the diff yields a correction for City
    fake_cc = MagicMock()
    fake_cc.record_corrections = MagicMock(return_value={
        "corrections": [{"field": "City", "agent": "Chester", "user": "Dundee"}],
        "unchanged": 0,
    })
    monkeypatch.setattr(
        "jobpulse.correction_capture.CorrectionCapture",
        lambda *a, **kw: fake_cc,
    )

    # Stub agent rules + browser cleanup
    monkeypatch.setattr(
        "jobpulse.agent_rules.AgentRulesDB",
        lambda: MagicMock(auto_generate_from_correction=lambda **kw: None),
    )

    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF-1.4")

    agent_mapping = {"City": "Chester"}
    final_mapping = {"City": "Dundee", "City__dom": {
        "selector": "input[name='city']",
        "widget_type": "text",
        "ancestor_classes": "addr-row",
        "aria_label": "City",
    }}

    applicator.confirm_application(
        dry_run_result={"success": True, "agent_mapping": agent_mapping},
        url="https://example.com/apply",
        cv_path=cv_path,
        agent_mapping=agent_mapping,
        final_mapping=final_mapping,
    )

    assert any(c.get("label") == "City" for c in captured), captured
    matching = [c for c in captured if c.get("label") == "City"][0]
    assert matching["selector"] == "input[name='city']"
    assert matching["widget_type"] == "text"
