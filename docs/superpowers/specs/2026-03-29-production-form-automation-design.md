# Production Form Automation Engine — Design Spec

> Multi-page ATS form filling with account creation, email verification, and 15+ input types.

## Problem

Current ATS adapters handle only text inputs + file uploads on single-page forms. Real production job applications require:
- Account creation with Gmail verification
- Login with saved credentials
- Multi-page wizard navigation
- 15+ input types (dropdowns, radios, checkboxes, date pickers, search/autocomplete, etc.)
- Validation error recovery
- Session timeout handling

## Architecture

3 layers, each building on the previous:

```
Layer 3: PlatformAuth       — login/signup/verification flows
Layer 2: WizardEngine       — multi-page navigation, modals, error recovery
Layer 1: FormEngine          — detect & fill any HTML input type
```

## Sub-Projects

Built in order — each gets its own plan → implementation cycle:

1. **Sub-project 1: Form Engine** — standalone, no dependencies
2. **Sub-project 2: Wizard Engine** — depends on Form Engine
3. **Sub-project 3: Platform Auth** — depends on Wizard Engine

After all 3 are built, existing ATS adapters get rewritten to use these layers.

---

## Sub-Project 1: Form Engine

### File Structure

```
jobpulse/form_engine/
  __init__.py
  detector.py              — detect input type from DOM element
  text_filler.py           — text, textarea, search/autocomplete
  select_filler.py         — native <select>, custom React dropdowns, cascading
  radio_filler.py          — radio buttons, yes/no/prefer-not-to-say
  checkbox_filler.py       — checkboxes, toggles, consent auto-accept
  date_filler.py           — native date, custom calendar, month/year only
  file_filler.py           — file upload, drag-and-drop, paste-text fallback
  multi_select_filler.py   — tag inputs, checkbox lists, dual listbox
  validation.py            — error detection, required field scanning, retry
```

### Input Type Detection (`detector.py`)

Examines a DOM element and returns its semantic type:

```python
class InputType(str, Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT_NATIVE = "select_native"
    SELECT_CUSTOM = "select_custom"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    DATE_NATIVE = "date_native"
    DATE_CUSTOM = "date_custom"
    SEARCH_AUTOCOMPLETE = "search_autocomplete"
    FILE_UPLOAD = "file_upload"
    MULTI_SELECT = "multi_select"
    TAG_INPUT = "tag_input"
    TOGGLE_SWITCH = "toggle_switch"
    RICH_TEXT_EDITOR = "rich_text_editor"
    READONLY = "readonly"
    UNKNOWN = "unknown"
```

Detection logic:
- `<select>` → `SELECT_NATIVE`
- `<input type="radio">` → `RADIO`
- `<input type="checkbox">` → `CHECKBOX`
- `<input type="date">` → `DATE_NATIVE`
- `<input type="file">` → `FILE_UPLOAD`
- `<textarea>` → `TEXTAREA`
- `<div role="listbox">` or `<div role="combobox">` → `SELECT_CUSTOM`
- `<input>` with autocomplete suggestions container → `SEARCH_AUTOCOMPLETE`
- `<div contenteditable>` or iframe with TinyMCE/Quill → `RICH_TEXT_EDITOR`
- `<input>` with `readonly` or `disabled` → `READONLY`
- Custom toggle/switch widgets → `TOGGLE_SWITCH`
- Element with `[aria-multiselectable="true"]` → `MULTI_SELECT`
- Fallback → `TEXT` for `<input>`, `UNKNOWN` otherwise

### Filler Interface

Every filler follows the same pattern:

```python
class FillResult:
    success: bool
    element_selector: str
    value_attempted: str
    value_set: str | None      # what actually ended up in the field
    error: str | None

async def fill_select(page, selector: str, value: str, timeout: int = 5000) -> FillResult:
    """Fill a dropdown/select element. Handles native and custom."""
```

All fillers return `FillResult` — never raise, never return bare booleans.

### Select Filler Edge Cases (`select_filler.py`)

| Edge Case | Strategy |
|---|---|
| Native `<select>` | `.select_option(label=value)` with fuzzy match fallback |
| Custom React dropdown | Click trigger element → wait for listbox/options panel → match option text → click |
| Search/filter dropdown | Type value into dropdown's search input → wait for filtered list → click match |
| Cascading/dependent | Fill parent first → wait for child to become enabled (poll `disabled` attr) → fill child |
| "Other" + text field | Select "Other" → detect newly visible `<input>` → fill it |
| Async-loaded options | Wait for `option` count > 0 (max 5s) before attempting fill |
| Grouped options (`<optgroup>`) | Flatten: match against option text ignoring group |
| Display text vs value mismatch | Always match on visible text, not `value` attribute |
| Readonly/disabled | Detect and skip — return `FillResult(success=True, skipped=True)` |
| Long list (200+) | Type-to-filter if searchable, else scroll within dropdown container |
| Auto-close on scroll | Scroll element into view BEFORE clicking to open |

