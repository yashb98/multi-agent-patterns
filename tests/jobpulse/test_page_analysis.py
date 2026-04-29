"""Tests for the feature-based page type classifier and calibration."""

import json
import sqlite3
from pathlib import Path

import pytest

from jobpulse.form_models import PageType
from jobpulse.page_analysis.classifier import (
    DEFAULT_WEIGHTS,
    PageFeatures,
    PageTypeClassifier,
    _find_matches,
)
from jobpulse.page_analysis.calibration import ClassifierCalibration


# ---------------------------------------------------------------------------
# Classifier tests
# ---------------------------------------------------------------------------


def _snapshot(
    buttons=None,
    fields=None,
    page_text="",
    verification_wall=None,
    has_file_inputs=False,
    url="https://example.com/apply",
    has_dialog=False,
):
    return {
        "buttons": buttons or [],
        "fields": fields or [],
        "page_text_preview": page_text,
        "verification_wall": verification_wall,
        "has_file_inputs": has_file_inputs,
        "url": url,
        "has_dialog": has_dialog,
    }


def test_classify_verification_wall():
    clf = PageTypeClassifier()
    snap = _snapshot(verification_wall={"type": "cloudflare"})
    pt, conf = clf.classify(snap)
    assert pt == PageType.VERIFICATION_WALL
    assert conf >= 0.9


def test_classify_confirmation():
    clf = PageTypeClassifier()
    snap = _snapshot(page_text="Thank you for applying! We have received your application.")
    pt, conf = clf.classify(snap)
    assert pt == PageType.CONFIRMATION
    assert conf >= 0.9


def test_classify_email_verification():
    clf = PageTypeClassifier()
    snap = _snapshot(page_text="Please check your email to verify your account.")
    pt, conf = clf.classify(snap)
    assert pt == PageType.EMAIL_VERIFICATION
    assert conf >= 0.8


def test_classify_session_expired():
    clf = PageTypeClassifier()
    snap = _snapshot(page_text="Your session has expired. Please sign in again.")
    pt, conf = clf.classify(snap)
    assert pt == PageType.SESSION_EXPIRED
    assert conf >= 0.9


def test_classify_login_form():
    clf = PageTypeClassifier()
    snap = _snapshot(
        fields=[
            {"input_type": "email", "label": "Email"},
            {"input_type": "password", "label": "Password"},
        ],
        buttons=[{"text": "Sign in"}],
    )
    pt, conf = clf.classify(snap)
    assert pt == PageType.LOGIN_FORM
    assert conf >= 0.8


def test_classify_signup_form():
    clf = PageTypeClassifier()
    snap = _snapshot(
        fields=[
            {"input_type": "email", "label": "Email"},
            {"input_type": "password", "label": "Password"},
            {"input_type": "password", "label": "Confirm Password"},
        ],
        buttons=[{"text": "Create Account"}],
    )
    pt, conf = clf.classify(snap)
    assert pt == PageType.SIGNUP_FORM
    assert conf >= 0.9


def test_classify_job_description():
    clf = PageTypeClassifier()
    snap = _snapshot(buttons=[{"text": "Apply Now"}])
    pt, conf = clf.classify(snap)
    assert pt == PageType.JOB_DESCRIPTION
    assert conf >= 0.8


def test_classify_application_form():
    clf = PageTypeClassifier()
    snap = _snapshot(
        fields=[
            {"input_type": "text", "label": "First Name"},
            {"input_type": "text", "label": "Last Name"},
        ],
        has_file_inputs=True,
    )
    pt, conf = clf.classify(snap)
    assert pt == PageType.APPLICATION_FORM


def test_classify_unknown():
    clf = PageTypeClassifier()
    snap = _snapshot(page_text="Welcome to our company.", buttons=[{"text": "Learn More"}])
    pt, conf = clf.classify(snap)
    assert pt == PageType.UNKNOWN
    assert conf < 0.5


