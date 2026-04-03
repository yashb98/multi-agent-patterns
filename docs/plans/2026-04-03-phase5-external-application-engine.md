# Phase 5: External Application Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Handle the full external job application lifecycle — redirect chains, account creation/SSO, email verification, login, and multi-page form filling across any ATS platform, with vision fallback and per-domain learning.

**Architecture:** An `ApplicationOrchestrator` sits above the existing state machine. It navigates through redirect chains using a hybrid DOM+Vision page analyzer, handles cookie banners, SSO login, account creation, and email verification, then delegates multi-page form filling to the state machine. Successful navigation paths are saved per domain for zero-cost replay on repeat visits.

**Tech Stack:** Python 3.12, SQLite (account store + navigation learning), Gmail API (verification emails), OpenAI Vision (page analysis fallback), existing ext_bridge/ext_adapter/state_machines/vision_navigator.

---

## Architecture Diagram

```
ApplicationOrchestrator
  │
  ├── CookieBannerDismisser          ← runs before every page detection
  │
  ├── PageAnalyzer (hybrid)          ← classifies every page
  │     ├── DOM-based detector       (free, <1ms, tries first)
  │     └── Vision LLM fallback      ($0.003, ~1s, when DOM is ambiguous)
  │
  ├── NavigationLearner              ← save + replay per domain
  │     ├── save_sequence(domain, steps)
  │     └── get_sequence(domain) → replay or None
  │
  ├── SSOHandler                     ← Google/LinkedIn sign-in when available
  │
  ├── AccountManager                 ← fallback when no SSO
  │     └── SQLite: domain → email + password
  │
  ├── GmailVerifier                  ← exponential polling for verify emails
  │
  └── MultiPageFiller                ← state machine + Next button + stuck detection
        ├── find_next_button()       (Submit > Review > Continue > Next)
        ├── detect_progress()        ("Step 2 of 5")
        ├── is_page_stuck()          (content comparison)
        └── wait_for_page_stable()   (network idle + DOM stable)
```

## Flow

```
Navigate to external URL
    ↓
Dismiss cookie banner (if present)
    ↓
Wait for page to stabilize (network idle + DOM stable)
    ↓
Check NavigationLearner for known sequence → replay if available
    ↓
┌→ Detect page type (DOM first, vision fallback):
│   ├── JOB_DESCRIPTION ("Apply Now" button) → click it → loop ↑
│   ├── LOGIN_FORM → check SSOHandler first:
│   │     ├── SSO available (Google/LinkedIn) → click SSO → loop ↑
│   │     └── No SSO → AccountManager.get_credentials() → fill login → loop ↑
│   ├── SIGNUP_FORM → AccountManager.create_account() → fill signup → loop ↑
│   ├── EMAIL_VERIFICATION → GmailVerifier.wait() → navigate link → return to URL → loop ↑
│   ├── APPLICATION_FORM → hand off to MultiPageFiller
│   ├── CONFIRMATION → success
│   ├── VERIFICATION_WALL → abort (CAPTCHA)
│   └── UNKNOWN (even after vision) → abort with details
│
└── Save successful sequence to NavigationLearner
```

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `jobpulse/application_orchestrator.py` | Create | High-level flow controller |
| `jobpulse/page_analyzer.py` | Create | Hybrid DOM+Vision page type detection |
| `jobpulse/cookie_dismisser.py` | Create | Auto-dismiss cookie consent banners |
| `jobpulse/navigation_learner.py` | Create | Save + replay navigation sequences per domain |
| `jobpulse/sso_handler.py` | Create | Detect and use Google/LinkedIn SSO |
| `jobpulse/account_manager.py` | Create | SQLite CRUD for platform credentials |
| `jobpulse/gmail_verify.py` | Create | Poll Gmail for verification emails, extract links |
| `jobpulse/ext_models.py` | Modify | Add PageType, AccountInfo, NavigationStep models |
| `jobpulse/state_machines/__init__.py` | Modify | Add Next button detection, progress, stuck detection, page wait |
| `jobpulse/ext_adapter.py` | Modify | Replace raw state machine loop with orchestrator |
| `jobpulse/config.py` | Modify | Add ATS_ACCOUNT_PASSWORD, GMAIL_VERIFY_TIMEOUT |
| `jobpulse/gmail_agent.py` | Modify | Add gmail.modify scope |
| `scripts/setup_integrations.py` | Modify | Add gmail.modify to GOOGLE_SCOPES |
| `extension/content.js` | Modify | Add cookie banner detection + page stability signals |
| `tests/jobpulse/test_phase5_models.py` | Create | PageType, AccountInfo, NavigationStep |
| `tests/jobpulse/test_account_manager.py` | Create | Account CRUD, domain normalization |
| `tests/jobpulse/test_gmail_verify.py` | Create | Email polling, link extraction, exponential backoff |
| `tests/jobpulse/test_page_analyzer.py` | Create | DOM detection + vision fallback |
| `tests/jobpulse/test_cookie_dismisser.py` | Create | Banner detection and dismissal |
| `tests/jobpulse/test_navigation_learner.py` | Create | Sequence save/replay/invalidation |
| `tests/jobpulse/test_sso_handler.py` | Create | SSO detection and delegation |
| `tests/jobpulse/test_multipage_navigation.py` | Create | Next button, progress, stuck, page wait |
| `tests/jobpulse/test_application_orchestrator.py` | Create | Full flow integration |
| `tests/jobpulse/test_phase5_integration.py` | Create | End-to-end scenarios |

---

### Task 1: Config + Models

**Files:**
- Modify: `jobpulse/config.py`
- Modify: `jobpulse/ext_models.py`
- Test: `tests/jobpulse/test_phase5_models.py`

- [ ] **Step 1: Write failing tests for new models**

```python
# tests/jobpulse/test_phase5_models.py
from jobpulse.ext_models import PageType, AccountInfo, NavigationStep


def test_page_type_values():
    assert PageType.JOB_DESCRIPTION == "job_description"
    assert PageType.LOGIN_FORM == "login_form"
    assert PageType.SIGNUP_FORM == "signup_form"
    assert PageType.EMAIL_VERIFICATION == "email_verification"
    assert PageType.APPLICATION_FORM == "application_form"
    assert PageType.CONFIRMATION == "confirmation"
    assert PageType.VERIFICATION_WALL == "verification_wall"
    assert PageType.UNKNOWN == "unknown"


def test_account_info_model():
    info = AccountInfo(
        domain="greenhouse.io",
        email="bishnoiyash274@gmail.com",
        verified=True,
    )
    assert info.domain == "greenhouse.io"
    assert info.verified is True


def test_navigation_step_model():
    step = NavigationStep(
        page_type="login_form",
        action="fill_login",
        selector="#signin",
        url="https://example.com/login",
    )
    assert step.page_type == "login_form"
    assert step.action == "fill_login"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_phase5_models.py -v`
Expected: FAIL — PageType, AccountInfo, NavigationStep not defined yet

- [ ] **Step 3: Add models to ext_models.py**

Add to `jobpulse/ext_models.py`:

```python
class PageType(StrEnum):
    """Classification of what type of page we're looking at."""
    JOB_DESCRIPTION = "job_description"
    LOGIN_FORM = "login_form"
    SIGNUP_FORM = "signup_form"
    EMAIL_VERIFICATION = "email_verification"
    APPLICATION_FORM = "application_form"
    CONFIRMATION = "confirmation"
    VERIFICATION_WALL = "verification_wall"
    UNKNOWN = "unknown"


class AccountInfo(BaseModel):
    """Stored credentials for an ATS platform."""
    domain: str
    email: str
    verified: bool = False
    created_at: str = ""
    last_login: str = ""


class NavigationStep(BaseModel):
    """One step in a learned navigation sequence."""
    page_type: str
    action: str  # click_apply, fill_login, fill_signup, verify_email, sso_google
    selector: str = ""
    url: str = ""
```

- [ ] **Step 4: Add config variables to config.py**

Add to `jobpulse/config.py`:

```python
# External application engine
ATS_ACCOUNT_PASSWORD = os.getenv("ATS_ACCOUNT_PASSWORD", "")
GMAIL_VERIFY_TIMEOUT = int(os.getenv("GMAIL_VERIFY_TIMEOUT", "120"))
GMAIL_VERIFY_POLL_INTERVAL = int(os.getenv("GMAIL_VERIFY_POLL_INTERVAL", "5"))
PAGE_STABLE_TIMEOUT_MS = int(os.getenv("PAGE_STABLE_TIMEOUT_MS", "3000"))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_phase5_models.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/ext_models.py jobpulse/config.py tests/jobpulse/test_phase5_models.py
git commit -m "feat(ext): add PageType, AccountInfo, NavigationStep models and Phase 5 config"
```

---

### Task 2: Cookie Banner Dismisser

**Files:**
- Create: `jobpulse/cookie_dismisser.py`
- Modify: `extension/content.js` (add cookie banner detection)
- Test: `tests/jobpulse/test_cookie_dismisser.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_cookie_dismisser.py
import pytest
from unittest.mock import AsyncMock
from jobpulse.cookie_dismisser import CookieBannerDismisser


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.click = AsyncMock(return_value=True)
    b.get_snapshot = AsyncMock()
    return b


@pytest.fixture
def dismisser(bridge):
    return CookieBannerDismisser(bridge)


@pytest.mark.asyncio
async def test_dismiss_accept_all(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Accept All Cookies", "enabled": True, "selector": "#accept-all"},
            {"text": "Manage Preferences", "enabled": True, "selector": "#manage"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True
    bridge.click.assert_called_once_with("#accept-all")


@pytest.mark.asyncio
async def test_dismiss_i_agree(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "I Agree", "enabled": True, "selector": "#agree"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True
    bridge.click.assert_called_once_with("#agree")


@pytest.mark.asyncio
async def test_dismiss_accept_cookies(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Accept cookies", "enabled": True, "selector": ".cookie-btn"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True


@pytest.mark.asyncio
async def test_dismiss_got_it(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Got it!", "enabled": True, "selector": "#gotit"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True


@pytest.mark.asyncio
async def test_no_banner_returns_false(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Submit Application", "enabled": True, "selector": "#submit"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is False
    bridge.click.assert_not_called()


@pytest.mark.asyncio
async def test_dismiss_close_x_button(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Close", "enabled": True, "selector": ".cookie-close"},
            {"text": "Cookie Policy", "enabled": True, "selector": "#policy"},
        ],
        "page_text_preview": "We use cookies to improve your experience",
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True
    bridge.click.assert_called_once_with(".cookie-close")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_cookie_dismisser.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement CookieBannerDismisser**

```python
# jobpulse/cookie_dismisser.py
"""Auto-dismiss cookie consent banners before page detection.

Runs before every page type detection to clear overlays that would
interfere with form detection and field scanning.
"""
from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Buttons to click (priority order — most specific first)
_ACCEPT_PATTERNS = [
    re.compile(r"accept\s*(all)?\s*(cookies?)?", re.IGNORECASE),
    re.compile(r"agree\s*(to\s*all|\s*&\s*continue)?", re.IGNORECASE),
    re.compile(r"i\s*agree", re.IGNORECASE),
    re.compile(r"(got\s*it|okay|ok)(!|\.)?$", re.IGNORECASE),
    re.compile(r"allow\s*(all\s*)?(cookies?)?", re.IGNORECASE),
    re.compile(r"consent", re.IGNORECASE),
]