Fuzzy matching: normalize both strings (lowercase, strip whitespace, remove punctuation), then exact match → startswith → contains → Levenshtein distance < 3.

### Radio Filler Edge Cases (`radio_filler.py`)

| Edge Case | Strategy |
|---|---|
| Label is `<label for="id">` | Match label text, click the associated radio |
| Label is sibling/parent text | Walk DOM: check siblings, then parent, for text content |
| Label is an image | Use `aria-label`, `title`, or `alt` attribute |
| Radio in table row | Find question text in same `<tr>`, match radios in same row |
| Pre-selected wrong answer | Always click correct option regardless of current state |
| Conditional section reveal | After click, wait 500ms, scan for newly visible fields |
| Custom styled (hidden input) | Click the visible wrapper element, not the hidden `<input>` |
| Yes/No/Prefer not to say | Map: sponsorship→No, relocate→Yes, disability→"Prefer not to say" |
| Question-to-answer mapping | Use `screening_answers.get_answer()` for the question text |

### Checkbox Filler Edge Cases (`checkbox_filler.py`)

| Edge Case | Strategy |
|---|---|
| Terms/privacy/GDPR consent | Auto-check all consent checkboxes (detect by label keywords: "agree", "consent", "terms", "privacy", "GDPR") |
| "Select all that apply" (skills) | Match labels against `job.required_skills + job.preferred_skills`, check matches |
| Already checked (wrong state) | Call `.is_checked()` first, only toggle if state is wrong |
| Triggers form expansion | After toggle, wait 500ms, scan for new visible fields |
| Custom toggle switches | Click the toggle container div, verify `aria-checked` changed |
| Indeterminate state | Force to checked or unchecked via `.evaluate("el.indeterminate = false")` |

### Text Filler Edge Cases (`text_filler.py`)

Handles `<input type="text">`, `<textarea>`, search/autocomplete, and rich text editors.

| Edge Case | Strategy |
|---|---|
| Character limit (`maxlength`) | Read attribute, truncate content to fit |
| Word count limit | Parse placeholder/label for "N words", count and trim LLM output |
| Rich text editor (TinyMCE/Quill) | Detect by iframe or `.ql-editor` class → use `page.evaluate()` to call editor API |
| Pre-filled textarea | Triple-click to select all → delete → then fill |
| Autocomplete suggestions | Type value → wait for suggestion dropdown (max 2s) → click matching suggestion. If no match and freeform allowed, press Escape and leave typed text |
| Autocomplete min chars | Type at least 3 chars before waiting for suggestions |
| Autocomplete debounce | Wait 1s after last keystroke before checking suggestions |
| Multiple textareas | Match each by label/question text, generate separate answers via `screening_answers` |
| Placeholder as instruction | Parse placeholder for constraints ("min 100 words"), pass to LLM |

### Date Filler Edge Cases (`date_filler.py`)

| Edge Case | Strategy |
|---|---|
| Native `<input type="date">` | `.fill("YYYY-MM-DD")` directly |
| Custom calendar widget | Try keyboard input first (type date + Enter). Fall back to: click field → navigate month/year → click day |
| MM/DD/YYYY vs DD/MM/YYYY | Detect locale from `<html lang>` or field placeholder. Default: DD/MM/YYYY (UK) |
| "Available from" with dropdown | Detect as select, not date — use `select_filler` |
| Date must be in future | Use `date.today() + timedelta(days=1)` as minimum |
| Month/Year only | Detect by label or input format → provide only month + year |
| Readonly date field | Skip — return success |
| Workday custom date picker | Type into the text input part of the widget, press Tab to confirm |

### File Filler Edge Cases (`file_filler.py`)

| Edge Case | Strategy |
|---|---|
| Standard `<input type="file">` | `.set_input_files(str(path))` |
| Hidden file input | Find via `page.query_selector("input[type='file']")` even if hidden |
| Drag-and-drop zone only | Find hidden `<input type="file">` inside the drop zone, use `set_input_files` |
| Multiple upload fields | Match by label text: "resume"/"cv" → cv_path, "cover"/"letter" → cover_letter_path |
| File type restriction | Read `accept` attribute, verify file matches |
| File size limit | Check file size before upload, log warning if too large |
| Upload progress indicator | Wait for upload indicator to disappear (max 30s) |
| "Paste resume text" alternative | Extract text from PDF, paste into textarea |
| Upload triggers auto-fill | After upload, wait 3s for ATS to parse and pre-fill fields |

