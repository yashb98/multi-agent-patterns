"""F4: drop noise fields from scanner output.

Live regression on Revolut welovealfa.com 2026-05-06: garbage labels
('Type your answer here (textarea)', 'Open Grammarly.', 'Back',
'Apply now', '_unlabeled_0') reached _fill_by_label and triggered
expensive escalation paths on non-fields.

The filter drops three noise classes:
  - tag is button/a (belongs in the buttons array)
  - label equals placeholder text (no real question text was found)
  - synthetic placeholder labels like '_unlabeled_*'
"""
from jobpulse.form_engine.field_scanner import _filter_noise_fields


def test_drops_button_and_anchor_tags():
    fields = [
        {"label": "Email", "type": "text", "tag": "input"},
        {"label": "Apply now", "type": "button", "tag": "button"},
        {"label": "Back", "type": "button", "tag": "button"},
    ]
    out = _filter_noise_fields(fields)
    assert {f["label"] for f in out} == {"Email"}


def test_drops_synthetic_unlabeled_placeholder():
    fields = [
        {"label": "_unlabeled_0", "type": "text"},
        {"label": "_unlabeled_1", "type": "text"},
        {"label": "Real Field", "type": "text"},
    ]
    out = _filter_noise_fields(fields)
    assert {f["label"] for f in out} == {"Real Field"}


def test_drops_label_that_matches_placeholder():
    """When the labelFor() walker fell back to the placeholder, drop the
    field — we have no real question text to ask the LLM about."""
    fields = [
        {"label": "Type your answer here", "type": "textarea",
         "placeholder": "Type your answer here"},
        {"label": "Email", "type": "text", "placeholder": "you@example.com"},
    ]
    out = _filter_noise_fields(fields)
    assert {f["label"] for f in out} == {"Email"}


def test_drops_browser_extension_signature():
    """Per advisor: fields whose ancestor is detectably extension-injected
    (e.g., Grammarly's custom element) get dropped. Detected via the
    is_extension_injected boolean the scanner emits."""
    fields = [
        {"label": "Open Grammarly.", "type": "text",
         "is_extension_injected": True},
        {"label": "First Name", "type": "text",
         "is_extension_injected": False},
    ]
    out = _filter_noise_fields(fields)
    assert {f["label"] for f in out} == {"First Name"}


def test_keeps_valid_fields_unchanged():
    fields = [
        {"label": "Do you require visa sponsorship?", "type": "combobox"},
        {"label": "What is your notice period?", "type": "select"},
    ]
    out = _filter_noise_fields(fields)
    assert len(out) == 2
