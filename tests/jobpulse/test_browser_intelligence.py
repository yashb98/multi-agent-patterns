"""Tests for browser intelligence capture + signal interpretation.

Covers: ring buffer, console/network filtering, classification tiers,
field association, correction transforms, temporal gating, DOM cross-check,
and FormExperienceDB signal_corrections table.
"""
from __future__ import annotations

import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.browser_intelligence import (
    BrowserIntelligence,
    CapturedSignal,
    _BUFFER_MAX,
    _CONSOLE_NOISE,
)
from jobpulse.signal_interpreter import (
    TRANSFORMS,
    CorrectionAction,
    SignalInterpreter,
    SignalType,
    SubmissionError,
    _classify_signal,
    _extract_range_bounds,
    _infer_transform,
    _is_form_relevant,
    _parse_date_to_iso,
)


# ── Ring Buffer ─────────────────────────────────────────────────────────


class TestRingBuffer:
    def test_buffer_max_capacity(self):
        bi = BrowserIntelligence()
        for i in range(_BUFFER_MAX + 10):
            bi._buffer.append(CapturedSignal(
                source="console", level="error", text=f"err {i}",
                timestamp_ms=float(i), url="", metadata={},
            ))
        assert len(bi._buffer) == _BUFFER_MAX
        assert bi._buffer[0].text == f"err 10"

    def test_clear_empties_buffer(self):
        bi = BrowserIntelligence()
        bi._buffer.append(CapturedSignal(
            source="console", level="error", text="test",
            timestamp_ms=1.0, url="", metadata={},
        ))
        bi.clear()
        assert len(bi._buffer) == 0
        assert bi._mutation_injected is False

    def test_get_signals_returns_all(self):
        bi = BrowserIntelligence()
        for i in range(5):
            bi._buffer.append(CapturedSignal(
                source="console", level="error", text=f"err {i}",
                timestamp_ms=float(i * 100), url="", metadata={},
            ))
        assert len(bi.get_signals()) == 5

    def test_get_signals_since_filters(self):
        bi = BrowserIntelligence()
        bi._buffer.append(CapturedSignal(
            source="console", level="error", text="old",
            timestamp_ms=100.0, url="", metadata={},
        ))
        bi._buffer.append(CapturedSignal(
            source="console", level="error", text="new",
            timestamp_ms=500.0, url="", metadata={},
        ))
        result = bi.get_signals(since_ms=300.0)
        assert len(result) == 1
        assert result[0].text == "new"


# ── Console Noise Filtering ────────────────────────────────────────────


class TestConsoleFiltering:
    def _make_msg(self, text: str, msg_type: str = "error") -> MagicMock:
        msg = MagicMock()
        msg.type = msg_type
        msg.text = text
        return msg

    def test_noise_patterns_dropped(self):
        bi = BrowserIntelligence()
        bi._page = MagicMock()
        bi._page.url = "https://example.com"
        for noise in ["[HMR] update", "Warning: Failed prop type", "gtag loaded",
                       "analytics init", "Download the React DevTools"]:
            bi._on_console(self._make_msg(noise))
        assert len(bi._buffer) == 0

    def test_validation_errors_kept(self):
        bi = BrowserIntelligence()
        bi._page = MagicMock()
        bi._page.url = "https://example.com"
        bi._on_console(self._make_msg("Email is required"))
        assert len(bi._buffer) == 1
        assert bi._buffer[0].text == "Email is required"

    def test_info_messages_dropped(self):
        bi = BrowserIntelligence()
        bi._page = MagicMock()
        bi._page.url = "https://example.com"
        bi._on_console(self._make_msg("some info", msg_type="info"))
        assert len(bi._buffer) == 0

    def test_short_messages_dropped(self):
        bi = BrowserIntelligence()
        bi._page = MagicMock()
        bi._page.url = "https://example.com"
        bi._on_console(self._make_msg("ab"))
        assert len(bi._buffer) == 0


# ── Network Filtering ──────────────────────────────────────────────────


