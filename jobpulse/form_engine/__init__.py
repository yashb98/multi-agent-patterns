"""Generic form engine — detect and fill any HTML input type."""

from jobpulse.form_engine.checkbox_filler import auto_check_consent_boxes
from jobpulse.form_engine.detector import detect_input_type
from jobpulse.form_engine.file_filler import find_file_inputs
from jobpulse.form_engine.models import FieldInfo, FillResult, InputType
from jobpulse.form_engine.page_filler import fill_field_by_type
from jobpulse.form_engine.validation import find_required_unfilled, has_errors, scan_for_errors

__all__ = [
    "InputType", "FillResult", "FieldInfo",
    "detect_input_type", "fill_field_by_type",
    "scan_for_errors", "find_required_unfilled", "has_errors",
    "auto_check_consent_boxes", "find_file_inputs",
]
