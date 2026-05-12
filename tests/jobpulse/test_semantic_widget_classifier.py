"""classify_widget maps the matched element to a fill-handler input_type."""


def test_classifies_role_switch():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "BUTTON", "role": "switch"}) == "switch"


def test_classifies_role_combobox():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "INPUT", "role": "combobox"}) == "combobox"


def test_classifies_native_select():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "SELECT", "role": ""}) == "select"


def test_classifies_textarea():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "TEXTAREA", "role": ""}) == "textarea"


def test_classifies_role_radio_to_radio_group():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "INPUT", "role": "radio"}) == "radio_group"


def test_classifies_role_checkbox():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "INPUT", "role": "checkbox"}) == "checkbox"


def test_button_with_haspopup_is_combobox():
    """Custom React selects render as <button aria-haspopup="listbox">."""
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({
        "tag": "BUTTON", "role": "",
        "aria_haspopup": "listbox",
    }) == "combobox"


def test_unknown_falls_back_to_text():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "DIV", "role": ""}) == "text"