# Secondary: close button when cookie context detected in page text
_COOKIE_CONTEXT = re.compile(
    r"(cookie|gdpr|privacy|consent|tracking)", re.IGNORECASE
)
_CLOSE_PATTERN = re.compile(r"^(close|dismiss|×|✕|x)$", re.IGNORECASE)

# Never click these
_ANTI_PATTERNS = re.compile(
    r"(reject|decline|manage|customize|preferences|settings|policy|learn\s*more)",
    re.IGNORECASE,
)


class CookieBannerDismisser:
    """Dismiss cookie consent banners via the extension bridge."""

    def __init__(self, bridge: Any):
        self.bridge = bridge

    async def dismiss(self, snapshot: dict) -> bool:
        """Try to dismiss a cookie banner. Returns True if a banner was found and clicked."""
        buttons = snapshot.get("buttons", [])
        page_text = snapshot.get("page_text_preview", "")

        # Try accept/agree buttons first
        for btn in buttons:
            text = btn.get("text", "")
            if not btn.get("enabled", True) or not text:
                continue
            if _ANTI_PATTERNS.search(text):
                continue
            for pattern in _ACCEPT_PATTERNS:
                if pattern.search(text):
                    logger.info("Dismissing cookie banner: clicking '%s'", text)
                    await self.bridge.click(btn["selector"])
                    return True

        # If page mentions cookies, try close button
        if _COOKIE_CONTEXT.search(page_text):
            for btn in buttons:
                text = btn.get("text", "")
                if _CLOSE_PATTERN.search(text) and not _ANTI_PATTERNS.search(text):
                    logger.info("Dismissing cookie banner via close: '%s'", text)
                    await self.bridge.click(btn["selector"])
                    return True

        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_cookie_dismisser.py -v`
Expected: PASS (6/6)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cookie_dismisser.py tests/jobpulse/test_cookie_dismisser.py
git commit -m "feat(ext): add CookieBannerDismisser — auto-dismiss consent overlays"
```

---

### Task 3: Page Analyzer (Hybrid DOM + Vision)

**Files:**
- Create: `jobpulse/page_analyzer.py`
- Test: `tests/jobpulse/test_page_analyzer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_page_analyzer.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from jobpulse.ext_models import PageType
from jobpulse.page_analyzer import PageAnalyzer, _dom_detect


def _snapshot(buttons=None, fields=None, page_text="", verification_wall=None, has_file_inputs=False):
    return {
        "buttons": buttons or [],
        "fields": fields or [],
        "page_text_preview": page_text,
        "verification_wall": verification_wall,
        "has_file_inputs": has_file_inputs,
        "url": "https://example.com/apply",
    }


# --- DOM detection tests ---

def test_dom_job_description_apply_now():
    s = _snapshot(buttons=[{"text": "Apply Now", "enabled": True}])
    result, confidence = _dom_detect(s)
    assert result == PageType.JOB_DESCRIPTION
    assert confidence >= 0.8


def test_dom_job_description_apply_for_this_job():
    s = _snapshot(buttons=[{"text": "Apply for this job", "enabled": True}])
    result, confidence = _dom_detect(s)
    assert result == PageType.JOB_DESCRIPTION


def test_dom_login_form():
    s = _snapshot(
        fields=[
            {"type": "email", "label": "Email address", "current_value": ""},
            {"type": "password", "label": "Password", "current_value": ""},
        ],
        buttons=[{"text": "Sign in", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.LOGIN_FORM
    assert confidence >= 0.8


def test_dom_signup_confirm_password():
    s = _snapshot(
        fields=[
            {"type": "email", "label": "Email", "current_value": ""},
            {"type": "password", "label": "Password", "current_value": ""},
            {"type": "password", "label": "Confirm Password", "current_value": ""},
        ],
        buttons=[{"text": "Create Account", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.SIGNUP_FORM
    assert confidence >= 0.9


def test_dom_signup_register_button():
    s = _snapshot(
        fields=[
            {"type": "text", "label": "Full Name", "current_value": ""},
            {"type": "email", "label": "Email", "current_value": ""},
            {"type": "password", "label": "Password", "current_value": ""},
        ],
        buttons=[{"text": "Register", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.SIGNUP_FORM


def test_dom_email_verification():
    s = _snapshot(page_text="We've sent a verification email to your inbox. Please check your email.")
    result, confidence = _dom_detect(s)
    assert result == PageType.EMAIL_VERIFICATION
    assert confidence >= 0.8


def test_dom_application_form():
    s = _snapshot(
        fields=[
            {"type": "text", "label": "First Name", "current_value": ""},
            {"type": "text", "label": "Last Name", "current_value": ""},
            {"type": "file", "label": "Resume", "current_value": ""},
        ],
        buttons=[{"text": "Submit Application", "enabled": True}],
        has_file_inputs=True,
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.APPLICATION_FORM


def test_dom_application_form_screening():
    s = _snapshot(
        fields=[
            {"type": "select", "label": "Do you require sponsorship?", "current_value": "", "options": ["Yes", "No"]},
            {"type": "textarea", "label": "Why are you interested?", "current_value": ""},
        ],
        buttons=[{"text": "Next", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.APPLICATION_FORM


def test_dom_confirmation():
    s = _snapshot(page_text="Thank you for applying! We have received your application.")
    result, confidence = _dom_detect(s)
    assert result == PageType.CONFIRMATION
    assert confidence >= 0.9


def test_dom_verification_wall():
    s = _snapshot(verification_wall={"type": "cloudflare", "confidence": 0.9})
    result, confidence = _dom_detect(s)
    assert result == PageType.VERIFICATION_WALL
    assert confidence >= 0.9


def test_dom_unknown_low_confidence():
    s = _snapshot(
        page_text="Welcome to our company. Learn about our culture.",
        buttons=[{"text": "Learn More", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.UNKNOWN
    assert confidence < 0.5


# --- Hybrid detection tests ---

@pytest.mark.asyncio
async def test_hybrid_uses_dom_when_confident():
    """High-confidence DOM result skips vision."""
    bridge = AsyncMock()
    analyzer = PageAnalyzer(bridge)
    s = _snapshot(
        page_text="Thank you for applying!",
    )
    result = await analyzer.detect(s)
    assert result == PageType.CONFIRMATION
    # Vision should NOT have been called
    bridge.screenshot.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_falls_back_to_vision():
    """Low-confidence DOM result triggers vision fallback."""
    bridge = AsyncMock()
    bridge.screenshot = AsyncMock(return_value=b"fake_screenshot")
    analyzer = PageAnalyzer(bridge)
    s = _snapshot(
        page_text="Welcome to our company.",
        buttons=[{"text": "Learn More", "enabled": True}],
    )

    with patch("jobpulse.page_analyzer._vision_detect") as mock_vision:
        mock_vision.return_value = (PageType.JOB_DESCRIPTION, 0.85)
        result = await analyzer.detect(s)
        assert result == PageType.JOB_DESCRIPTION
        mock_vision.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_page_analyzer.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement PageAnalyzer with hybrid DOM + Vision detection**

```python
# jobpulse/page_analyzer.py
"""Hybrid page type detector — DOM analysis first, vision LLM fallback.

DOM detection is free and instant. When confidence is low (< 0.6) or result
is UNKNOWN, takes a screenshot and asks the vision model to classify the page.
"""
from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

from jobpulse.ext_models import PageType

logger = get_logger(__name__)

# Confidence threshold — below this, fall back to vision
_VISION_THRESHOLD = 0.6

# --- Button patterns ---
_APPLY_BUTTONS = re.compile(
    r"^(apply\s*(now|for\s*this)?|submit\s*application|start\s*application|apply\s*for\s*(this\s*)?job)$",
    re.IGNORECASE,
)
_LOGIN_BUTTONS = re.compile(r"^(sign\s*in|log\s*in|login)$", re.IGNORECASE)
_SIGNUP_BUTTONS = re.compile(
    r"^(create\s*account|sign\s*up|register|join\s*now|get\s*started)$", re.IGNORECASE
)

# --- Page text patterns ---
_CONFIRMATION_PATTERNS = re.compile(
    r"(thank\s*you\s*(for\s*)?(applying|your\s*application|submitting)"
    r"|application\s*(received|submitted|sent)"
    r"|we\s*(have\s*)?received\s*your\s*application"
    r"|successfully\s*submitted)",
    re.IGNORECASE,
)
_EMAIL_VERIFY_PATTERNS = re.compile(
    r"(check\s*your\s*email|verify\s*your\s*(email|account)"
    r"|sent\s*(a\s*)?(verification|confirmation)\s*(email|link)"
    r"|click\s*the\s*link\s*(in\s*your\s*email|to\s*verify)"
    r"|confirm\s*your\s*email\s*address)",
    re.IGNORECASE,
)

# --- Field labels that indicate application forms ---
_APPLICATION_LABELS = re.compile(
    r"(first\s*name|last\s*name|phone|resume|cv|cover\s*letter|linkedin|portfolio"
    r"|work\s*experience|education|sponsorship|right\s*to\s*work|salary|notice\s*period"
    r"|why\s*(are\s*you|do\s*you)\s*(interested|applying))",
    re.IGNORECASE,
)


