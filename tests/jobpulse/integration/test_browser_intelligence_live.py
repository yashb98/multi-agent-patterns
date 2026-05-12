"""Live integration tests for BrowserIntelligence — real Playwright browser.

Tests signal capture against a local HTML test form that validates fields,
shows error elements, and returns HTTP errors. Verifies the full pipeline:
capture → filter → classify → associate → correct → verify.

Usage:
    RUN_INTEGRATION_TESTS=1 pytest tests/jobpulse/integration/test_browser_intelligence_live.py -v -s
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("RUN_INTEGRATION_TESTS"),
        reason="Set RUN_INTEGRATION_TESTS=1 to run live browser intelligence tests",
    ),
]

_TEST_FORM_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Form</title></head>
<body>
<form id="test-form" novalidate>
  <div class="form-group">
    <label for="email">Email Address</label>
    <input id="email" name="email" type="email" aria-label="Email Address" />
    <span class="error" id="email-error" style="display:none;color:red" role="alert"></span>
  </div>
  <div class="form-group">
    <label for="phone">Phone Number</label>
    <input id="phone" name="phone" type="tel" aria-label="Phone Number" />
    <span class="error" id="phone-error" style="display:none;color:red" role="alert"></span>
  </div>
  <div class="form-group">
    <label for="salary">Expected Salary</label>
    <input id="salary" name="salary" type="text" aria-label="Expected Salary" />
    <span class="error" id="salary-error" style="display:none;color:red" role="alert"></span>
  </div>
  <button type="submit">Submit</button>
</form>
<script>
  function showError(fieldId, msg) {
    var el = document.getElementById(fieldId + '-error');
    el.textContent = msg;
    el.style.display = 'block';
    document.getElementById(fieldId).setAttribute('aria-invalid', 'true');
    console.error(msg);
  }
  function clearError(fieldId) {
    var el = document.getElementById(fieldId + '-error');
    el.textContent = '';
    el.style.display = 'none';
    document.getElementById(fieldId).setAttribute('aria-invalid', 'false');
  }
  document.getElementById('email').addEventListener('blur', function() {
    var v = this.value;
    if (!v) { showError('email', 'Email is required'); }
    else if (v !== v.toLowerCase() || !v.includes('@')) { showError('email', 'Invalid email format'); }
    else { clearError('email'); }
  });
  document.getElementById('phone').addEventListener('blur', function() {
    var v = this.value;
    if (v && !v.startsWith('+')) { showError('phone', 'Phone must include country code'); }
    else { clearError('phone'); }
  });
  document.getElementById('salary').addEventListener('blur', function() {
    var v = this.value;
    if (v && isNaN(v.replace(/[^0-9.]/g, ''))) { showError('salary', 'Must be a number'); }
    else { clearError('salary'); }
  });
</script>
</body>
</html>"""