### Multi-Select Filler Edge Cases (`multi_select_filler.py`)

| Edge Case | Strategy |
|---|---|
| Tag input (type + Enter) | For each value: type → press Enter → verify tag appeared |
| Checkbox list | Match labels against values, check matching checkboxes |
| Dual listbox (left → right) | Click items on left, click transfer button |
| Max selection limit | Read limit from label/validation, stop at limit, prioritise by job relevance |
| Pre-selected items | Check current selections first, don't duplicate |
| "Select at least N" | Ensure minimum from job skills list |
| Native `<select multiple>` | `.select_option()` with multiple values |

### Validation & Error Detection (`validation.py`)

```python
class ValidationError:
    field_selector: str
    error_message: str
    field_label: str | None

def scan_for_errors(page) -> list[ValidationError]:
    """Scan page for validation error messages."""

def find_required_fields(page) -> list[str]:
    """Find all unfilled required fields (*, required attr, aria-required)."""

def retry_with_fixes(page, errors: list[ValidationError], fill_fn) -> bool:
    """Parse error messages, adjust values, re-fill affected fields."""
```

Detection strategies:
- Elements with class containing "error", "invalid", "danger"
- `[aria-invalid="true"]` elements
- Visible elements with red border/text color
- Text containing "required", "please fill", "this field is required"
- `role="alert"` elements that appeared after form interaction

---

## Sub-Project 2: Wizard Engine

### File Structure

```
jobpulse/wizard_engine/
  __init__.py
  navigator.py         — next/continue detection, page progression
  state_tracker.py     — filled fields cache, page count, progress
  modal_handler.py     — dismiss popups, cookie consent, chat widgets
  review_page.py       — detect review/summary page, verify, submit
  error_recovery.py    — validation errors, session timeout, re-auth
```

### Navigator (`navigator.py`)

Detects and clicks progression buttons:

```python
async def find_next_button(page) -> ElementHandle | None:
    """Find the 'Next', 'Continue', 'Save & Continue', 'Proceed' button."""

async def find_submit_button(page) -> ElementHandle | None:
    """Find the final 'Submit', 'Apply', 'Submit Application' button."""

async def advance_page(page) -> PageTransitionResult:
    """Click next/submit, wait for navigation, return result."""
```

Button detection priority:
1. `button[type="submit"]` with text containing "next", "continue", "proceed", "save"
2. `button` or `a` with text matching progression keywords
3. `[data-automation-id]` patterns (Workday-specific)
4. `input[type="submit"]`
5. Visible `button` at bottom-right of form

Edge cases:
- Button disabled until required fields filled → call `validation.find_required_fields()`, fill them, retry
- Button hidden behind scroll → scroll to bottom first
- Multiple submit-like buttons → prefer the one inside the form, not header/footer
- "Save as draft" vs "Submit" → never click draft, only final submit
- Confirmation dialog after submit → click "Yes" / "Confirm"

### State Tracker (`state_tracker.py`)

```python
class WizardState:
    pages_seen: int
    current_page: int
    fields_filled: dict[str, str]       # selector → value
    pages_content: list[str]            # page HTML snapshots for debugging
    started_at: datetime
    last_activity: datetime

    def is_timed_out(self, max_minutes: int = 25) -> bool
    def is_duplicate_page(self, page_html: str) -> bool
    def get_unfilled_required(self, page) -> list[str]
```

Edge cases:
- Session timeout (15-30min) → detect timeout page/modal, trigger re-auth
- Duplicate page detection → if same page HTML seen twice, we're stuck in a loop → abort
- Track total time to prevent infinite loops (max 25min per application)
- Cache all filled values for consistency across pages (same email on page 1 and page 3)

### Modal Handler (`modal_handler.py`)

```python
async def dismiss_modals(page) -> int:
    """Dismiss all blocking overlays. Returns count dismissed."""
```

Detection targets:
- Cookie consent: buttons with "Accept", "Accept All", "I agree", "Got it"
- Chat widgets: Intercom, Drift, Zendesk bubbles → click close/minimize
- Survey popups: "How did you hear about us?" modals → close button
- Newsletter popups: email signup overlays → close/X button
- Overlay divs: `[class*="modal"]`, `[class*="overlay"]`, `[role="dialog"]` that block interaction

Strategy:
1. Before each page interaction, scan for blocking overlays
2. Try clicking close/dismiss buttons
3. If no close button, try pressing Escape
4. If overlay persists, try clicking outside it
5. Wait 500ms after dismiss, verify it's gone