class TestNetworkFiltering:
    def _make_response(self, method: str, status: int, body: str = "") -> MagicMock:
        resp = MagicMock()
        resp.request.method = method
        resp.status = status
        resp.url = "https://api.example.com/submit"
        resp.text.return_value = body
        return resp

    def test_get_200_dropped(self):
        bi = BrowserIntelligence()
        bi._page = MagicMock()
        bi._on_response(self._make_response("GET", 200))
        assert len(bi._buffer) == 0

    def test_post_200_dropped(self):
        bi = BrowserIntelligence()
        bi._page = MagicMock()
        bi._on_response(self._make_response("POST", 200))
        assert len(bi._buffer) == 0

    def test_post_422_kept(self):
        bi = BrowserIntelligence()
        bi._page = MagicMock()
        bi._on_response(self._make_response("POST", 422, '{"errors": {"email": "invalid"}}'))
        assert len(bi._buffer) == 1
        assert bi._buffer[0].source == "network"
        assert bi._buffer[0].metadata["status_code"] == 422

    def test_put_400_kept(self):
        bi = BrowserIntelligence()
        bi._page = MagicMock()
        bi._on_response(self._make_response("PUT", 400, "bad request"))
        assert len(bi._buffer) == 1


# ── Signal Classification (Tier 1 + Tier 2) ───────────────────────────


class TestClassification:
    @pytest.mark.parametrize("text,expected", [
        ("This field is required", SignalType.REQUIRED_FIELD),
        ("Email cannot be blank", SignalType.REQUIRED_FIELD),
        ("Please fill in this field", SignalType.REQUIRED_FIELD),
        ("Email already registered", SignalType.DUPLICATE),
        ("Account already exists", SignalType.DUPLICATE),
        ("Please select an option", SignalType.OPTION_INVALID),
        ("Not a valid option", SignalType.OPTION_INVALID),
        ("Fix errors before submitting", SignalType.SUBMISSION_BLOCKED),
        ("Please correct the errors below", SignalType.SUBMISSION_BLOCKED),
    ])
    def test_tier1_exact_phrases(self, text, expected):
        assert _classify_signal(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("Phone format must be international", SignalType.FORMAT_ERROR),
        ("Invalid email format", SignalType.FORMAT_ERROR),
        ("Must be between 1 and 100", SignalType.RANGE_ERROR),
        ("At least 3 characters required", SignalType.RANGE_ERROR),
        ("Value is too short", SignalType.RANGE_ERROR),
        ("Must be a number", SignalType.TYPE_MISMATCH),
        ("Only numeric values allowed", SignalType.TYPE_MISMATCH),
    ])
    def test_tier2_keyword_clusters(self, text, expected):
        assert _classify_signal(text) == expected

    def test_unknown_for_unrecognized(self):
        assert _classify_signal("Something happened") == SignalType.UNKNOWN

    def test_case_insensitive(self):
        assert _classify_signal("THIS FIELD IS REQUIRED") == SignalType.REQUIRED_FIELD


# ── Form Relevance Filter ─────────────────────────────────────────────


class TestFormRelevance:
    def test_relevant_texts(self):
        assert _is_form_relevant("This field is required") is True
        assert _is_form_relevant("Invalid email format") is True
        assert _is_form_relevant("Please enter a valid number") is True

    def test_irrelevant_texts(self):
        assert _is_form_relevant("Loading page...") is False
        assert _is_form_relevant("Welcome to our site") is False


# ── Correction Transforms ─────────────────────────────────────────────


class TestTransforms:
    def test_prepend_country_code(self):
        assert TRANSFORMS["prepend_country_code"]("07911123456") == "+447911123456"
        assert TRANSFORMS["prepend_country_code"]("+447911123456") == "+447911123456"

    def test_strip_non_numeric(self):
        assert TRANSFORMS["strip_non_numeric"]("£45,000") == "45000"

    def test_strip_currency(self):
        assert TRANSFORMS["strip_currency"]("£45,000") == "45000"
        assert TRANSFORMS["strip_currency"]("$1,200.50") == "1200.50"

    def test_to_iso_date(self):
        assert TRANSFORMS["to_iso_date"]("25/12/2024") == "2024-12-25"
        assert TRANSFORMS["to_iso_date"]("12/25/2024") == "2024-12-25"

    def test_lowercase_email(self):
        assert TRANSFORMS["lowercase_email"]("  User@Example.COM  ") == "user@example.com"

    def test_strip_whitespace(self):
        assert TRANSFORMS["strip_whitespace"]("  AB1 2CD  ") == "AB1 2CD"

    def test_none_transform(self):
        assert TRANSFORMS["none"]("anything") == "anything"


