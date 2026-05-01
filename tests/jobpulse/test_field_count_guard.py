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
