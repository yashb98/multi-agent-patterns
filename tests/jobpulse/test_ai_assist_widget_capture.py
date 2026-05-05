"""record_fix optionally captures widget DOM signature."""


def test_record_fix_with_dom_signature_writes_to_gotchas(tmp_path, monkeypatch):
    from jobpulse.ai_assist_logger import AIAssistLogger

    captured = {}

    def fake_record(self, **kw):
        captured.update(kw)

    monkeypatch.setattr(
        "jobpulse.form_engine.gotchas.GotchasDB.record_widget_pattern",
        fake_record,
    )

    log = AIAssistLogger(db_path=str(tmp_path / "ai.db"))
    sess = log.start_session(
        "claude", job_id="abc",
        domain="welovealfa.com", platform="generic",
    )
    log.record_fix(
        sess.session_id,
        field_label="Do you require visa sponsorship?",
        old_value="",
        new_value="No",
        reasoning="user fix",
        fix_category="screening_answer",
        confidence=1.0,
        dom_signature={
            "selector": "div[data-q='visa'] button",
            "widget_type": "custom_select",
            "ancestor_classes": "styles-bi1IZa-q",
            "aria_label": "",
        },
    )
    assert captured["domain"] == "welovealfa.com"
    assert captured["label"] == "Do you require visa sponsorship?"
    assert captured["widget_type"] == "custom_select"


def test_record_fix_without_dom_signature_skips_widget_record(tmp_path, monkeypatch):
    """Backwards compat: existing call sites that don't pass dom_signature
    must keep working — just no widget pattern stored."""
    from jobpulse.ai_assist_logger import AIAssistLogger

    calls = []
    monkeypatch.setattr(
        "jobpulse.form_engine.gotchas.GotchasDB.record_widget_pattern",
        lambda *a, **kw: calls.append(kw),
    )

    log = AIAssistLogger(db_path=str(tmp_path / "ai.db"))
    sess = log.start_session(
        "claude", job_id="abc",
        domain="x.com", platform="generic",
    )
    log.record_fix(
        sess.session_id,
        field_label="Q",
        old_value="", new_value="A",
        reasoning="r",
        fix_category="screening_answer",
        confidence=1.0,
    )
    assert calls == []