def test_classify_dialog_overrides_apply_button():
    """Dialog with form fields should be APPLICATION_FORM even with apply button and job URL."""
    clf = PageTypeClassifier()
    snap = {
        "buttons": [{"text": "Submit application"}],
        "fields": [
            {"label": "First Name", "input_type": "text", "selector": "input[name='firstName']"},
            {"label": "Last Name", "input_type": "text", "selector": "input[name='lastName']"},
        ],
        "page_text_preview": "Apply for Data Scientist role",
        "url": "https://linkedin.com/jobs/view/123",
        "has_file_inputs": False,
        "has_dialog": True,
    }
    pt, conf = clf.classify(snap)
    assert pt == PageType.APPLICATION_FORM
    assert conf >= 0.85


def test_classify_from_features():
    clf = PageTypeClassifier()
    features = PageFeatures(
        has_application_labels=True,
        has_file_inputs=False,
        has_login_button=False,
        has_signup_button=False,
        password_count=0,
        confirmation_signals=[],
        email_verify_signals=[],
        session_expired_signals=[],
        consent_signals=[],
        dialog_present=False,
        field_count=2,
        button_count=0,
        url_path="",
        verification_wall_present=False,
        has_apply_button=False,
        has_email_field=False,
        has_accept_button=False,
    )
    pt, conf = clf.classify_from_features(features)
    assert pt == PageType.APPLICATION_FORM


def test_default_weights_structure():
    """All PageType values must have a weights entry."""
    for pt in PageType:
        assert pt.value in DEFAULT_WEIGHTS, f"Missing weights for {pt.value}"


def test_load_custom_weights(tmp_path):
    weights = {"unknown": {"bias": 99.0}}
    path = tmp_path / "weights.json"
    path.write_text(json.dumps(weights))
    clf = PageTypeClassifier(str(path))
    assert clf.weights["unknown"]["bias"] == 99.0


def test_find_matches():
    import re

    pattern = re.compile(r"hello")
    assert _find_matches(pattern, "hello world hello") == ["hello", "hello"]
    assert _find_matches(pattern, "no match here") == []


# ---------------------------------------------------------------------------
# Calibration tests
# ---------------------------------------------------------------------------


def test_calibration_record_and_evaluate(tmp_path):
    db_path = str(tmp_path / "calibration.db")
    cal = ClassifierCalibration(db_path=db_path)

    snap = _snapshot(
        buttons=[{"text": "Apply Now"}],
        page_text="",
    )
    cal.record_example(snap, PageType.JOB_DESCRIPTION)

    metrics = cal.evaluate()
    assert "accuracy" in metrics
    assert metrics["accuracy"] == 1.0


def test_calibration_empty_evaluate(tmp_path):
    db_path = str(tmp_path / "empty.db")
    cal = ClassifierCalibration(db_path=db_path)
    assert cal.evaluate() == {}


def test_calibration_calibrate_returns_weights(tmp_path):
    db_path = str(tmp_path / "calibration.db")
    cal = ClassifierCalibration(db_path=db_path)

    # Seed with a few examples
    for _ in range(3):
        cal.record_example(
            _snapshot(buttons=[{"text": "Apply Now"}]),
            PageType.JOB_DESCRIPTION,
        )

    weights = cal.calibrate_weights()
    assert "job_description" in weights
    assert isinstance(weights["job_description"], dict)


def test_calibration_schema(tmp_path):
    db_path = str(tmp_path / "calibration.db")
    cal = ClassifierCalibration(db_path=db_path)

    snap = _snapshot(page_text="Thank you for applying")
    cal.record_example(snap, PageType.CONFIRMATION)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT url, true_label, predicted_label, confidence FROM examples"
        ).fetchone()

    assert row is not None
    assert row[1] == "confirmation"
    assert isinstance(row[3], float)
    assert 0.0 <= row[3] <= 1.0


def test_calibration_db_uses_data_dir_by_default():
    cal = ClassifierCalibration()
    assert cal.db_path.endswith("page_classifier_examples.db")