# ── Transform Inference ────────────────────────────────────────────────


class TestTransformInference:
    def test_phone_country_code(self):
        result = _infer_transform(SignalType.FORMAT_ERROR, "Phone must include country code", "Phone Number")
        assert result == "prepend_country_code"

    def test_email_format(self):
        result = _infer_transform(SignalType.FORMAT_ERROR, "Invalid email", "Email Address")
        assert result == "lowercase_email"

    def test_date_format(self):
        result = _infer_transform(SignalType.FORMAT_ERROR, "Invalid date format", "Start Date")
        assert result == "to_iso_date"

    def test_postcode_format(self):
        result = _infer_transform(SignalType.FORMAT_ERROR, "Invalid format", "Postcode")
        assert result == "strip_whitespace"

    def test_type_mismatch_strips_numeric(self):
        result = _infer_transform(SignalType.TYPE_MISMATCH, "Must be a number", "Salary")
        assert result == "strip_non_numeric"

    def test_salary_range_strips_currency(self):
        result = _infer_transform(SignalType.RANGE_ERROR, "Value must be £20,000-£50,000", "Salary")
        assert result == "strip_currency"

    def test_unknown_returns_none(self):
        result = _infer_transform(SignalType.REQUIRED_FIELD, "Field is required", "Name")
        assert result == "none"


# ── Range Extraction ──────────────────────────────────────────────────


class TestRangeExtraction:
    def test_between(self):
        assert _extract_range_bounds("Value must be between 1 and 100") == (1, 100)

    def test_at_least(self):
        assert _extract_range_bounds("At least 3 characters") == (3, None)

    def test_no_more_than(self):
        assert _extract_range_bounds("No more than 50 characters") == (None, 50)

    def test_maximum(self):
        assert _extract_range_bounds("Maximum 255 characters allowed") == (None, 255)

    def test_no_bounds(self):
        assert _extract_range_bounds("Some random text") == (None, None)


# ── Date Parsing ──────────────────────────────────────────────────────


class TestDateParsing:
    def test_dd_mm_yyyy_slash(self):
        assert _parse_date_to_iso("25/12/2024") == "2024-12-25"

    def test_mm_dd_yyyy_slash(self):
        assert _parse_date_to_iso("12/25/2024") == "2024-12-25"

    def test_dd_mm_yyyy_dash(self):
        assert _parse_date_to_iso("25-12-2024") == "2024-12-25"

    def test_dd_mm_yyyy_dot(self):
        assert _parse_date_to_iso("25.12.2024") == "2024-12-25"

    def test_unparseable_passthrough(self):
        assert _parse_date_to_iso("not-a-date") == "not-a-date"


# ── Field Association ─────────────────────────────────────────────────