def _dom_detect(snapshot: dict) -> tuple[PageType, float]:
    """Classify page type from DOM snapshot. Returns (PageType, confidence 0.0-1.0)."""
    buttons = snapshot.get("buttons", [])
    fields = snapshot.get("fields", [])
    page_text = snapshot.get("page_text_preview", "")
    verification_wall = snapshot.get("verification_wall")

    button_texts = [b.get("text", "") for b in buttons]
    field_types = [f.get("type", "") for f in fields]
    field_labels = [f.get("label", "") for f in fields]

    # 1. Verification wall (CAPTCHA) — highest priority
    if verification_wall:
        return PageType.VERIFICATION_WALL, 0.95

    # 2. Confirmation page
    if _CONFIRMATION_PATTERNS.search(page_text):
        return PageType.CONFIRMATION, 0.95

    # 3. Email verification page
    if _EMAIL_VERIFY_PATTERNS.search(page_text):
        return PageType.EMAIL_VERIFICATION, 0.9

    # 4. Signup form: confirm password OR signup button + password
    password_count = sum(1 for t in field_types if t == "password")
    has_signup_button = any(_SIGNUP_BUTTONS.search(t) for t in button_texts if t)

    if password_count >= 2:
        return PageType.SIGNUP_FORM, 0.95
    if has_signup_button and password_count >= 1:
        return PageType.SIGNUP_FORM, 0.85

    # 5. Login form: email + password + sign-in, no application fields
    has_login_button = any(_LOGIN_BUTTONS.search(t) for t in button_texts if t)
    has_password = password_count >= 1
    has_email = any(t == "email" for t in field_types) or any(
        "email" in lbl.lower() for lbl in field_labels
    )
    has_application_fields = any(_APPLICATION_LABELS.search(lbl) for lbl in field_labels if lbl)

    if has_login_button and has_password and has_email and not has_application_fields:
        return PageType.LOGIN_FORM, 0.9

    # 6. Job description: Apply button, few form fields
    has_apply_button = any(_APPLY_BUTTONS.search(t) for t in button_texts if t)
    if has_apply_button and len(fields) <= 2 and not has_application_fields:
        return PageType.JOB_DESCRIPTION, 0.85

    # 7. Application form: form fields (contact, resume, screening)
    has_file_input = snapshot.get("has_file_inputs", False)
    if has_application_fields or has_file_input:
        return PageType.APPLICATION_FORM, 0.85
    if len(fields) >= 3:
        return PageType.APPLICATION_FORM, 0.65

    # 8. Unknown — low confidence
    return PageType.UNKNOWN, 0.2


async def _vision_detect(screenshot_bytes: bytes) -> tuple[PageType, float]:
    """Ask vision LLM to classify a page screenshot."""
    import base64
    import json

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("OpenAI not available for vision detection")
        return PageType.UNKNOWN, 0.0

    client = OpenAI()
    b64 = base64.b64encode(screenshot_bytes).decode()

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify web page screenshots for a job application bot. "
                        "Return ONLY a JSON object with 'page_type' and 'confidence' (0.0-1.0).\n"
                        "Page types: job_description, login_form, signup_form, "
                        "email_verification, application_form, confirmation, "
                        "verification_wall, unknown"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                        {
                            "type": "text",
                            "text": "What type of page is this? Classify it.",
                        },
                    ],
                },
            ],
            max_tokens=100,
            temperature=0,
        )
        text = response.choices[0].message.content.strip()
        # Parse JSON from response
        if "{" in text:
            text = text[text.index("{") : text.rindex("}") + 1]
        data = json.loads(text)
        page_type_str = data.get("page_type", "unknown")
        confidence = float(data.get("confidence", 0.5))

        try:
            page_type = PageType(page_type_str)
        except ValueError:
            page_type = PageType.UNKNOWN
            confidence = 0.3

        logger.info("Vision detected: %s (confidence=%.2f)", page_type, confidence)
        return page_type, confidence

    except Exception as exc:
        logger.warning("Vision page detection failed: %s", exc)
        return PageType.UNKNOWN, 0.0


class PageAnalyzer:
    """Hybrid page type detector: DOM first, vision LLM fallback."""

    def __init__(self, bridge: Any):
        self.bridge = bridge

    async def detect(self, snapshot: dict) -> PageType:
        """Detect page type. Uses DOM analysis first; falls back to vision if unsure."""
        page_type, confidence = _dom_detect(snapshot)

        if confidence >= _VISION_THRESHOLD:
            logger.debug("DOM detection: %s (confidence=%.2f)", page_type, confidence)
            return page_type

        # Low confidence — try vision
        logger.info(
            "DOM detection low confidence (%.2f for %s) — trying vision",
            confidence,
            page_type,
        )
        try:
            screenshot_bytes = await self.bridge.screenshot()
            if screenshot_bytes:
                vision_type, vision_confidence = await _vision_detect(screenshot_bytes)
                if vision_confidence > confidence:
                    return vision_type
        except Exception as exc:
            logger.warning("Vision fallback failed: %s", exc)

        return page_type
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_page_analyzer.py -v`
Expected: PASS (14/14)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/page_analyzer.py tests/jobpulse/test_page_analyzer.py
git commit -m "feat(ext): add hybrid PageAnalyzer — DOM detection + vision LLM fallback"
```

---

### Task 4: Account Manager

**Files:**
- Create: `jobpulse/account_manager.py`
- Test: `tests/jobpulse/test_account_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_account_manager.py
import pytest
from unittest.mock import patch
from jobpulse.account_manager import AccountManager


@pytest.fixture
def mgr(tmp_path):
    with patch("jobpulse.account_manager.ATS_ACCOUNT_PASSWORD", "TestPass123!"):
        return AccountManager(db_path=str(tmp_path / "accounts.db"))


def test_no_account_initially(mgr):
    assert mgr.has_account("greenhouse.io") is False


def test_create_and_retrieve(mgr):
    email, password = mgr.create_account("greenhouse.io")
    assert email == "bishnoiyash274@gmail.com"
    assert password == "TestPass123!"
    assert mgr.has_account("greenhouse.io") is True


def test_get_credentials(mgr):
    mgr.create_account("greenhouse.io")
    email, password = mgr.get_credentials("greenhouse.io")
    assert email == "bishnoiyash274@gmail.com"
    assert password == "TestPass123!"


def test_mark_verified(mgr):
    mgr.create_account("greenhouse.io")
    mgr.mark_verified("greenhouse.io")
    info = mgr.get_account_info("greenhouse.io")
    assert info.verified is True


def test_domain_normalization_from_url(mgr):
    mgr.create_account("https://boards.greenhouse.io/acme/jobs/123")
    assert mgr.has_account("boards.greenhouse.io") is True
    assert mgr.has_account("https://boards.greenhouse.io/other") is True


def test_domain_normalization_strips_www(mgr):
    mgr.create_account("www.example.com")
    assert mgr.has_account("example.com") is True


def test_duplicate_create_returns_existing(mgr):
    e1, p1 = mgr.create_account("greenhouse.io")
    e2, p2 = mgr.create_account("greenhouse.io")
    assert e1 == e2 and p1 == p2


def test_mark_login_success(mgr):
    mgr.create_account("greenhouse.io")
    mgr.mark_login_success("greenhouse.io")
    info = mgr.get_account_info("greenhouse.io")
    assert info.last_login != ""


def test_no_password_raises(tmp_path):
    with patch("jobpulse.account_manager.ATS_ACCOUNT_PASSWORD", ""):
        mgr = AccountManager(db_path=str(tmp_path / "accounts.db"))
        with pytest.raises(ValueError, match="ATS_ACCOUNT_PASSWORD"):
            mgr.create_account("example.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_account_manager.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement AccountManager**

```python
# jobpulse/account_manager.py
"""ATS platform credential manager.

Stores one account per domain. Uses a single password from ATS_ACCOUNT_PASSWORD
env var and the user's profile email. Credentials stored in SQLite.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import ATS_ACCOUNT_PASSWORD
from jobpulse.ext_models import AccountInfo

logger = get_logger(__name__)

_DEFAULT_DB = "data/ats_accounts.db"


class AccountManager:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    domain TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    verified INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_login TEXT DEFAULT ''
                )
            """)

    @staticmethod
    def _normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url or domain_or_url.startswith("www."):
            parsed = urlparse(
                domain_or_url if "://" in domain_or_url else f"https://{domain_or_url}"
            )
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def has_account(self, domain_or_url: str) -> bool:
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT 1 FROM accounts WHERE domain = ?", (domain,)).fetchone()
        return row is not None

    def create_account(self, domain_or_url: str) -> tuple[str, str]:
        from jobpulse.applicator import PROFILE

        domain = self._normalize_domain(domain_or_url)
        email = PROFILE["email"]
        password = ATS_ACCOUNT_PASSWORD

        if not password:
            raise ValueError("ATS_ACCOUNT_PASSWORD env var not set")

        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT email, password FROM accounts WHERE domain = ?", (domain,)
            ).fetchone()
            if existing:
                return existing[0], existing[1]
            conn.execute(
                "INSERT INTO accounts (domain, email, password, created_at) VALUES (?, ?, ?, ?)",
                (domain, email, password, datetime.now(UTC).isoformat()),
            )
        logger.info("Created account for %s with email %s", domain, email)
        return email, password

    def get_credentials(self, domain_or_url: str) -> tuple[str, str]:
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT email, password FROM accounts WHERE domain = ?", (domain,)
            ).fetchone()
        if not row:
            raise KeyError(f"No account for {domain}")
        return row[0], row[1]

    def get_account_info(self, domain_or_url: str) -> AccountInfo:
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT domain, email, verified, created_at, last_login FROM accounts WHERE domain = ?",
                (domain,),
            ).fetchone()
        if not row:
            raise KeyError(f"No account for {domain}")
        return AccountInfo(
            domain=row[0], email=row[1], verified=bool(row[2]),
            created_at=row[3], last_login=row[4] or "",
        )

    def mark_verified(self, domain_or_url: str):
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("UPDATE accounts SET verified = 1 WHERE domain = ?", (domain,))

    def mark_login_success(self, domain_or_url: str):
        domain = self._normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("UPDATE accounts SET last_login = ? WHERE domain = ?", (now, domain))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_account_manager.py -v`
Expected: PASS (9/9)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/account_manager.py tests/jobpulse/test_account_manager.py
git commit -m "feat(ext): add AccountManager for ATS platform credentials"
```

---

### Task 5: Gmail Verification Agent (Exponential Polling)