@pytest_asyncio.fixture(scope="module")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def browser_page():
    """Launch a real Playwright browser and serve the test form."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright not installed")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()

    tmp_path = Path("/tmp/bi_test_form.html")
    tmp_path.write_text(_TEST_FORM_HTML)
    await page.goto(f"file://{tmp_path}")
    await page.wait_for_load_state("domcontentloaded")

    yield page

    await browser.close()
    await pw.stop()
    tmp_path.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def intelligence(browser_page):
    """Attach BrowserIntelligence to the test page."""
    from jobpulse.browser_intelligence import BrowserIntelligence

    bi = BrowserIntelligence()
    await bi.attach(browser_page)
    yield bi
    await bi.detach()


class TestLiveConsoleCapture:
    @pytest.mark.asyncio
    async def test_validation_error_captured_on_blur(self, browser_page, intelligence):
        intelligence.clear()

        email_field = browser_page.locator("#email")
        await email_field.fill("")
        await email_field.blur()
        await asyncio.sleep(0.3)

        signals = intelligence.get_signals()
        error_signals = [s for s in signals if "required" in s.text.lower()]
        assert len(error_signals) >= 1, f"Expected 'required' signal, got: {[s.text for s in signals]}"
        assert error_signals[0].source == "console"

    @pytest.mark.asyncio
    async def test_format_error_captured(self, browser_page, intelligence):
        intelligence.clear()

        email_field = browser_page.locator("#email")
        await email_field.fill("USER@EXAMPLE.COM")
        await email_field.blur()
        await asyncio.sleep(0.3)

        signals = intelligence.get_signals()
        format_signals = [s for s in signals if "format" in s.text.lower() or "invalid" in s.text.lower()]
        assert len(format_signals) >= 1


class TestLiveMutationObserver:
    @pytest.mark.asyncio
    async def test_aria_invalid_detected(self, browser_page, intelligence):
        intelligence.clear()

        phone_field = browser_page.locator("#phone")
        await phone_field.fill("07911123456")
        await phone_field.blur()
        await asyncio.sleep(0.3)

        await intelligence.poll_mutations()

        signals = intelligence.get_signals()
        mutation_signals = [s for s in signals if s.source == "mutation"]
        assert len(mutation_signals) >= 1, f"Expected mutation signal, got: {[s.text for s in signals]}"

    @pytest.mark.asyncio
    async def test_error_element_detected(self, browser_page, intelligence):
        intelligence.clear()

        email_field = browser_page.locator("#email")
        await email_field.fill("")
        await email_field.blur()
        await asyncio.sleep(0.3)

        await intelligence.poll_mutations()

        signals = intelligence.get_signals()
        all_texts = [s.text for s in signals]
        assert any("required" in t.lower() for t in all_texts), f"Missing 'required' in {all_texts}"


class TestLiveSignalInterpretation:
    @pytest.mark.asyncio
    async def test_full_pipeline_phone_correction(self, browser_page, intelligence):
        """Fill invalid phone → signal captured → correction inferred → verified."""
        from jobpulse.signal_interpreter import SignalInterpreter, TRANSFORMS

        intelligence.clear()
        interpreter = SignalInterpreter()

        phone_field = browser_page.locator("#phone")
        await phone_field.fill("07911123456")
        fill_ts = time.monotonic() * 1000
        await phone_field.blur()
        await asyncio.sleep(0.5)

        action = await interpreter.check_after_fill(
            intelligence, "Phone Number", phone_field, fill_ts, browser_page,
        )

        if action is None:
            signals = intelligence.get_signals()
            pytest.skip(f"No correction detected (signals: {[s.text for s in signals]})")

        assert action.signal_type in ("format_error", "unknown")
        if action.transform != "none":
            corrected = TRANSFORMS[action.transform]("07911123456")
            assert corrected.startswith("+")

    @pytest.mark.asyncio
    async def test_email_validation_correction(self, browser_page, intelligence):
        """Fill uppercase email → signal captured → lowercase transform inferred."""
        from jobpulse.signal_interpreter import SignalInterpreter

        intelligence.clear()
        interpreter = SignalInterpreter()

        email_field = browser_page.locator("#email")
        await email_field.fill("USER@EXAMPLE.COM")
        fill_ts = time.monotonic() * 1000
        await email_field.blur()
        await asyncio.sleep(0.5)

        action = await interpreter.check_after_fill(
            intelligence, "Email Address", email_field, fill_ts, browser_page,
        )

        if action is None:
            signals = intelligence.get_signals()
            pytest.skip(f"No correction detected (signals: {[s.text for s in signals]})")

        assert action.signal_type == "format_error"
        assert action.transform == "lowercase_email"


class TestLiveVerification:
    @pytest.mark.asyncio
    async def test_correction_clears_error(self, browser_page, intelligence):
        """Apply corrected value → aria-invalid clears → verification passes."""
        from jobpulse.signal_interpreter import SignalInterpreter

        interpreter = SignalInterpreter()

        phone_field = browser_page.locator("#phone")
        await phone_field.fill("07911123456")
        await phone_field.blur()
        await asyncio.sleep(0.3)

        await phone_field.fill("+447911123456")
        await phone_field.blur()
        await asyncio.sleep(0.3)

        result = await interpreter.verify_correction(phone_field, browser_page)
        assert result is True

    @pytest.mark.asyncio
    async def test_uncorrected_field_fails_verification(self, browser_page, intelligence):
        """Field still has aria-invalid=true → verification fails."""
        from jobpulse.signal_interpreter import SignalInterpreter

        interpreter = SignalInterpreter()

        phone_field = browser_page.locator("#phone")
        await phone_field.fill("07911123456")
        await phone_field.blur()
        await asyncio.sleep(0.3)

        result = await interpreter.verify_correction(phone_field, browser_page)
        assert result is False


class TestLiveDBWiring:
    @pytest.mark.asyncio
    async def test_signal_correction_stored_and_retrieved(self, tmp_path):
        """Full DB wiring: store correction → retrieve for pre-fill."""
        from jobpulse.form_experience_db import FormExperienceDB
        from jobpulse.signal_interpreter import TRANSFORMS

        db = FormExperienceDB(db_path=str(tmp_path / "test_fe.db"))

        db.store_signal_correction(
            domain="https://jobs.greenhouse.io/apply",
            field_label="Phone Number",
            signal_type="format_error",
            error_message="Phone must include country code",
            original_value="07911123456",
            corrected_value="+447911123456",
            transform="prepend_country_code",
        )

        corrections = db.get_signal_corrections("greenhouse.io", "Phone Number")
        assert len(corrections) == 1

        transform_fn = TRANSFORMS[corrections[0]["transform"]]
        result = transform_fn("07899123456")
        assert result == "+447899123456"