### Review Page Detection (`review_page.py`)

```python
async def is_review_page(page) -> bool:
    """Detect if current page is a review/summary before final submit."""

async def verify_review_data(page, expected: dict) -> list[str]:
    """Check review page data against expected values. Returns mismatches."""
```

Detection: page contains read-only summary of previously entered data + a "Submit" or "Confirm" button but no editable fields.

### Error Recovery (`error_recovery.py`)

```python
async def handle_page_error(page, wizard_state: WizardState) -> RecoveryAction:
    """Detect and recover from errors after page transition."""
```

Recovery actions:
- `RETRY_CURRENT_PAGE` — validation errors, re-fill and re-submit
- `RE_AUTHENTICATE` — session expired, trigger auth flow
- `ABORT_MANUAL_REVIEW` — CAPTCHA, security questions, unrecoverable
- `SKIP_APPLICATION` — "Already applied", duplicate detection
- `CONTINUE` — no error, proceed normally

Detection:
- URL changed to login page → `RE_AUTHENTICATE`
- "Already applied" text → `SKIP_APPLICATION`
- CAPTCHA element visible → `ABORT_MANUAL_REVIEW`
- Validation errors → `RETRY_CURRENT_PAGE`
- Same page after submit click → `RETRY_CURRENT_PAGE`

---

## Sub-Project 3: Platform Auth

### File Structure

```
jobpulse/platform_auth/
  __init__.py
  auth_detector.py     — detect if page is login, signup, or direct form
  login_handler.py     — login with credentials, fallback chain
  signup_handler.py    — create account, trigger verification
  gmail_verifier.py    — poll Gmail API, extract link/code, complete verification
  session_manager.py   — session health check, re-auth on expiry
```

### Auth Detection (`auth_detector.py`)

```python
class PageType(str, Enum):
    DIRECT_APPLICATION = "direct_application"
    LOGIN_PAGE = "login_page"
    SIGNUP_PAGE = "signup_page"
    VERIFICATION_PENDING = "verification_pending"
    ALREADY_APPLIED = "already_applied"
    CAPTCHA_BLOCKED = "captcha_blocked"
    UNKNOWN = "unknown"

async def detect_page_type(page) -> PageType:
    """Analyze current page to determine which auth flow to use."""
```

Detection signals:
- Login: password field + "Sign in" / "Log in" button
- Signup: "Create account" / "Register" button + multiple input fields
- Direct form: application form fields (name, resume upload) without login
- Verification pending: "Check your email" / "Verify your email" text
- Already applied: "You have already applied" text
- CAPTCHA: `iframe[src*="recaptcha"]` or `[class*="captcha"]`

### Login Handler (`login_handler.py`)

```python
async def attempt_login(page, email: str, password: str) -> LoginResult:
    """Fill login form, submit, detect outcome."""

class LoginResult:
    success: bool
    page_type_after: PageType    # what page are we on now?
    error: str | None            # "invalid password", "account not found", etc.
```

Flow:
1. Find email/username field → fill with `PROFILE["email"]`
2. Find password field → fill with password
3. Click login/submit button
4. Wait for navigation (max 10s)
5. Detect outcome:
   - Application form visible → `success=True`
   - "Invalid password" → `success=False, error="invalid_password"`
   - "Account not found" → `success=False, error="account_not_found"`
   - CAPTCHA appeared → `success=False, error="captcha"`
   - Same login page → `success=False, error="login_failed"`

Credential fallback chain:
```
1. Try JOB_APPLY_ALREADY_APPLIED_PASSWORD
2. If "invalid password" → try JOB_APPLY_PASSWORD
3. If "account not found" → route to signup_handler
4. If both passwords fail → try "Forgot password" flow
5. If CAPTCHA → route to manual review
```

### Signup Handler (`signup_handler.py`)

```python
async def create_account(page, email: str, password: str) -> SignupResult:
    """Fill signup form, submit, trigger verification."""
```

Flow:
1. Fill email field with `PROFILE["email"]`
2. Fill password + confirm password with `JOB_APPLY_PASSWORD`
3. Fill name fields if present (from `PROFILE`)
4. Check any terms/consent checkboxes
5. Click "Create Account" / "Register"
6. Wait for result:
   - Verification page → trigger `gmail_verifier`
   - Direct login → `success=True`
   - "Account already exists" → route to `login_handler`
   - Password requirements failure → append `!1A` to password, retry once

Password requirements edge case:
- Detect error message mentioning "special character", "uppercase", "number", "length"
- Modify password to meet requirements: append `!1A` if missing special/number/uppercase
- Log the modification so user knows the actual password used

