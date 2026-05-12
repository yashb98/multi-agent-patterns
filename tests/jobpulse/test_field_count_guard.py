"""Tests for the post-LLM field-count guard."""
from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction


def _action(field_fills, action="fill_and_advance"):
    return PageAction(
        page_understanding="t", action=action, target_text="",
        reasoning="t", confidence=0.9, page_type="application_form",
        field_fills=field_fills, advance_button="Submit",
        overlays_to_dismiss=[], expected_outcome="url_changes",
    )


class TestFieldCountGuard:
    def test_full_coverage_passes(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Email", "input_type": "email", "required": True},
        ]
        action = _action([
            {"label": "First name", "value": "X", "method": "fill"},
            {"label": "Email", "value": "x@y.com", "method": "fill"},
        ])
        guarded = pr._apply_field_count_guard(action, snap_fields)
        assert guarded.action == "fill_and_advance"
        assert guarded.confidence >= 0.9

    def test_dropped_required_field_lowers_confidence(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Email", "input_type": "email", "required": True},
            {"label": "Phone", "input_type": "tel", "required": True},
            {"label": "City", "input_type": "text", "required": True},
            {"label": "Country", "input_type": "text", "required": True},
        ]
        action = _action([
            {"label": "Email", "value": "x@y.com", "method": "fill"},
        ])
        guarded = pr._apply_field_count_guard(action, snap_fields)
        # Coverage 1/5 = 20% → guard kicks in
        assert guarded.confidence < 0.5
        assert "field" in guarded.reasoning.lower() or "coverage" in guarded.reasoning.lower()

    def test_optional_fields_are_not_counted(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Newsletter", "input_type": "checkbox", "required": False},
        ]
        action = _action([
            {"label": "First name", "value": "X", "method": "fill"},
        ])
        guarded = pr._apply_field_count_guard(action, snap_fields)
        # Required fields = 1, covered = 1 → 100%
        assert guarded.confidence >= 0.9

    def test_skip_method_does_not_count_as_covered(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Email", "input_type": "email", "required": True},
        ]
        # method="skip" should NOT count as covered
        action = _action([
            {"label": "First name", "value": "", "method": "skip"},
            {"label": "Email", "value": "x@y.com", "method": "fill"},
        ])
        guarded = pr._apply_field_count_guard(action, snap_fields)
        # Coverage 1/2 = 50% → confidence lowered
        assert guarded.confidence < 0.9

    def test_non_fill_action_passes_through(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Email", "input_type": "email", "required": True},
        ]
        action = _action([], action="abort")
        guarded = pr._apply_field_count_guard(action, snap_fields)
        # abort action should pass through untouched
        assert guarded.confidence >= 0.9
        assert guarded.action == "abort"


class TestZeroFieldsGuard:
    """Guard against LLM hallucinating fill_form on pages with 0 fields."""

    def test_fill_form_with_zero_fields_apply_button_present(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        # LLM hallucinated fill_form on a Workday job description page
        action = _action([], action="fill_form")
        snap_fields: list[dict] = []
        snap_buttons = [
            {"text": "Apply"},
            {"text": "Sign In"},
            {"text": "Save Job"},
        ]
        guarded = pr._apply_zero_fields_guard(action, snap_fields, snap_buttons)
        # Should override to click_element targeting Apply
        assert guarded.action == "click_element"
        assert guarded.target_text == "Apply"
        assert guarded.page_type == "job_description"
        assert guarded.expected_outcome == "url_changes"

    def test_fill_form_with_zero_fields_only_signin(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        action = _action([], action="fill_form")
        snap_buttons = [{"text": "Sign In"}, {"text": "Cancel"}]
        guarded = pr._apply_zero_fields_guard(action, [], snap_buttons)
        assert guarded.action == "click_element"
        assert guarded.target_text == "Sign In"

    def test_fill_form_with_zero_fields_no_apply_button(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        action = _action([], action="fill_form")
        snap_buttons = [{"text": "Cancel"}, {"text": "Back"}]
        guarded = pr._apply_zero_fields_guard(action, [], snap_buttons)
        # No Apply or Sign In → abort with low confidence
        assert guarded.action == "abort"
        assert guarded.confidence < 0.5

    def test_fill_form_with_real_fields_passes_through(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        action = _action([{"label": "Email", "value": "x@y.com", "method": "fill"}],
                         action="fill_form")
        snap_fields = [{"label": "Email", "input_type": "email"}]
        snap_buttons = [{"text": "Submit"}]
        guarded = pr._apply_zero_fields_guard(action, snap_fields, snap_buttons)
        # Has real fields → pass through unchanged
        assert guarded.action == "fill_form"
        assert guarded.confidence == 0.9

    def test_honeypot_only_fields_treated_as_zero(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        action = _action([], action="fill_form")
        snap_fields = [{"label": "honeypot_field"}]
        snap_buttons = [{"text": "Apply Now"}]
        guarded = pr._apply_zero_fields_guard(action, snap_fields, snap_buttons)
        # Only honeypot → treat as 0 fields → override
        assert guarded.action == "click_element"
        assert "Apply Now" in guarded.target_text

    def test_non_fill_action_unchanged(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        action = _action([], action="click_element")
        guarded = pr._apply_zero_fields_guard(action, [], [{"text": "Apply"}])
        # click_element already → pass through
        assert guarded.action == "click_element"
