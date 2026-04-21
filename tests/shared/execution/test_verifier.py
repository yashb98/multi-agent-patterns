"""Tests for FormVerifier heuristic field checks."""

import pytest


class TestFormVerifier:
    def test_detects_name_in_phone_field(self):
        from shared.execution._verifier import FormVerifier, VerifyResult
        v = FormVerifier()
        results = [{"label": "Phone", "value": "John Smith", "ok": True}]
        vr = v.check_field_mismatches(results)
        assert vr.field_mismatch is True
        assert "Phone" in vr.details

    def test_no_mismatch_for_valid_phone(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        results = [{"label": "Phone", "value": "+447123456789", "ok": True}]
        vr = v.check_field_mismatches(results)
        assert vr.field_mismatch is False

    def test_detects_duplicate_upload(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        events = [
            {"event_type": "form.fields_filled", "payload": {"page": 1, "results": [{"label": "Resume", "value": "cv.pdf"}]}},
            {"event_type": "form.fields_filled", "payload": {"page": 2, "results": [{"label": "Resume", "value": "cv.pdf"}]}},
        ]
        vr = v.check_duplicate_uploads(events)
        assert vr.duplicate_upload is True

    def test_no_duplicate_for_single_upload(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        events = [
            {"event_type": "form.fields_filled", "payload": {"page": 1, "results": [{"label": "Resume", "value": "cv.pdf"}]}},
        ]
        vr = v.check_duplicate_uploads(events)
        assert vr.duplicate_upload is False

    def test_detects_empty_required(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        results = [
            {"label": "Name", "value": "Yash", "ok": True, "required": True},
            {"label": "Email", "value": "", "ok": True, "required": True},
        ]
        vr = v.check_empty_required(results)
        assert vr.empty_required is True
        assert "Email" in vr.details

    def test_all_ok_when_no_issues(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        results = [
            {"label": "Name", "value": "Yash Bishnoi", "ok": True},
            {"label": "Email", "value": "yash@example.com", "ok": True},
            {"label": "Phone", "value": "+447123456789", "ok": True},
        ]
        field_vr = v.check_field_mismatches(results)
        empty_vr = v.check_empty_required(results)
        assert field_vr.all_ok is True
        assert empty_vr.all_ok is True