class TestFieldAssociation:
    def setup_method(self):
        self.interpreter = SignalInterpreter()

    def test_mutation_with_matching_label(self):
        signal = CapturedSignal(
            source="mutation", level="error", text="Field invalid",
            timestamp_ms=100.0, url="", metadata={"field_label": "email"},
        )
        result = self.interpreter._associate_signal_to_field(signal, "Email Address")
        assert result == "Email Address"

    def test_mutation_with_non_matching_label(self):
        signal = CapturedSignal(
            source="mutation", level="error", text="Field invalid",
            timestamp_ms=100.0, url="", metadata={"field_label": "phone"},
        )
        result = self.interpreter._associate_signal_to_field(signal, "Email Address")
        assert result is None

    def test_mutation_without_label_defaults_to_filled(self):
        signal = CapturedSignal(
            source="mutation", level="error", text="Field invalid",
            timestamp_ms=100.0, url="", metadata={},
        )
        result = self.interpreter._associate_signal_to_field(signal, "Email")
        assert result == "Email"

    def test_network_with_matching_field_error(self):
        signal = CapturedSignal(
            source="network", level="error",
            text='{"errors": {"email": "invalid format"}}',
            timestamp_ms=100.0, url="", metadata={"status_code": 422},
        )
        result = self.interpreter._associate_signal_to_field(signal, "Email")
        assert result == "Email"

    def test_network_with_non_matching_field_error(self):
        signal = CapturedSignal(
            source="network", level="error",
            text='{"errors": {"phone": "invalid"}}',
            timestamp_ms=100.0, url="", metadata={"status_code": 422},
        )
        result = self.interpreter._associate_signal_to_field(signal, "Email")
        assert result is None

    def test_console_defaults_to_filled_field(self):
        signal = CapturedSignal(
            source="console", level="error", text="Validation failed",
            timestamp_ms=100.0, url="", metadata={},
        )
        result = self.interpreter._associate_signal_to_field(signal, "Name")
        assert result == "Name"


# ── Label Matching ────────────────────────────────────────────────────


class TestLabelMatching:
    def test_exact_match(self):
        assert SignalInterpreter._labels_match("email", "email") is True

    def test_case_insensitive(self):
        assert SignalInterpreter._labels_match("Email", "email") is True

    def test_substring_match(self):
        assert SignalInterpreter._labels_match("email", "Email Address") is True

    def test_no_match(self):
        assert SignalInterpreter._labels_match("phone", "email") is False

    def test_empty_labels(self):
        assert SignalInterpreter._labels_match("", "email") is False
        assert SignalInterpreter._labels_match("email", "") is False


# ── Network Error Extraction ──────────────────────────────────────────


class TestNetworkErrorExtraction:
    def setup_method(self):
        self.interpreter = SignalInterpreter()

    def test_dict_errors(self):
        body = '{"errors": {"email": "invalid format", "phone": "required"}}'
        result = self.interpreter._extract_network_field_errors(body)
        assert result == {"email": "invalid format", "phone": "required"}

    def test_list_errors(self):
        body = '{"errors": {"email": ["too short", "invalid"]}}'
        result = self.interpreter._extract_network_field_errors(body)
        assert result == {"email": "too short; invalid"}

    def test_error_key(self):
        body = '{"error": {"name": "cannot be blank"}}'
        result = self.interpreter._extract_network_field_errors(body)
        assert result == {"name": "cannot be blank"}

    def test_field_errors_key(self):
        body = '{"fieldErrors": {"salary": "must be numeric"}}'
        result = self.interpreter._extract_network_field_errors(body)
        assert result == {"salary": "must be numeric"}

    def test_invalid_json(self):
        result = self.interpreter._extract_network_field_errors("not json")
        assert result == {}

    def test_non_dict_errors(self):
        body = '{"errors": "something went wrong"}'
        result = self.interpreter._extract_network_field_errors(body)
        assert result == {}


# ── Check After Fill (Integration) ────────────────────────────────────