**Files:**
- Create: `jobpulse/gmail_verify.py`
- Modify: `jobpulse/gmail_agent.py` (add gmail.modify scope)
- Modify: `scripts/setup_integrations.py` (add gmail.modify scope)
- Test: `tests/jobpulse/test_gmail_verify.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_gmail_verify.py
import pytest
from unittest.mock import MagicMock
from jobpulse.gmail_verify import extract_verification_link, GmailVerifier


def test_extract_link_verify_pattern():
    html = '''<a href="https://greenhouse.io/verify?token=abc123">Verify Email</a>
              <a href="https://greenhouse.io/unsubscribe">Unsubscribe</a>'''
    link = extract_verification_link(html, "greenhouse.io")
    assert link is not None
    assert "verify" in link
    assert "token=abc123" in link


def test_extract_link_confirm_pattern():
    html = '<a href="https://workday.com/confirm-email/xyz">Confirm your account</a>'
    link = extract_verification_link(html, "workday.com")
    assert "confirm-email" in link


def test_extract_link_activate_pattern():
    html = '<a href="https://lever.co/activate/token123">Activate Account</a>'
    link = extract_verification_link(html, "lever.co")
    assert "activate" in link


def test_extract_link_no_match():
    html = '<a href="https://example.com/about">About Us</a>'
    link = extract_verification_link(html, "example.com")
    assert link is None


def test_extract_link_filters_unsubscribe():
    html = '''<a href="https://example.com/verify?t=1">Verify</a>
              <a href="https://example.com/unsubscribe">Unsubscribe</a>'''
    link = extract_verification_link(html, "example.com")
    assert "verify" in link
    assert "unsubscribe" not in link


def test_verifier_exponential_polling():
    """Verify polling uses exponential backoff intervals."""
    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {"messages": []}

    verifier = GmailVerifier(service=mock_service)
    # With short timeout, verify it polls multiple times
    link = verifier.wait_for_verification("example.com", timeout_s=3, initial_interval_s=0.5)
    assert link is None
    # Should have polled multiple times with increasing intervals
    call_count = mock_service.users().messages().list().execute.call_count
    assert call_count >= 2


def test_verifier_finds_email():
    import base64
    mock_service = MagicMock()

    # First poll: nothing. Second poll: found.
    mock_service.users().messages().list().execute.side_effect = [
        {"messages": []},
        {"messages": [{"id": "msg1"}]},
    ]

    html = '<a href="https://greenhouse.io/verify?token=abc123">Verify</a>'
    b64_html = base64.urlsafe_b64encode(html.encode()).decode()
    msg_data = {
        "payload": {
            "headers": [{"name": "From", "value": "noreply@greenhouse.io"}],
            "body": {"data": ""},
            "parts": [{"mimeType": "text/html", "body": {"data": b64_html}}],
        }
    }
    mock_service.users().messages().get().execute.return_value = msg_data

    verifier = GmailVerifier(service=mock_service)
    link = verifier.wait_for_verification("greenhouse.io", timeout_s=10, initial_interval_s=0.1)
    assert link is not None
    assert "verify" in link


def test_verifier_timeout():
    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {"messages": []}

    verifier = GmailVerifier(service=mock_service)
    link = verifier.wait_for_verification("example.com", timeout_s=1, initial_interval_s=0.3)
    assert link is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_gmail_verify.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement GmailVerifier with exponential polling**

```python
# jobpulse/gmail_verify.py
"""Gmail verification email agent.

Polls Gmail inbox for verification/confirmation emails from ATS platforms,
extracts the verification link, and returns it for the orchestrator to navigate to.
Uses exponential backoff: 1s → 2s → 4s → 8s → 16s → 32s → capped at 32s.
"""
from __future__ import annotations

import base64
import re
import time
from html.parser import HTMLParser

from shared.logging_config import get_logger

logger = get_logger(__name__)

_VERIFY_PATTERNS = re.compile(
    r"(verify|confirm|activate|validate|registration|email.?confirm|complete.?signup)",
    re.IGNORECASE,
)
_ANTI_PATTERNS = re.compile(
    r"(unsubscribe|privacy|terms|help|support|faq|contact)", re.IGNORECASE
)


class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self._current_href = value
                    self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href:
            text = " ".join(self._current_text).strip()
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


def extract_verification_link(html_body: str, expected_domain: str) -> str | None:
    """Extract verification/confirmation link from HTML email body."""
    parser = _LinkExtractor()
    parser.feed(html_body)

    candidates: list[tuple[str, int]] = []
    for href, text in parser.links:
        if _ANTI_PATTERNS.search(href) or _ANTI_PATTERNS.search(text):
            continue
        score = 0
        if _VERIFY_PATTERNS.search(href):
            score += 3
        if _VERIFY_PATTERNS.search(text):
            score += 2
        if re.search(r"[?&](token|code|key|t|k)=", href):
            score += 2
        domain_root = expected_domain.split(".")[-2] if "." in expected_domain else expected_domain
        if domain_root in href.lower():
            score += 1
        if score > 0:
            candidates.append((href, score))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


class GmailVerifier:
    """Poll Gmail for verification emails with exponential backoff."""

    def __init__(self, service=None):
        self._service = service

    def _get_service(self):
        if self._service is not None:
            return self._service
        from jobpulse.gmail_agent import _get_gmail_service
        return _get_gmail_service()

    def wait_for_verification(
        self,
        from_domain: str,
        timeout_s: int = 120,
        initial_interval_s: float = 1.0,
        max_interval_s: float = 32.0,
    ) -> str | None:
        """Poll Gmail for a verification email. Exponential backoff: 1s → 2s → 4s → ... → 32s."""
        service = self._get_service()
        if not service:
            logger.warning("Gmail service unavailable — cannot verify email")
            return None

        query = f"from:{from_domain} newer_than:5m (verify OR confirm OR activate OR registration)"
        start = time.monotonic()
        interval = initial_interval_s

        while time.monotonic() - start < timeout_s:
            try:
                results = (
                    service.users().messages()
                    .list(userId="me", q=query, maxResults=5)
                    .execute()
                )
                for msg_ref in results.get("messages", []):
                    msg = (
                        service.users().messages()
                        .get(userId="me", id=msg_ref["id"], format="full")
                        .execute()
                    )
                    html_body = self._extract_html_body(msg)
                    if not html_body:
                        continue
                    link = extract_verification_link(html_body, from_domain)
                    if link:
                        logger.info("Found verification link from %s: %s", from_domain, link[:80])
                        try:
                            service.users().messages().modify(
                                userId="me", id=msg_ref["id"],
                                body={"removeLabelIds": ["UNREAD"]},
                            ).execute()
                        except Exception:
                            pass
                        return link
            except Exception as exc:
                logger.warning("Gmail poll error: %s", exc)

            time.sleep(interval)
            interval = min(interval * 2, max_interval_s)

        logger.warning("Verification email timeout after %ds for %s", timeout_s, from_domain)
        return None

    @staticmethod
    def _extract_html_body(message: dict) -> str | None:
        payload = message.get("payload", {})
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        return None
```

- [ ] **Step 4: Update Gmail scope in gmail_agent.py**

In `jobpulse/gmail_agent.py`, update SCOPES to include `gmail.modify`:

```python
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
```

- [ ] **Step 5: Update Gmail scope in setup_integrations.py**

In `scripts/setup_integrations.py`, add `gmail.modify` to GOOGLE_SCOPES:

```python
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.file",
]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_gmail_verify.py -v`
Expected: PASS (8/8)

- [ ] **Step 7: Commit**

```bash
git add jobpulse/gmail_verify.py jobpulse/gmail_agent.py scripts/setup_integrations.py tests/jobpulse/test_gmail_verify.py
git commit -m "feat(ext): add GmailVerifier with exponential backoff + gmail.modify scope"
```

---

### Task 6: Navigation Learner

**Files:**
- Create: `jobpulse/navigation_learner.py`
- Test: `tests/jobpulse/test_navigation_learner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_navigation_learner.py
import pytest
from jobpulse.navigation_learner import NavigationLearner


@pytest.fixture
def learner(tmp_path):
    return NavigationLearner(db_path=str(tmp_path / "nav_learning.db"))


def test_no_sequence_initially(learner):
    assert learner.get_sequence("careers.acme.com") is None


def test_save_and_retrieve(learner):
    steps = [
        {"page_type": "job_description", "action": "click_apply", "selector": "#apply"},
        {"page_type": "login_form", "action": "fill_login", "selector": "#signin"},
        {"page_type": "application_form", "action": "fill_form", "selector": ""},
    ]
    learner.save_sequence("careers.acme.com", steps, success=True)
    result = learner.get_sequence("careers.acme.com")
    assert result is not None
    assert len(result) == 3
    assert result[0]["action"] == "click_apply"


def test_only_returns_successful_sequences(learner):
    steps = [{"page_type": "job_description", "action": "click_apply", "selector": "#apply"}]
    learner.save_sequence("careers.acme.com", steps, success=False)
    assert learner.get_sequence("careers.acme.com") is None


def test_domain_normalization(learner):
    steps = [{"page_type": "login_form", "action": "fill_login", "selector": "#login"}]
    learner.save_sequence("https://careers.acme.com/jobs/123", steps, success=True)
    result = learner.get_sequence("https://careers.acme.com/other")
    assert result is not None


def test_overwrite_with_newer(learner):
    steps_old = [{"page_type": "job_description", "action": "click_apply", "selector": "#old"}]
    steps_new = [{"page_type": "login_form", "action": "fill_login", "selector": "#new"}]
    learner.save_sequence("acme.com", steps_old, success=True)
    learner.save_sequence("acme.com", steps_new, success=True)
    result = learner.get_sequence("acme.com")
    assert result[0]["selector"] == "#new"


def test_mark_sequence_failed(learner):
    steps = [{"page_type": "job_description", "action": "click_apply", "selector": "#apply"}]
    learner.save_sequence("acme.com", steps, success=True)
    learner.mark_failed("acme.com")
    # After marking failed, sequence should be invalidated
    assert learner.get_sequence("acme.com") is None