### Gmail Verifier (`gmail_verifier.py`)

```python
async def verify_via_gmail(
    page,                          # browser page to click verification link in
    sender_pattern: str = "",      # e.g. "workday" or "myworkday"
    max_wait_seconds: int = 120,
    poll_interval: int = 5,
) -> VerificationResult:
```

Uses the existing Gmail API infrastructure from `jobpulse/gmail_agent.py`:

Flow:
1. Record current time as `search_after`
2. Poll Gmail API every `poll_interval` seconds:
   - Search: `is:unread newer_than:5m` + subject contains "verify" or "confirm" or "activate"
   - Filter by sender containing company domain or "workday"
3. When email found:
   - Parse body for verification link (regex: `https?://[^\s"<>]+(?:verify|confirm|activate|token)[^\s"<>]*`)
   - OR parse for 6-digit code (regex: `\b\d{6}\b`)
4. If link found → open in browser page, wait for redirect
5. If code found → type into verification code input field on the pending page
6. Verify success: page shows "verified" / "confirmed" or redirects to application
7. If no email after `max_wait_seconds` → check spam folder → fail with timeout

Edge cases:
- Email in spam → search `in:anywhere` not just `in:inbox`
- Multiple verification emails → use most recent (sort by date desc)
- Verification link expired → request resend, poll again
- Code vs link → detect which the page expects (code input field visible vs "check email" text)
- Email from different sender domain → broaden search, don't filter strictly

### Session Manager (`session_manager.py`)

```python
async def ensure_authenticated(page, job_url: str) -> AuthResult:
    """Main entry point: detect page state and handle auth as needed."""
```

This is the orchestrator that ties auth_detector + login + signup + gmail_verifier together:

```
detect_page_type(page)
  ├── DIRECT_APPLICATION → return success
  ├── LOGIN_PAGE → attempt_login()
  │     ├── success → return success
  │     ├── account_not_found → create_account() → verify_via_gmail() → return
  │     ├── invalid_password → try JOB_APPLY_PASSWORD → return
  │     └── captcha → return manual_review
  ├── SIGNUP_PAGE → create_account() → verify_via_gmail() → return
  ├── VERIFICATION_PENDING → verify_via_gmail() → return
  ├── ALREADY_APPLIED → return skip
  └── CAPTCHA_BLOCKED → return manual_review
```

### Config Additions (`config.py`)

```python
JOB_APPLY_PASSWORD = os.getenv("JOB_APPLY_PASSWORD", "")
JOB_APPLY_ALREADY_APPLIED_PASSWORD = os.getenv("JOB_APPLY_ALREADY_APPLIED_PASSWORD", "")
```

---

## Integration with Existing Adapters

After all 3 sub-projects are built, `applicator.py` changes to:

```python
async def apply_job(url, ats_platform, cv_path, ...):
    # 1. Auth (new)
    auth_result = await session_manager.ensure_authenticated(page, url)
    if auth_result.needs_manual_review:
        return {"success": False, "error": "manual_review_required", ...}

    # 2. Fill all pages (new)
    wizard = WizardEngine(page, form_engine)
    result = await wizard.fill_application(
        profile=PROFILE,
        cv_path=cv_path,
        cover_letter_path=cover_letter_path,
        screening_answers=merged_answers,
        job=job_listing,
    )

    # 3. Submit
    if result.reached_review_page:
        await review_page.verify_and_submit(page, expected=result.filled_values)

    return {"success": result.submitted, ...}
```

Each ATS adapter becomes a thin configuration layer:
- Platform-specific selectors and quirks
- Custom dropdown handling for that platform's React components
- Platform-specific wizard page ordering

The heavy lifting moves to the shared engines.

---

## What We Are NOT Building

- **CAPTCHA solving** — route to manual review with screenshot
- **2FA handling** — route to manual review
- **Security question answering** — route to manual review
- **"Forgot password" flow** — only as last-resort fallback, not primary path
- **OAuth/SSO login** — only email+password, not Google/LinkedIn sign-in buttons
- **Resume parsing correction** — if ATS pre-fills wrong data from resume, we don't fix it (too fragile)

These are intentional manual-review escapes with Telegram notification, not gaps.

---

## Testing Strategy

- **Unit tests**: Each filler function tested with mock Playwright page objects
- **Integration tests**: Full wizard flow with mock multi-page form HTML
- **Platform-specific tests**: Recorded HTML snapshots from real Greenhouse/Workday/Lever forms
- **Gmail verifier tests**: Mock Gmail API responses with sample verification emails

All tests mock Playwright and Gmail API — no real browser or real email access in tests.