class TestCheckAfterFill:
    @pytest.mark.asyncio
    async def test_no_signals_returns_none(self):
        interpreter = SignalInterpreter()
        intelligence = BrowserIntelligence()
        intelligence._mutation_injected = True
        intelligence._page = MagicMock()
        intelligence._page.evaluate = AsyncMock(return_value=[])

        locator = MagicMock()
        page = MagicMock()

        result = await interpreter.check_after_fill(
            intelligence, "Email", locator, time.monotonic() * 1000, page,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_stale_signal_filtered(self):
        interpreter = SignalInterpreter()
        intelligence = BrowserIntelligence()
        intelligence._mutation_injected = True
        intelligence._page = MagicMock()
        intelligence._page.evaluate = AsyncMock(return_value=[])

        old_ts = time.monotonic() * 1000 - 5000
        intelligence._buffer.append(CapturedSignal(
            source="console", level="error", text="Email is required",
            timestamp_ms=old_ts, url="", metadata={},
        ))

        fill_ts = time.monotonic() * 1000
        result = await interpreter.check_after_fill(
            intelligence, "Email", MagicMock(), fill_ts, MagicMock(),
        )
        assert result is None


# ── Check After Submit ────────────────────────────────────────────────


class TestCheckAfterSubmit:
    @pytest.mark.asyncio
    async def test_network_422_produces_errors(self):
        interpreter = SignalInterpreter()
        intelligence = BrowserIntelligence()
        intelligence._mutation_injected = True
        intelligence._page = MagicMock()
        intelligence._page.evaluate = AsyncMock(return_value=[])

        intelligence._buffer.append(CapturedSignal(
            source="network", level="error",
            text='{"errors": {"email": "already registered"}}',
            timestamp_ms=100.0, url="",
            metadata={"status_code": 422},
        ))

        page = MagicMock()
        errors = await interpreter.check_after_submit(intelligence, page)
        assert len(errors) == 1
        assert errors[0].field_label == "email"
        assert errors[0].signal_type == SignalType.DUPLICATE.value

    @pytest.mark.asyncio
    async def test_submission_blocked_signal(self):
        interpreter = SignalInterpreter()
        intelligence = BrowserIntelligence()
        intelligence._mutation_injected = True
        intelligence._page = MagicMock()
        intelligence._page.evaluate = AsyncMock(return_value=[])

        intelligence._buffer.append(CapturedSignal(
            source="console", level="error",
            text="Please correct the errors below",
            timestamp_ms=100.0, url="", metadata={},
        ))

        errors = await interpreter.check_after_submit(intelligence, MagicMock())
        assert len(errors) == 1
        assert errors[0].signal_type == SignalType.SUBMISSION_BLOCKED.value


# ── FormExperienceDB signal_corrections ───────────────────────────────


class TestSignalCorrectionsDB:
    def test_store_and_retrieve(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB
        db = FormExperienceDB(db_path=str(tmp_path / "test_fe.db"))

        db.store_signal_correction(
            domain="https://jobs.example.com/apply",
            field_label="Phone",
            signal_type="format_error",
            error_message="Must include country code",
            original_value="07911123456",
            corrected_value="+447911123456",
            transform="prepend_country_code",
        )

        corrections = db.get_signal_corrections("https://jobs.example.com/apply", "Phone")
        assert len(corrections) == 1
        assert corrections[0]["domain"] == "jobs.example.com"
        assert corrections[0]["field_label"] == "Phone"
        assert corrections[0]["transform"] == "prepend_country_code"

    def test_domain_normalization(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB
        db = FormExperienceDB(db_path=str(tmp_path / "test_fe.db"))

        db.store_signal_correction(
            domain="https://www.greenhouse.io/apply",
            field_label="Email",
            signal_type="format_error",
            error_message="Invalid email",
            original_value="User@Test.COM",
            corrected_value="user@test.com",
            transform="lowercase_email",
        )

        corrections = db.get_signal_corrections("greenhouse.io")
        assert len(corrections) == 1

    def test_multiple_corrections_ordered_by_recency(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB
        db = FormExperienceDB(db_path=str(tmp_path / "test_fe.db"))

        for i in range(3):
            db.store_signal_correction(
                domain="example.com",
                field_label="Salary",
                signal_type="type_mismatch",
                error_message="Must be numeric",
                original_value=f"£{i}0,000",
                corrected_value=f"{i}0000",
                transform="strip_non_numeric",
            )

        corrections = db.get_signal_corrections("example.com", "Salary")
        assert len(corrections) == 3

    def test_get_all_domain_corrections(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB
        db = FormExperienceDB(db_path=str(tmp_path / "test_fe.db"))

        db.store_signal_correction(
            domain="example.com", field_label="Phone",
            signal_type="format_error", error_message="err",
            original_value="0791", corrected_value="+4479",
            transform="prepend_country_code",
        )
        db.store_signal_correction(
            domain="example.com", field_label="Email",
            signal_type="format_error", error_message="err",
            original_value="A@B", corrected_value="a@b",
            transform="lowercase_email",
        )

        corrections = db.get_signal_corrections("example.com")
        assert len(corrections) == 2

    def test_empty_corrections(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB
        db = FormExperienceDB(db_path=str(tmp_path / "test_fe.db"))

        corrections = db.get_signal_corrections("nonexistent.com")
        assert corrections == []


# ── CDP Log Listener ──────────────────────────────────────────────────


class TestCDPLogListener:
    def test_error_entries_captured(self):
        bi = BrowserIntelligence()
        bi._on_log_entry({"entry": {
            "level": "error",
            "text": "Form validation failed for field 'email'",
            "url": "https://example.com",
        }})
        assert len(bi._buffer) == 1
        assert bi._buffer[0].source == "browser_log"

    def test_info_entries_dropped(self):
        bi = BrowserIntelligence()
        bi._on_log_entry({"entry": {"level": "info", "text": "Page loaded"}})
        assert len(bi._buffer) == 0

    def test_noise_entries_dropped(self):
        bi = BrowserIntelligence()
        bi._on_log_entry({"entry": {
            "level": "error",
            "text": "third-party cookie will be blocked",
        }})
        assert len(bi._buffer) == 0

    def test_short_entries_dropped(self):
        bi = BrowserIntelligence()
        bi._on_log_entry({"entry": {"level": "error", "text": "ab"}})
        assert len(bi._buffer) == 0


# ── Mutation Observer Polling ─────────────────────────────────────────


class TestMutationPolling:
    @pytest.mark.asyncio
    async def test_poll_captures_dom_errors(self):
        bi = BrowserIntelligence()
        bi._mutation_injected = True
        bi._page = MagicMock()
        bi._page.url = "https://example.com/apply"
        bi._page.evaluate = AsyncMock(return_value=[
            {"type": "dom_error", "text": "Email is required", "label": "email", "selector": "SPAN.error"},
        ])

        await bi.poll_mutations()
        assert len(bi._buffer) == 1
        assert bi._buffer[0].source == "mutation"
        assert bi._buffer[0].text == "Email is required"
        assert bi._buffer[0].metadata["field_label"] == "email"

    @pytest.mark.asyncio
    async def test_poll_skips_when_not_injected(self):
        bi = BrowserIntelligence()
        bi._mutation_injected = False
        bi._page = MagicMock()

        await bi.poll_mutations()
        assert len(bi._buffer) == 0

    @pytest.mark.asyncio
    async def test_poll_handles_error_gracefully(self):
        bi = BrowserIntelligence()
        bi._mutation_injected = True
        bi._page = MagicMock()
        bi._page.evaluate = AsyncMock(side_effect=Exception("page crashed"))

        await bi.poll_mutations()
        assert len(bi._buffer) == 0


# ── Verify Correction ────────────────────────────────────────────────


class TestVerifyCorrection:
    @pytest.mark.asyncio
    async def test_verification_passes_when_no_errors(self):
        interpreter = SignalInterpreter()
        locator = MagicMock()
        locator.element_handle = AsyncMock(return_value=MagicMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"invalid": False, "hasErrorEl": False})

        result = await interpreter.verify_correction(locator, page)
        assert result is True

    @pytest.mark.asyncio
    async def test_verification_fails_when_still_invalid(self):
        interpreter = SignalInterpreter()
        locator = MagicMock()
        locator.element_handle = AsyncMock(return_value=MagicMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"invalid": True, "hasErrorEl": True})

        result = await interpreter.verify_correction(locator, page)
        assert result is False

    @pytest.mark.asyncio
    async def test_verification_degrades_gracefully_on_exception(self):
        """When DOM check fails, verify_correction returns False (can't confirm fix)."""
        interpreter = SignalInterpreter()
        locator = MagicMock()
        page = MagicMock()
        with patch.object(interpreter, "_dom_cross_check", new_callable=AsyncMock, side_effect=Exception("crash")):
            result = await interpreter.verify_correction(locator, page)
        assert result is False