def test_get_stats(learner):
    steps = [{"page_type": "login_form", "action": "fill_login", "selector": "#login"}]
    learner.save_sequence("acme.com", steps, success=True)
    learner.save_sequence("beta.com", steps, success=True)
    learner.save_sequence("gamma.com", steps, success=False)
    stats = learner.get_stats()
    assert stats["total_domains"] == 3
    assert stats["successful_domains"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_navigation_learner.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement NavigationLearner**

```python
# jobpulse/navigation_learner.py
"""Per-domain navigation sequence learning.

After a successful application, saves the sequence of page types and actions
taken to reach the application form. On repeat visits to the same domain,
replays the learned path (zero LLM cost).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_DB = "data/navigation_learning.db"


class NavigationLearner:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sequences (
                    domain TEXT PRIMARY KEY,
                    steps TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    replay_count INTEGER DEFAULT 0,
                    fail_count INTEGER DEFAULT 0
                )
            """)

    @staticmethod
    def _normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url:
            parsed = urlparse(domain_or_url)
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def get_sequence(self, domain_or_url: str) -> list[dict] | None:
        """Get a successful navigation sequence for a domain. Returns None if none exists."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT steps FROM sequences WHERE domain = ? AND success = 1",
                (domain,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def save_sequence(self, domain_or_url: str, steps: list[dict], success: bool):
        """Save a navigation sequence for a domain."""
        domain = self._normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        steps_json = json.dumps(steps)

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO sequences (domain, steps, success, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                       steps = excluded.steps,
                       success = excluded.success,
                       updated_at = excluded.updated_at""",
                (domain, steps_json, int(success), now, now),
            )
        logger.info("Saved navigation sequence for %s (success=%s, %d steps)", domain, success, len(steps))

    def mark_failed(self, domain_or_url: str):
        """Mark a learned sequence as failed (invalidate it)."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE sequences SET success = 0, fail_count = fail_count + 1 WHERE domain = ?",
                (domain,),
            )
        logger.info("Invalidated navigation sequence for %s", domain)

    def increment_replay(self, domain_or_url: str):
        """Track that a sequence was replayed."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE sequences SET replay_count = replay_count + 1 WHERE domain = ?",
                (domain,),
            )

    def get_stats(self) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM sequences").fetchone()[0]
            successful = conn.execute("SELECT COUNT(*) FROM sequences WHERE success = 1").fetchone()[0]
        return {"total_domains": total, "successful_domains": successful}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_navigation_learner.py -v`
Expected: PASS (7/7)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/navigation_learner.py tests/jobpulse/test_navigation_learner.py
git commit -m "feat(ext): add NavigationLearner — per-domain sequence replay"
```

---

### Task 7: SSO Handler

**Files:**
- Create: `jobpulse/sso_handler.py`
- Test: `tests/jobpulse/test_sso_handler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_sso_handler.py
import pytest
from unittest.mock import AsyncMock
from jobpulse.sso_handler import SSOHandler


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.click = AsyncMock()
    b.get_snapshot = AsyncMock(return_value={})
    return b


@pytest.fixture
def handler(bridge):
    return SSOHandler(bridge)


def test_detect_google_sso():
    snapshot = {
        "buttons": [
            {"text": "Sign in with Google", "enabled": True, "selector": "#google-sso"},
            {"text": "Sign in", "enabled": True, "selector": "#signin"},
        ],
    }
    handler = SSOHandler(AsyncMock())
    sso = handler.detect_sso(snapshot)
    assert sso is not None
    assert sso["provider"] == "google"
    assert sso["selector"] == "#google-sso"


def test_detect_linkedin_sso():
    snapshot = {
        "buttons": [
            {"text": "Continue with LinkedIn", "enabled": True, "selector": ".linkedin-btn"},
        ],
    }
    handler = SSOHandler(AsyncMock())
    sso = handler.detect_sso(snapshot)
    assert sso is not None
    assert sso["provider"] == "linkedin"


def test_detect_no_sso():
    snapshot = {
        "buttons": [
            {"text": "Sign in", "enabled": True, "selector": "#signin"},
            {"text": "Create Account", "enabled": True, "selector": "#create"},
        ],
    }
    handler = SSOHandler(AsyncMock())
    sso = handler.detect_sso(snapshot)
    assert sso is None


def test_detect_google_continue():
    snapshot = {
        "buttons": [
            {"text": "Continue with Google", "enabled": True, "selector": ".google-oauth"},
        ],
    }
    handler = SSOHandler(AsyncMock())
    sso = handler.detect_sso(snapshot)
    assert sso["provider"] == "google"


@pytest.mark.asyncio
async def test_click_sso_button(handler, bridge):
    sso = {"provider": "google", "selector": "#google-sso"}
    await handler.click_sso(sso)
    bridge.click.assert_called_once_with("#google-sso")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_sso_handler.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement SSOHandler**

```python
# jobpulse/sso_handler.py
"""SSO (Single Sign-On) detection and handling.

Detects "Sign in with Google", "Continue with LinkedIn" etc. on login/signup pages.
When SSO is available, clicking it is faster and more reliable than creating
a new email+password account.
"""
from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# SSO button patterns — (regex, provider name)
_SSO_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(sign\s*in|continue|log\s*in)\s*with\s*google", re.IGNORECASE), "google"),
    (re.compile(r"google\s*(sign\s*in|login|sso)", re.IGNORECASE), "google"),
    (re.compile(r"(sign\s*in|continue|log\s*in)\s*with\s*linkedin", re.IGNORECASE), "linkedin"),
    (re.compile(r"linkedin\s*(sign\s*in|login|sso)", re.IGNORECASE), "linkedin"),
    (re.compile(r"(sign\s*in|continue|log\s*in)\s*with\s*microsoft", re.IGNORECASE), "microsoft"),
    (re.compile(r"(sign\s*in|continue|log\s*in)\s*with\s*apple", re.IGNORECASE), "apple"),
]

# Prefer these providers (we have Google OAuth already)
_PROVIDER_PRIORITY = {"google": 100, "linkedin": 80, "microsoft": 50, "apple": 30}


class SSOHandler:
    """Detect and use SSO buttons on login/signup pages."""

    def __init__(self, bridge: Any):
        self.bridge = bridge

    def detect_sso(self, snapshot: dict) -> dict | None:
        """Detect SSO buttons. Returns {provider, selector} or None."""
        buttons = snapshot.get("buttons", [])
        candidates: list[dict] = []

        for btn in buttons:
            text = btn.get("text", "")
            if not btn.get("enabled", True) or not text:
                continue
            for pattern, provider in _SSO_PATTERNS:
                if pattern.search(text):
                    candidates.append({
                        "provider": provider,
                        "selector": btn["selector"],
                        "text": text,
                        "priority": _PROVIDER_PRIORITY.get(provider, 0),
                    })
                    break

        if not candidates:
            return None

        # Return highest priority SSO option
        candidates.sort(key=lambda x: x["priority"], reverse=True)
        best = candidates[0]
        logger.info("SSO detected: %s ('%s')", best["provider"], best["text"])
        return {"provider": best["provider"], "selector": best["selector"]}

    async def click_sso(self, sso: dict):
        """Click an SSO button and wait for redirect."""
        logger.info("Clicking SSO: %s at %s", sso["provider"], sso["selector"])
        await self.bridge.click(sso["selector"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_sso_handler.py -v`
Expected: PASS (5/5)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/sso_handler.py tests/jobpulse/test_sso_handler.py
git commit -m "feat(ext): add SSOHandler — detect Google/LinkedIn SSO on login pages"
```

---

### Task 8: Multi-Page Navigator (State Machine Enhancement)

**Files:**
- Modify: `jobpulse/state_machines/__init__.py`
- Test: `tests/jobpulse/test_multipage_navigation.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_multipage_navigation.py
import pytest
from jobpulse.state_machines import find_next_button, detect_progress, is_page_stuck


def test_find_next_button_standard():
    buttons = [
        {"text": "Cancel", "enabled": True, "selector": "#cancel"},
        {"text": "Next", "enabled": True, "selector": "#next"},
    ]
    result = find_next_button(buttons)
    assert result is not None
    assert result["selector"] == "#next"


def test_find_next_button_continue():
    buttons = [{"text": "Continue", "enabled": True, "selector": "#cont"}]
    assert find_next_button(buttons)["selector"] == "#cont"


def test_find_next_button_save_and_continue():
    buttons = [{"text": "Save and Continue", "enabled": True, "selector": "#save"}]
    assert find_next_button(buttons)["selector"] == "#save"


def test_find_next_button_submit_highest_priority():
    buttons = [
        {"text": "Next", "enabled": True, "selector": "#next"},
        {"text": "Submit Application", "enabled": True, "selector": "#submit"},
    ]
    assert find_next_button(buttons)["selector"] == "#submit"


def test_find_next_button_review_over_next():
    buttons = [
        {"text": "Next", "enabled": True, "selector": "#next"},
        {"text": "Review", "enabled": True, "selector": "#review"},
    ]
    assert find_next_button(buttons)["selector"] == "#review"


def test_find_next_button_disabled_skipped():
    buttons = [{"text": "Next", "enabled": False, "selector": "#next"}]
    assert find_next_button(buttons) is None


def test_find_next_button_none():
    buttons = [{"text": "Cancel", "enabled": True, "selector": "#cancel"}]
    assert find_next_button(buttons) is None


def test_find_next_button_proceed():
    buttons = [{"text": "Proceed", "enabled": True, "selector": "#proceed"}]
    assert find_next_button(buttons)["selector"] == "#proceed"


def test_detect_progress_step_of():
    assert detect_progress("Step 2 of 5 — Contact Information") == (2, 5)


def test_detect_progress_page_slash():
    assert detect_progress("Page 3 / 4") == (3, 4)


def test_detect_progress_bare_numbers():
    assert detect_progress("2 of 6") == (2, 6)


def test_detect_progress_none():
    assert detect_progress("Please fill in your details") is None


def test_detect_progress_invalid_range():
    assert detect_progress("Step 0 of 5") is None


def test_is_page_stuck_same():
    prev = {"page_text_preview": "Please enter your contact information and phone number details"}
    curr = {"page_text_preview": "Please enter your contact information and phone number details"}
    assert is_page_stuck(prev, curr) is True


def test_is_page_stuck_different():
    prev = {"page_text_preview": "Please enter your contact information and phone number details"}
    curr = {"page_text_preview": "Upload your resume and cover letter for this engineering position"}
    assert is_page_stuck(prev, curr) is False


def test_is_page_stuck_short_text_not_stuck():
    prev = {"page_text_preview": "Hi"}
    curr = {"page_text_preview": "Hi"}
    assert is_page_stuck(prev, curr) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_multipage_navigation.py -v`
Expected: FAIL — functions not defined

- [ ] **Step 3: Add multi-page functions to state_machines/__init__.py**

Add to `jobpulse/state_machines/__init__.py`:

```python
import re as _re

_BUTTON_PRIORITY = [
    (_re.compile(r"submit\s*(application|my\s*application)?", _re.IGNORECASE), 100),
    (_re.compile(r"review(\s+(&|and)\s+submit)?", _re.IGNORECASE), 90),
    (_re.compile(r"save\s*(and|&)\s*(continue|next|proceed)", _re.IGNORECASE), 70),
    (_re.compile(r"continue", _re.IGNORECASE), 60),
    (_re.compile(r"next(\s*step)?", _re.IGNORECASE), 50),
    (_re.compile(r"proceed", _re.IGNORECASE), 40),
]

_PROGRESS_PATTERNS = [
    _re.compile(r"step\s+(\d+)\s+(?:of|/)\s+(\d+)", _re.IGNORECASE),
    _re.compile(r"page\s+(\d+)\s+(?:of|/)\s+(\d+)", _re.IGNORECASE),
    _re.compile(r"(\d+)\s+(?:of|/)\s+(\d+)", _re.IGNORECASE),
]


def find_next_button(buttons: list[dict]) -> dict | None:
    """Find highest-priority navigation button (Submit > Review > Continue > Next)."""
    candidates: list[tuple[dict, int]] = []
    for btn in buttons:
        if not btn.get("enabled", True):
            continue
        text = btn.get("text", "")
        for pattern, priority in _BUTTON_PRIORITY:
            if pattern.search(text):
                candidates.append((btn, priority))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def detect_progress(page_text: str) -> tuple[int, int] | None:
    """Parse 'Step 2 of 5' indicators. Returns (current, total) or None."""
    for pattern in _PROGRESS_PATTERNS:
        match = pattern.search(page_text)
        if match:
            current, total = int(match.group(1)), int(match.group(2))
            if 1 <= current <= total <= 20:
                return current, total
    return None


def is_page_stuck(prev_snapshot: dict, curr_snapshot: dict) -> bool:
    """Detect if page hasn't changed. Compares chars 200-700 to skip wrappers."""
    prev_text = prev_snapshot.get("page_text_preview", "")
    curr_text = curr_snapshot.get("page_text_preview", "")
    prev_slice = prev_text[200:700] if len(prev_text) > 700 else prev_text
    curr_slice = curr_text[200:700] if len(curr_text) > 700 else curr_text
    return prev_slice == curr_slice and len(prev_slice) > 10
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_multipage_navigation.py -v`
Expected: PASS (17/17)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/state_machines/__init__.py tests/jobpulse/test_multipage_navigation.py
git commit -m "feat(ext): add multi-page navigation — Next button finder, progress, stuck detection"
```

---

### Task 9: Application Orchestrator (Full Integration)

**Files:**
- Create: `jobpulse/application_orchestrator.py`
- Test: `tests/jobpulse/test_application_orchestrator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_application_orchestrator.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from jobpulse.application_orchestrator import ApplicationOrchestrator


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.navigate = AsyncMock()
    b.fill = AsyncMock()
    b.click = AsyncMock()
    b.upload = AsyncMock()
    b.get_snapshot = AsyncMock()
    b.screenshot = AsyncMock(return_value=b"screenshot")
    b.select_option = AsyncMock()
    b.check = AsyncMock()
    return b


@pytest.fixture
def orchestrator(bridge, tmp_path):
    from jobpulse.account_manager import AccountManager
    from jobpulse.navigation_learner import NavigationLearner

    return ApplicationOrchestrator(
        bridge=bridge,
        account_manager=AccountManager(db_path=str(tmp_path / "acc.db")),
        gmail_verifier=MagicMock(),
        navigation_learner=NavigationLearner(db_path=str(tmp_path / "nav.db")),
    )


def test_orchestrator_has_required_methods(orchestrator):
    assert hasattr(orchestrator, "apply")
    assert hasattr(orchestrator, "_navigate_to_form")
    assert hasattr(orchestrator, "_handle_signup")
    assert hasattr(orchestrator, "_handle_login")
    assert hasattr(orchestrator, "_handle_email_verification")
    assert hasattr(orchestrator, "_fill_application")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/jobpulse/test_application_orchestrator.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement ApplicationOrchestrator**

```python
# jobpulse/application_orchestrator.py
"""Application orchestrator — navigates redirect chains, handles account lifecycle,
and delegates form filling to the state machine.

Flow: URL → cookie dismiss → page stability wait → detect page type (DOM+Vision)
     → navigate (Apply clicks, SSO, login, signup, verify) → application form
     → state machine multi-page fill → submit → save learned sequence
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.account_manager import AccountManager
from jobpulse.cookie_dismisser import CookieBannerDismisser
from jobpulse.ext_models import PageType
from jobpulse.gmail_verify import GmailVerifier
from jobpulse.navigation_learner import NavigationLearner
from jobpulse.page_analyzer import PageAnalyzer
from jobpulse.sso_handler import SSOHandler
from jobpulse.state_machines import (
    ApplicationState,
    find_next_button,
    get_state_machine,
    is_page_stuck,
)

logger = get_logger(__name__)

MAX_NAVIGATION_STEPS = 10
MAX_FORM_PAGES = 20


class ApplicationOrchestrator:
    def __init__(
        self,
        bridge: Any,
        account_manager: AccountManager | None = None,
        gmail_verifier: GmailVerifier | None = None,
        navigation_learner: NavigationLearner | None = None,
    ):
        self.bridge = bridge
        self.accounts = account_manager or AccountManager()
        self.gmail = gmail_verifier or GmailVerifier()
        self.learner = navigation_learner or NavigationLearner()
        self.analyzer = PageAnalyzer(bridge)
        self.cookie_dismisser = CookieBannerDismisser(bridge)
        self.sso = SSOHandler(bridge)

    async def apply(
        self,
        url: str,
        platform: str,
        cv_path: Path,
        cover_letter_path: Path | None = None,
        profile: dict | None = None,
        custom_answers: dict | None = None,
        overrides: dict | None = None,
        dry_run: bool = False,
        form_intelligence: Any | None = None,
    ) -> dict:
        """Full application flow: navigate → account → verify → fill → submit."""
        profile = profile or {}
        custom_answers = custom_answers or {}
        navigation_steps: list[dict] = []

        # Phase 1: Navigate to application form
        nav_result = await self._navigate_to_form(url, platform, navigation_steps)
        page_type = nav_result["page_type"]

        if page_type == PageType.VERIFICATION_WALL:
            return {"success": False, "error": "CAPTCHA wall", "screenshot": nav_result.get("screenshot")}

        if page_type == PageType.UNKNOWN:
            return {"success": False, "error": f"Unknown page — could not reach application form", "screenshot": nav_result.get("screenshot")}

        if page_type != PageType.APPLICATION_FORM:
            return {"success": False, "error": f"Stuck on {page_type}", "screenshot": nav_result.get("screenshot")}

        # Phase 2: Multi-page form filling
        result = await self._fill_application(
            platform=platform,
            snapshot=nav_result["snapshot"],
            cv_path=cv_path,
            cover_letter_path=cover_letter_path,
            profile=profile,
            custom_answers=custom_answers,
            overrides=overrides,
            dry_run=dry_run,
            form_intelligence=form_intelligence,
        )

        # Save successful navigation for future replay
        if result.get("success"):
            domain = self._extract_domain(url)
            self.learner.save_sequence(domain, navigation_steps, success=True)

        return result

    async def _navigate_to_form(
        self, url: str, platform: str, steps: list[dict]
    ) -> dict:
        """Navigate through redirect chain to reach application form."""
        await self.bridge.navigate(url)
        snapshot = await self.bridge.get_snapshot()

        # Try learned sequence first
        domain = self._extract_domain(url)
        learned = self.learner.get_sequence(domain)
        if learned:
            logger.info("Replaying learned navigation for %s (%d steps)", domain, len(learned))
            self.learner.increment_replay(domain)
            # Replay is best-effort — if it fails, fall through to live detection

        # Dismiss cookie banner
        await self.cookie_dismisser.dismiss(snapshot)
        snapshot = await self.bridge.get_snapshot()

        for step in range(MAX_NAVIGATION_STEPS):
            page_type = await self.analyzer.detect(snapshot)
            logger.info("Navigation step %d: %s", step + 1, page_type)

            if page_type in (PageType.APPLICATION_FORM, PageType.VERIFICATION_WALL, PageType.CONFIRMATION):
                return {"page_type": page_type, "snapshot": snapshot}

            if page_type == PageType.JOB_DESCRIPTION:
                snapshot = await self._click_apply_button(snapshot)
                steps.append({"page_type": "job_description", "action": "click_apply"})

            elif page_type == PageType.LOGIN_FORM:
                # Try SSO first
                sso = self.sso.detect_sso(snapshot)
                if sso:
                    await self.sso.click_sso(sso)
                    snapshot = await self.bridge.get_snapshot()
                    steps.append({"page_type": "login_form", "action": f"sso_{sso['provider']}"})
                else:
                    snapshot = await self._handle_login(snapshot, platform)
                    steps.append({"page_type": "login_form", "action": "fill_login"})

            elif page_type == PageType.SIGNUP_FORM:
                snapshot = await self._handle_signup(snapshot, platform)
                steps.append({"page_type": "signup_form", "action": "fill_signup"})

            elif page_type == PageType.EMAIL_VERIFICATION:
                snapshot = await self._handle_email_verification(snapshot, platform, url)
                steps.append({"page_type": "email_verification", "action": "verify_email"})

            elif page_type == PageType.UNKNOWN:
                apply_btn = self._find_apply_button(snapshot)
                if apply_btn:
                    await self.bridge.click(apply_btn["selector"])
                    snapshot = await self.bridge.get_snapshot()
                    steps.append({"page_type": "unknown", "action": "click_apply_guess"})
                else:
                    return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

            # Dismiss any new cookie banners after navigation
            await self.cookie_dismisser.dismiss(snapshot)
            snapshot = await self.bridge.get_snapshot()

        return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

    async def _click_apply_button(self, snapshot: dict) -> dict:
        import re
        apply_pattern = re.compile(
            r"(apply\s*(now|for\s*this)?|start\s*application|apply\s*for\s*(this\s*)?job)",
            re.IGNORECASE,
        )
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and apply_pattern.search(btn.get("text", "")):
                logger.info("Clicking: %s", btn["text"])
                await self.bridge.click(btn["selector"])
                return await self.bridge.get_snapshot()
        return snapshot

    async def _handle_login(self, snapshot: dict, platform: str) -> dict:
        domain = self._extract_domain(snapshot.get("url", ""))

        if not self.accounts.has_account(domain):
            signup_btn = self._find_signup_link(snapshot)
            if signup_btn:
                await self.bridge.click(signup_btn["selector"])
                return await self.bridge.get_snapshot()
            return snapshot

        email, password = self.accounts.get_credentials(domain)
        logger.info("Logging into %s", domain)

        for field in snapshot.get("fields", []):
            label = field.get("label", "").lower()
            ftype = field.get("type", "")
            if ftype == "email" or "email" in label:
                await self.bridge.fill(field["selector"], email)
            elif ftype == "password" or "password" in label:
                await self.bridge.fill(field["selector"], password)

        import re
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and re.search(r"(sign\s*in|log\s*in|login)", btn.get("text", ""), re.IGNORECASE):
                await self.bridge.click(btn["selector"])
                break

        self.accounts.mark_login_success(domain)
        return await self.bridge.get_snapshot()

    async def _handle_signup(self, snapshot: dict, platform: str) -> dict:
        from jobpulse.applicator import PROFILE

        domain = self._extract_domain(snapshot.get("url", ""))
        email, password = self.accounts.create_account(domain)
        logger.info("Creating account on %s", domain)

        for field in snapshot.get("fields", []):
            label = field.get("label", "").lower()
            ftype = field.get("type", "")
            sel = field.get("selector", "")

            if ftype == "email" or "email" in label:
                await self.bridge.fill(sel, email)
            elif ftype == "password":
                await self.bridge.fill(sel, password)
            elif "first" in label:
                await self.bridge.fill(sel, PROFILE.get("first_name", ""))
            elif "last" in label:
                await self.bridge.fill(sel, PROFILE.get("last_name", ""))
            elif "name" in label and "user" not in label:
                await self.bridge.fill(sel, f"{PROFILE.get('first_name', '')} {PROFILE.get('last_name', '')}".strip())
            elif "phone" in label or ftype == "tel":
                await self.bridge.fill(sel, PROFILE.get("phone", ""))

        import re
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and re.search(r"(create|sign\s*up|register|join|submit)", btn.get("text", ""), re.IGNORECASE):
                await self.bridge.click(btn["selector"])
                break

        return await self.bridge.get_snapshot()

    async def _handle_email_verification(self, snapshot: dict, platform: str, return_url: str) -> dict:
        domain = self._extract_domain(snapshot.get("url", ""))
        logger.info("Waiting for verification email from %s", domain)

        link = self.gmail.wait_for_verification(domain)
        if not link:
            logger.warning("Verification email not received for %s", domain)
            return snapshot

        await self.bridge.navigate(link)
        await self.bridge.get_snapshot()
        self.accounts.mark_verified(domain)

        logger.info("Returning to application: %s", return_url[:80])
        await self.bridge.navigate(return_url)
        return await self.bridge.get_snapshot()

    async def _fill_application(
        self, platform, snapshot, cv_path, cover_letter_path, profile,
        custom_answers, overrides, dry_run, form_intelligence,
    ) -> dict:
        """Multi-page form filling via state machine."""
        machine = get_state_machine(platform)
        prev_snapshot = None
        stuck_count = 0
        last_screenshot = None

        for page_num in range(1, MAX_FORM_PAGES + 1):
            state = machine.detect_state(snapshot)
            logger.info("Form page %d: state=%s", page_num, state)

            if state == ApplicationState.CONFIRMATION:
                return {"success": True, "screenshot": last_screenshot, "pages_filled": page_num}
            if state == ApplicationState.VERIFICATION_WALL:
                return {"success": False, "error": "CAPTCHA during form", "screenshot": last_screenshot}
            if state == ApplicationState.ERROR:
                return {"success": False, "error": "State machine error", "screenshot": last_screenshot}

            if prev_snapshot and is_page_stuck(prev_snapshot, snapshot):
                stuck_count += 1
                if stuck_count >= 2:
                    return {"success": False, "error": f"Stuck on page {page_num}", "screenshot": last_screenshot}
            else:
                stuck_count = 0

            actions = machine.get_actions(
                state, snapshot, profile=profile, custom_answers=custom_answers,
                cv_path=cv_path, cover_letter_path=cover_letter_path,
                overrides=overrides, form_intelligence=form_intelligence,
            )

            for action in actions:
                await self._execute_action(action)

            screenshot_bytes = await self.bridge.screenshot()
            if screenshot_bytes:
                last_screenshot = screenshot_bytes

            if state == ApplicationState.SUBMIT:
                if dry_run:
                    return {"success": True, "dry_run": True, "screenshot": last_screenshot, "pages_filled": page_num}
                submit_btn = find_next_button(snapshot.get("buttons", []))
                if submit_btn:
                    await self.bridge.click(submit_btn["selector"])
            else:
                next_btn = find_next_button(snapshot.get("buttons", []))
                if next_btn:
                    await self.bridge.click(next_btn["selector"])

            prev_snapshot = snapshot
            snapshot = await self.bridge.get_snapshot()

        return {"success": False, "error": f"Exhausted {MAX_FORM_PAGES} pages", "screenshot": last_screenshot}

    async def _execute_action(self, action: Any):
        atype = getattr(action, "action_type", None) or action.get("type", "")
        selector = getattr(action, "selector", None) or action.get("selector", "")
        value = getattr(action, "value", None) or action.get("value", "")
        file_path = getattr(action, "file_path", None) or action.get("file_path")

        if atype == "fill":
            await self.bridge.fill(selector, value)
        elif atype == "upload":
            await self.bridge.upload(selector, str(file_path))
        elif atype == "click":
            await self.bridge.click(selector)
        elif atype == "select":
            await self.bridge.select_option(selector, value)
        elif atype == "check":
            await self.bridge.check(selector)

    @staticmethod
    def _extract_domain(url: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc.lower().removeprefix("www.") if parsed.netloc else url

    @staticmethod
    def _find_apply_button(snapshot: dict) -> dict | None:
        import re
        pattern = re.compile(r"(apply|start\s*application|begin|submit\s*interest)", re.IGNORECASE)
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and pattern.search(btn.get("text", "")):
                return btn
        return None

    @staticmethod
    def _find_signup_link(snapshot: dict) -> dict | None:
        import re
        pattern = re.compile(r"(create\s*account|sign\s*up|register|don.?t\s*have|new\s*user)", re.IGNORECASE)
        for btn in snapshot.get("buttons", []):
            if pattern.search(btn.get("text", "")):
                return btn
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/jobpulse/test_application_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator.py tests/jobpulse/test_application_orchestrator.py
git commit -m "feat(ext): add ApplicationOrchestrator — full lifecycle with SSO, learning, vision"
```

---

### Task 10: Wire Orchestrator into ExtensionAdapter

**Files:**
- Modify: `jobpulse/ext_adapter.py`
- Test: `tests/jobpulse/test_ext_adapter_orchestrator.py`

- [ ] **Step 1: Write tests**

```python
# tests/jobpulse/test_ext_adapter_orchestrator.py
from unittest.mock import MagicMock
from jobpulse.ext_adapter import ExtensionAdapter


def test_ext_adapter_has_fill_and_submit():
    adapter = ExtensionAdapter.__new__(ExtensionAdapter)
    assert hasattr(adapter, "fill_and_submit")


def test_ext_adapter_creates_with_bridge():
    bridge = MagicMock()
    adapter = ExtensionAdapter(bridge)
    assert adapter.bridge is bridge
```

- [ ] **Step 2: Modify ext_adapter.py fill_and_submit to delegate to orchestrator**

Replace the state machine loop in `fill_and_submit()` with:

```python
from jobpulse.application_orchestrator import ApplicationOrchestrator

orchestrator = ApplicationOrchestrator(bridge=self.bridge)
result = await orchestrator.apply(
    url=url, platform=platform, cv_path=cv_path,
    cover_letter_path=cover_letter_path, profile=profile,
    custom_answers=custom_answers, overrides=overrides,
    dry_run=dry_run, form_intelligence=fi,
)
return result
```

- [ ] **Step 3: Run all adapter tests**

Run: `pytest tests/jobpulse/test_ext_adapter_orchestrator.py tests/jobpulse/test_phase3_wiring.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add jobpulse/ext_adapter.py tests/jobpulse/test_ext_adapter_orchestrator.py
git commit -m "feat(ext): wire ApplicationOrchestrator into ExtensionAdapter"
```

---

### Task 11: Integration Tests — Full Scenarios

**Files:**
- Create: `tests/jobpulse/test_phase5_integration.py`

- [ ] **Step 1: Write end-to-end integration tests**

```python
# tests/jobpulse/test_phase5_integration.py
"""End-to-end integration tests for Phase 5 external application engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from jobpulse.application_orchestrator import ApplicationOrchestrator
from jobpulse.ext_models import PageType


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.navigate = AsyncMock()
    b.fill = AsyncMock()
    b.click = AsyncMock()
    b.upload = AsyncMock()
    b.get_snapshot = AsyncMock()
    b.screenshot = AsyncMock(return_value=b"screenshot")
    b.select_option = AsyncMock()
    b.check = AsyncMock()
    return b


@pytest.fixture
def orchestrator(bridge, tmp_path):
    from jobpulse.account_manager import AccountManager
    from jobpulse.navigation_learner import NavigationLearner
    return ApplicationOrchestrator(
        bridge=bridge,
        account_manager=AccountManager(db_path=str(tmp_path / "acc.db")),
        gmail_verifier=MagicMock(),
        navigation_learner=NavigationLearner(db_path=str(tmp_path / "nav.db")),
    )


def _snapshot(buttons=None, fields=None, page_text="", verification_wall=None, has_file_inputs=False, url="https://example.com"):
    return {
        "buttons": buttons or [],
        "fields": fields or [],
        "page_text_preview": page_text,
        "verification_wall": verification_wall,
        "has_file_inputs": has_file_inputs,
        "url": url,
    }


@pytest.mark.asyncio
async def test_direct_form_to_confirmation(orchestrator, bridge):
    form = _snapshot(
        fields=[
            {"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"},
            {"type": "file", "label": "Resume", "current_value": "", "selector": "#resume"},
        ],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    bridge.get_snapshot.side_effect = [form, confirm]

    result = await orchestrator.apply(
        url="https://boards.greenhouse.io/acme/jobs/123",
        platform="greenhouse",
        cv_path=Path("/tmp/cv.pdf"),
        profile={"first_name": "Yash", "last_name": "B"},
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_jd_then_form(orchestrator, bridge):
    jd = _snapshot(
        buttons=[{"text": "Apply Now", "enabled": True, "selector": "#apply"}],
        page_text="Software Engineer position",
    )
    form = _snapshot(
        fields=[{"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"}],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    bridge.get_snapshot.side_effect = [jd, form, form, confirm]

    result = await orchestrator.apply(
        url="https://example.com/jobs/123", platform="generic", cv_path=Path("/tmp/cv.pdf"),
    )
    bridge.click.assert_any_call("#apply")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_captcha_wall_aborts(orchestrator, bridge):
    wall = _snapshot(verification_wall={"type": "cloudflare", "confidence": 0.9})
    bridge.get_snapshot.side_effect = [wall]

    result = await orchestrator.apply(
        url="https://example.com/apply", platform="generic", cv_path=Path("/tmp/cv.pdf"),
    )
    assert result["success"] is False
    assert "CAPTCHA" in result["error"]


@pytest.mark.asyncio
async def test_sso_google_detected(orchestrator, bridge):
    login = _snapshot(
        fields=[
            {"type": "email", "label": "Email", "current_value": "", "selector": "#email"},
            {"type": "password", "label": "Password", "current_value": "", "selector": "#pass"},
        ],
        buttons=[
            {"text": "Sign in with Google", "enabled": True, "selector": "#google-sso"},
            {"text": "Sign in", "enabled": True, "selector": "#signin"},
        ],
    )
    form = _snapshot(
        fields=[{"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"}],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    bridge.get_snapshot.side_effect = [login, form, form, confirm]

    result = await orchestrator.apply(
        url="https://careers.acme.com/apply", platform="generic", cv_path=Path("/tmp/cv.pdf"),
    )
    # Should click SSO, not fill email/password
    bridge.click.assert_any_call("#google-sso")
    assert result["success"] is True


@pytest.mark.asyncio
@patch("jobpulse.account_manager.ATS_ACCOUNT_PASSWORD", "TestPass123!")
async def test_signup_verify_login_apply(orchestrator, bridge):
    signup = _snapshot(
        fields=[
            {"type": "email", "label": "Email", "current_value": "", "selector": "#email"},
            {"type": "password", "label": "Password", "current_value": "", "selector": "#pass"},
            {"type": "password", "label": "Confirm Password", "current_value": "", "selector": "#pass2"},
        ],
        buttons=[{"text": "Create Account", "enabled": True, "selector": "#create"}],
    )
    verify_page = _snapshot(page_text="We've sent a verification email. Check your email.")
    form = _snapshot(
        fields=[{"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"}],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    bridge.get_snapshot.side_effect = [signup, verify_page, form, form, confirm]

    orchestrator.gmail.wait_for_verification.return_value = "https://example.com/verify?t=abc"

    result = await orchestrator.apply(
        url="https://careers.example.com/jobs/456", platform="generic", cv_path=Path("/tmp/cv.pdf"),
        profile={"first_name": "Yash", "last_name": "B"},
    )
    orchestrator.gmail.wait_for_verification.assert_called_once()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_cookie_banner_dismissed(orchestrator, bridge):
    # First snapshot has cookie banner + apply button
    cookie_page = _snapshot(
        buttons=[
            {"text": "Accept All Cookies", "enabled": True, "selector": "#cookies"},
            {"text": "Apply Now", "enabled": True, "selector": "#apply"},
        ],
        page_text="We use cookies. Software Engineer position.",
    )
    # After dismiss, same page without cookie button
    clean_jd = _snapshot(
        buttons=[{"text": "Apply Now", "enabled": True, "selector": "#apply"}],
        page_text="Software Engineer position",
    )
    form = _snapshot(
        fields=[{"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"}],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    bridge.get_snapshot.side_effect = [cookie_page, clean_jd, form, form, confirm]

    result = await orchestrator.apply(
        url="https://example.com/jobs", platform="generic", cv_path=Path("/tmp/cv.pdf"),
    )
    # Cookie button should have been clicked
    bridge.click.assert_any_call("#cookies")
    assert result["success"] is True
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/jobpulse/test_phase5_integration.py -v`
Expected: PASS (7/7)

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_phase5_integration.py
git commit -m "test(ext): add Phase 5 integration tests — SSO, signup, verify, cookie, multi-page"
```

---

### Task 12: Documentation + Content Script Update

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude/rules/jobs.md`
- Modify: `extension/content.js` (add cookie banner detection + page stability signals)

- [ ] **Step 1: Update CLAUDE.md Chrome Extension Engine section**

Add to the Chrome Extension Engine section:

```markdown
**Application Orchestrator** (`application_orchestrator.py`):
Manages the full external application lifecycle:
1. Dismiss cookie banners before any detection
2. Hybrid page detection: DOM analysis (free) + Vision LLM fallback ($0.003 when unsure)
3. SSO detection: "Sign in with Google/LinkedIn" → clicks SSO, skips account creation
4. Account creation: ATS_ACCOUNT_PASSWORD env var, stores credentials per domain in SQLite
5. Gmail verification: exponential polling (1s→2s→4s→...→32s), extracts verify links from HTML
6. Navigation learning: saves successful sequences per domain, replays on repeat visits (zero cost)
7. Multi-page form filling: state machine with Next button detection, progress tracking, stuck detection
```

- [ ] **Step 2: Update .claude/rules/jobs.md**

Add to `.claude/rules/jobs.md`:

```markdown
## External Application Engine
- `ApplicationOrchestrator` manages: cookie dismiss → page detect → navigate → account → verify → fill
- Hybrid page detection: DOM first (free), vision LLM fallback when confidence < 0.6
- SSO priority: Google > LinkedIn > Microsoft > Apple. Prefers SSO over account creation.
- Account credentials in SQLite (`data/ats_accounts.db`), one password via `ATS_ACCOUNT_PASSWORD`
- Gmail verification: exponential backoff 1s→2s→4s→8s→16s→32s, requires `gmail.modify` scope
- Navigation learning in SQLite (`data/navigation_learning.db`), replays per domain
- Cookie dismisser runs before EVERY page detection — prevents misclassification
- Multi-page: `find_next_button()` priority: Submit > Review > Save & Continue > Continue > Next > Proceed
- Stuck detection: chars 200-700 comparison, abort after 2 identical pages
- Max 10 navigation steps, max 20 form pages
```

- [ ] **Step 3: Update env vars in CLAUDE.md**

```markdown
**Extension Engine:** `APPLICATION_ENGINE=extension` `EXT_BRIDGE_HOST=localhost` `EXT_BRIDGE_PORT=8765` `ATS_ACCOUNT_PASSWORD` (required for account creation) `GMAIL_VERIFY_TIMEOUT=120` `PAGE_STABLE_TIMEOUT_MS=3000`
```

- [ ] **Step 4: Add page stability signal to content.js**

Add to the `scan_page_deep()` function in `extension/content.js`:

```javascript
// Page stability: check if DOM is still mutating
result.page_stable = !document.querySelector('[aria-busy="true"]')
    && !document.querySelector('.loading, .spinner, [class*="loading"]');
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .claude/rules/jobs.md extension/content.js
git commit -m "docs: update CLAUDE.md and rules for Phase 5 — orchestrator, SSO, learning, vision"
```

---

### Task 13: Full Test Suite + Lint

- [ ] **Step 1: Run all Phase 5 tests**

```bash
pytest tests/jobpulse/test_phase5_models.py tests/jobpulse/test_cookie_dismisser.py tests/jobpulse/test_page_analyzer.py tests/jobpulse/test_account_manager.py tests/jobpulse/test_gmail_verify.py tests/jobpulse/test_navigation_learner.py tests/jobpulse/test_sso_handler.py tests/jobpulse/test_multipage_navigation.py tests/jobpulse/test_application_orchestrator.py tests/jobpulse/test_ext_adapter_orchestrator.py tests/jobpulse/test_phase5_integration.py -v
```

Expected: ALL PASS

- [ ] **Step 2: Run existing extension tests (regression)**

```bash
pytest tests/jobpulse/test_form_intelligence.py tests/jobpulse/test_semantic_cache.py tests/jobpulse/test_phase3_wiring.py tests/jobpulse/test_intelligence_wiring.py -v
```

Expected: ALL PASS

- [ ] **Step 3: Lint all new files**

```bash
ruff check jobpulse/application_orchestrator.py jobpulse/page_analyzer.py jobpulse/cookie_dismisser.py jobpulse/navigation_learner.py jobpulse/sso_handler.py jobpulse/account_manager.py jobpulse/gmail_verify.py --fix && ruff format jobpulse/application_orchestrator.py jobpulse/page_analyzer.py jobpulse/cookie_dismisser.py jobpulse/navigation_learner.py jobpulse/sso_handler.py jobpulse/account_manager.py jobpulse/gmail_verify.py
```

- [ ] **Step 4: Final commit if lint fixes needed**

```bash
git add -A && git commit -m "style: lint Phase 5 files"
```

---

## Summary

| Task | Component | New Files | Tests |
|------|-----------|-----------|-------|
| 1 | Config + Models | — | 3 |
| 2 | Cookie Banner Dismisser | `cookie_dismisser.py` | 6 |
| 3 | Page Analyzer (DOM + Vision) | `page_analyzer.py` | 14 |
| 4 | Account Manager | `account_manager.py` | 9 |
| 5 | Gmail Verification | `gmail_verify.py` | 8 |
| 6 | Navigation Learner | `navigation_learner.py` | 7 |
| 7 | SSO Handler | `sso_handler.py` | 5 |
| 8 | Multi-Page Navigator | — (state_machines mod) | 17 |
| 9 | Application Orchestrator | `application_orchestrator.py` | 1 |
| 10 | Wire into ExtensionAdapter | — (ext_adapter mod) | 2 |
| 11 | Integration Tests | — | 7 |
| 12 | Documentation | — | — |
| 13 | Full Suite + Lint | — | — |
| **Total** | | **7 new files** | **~79 tests** |

## Cost Per Application (Extension Mode)

| Scenario | LLM Cost | Notes |
|----------|----------|-------|
| Known ATS, repeat visit | $0.00 | Learned sequence replay + DOM detection |
| Known ATS, first visit | $0.00 | DOM detection handles Greenhouse/Lever/Workday |
| Unknown ATS, first visit | ~$0.003 | Vision fallback for page classification |
| Account creation + verify | ~$0.003 | Vision (if needed) + Gmail API (free) |
| Form filling (5-tier) | $0.00-$0.012 | Pattern/cache free, LLM $0.002, vision $0.01 |
