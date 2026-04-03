# Chrome Extension Job Application Engine — Design Spec

> Replace Playwright automation with a Chrome extension that operates inside the user's real browser — zero bot detection, company-tailored answers via Perplexity Sonar, 4-tier form intelligence (including Chrome's built-in Prompt API with Gemini Nano), and self-improving application quality.
>
> **Research date:** 3 April 2026. All APIs, models, and techniques verified against current documentation.

## Problem Statement

The current job autopilot uses Playwright to launch a separate Chromium instance. Despite anti-detection flags (`--disable-blink-features=AutomationControlled`, persistent profile, human-like delays), modern bot detection (Cloudflare, DataDome, PerimeterX) still catches it because:

1. **CDP detection is now standard** — Anti-bot systems (Cloudflare, DataDome, PerimeterX) detect CDP connections via `cdc_` string constants in the `window` object, Runtime.evaluate artifacts, and automation-specific DOM properties. Playwright stealth plugins "can't solve the deeper fingerprinting and behavioral detection that platforms use in 2026" (source: dicloak.com/blog-detail/playwright-stealth-what-works-in-2026).
2. **Composite fingerprinting** — Screen resolution, timezone, installed fonts, hardware concurrency, memory size, and WebGL capabilities create a unique signature. Playwright's rendering engine produces consistent, detectable patterns that stealth plugins don't randomize.
3. **LLM crawler detection** — DataDome added LLM crawler detection in 2025; LLM crawler traffic quadrupled across their customer base, rising from 2.6% to over 10% of verified bot traffic. AI agent traffic is now a first-class detection category.
4. **Per-customer ML models** — PerimeterX trains custom ML models for each website based on that site's historical traffic patterns. Generic anti-detection flags no longer work.
5. **Behavioral analysis** — DataDome tracks mouse movements, scroll patterns, and typing cadence. Perfect mouse movements or identical timing between actions are dead giveaways.

Result: daily rate limits of 15-30 applications with 2-8 hour cooldowns after verification walls. Perplexity's job feature applies to 50+ in 30 minutes because it runs inside the user's real browser via an extension.

## Solution: Chrome Extension + Python Backend

A Manifest V3 Chrome extension that:
- Runs inside the user's real Chrome with their cookies, extensions, history, and fingerprint
- Communicates with the Python backend via WebSocket on localhost
- Uses a 4-tier intelligence system (pattern → Gemini Nano → LLM API → vision)
- Receives company research from Perplexity before answering open-ended questions
- Streams progress to Telegram for human oversight on uncertain answers
- Follows platform-specific state machines for predictable, recoverable application flows

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  PYTHON BACKEND (jobpulse/)                                    │
│                                                                │
│  job_autopilot.py                                              │
│       │                                                        │
│       ├── ext_adapter.py ──→ WebSocket Server (ws://8765)      │
│       │       │                                                │
│       │       ├── StateMachine (per-platform flow definitions) │
│       │       ├── perplexity.py (company + salary research)    │
│       │       ├── pre_submit_gate.py (LLM quality review)     │
│       │       └── telegram_stream.py (live oversight)          │
│       │                                                        │
│       └── screening_answers.py + form_engine/ (existing)       │
└────────────────────────────────┬───────────────────────────────┘
                                 │ WebSocket (ws://localhost:8765)
┌────────────────────────────────▼───────────────────────────────┐
│  CHROME EXTENSION (Manifest V3)                                │
│                                                                │
│  background.js (service worker)                                │
│       ├── WebSocket client ↔ Python                            │
│       ├── chrome.sidePanel (real-time dashboard)               │
│       └── chrome.scripting (cross-origin iframe injection)     │
│                                                                │
│  content.js                                                    │
│       ├── deepScan() — Shadow DOM + iframe penetration         │
│       ├── Gemini Nano — local Tier 2 field analysis            │
│       ├── fillField() / clickElement() / uploadFile()          │
│       ├── behaviorProfile — user's typing/scroll/mouse patterns│
│       └── MutationObserver — auto-report DOM changes           │
│                                                                │
│  sidepanel.html/js — progress, company intel, controls         │
│  popup.html/js — connect/disconnect, status indicator          │
└────────────────────────────────────────────────────────────────┘
```

## Phase 1: The Foundation

### 1.1 Chrome Extension Core

**manifest.json (Manifest V3):**

```json
{
  "manifest_version": 3,
  "name": "JobPulse Application Engine",
  "version": "1.0.0",
  "permissions": [
    "activeTab",
    "scripting",
    "sidePanel",
    "storage",
    "tabs"
  ],
  "host_permissions": ["<all_urls>"],
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [{
    "matches": ["<all_urls>"],
    "js": ["content.js"],
    "run_at": "document_idle",
    "all_frames": true
  }],
  "side_panel": {
    "default_path": "sidepanel.html"
  },
  "action": {
    "default_popup": "popup.html",
    "default_icon": "icons/icon48.png"
  }
}
```

**Why `<all_urls>` host permission:** Job applications span hundreds of company domains (greenhouse.io, lever.co, workday.com, myworkdayjobs.com, boards.eu.greenhouse.io, etc.). Enumerating them all is impractical and new ATS domains appear constantly.

### 1.2 WebSocket Protocol

**Message format (JSON, bidirectional):**

```typescript
// Python → Extension (commands)
{
  id: string,           // UUID for ack/response matching
  action: "navigate" | "fill" | "click" | "upload" | "screenshot" 
        | "select" | "check" | "scroll" | "wait" | "close_tab",
  payload: {
    selector?: string,
    value?: string,
    url?: string,
    file_base64?: string,
    file_name?: string,
    direction?: "up" | "down",
    timeout_ms?: number,
  }
}

// Extension → Python (responses & events)
{
  id: string,           // Matches command id (for responses)
  type: "ack" | "result" | "snapshot" | "navigation" | "mutation" | "error",
  payload: {
    success?: boolean,
    snapshot?: PageSnapshot,
    url?: string,
    error?: string,
  }
}
```

**PageSnapshot structure (sent automatically on page load and DOM mutations):**

```typescript
interface PageSnapshot {
  url: string;
  title: string;
  fields: FieldInfo[];
  buttons: ButtonInfo[];
  verification_wall: VerificationWall | null;
  page_text_preview: string;   // First 500 chars of visible text
  has_file_inputs: boolean;
  iframe_count: number;
  timestamp: number;
}

interface FieldInfo {
  selector: string;            // Unique CSS selector
  input_type: "text" | "textarea" | "select" | "radio" | "checkbox" 
            | "file" | "date" | "email" | "number" | "tel" | "custom_select"
            | "search_autocomplete" | "multi_select" | "toggle" | "rich_text";
  label: string;
  required: boolean;
  current_value: string;
  options: string[];           // For select/radio/checkbox
  attributes: Record<string, string>;  // name, id, placeholder, aria-label
  in_shadow_dom: boolean;
  in_iframe: boolean;
  iframe_index: number | null;
}

interface ButtonInfo {
  selector: string;
  text: string;
  type: string;                // submit, button, link
  enabled: boolean;
}

interface VerificationWall {
  wall_type: "cloudflare" | "recaptcha" | "hcaptcha" | "text_challenge" | "http_block";
  confidence: number;
  details: string;
}
```

**Reliability:** Every command gets an `ack` within 1 second. If no ack, Python retries once, then marks the action as failed. Commands have monotonic IDs for ordering. Reconnection with exponential backoff (1s → 2s → 4s → max 30s).

**MV3 Service Worker Keepalive (critical):** Chrome 116+ extends service worker lifetime for active WebSocket connections, BUT the service worker idle timer still needs periodic reset. The background.js must implement heartbeat pinging every 20 seconds to prevent the 30-second service worker termination:

```javascript
// background.js — keepalive heartbeat
setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping" }));
    }
}, 20_000);  // 20s interval (within 30s service worker timeout)
```

Python server responds with `pong` to keep the connection alive. This is a documented requirement for MV3 WebSocket connections (source: developer.chrome.com/docs/extensions/how-to/web-platform/websockets).

### 1.3 Content Script — Deep Page Scanner

**deepScan()** — the core page analysis function:

```javascript
function deepScan(root = document, depth = 0, iframeIndex = null) {
    const fields = [];
    const MAX_DEPTH = 5;  // Prevent infinite recursion
    
    if (depth > MAX_DEPTH) return fields;
    
    // 1. Scan regular form fields
    const inputs = root.querySelectorAll(
        "input, select, textarea, [contenteditable='true'], " +
        "[role='listbox'], [role='combobox'], [role='radiogroup'], " +
        "[role='switch'], [role='textbox']"
    );
    
    for (const el of inputs) {
        fields.push(extractFieldInfo(el, iframeIndex));
    }
    
    // 2. Penetrate shadow roots
    root.querySelectorAll("*").forEach(el => {
        if (el.shadowRoot) {
            fields.push(...deepScan(el.shadowRoot, depth + 1, iframeIndex));
        }
    });
    
    // 3. Penetrate same-origin iframes
    const iframes = root.querySelectorAll("iframe");
    iframes.forEach((iframe, idx) => {
        try {
            if (iframe.contentDocument) {
                fields.push(...deepScan(iframe.contentDocument, depth + 1, idx));
            }
        } catch (e) {
            // Cross-origin — handled by background.js via chrome.scripting
        }
    });
    
    return fields;
}
```

**Cross-origin iframes** (common with embedded Greenhouse/Lever forms): The background service worker uses `chrome.scripting.executeScript({ target: { tabId, frameIds } })` to inject the scanner into cross-origin frames. This is something only an extension can do — not CDP, not Playwright.

**Verification wall detection** — reuses the same patterns from `verification_detector.py`:

```javascript
function detectVerificationWall() {
    // CSS selectors (Cloudflare, reCAPTCHA, hCaptcha)
    const SELECTOR_PATTERNS = [
        { sel: "#challenge-running, .cf-turnstile, #cf-challenge-running", type: "cloudflare", conf: 0.95 },
        { sel: ".g-recaptcha, #recaptcha-anchor, [data-sitekey]", type: "recaptcha", conf: 0.90 },
        { sel: ".h-captcha", type: "hcaptcha", conf: 0.90 },
    ];
    
    for (const { sel, type, conf } of SELECTOR_PATTERNS) {
        if (document.querySelector(sel)) return { wall_type: type, confidence: conf };
    }
    
    // iframe URL patterns
    for (const frame of document.querySelectorAll("iframe")) {
        const src = frame.src || "";
        if (src.includes("challenges.cloudflare.com")) return { wall_type: "cloudflare", confidence: 0.95 };
        if (src.includes("google.com/recaptcha")) return { wall_type: "recaptcha", confidence: 0.90 };
        if (src.includes("hcaptcha.com")) return { wall_type: "hcaptcha", confidence: 0.90 };
    }
    
    // Text patterns
    const body = document.body?.innerText?.toLowerCase() || "";
    if (/verify you are human|are you a robot|confirm you're not a robot/.test(body))
        return { wall_type: "text_challenge", confidence: 0.85 };
    if (/access denied|403 forbidden|you have been blocked/.test(body))
        return { wall_type: "http_block", confidence: 0.80 };
    
    return null;
}
```

**MutationObserver** — watches for DOM changes after fill/click actions:

```javascript
const observer = new MutationObserver((mutations) => {
    // Debounce: wait 500ms after last mutation before scanning
    clearTimeout(scanTimeout);
    scanTimeout = setTimeout(() => {
        const snapshot = buildSnapshot();
        sendToBackground({ type: "mutation", payload: { snapshot } });
    }, 500);
});

observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden", "disabled", "aria-hidden"]
});
```

### 1.4 Content Script — Form Actions

**fillField()** — fills a field with proper event dispatch:

```javascript
async function fillField(selector, value, behaviorProfile) {
    const el = resolveSelector(selector);  // Handles shadow DOM + iframe selectors
    if (!el) return { success: false, error: "Element not found" };
    
    // Scroll into view smoothly
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await delay(behaviorProfile.field_to_field_gap);
    
    // Focus
    el.focus();
    el.dispatchEvent(new Event("focus", { bubbles: true }));
    
    // Clear existing value
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    
    // Type character by character with user's rhythm
    for (const char of value) {
        el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
        el.value += char;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
        await delay(behaviorProfile.avg_typing_speed * (1 + (Math.random() - 0.5) * behaviorProfile.typing_variance));
    }
    
    // Blur
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    
    return { success: true, value_set: el.value };
}
```

**uploadFile()** — DataTransfer API for file uploads:

```javascript
async function uploadFile(selector, base64Data, fileName, mimeType) {
    const el = resolveSelector(selector);
    if (!el) return { success: false, error: "Element not found" };
    
    const bytes = Uint8Array.from(atob(base64Data), c => c.charCodeAt(0));
    const file = new File([bytes], fileName, { type: mimeType });
    
    const dt = new DataTransfer();
    dt.items.add(file);
    el.files = dt.files;
    
    el.dispatchEvent(new Event("change", { bubbles: true }));
    
    // Wait for upload progress indicators
    await waitForUploadComplete(el);
    
    return { success: true, value_set: fileName };
}
```

**clickElement()** — click with human-like behavior:

```javascript
async function clickElement(selector, behaviorProfile) {
    const el = resolveSelector(selector);
    if (!el) return { success: false, error: "Element not found" };
    
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await delay(behaviorProfile.reading_pause * (0.5 + Math.random()));
    
    // Move mouse toward element (curved path)
    await simulateMouseMove(el, behaviorProfile);
    
    el.click();
    
    return { success: true };
}
```

### 1.5 Human Behavior Fingerprinting

The extension observes the user's real browsing patterns during a calibration phase (first hour of installation), then replays those patterns during automated form-filling.

**Captured signals:**

```javascript
const behaviorProfile = {
    avg_typing_speed: 0,     // ms per char
    typing_variance: 0,       // 0-1, how much speed varies
    scroll_speed: 0,          // px/s
    scroll_pattern: "",       // "smooth" | "stepped" | "fast_then_slow"
    mouse_curve: "",          // "curved" | "straight" | "erratic"
    reading_pause: 0,         // seconds pause on text before acting
    field_to_field_gap: 0,    // seconds between filling two fields
    click_offset: { x: 0, y: 0 },  // avg offset from element center
};
```

**Calibration:** Passive observation via `document.addEventListener` for `keydown`, `mousemove`, `scroll`, `click` events. No data leaves the browser — stored in `chrome.storage.local`. After 500+ keystrokes and 100+ clicks, calibration is complete. Recalibrates weekly.

### 1.6 Chrome Built-in AI — Local Tier 2 Intelligence

**Chrome AI APIs (April 2026 status):**
- **Prompt API** (`self.ai.languageModel`) — General-purpose prompting of Gemini Nano. Available in Chrome stable via origin trial (Chrome 137-148). Extensions can register via `chrome-extension://EXTENSION_ID` origin.
- **Writer API** (`self.ai.writer`) — Content generation conforming to a writing task. Origin trial Chrome 137-148.
- **Rewriter API** (`self.ai.rewriter`) — Revise and restructure text. Origin trial Chrome 137-148.
- **Summarizer API** — Adopted by W3C WebML Working Group for cross-browser standardization.

**System requirements:** macOS 13+, 22 GB free disk space for Gemini Nano model download. Chrome flag: `chrome://flags/#optimization-guide-on-device-model` must be enabled.

**Our usage — Prompt API for field analysis, Writer API for short answers:**

```javascript
async function analyzeFieldLocally(fieldLabel, inputType, options) {
    // Check if Prompt API is available
    const capabilities = await self.ai.languageModel.capabilities();
    if (capabilities.available === "no") return null;  // Fall through to Tier 3
    
    try {
        const session = await self.ai.languageModel.create({
            systemPrompt: "You fill job application forms for an ML Engineer with 2 years experience in the UK. Return only the answer value, nothing else."
        });
        
        let prompt = `Field: "${fieldLabel}" (${inputType})`;
        if (options.length > 0) prompt += `\nOptions: ${options.join(", ")}`;
        prompt += "\nAnswer:";
        
        const answer = await session.prompt(prompt);
        session.destroy();
        return answer.trim();
    } catch (e) {
        return null;  // Gemini Nano unavailable — fall through to Tier 3
    }
}

// Writer API for short professional answers (textarea fields)
async function writeShortAnswer(question, context) {
    const capabilities = await self.ai.writer.capabilities();
    if (capabilities.available === "no") return null;
    
    try {
        const writer = await self.ai.writer.create({
            tone: "formal",
            length: "short",
            sharedContext: "Job application for ML Engineer position in the UK."
        });
        const answer = await writer.write(question);
        writer.destroy();
        return answer;
    } catch (e) {
        return null;
    }
}
```

**When Chrome AI handles it (Tier 2):**
- "How did you hear about us?" → Prompt API → "LinkedIn"
- "Earliest start date?" → Prompt API → "Immediately"
- "Do you have a driving licence?" → Prompt API → "Yes"
- Short textarea answers → Writer API → professional 2-3 sentences
- Select dropdowns with obvious best option → Prompt API

**When it falls through to Tier 3 (API):**
- Open-ended "Why do you want to work here?" (needs company research from Perplexity)
- "Describe your experience with X" (needs full profile context)
- Complex multi-part questions
- Gemini Nano model not downloaded (22GB requirement not met)

### 1.7 Python ext_adapter.py — Replaces browser_manager.py

**Interface:** Implements the same contract that `BaseATSAdapter.fill_and_submit()` expects, but routes through the WebSocket instead of Playwright.

```python
class ExtensionBridge:
    """WebSocket server that communicates with the Chrome extension."""
    
    def __init__(self, host: str = "localhost", port: int = 8765):
        self.host = host
        self.port = port
        self._ws: WebSocket | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._snapshot: PageSnapshot | None = None
    
    async def start(self) -> None:
        """Start WebSocket server, wait for extension to connect."""
    
    async def stop(self) -> None:
        """Gracefully close connection and server."""
    
    async def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """Block until extension connects or timeout."""
    
    async def navigate(self, url: str, timeout_ms: int = 30000) -> PageSnapshot:
        """Navigate to URL, wait for snapshot."""
    
    async def fill(self, selector: str, value: str) -> FillResult:
        """Fill a field, wait for ack."""
    
    async def click(self, selector: str) -> bool:
        """Click element, wait for potential navigation."""
    
    async def upload(self, selector: str, file_path: Path) -> bool:
        """Read file, base64 encode, send to extension for DataTransfer upload."""
    
    async def screenshot(self) -> bytes:
        """Request screenshot from extension (canvas capture)."""
    
    async def select_option(self, selector: str, value: str) -> bool:
        """Select dropdown option."""
    
    async def check(self, selector: str, should_check: bool) -> bool:
        """Check/uncheck checkbox."""
    
    async def get_snapshot(self) -> PageSnapshot:
        """Get latest page snapshot (cached from last navigation/mutation)."""
    
    @property
    def connected(self) -> bool:
        """Whether extension is currently connected."""
```

**Adapter wrapper** — makes the extension look like a Playwright page to existing code:

```python
class ExtensionAdapter(BaseATSAdapter):
    """ATS adapter that uses the Chrome extension instead of Playwright."""
    name: str = "extension"
    
    def __init__(self, bridge: ExtensionBridge):
        self.bridge = bridge
    
    async def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None,
        profile: dict,
        custom_answers: dict,
        overrides: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Main entry point — uses state machine to drive the application."""
        platform = detect_ats_platform(url)
        machine = get_state_machine(platform)
        
        snapshot = await self.bridge.navigate(url)
        
        while not machine.is_terminal:
            state = machine.detect_state(snapshot)
            
            if state == "verification_wall":
                return {"success": False, "error": "Verification wall detected", "wall": snapshot.verification_wall}
            
            actions = machine.get_actions(state, snapshot, profile, custom_answers, cv_path, cover_letter_path)
            
            for action in actions:
                if action.type == "fill":
                    await self.bridge.fill(action.selector, action.value)
                elif action.type == "upload":
                    await self.bridge.upload(action.selector, action.file_path)
                elif action.type == "click":
                    await self.bridge.click(action.selector)
                elif action.type == "select":
                    await self.bridge.select_option(action.selector, action.value)
                elif action.type == "check":
                    await self.bridge.check(action.selector, action.value)
            
            # Wait for page update
            snapshot = await self.bridge.get_snapshot()
            machine.transition(state, snapshot)
        
        return {"success": True}
```

### 1.8 State Machines Per Platform

Each platform has a defined state machine. The snapshot tells us which state we're in, the machine tells us what to do.

```python
class ApplicationState(str, Enum):
    INITIAL = "initial"
    LOGIN_WALL = "login_wall"
    CONTACT_INFO = "contact_info"
    RESUME_UPLOAD = "resume_upload"
    EXPERIENCE = "experience"
    SCREENING_QUESTIONS = "screening_questions"
    REVIEW = "review"
    SUBMIT = "submit"
    CONFIRMATION = "confirmation"
    VERIFICATION_WALL = "verification_wall"
    ERROR = "error"

class PlatformStateMachine:
    """Base state machine for job application flows."""
    
    platform: str
    current_state: ApplicationState = ApplicationState.INITIAL
    
    def detect_state(self, snapshot: PageSnapshot) -> ApplicationState:
        """Analyze snapshot to determine current application state."""
        if snapshot.verification_wall:
            return ApplicationState.VERIFICATION_WALL
        # Platform-specific detection logic
        ...
    
    def get_actions(self, state, snapshot, profile, answers, cv_path, cl_path) -> list[Action]:
        """Return ordered list of actions for current state."""
        ...
    
    def transition(self, from_state, new_snapshot) -> ApplicationState:
        """Transition to next state based on new snapshot."""
        ...
    
    @property
    def is_terminal(self) -> bool:
        return self.current_state in (
            ApplicationState.CONFIRMATION,
            ApplicationState.VERIFICATION_WALL,
            ApplicationState.ERROR,
        )
```

**Platform-specific machines:**

| Platform | States | Key Differences |
|----------|--------|----------------|
| LinkedIn | login_check → contact → resume → experience → screening (multi-page) → review → submit | Multi-page wizard in modal, typeahead location field |
| Greenhouse | initial → contact → resume+CL → screening → submit | Single-page form, standard HTML IDs |
| Lever | initial → contact → resume+CL → screening → submit | Similar to Greenhouse, URLs patterns differ |
| Indeed | popup_dismiss → contact → resume → screening → submit | Cookie popups, aggressive bot detection |
| Workday | initial → sign_in_check → contact → resume → screening (multi-step) → submit | React SPA, data-automation-id attributes |
| Generic | initial → detect_form → fill_by_pattern → submit | Heuristic field matching |

**State detection uses snapshot signals:**
- URL patterns: `/apply`, `/jobs/`, `#apply`
- Field labels: "First Name" → contact_info, "Resume" → resume_upload
- Button text: "Submit Application" → review/submit state
- Page text: "Thank you" / "Application received" → confirmation

### 1.9 Perplexity Integration

**Module:** `jobpulse/perplexity.py`

**Perplexity Sonar API (April 2026):**
- `sonar` — Lightweight grounded search. $1/M input, $1/M output tokens. Latency < 2s. Best for: company lookups, salary queries.
- `sonar-pro` — Deeper retrieval with follow-ups. $3/M input, $15/M output. Best for: detailed company research with citations.
- `sonar-reasoning` — Real-time reasoning with search. Best for: complex salary benchmarking.
- `sonar-reasoning-pro` — Powered by DeepSeek-R1, exposes visible reasoning. Best for: red flag analysis.
- `sonar-deep-research` — Multi-step retrieval producing source-dense reports. $2/M input, $8/M output. Best for: deep company due diligence.

Citation tokens are no longer billed for `sonar` and `sonar-pro` (2026 update).

```python
class PerplexityClient:
    """Perplexity API client for company research and salary intelligence."""
    
    BASE_URL = "https://api.perplexity.ai/chat/completions"
    MODEL_FAST = "sonar"              # Quick lookups (~$0.002/call)
    MODEL_DEEP = "sonar-pro"          # Detailed research (~$0.01/call)
    MODEL_REASONING = "sonar-reasoning"  # Complex analysis
    
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("PERPLEXITY_API_KEY", "")
        self._cache = PerplexityCache()  # SQLite cache
    
    def research_company(self, company: str, deep: bool = False) -> CompanyResearch:
        """Full company research — cached for 7 days.
        
        Uses sonar for quick lookup, sonar-pro for deep research (e.g., dream companies).
        """
        cached = self._cache.get(company, "company")
        if cached:
            return cached
        
        model = self.MODEL_DEEP if deep else self.MODEL_FAST
        response = self._query(
            f"Company research for job application: {company}. "
            f"Return: 1) What the company does (1 sentence), "
            f"2) Industry and size (startup/SME/enterprise, employee count), "
            f"3) Tech stack (languages, frameworks, cloud), "
            f"4) Recent news (funding, layoffs, product launches), "
            f"5) Red flags (lawsuits, mass layoffs, glassdoor rating < 3.0), "
            f"6) Engineering culture (remote/hybrid, blog posts, open source).",
            model=model,
        )
        
        result = self._parse_company_research(response)
        self._cache.store(company, "company", result, ttl_days=7)
        return result
    
    def research_salary(self, role: str, company: str, location: str) -> SalaryResearch:
        """Salary range research — cached for 30 days."""
        cached = self._cache.get(f"{role}@{company}@{location}", "salary")
        if cached:
            return cached
        
        response = self._query(
            f"What is the salary range for {role} at {company} in {location} in 2026? "
            f"Check Glassdoor, Levels.fyi, LinkedIn Salary Insights. "
            f"Return: min, median, max in GBP. If company-specific data unavailable, "
            f"use industry average for {location}."
        )
        
        result = self._parse_salary(response)
        self._cache.store(f"{role}@{company}@{location}", "salary", result, ttl_days=30)
        return result
    
    def _query(self, prompt: str, model: str | None = None) -> str:
        """Make Perplexity Sonar API call. OpenAI-compatible endpoint."""
        resp = httpx.post(
            self.BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model or self.MODEL_FAST,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
```

**Pydantic models:**

```python
class CompanyResearch(BaseModel):
    company: str
    description: str = ""
    industry: str = ""
    size: str = ""                    # "startup" | "sme" | "enterprise"
    employee_count: int | None = None
    tech_stack: list[str] = []
    recent_news: list[str] = []
    red_flags: list[str] = []
    culture: str = ""
    glassdoor_rating: float | None = None
    researched_at: str = ""

class SalaryResearch(BaseModel):
    role: str
    company: str
    location: str
    min_gbp: int = 0
    median_gbp: int = 0
    max_gbp: int = 0
    source: str = ""                  # "glassdoor" | "levels_fyi" | "industry_avg"
    researched_at: str = ""
```

**Integration points:**

| Where | What Perplexity Provides |
|-------|-------------------------|
| `screening_answers.get_answer()` | Company context for "Why do you want to work here?" |
| `screening_answers._resolve_role_salary()` | Actual salary data instead of hardcoded ranges |
| `gate4_quality.check_company_background()` | Real company verification (not just generic name detection) |
| `pre_submit_gate.py` | Company-aware quality review |
| Side panel | Company intel displayed alongside the form |

**Cost (Perplexity Sonar pricing, April 2026):**
- Company lookup via `sonar`: ~$0.002/call (input $1/M + output $1/M, ~1K tokens each)
- Deep research via `sonar-pro`: ~$0.01/call (input $3/M + output $15/M, ~500 tokens each)
- Salary lookup via `sonar`: ~$0.002/call
- Citation tokens: FREE (2026 update — no longer billed for sonar and sonar-pro)
- Cached 7 days (company) / 30 days (salary)
- At 30 applications/day across ~20 unique companies: ~$0.08/day = ~$2.50/month

### 1.10 Pre-Submit Quality Gate

Before clicking Submit, the filled application is reviewed by an LLM:

```python
class PreSubmitGate:
    """Reviews the filled application as a recruiter would."""
    
    def review(
        self,
        snapshot: PageSnapshot,
        filled_answers: dict[str, str],
        jd_keywords: list[str],
        company_research: CompanyResearch,
    ) -> GateResult:
        """Score the application 0-10. Block if < 7."""
        
        prompt = (
            f"You are a FAANG recruiter reviewing this application for "
            f"{company_research.company}.\n\n"
            f"JD keywords: {', '.join(jd_keywords)}\n"
            f"Company: {company_research.description}\n\n"
            f"Filled answers:\n"
        )
        for label, answer in filled_answers.items():
            prompt += f"  {label}: {answer}\n"
        
        prompt += (
            "\nScore 0-10 and return JSON:\n"
            '{"score": N, "weaknesses": ["..."], "suggestions": ["..."]}\n'
            "Focus on: generic/copy-pasted text, missing JD keywords, "
            "tone mismatches, factual errors."
        )
        
        # Parse response, block if score < 7
        ...
    
    def optimize_weak_answers(
        self,
        weak_fields: list[str],
        filled_answers: dict[str, str],
        jd_keywords: list[str],
        company_research: CompanyResearch,
    ) -> dict[str, str]:
        """Rewrite weak answers with ATS keyword injection."""
        ...
```

**Flow:**
1. All fields filled → snapshot captured
2. PreSubmitGate reviews → score + weaknesses
3. If score >= 7 → proceed to submit
4. If score < 7 → optimize weak answers → re-fill → re-review (max 2 iterations)
5. If still < 7 after 2 iterations → send to Notion "Needs Review" (don't submit)

### 1.11 Telegram Live Stream

```python
class TelegramApplicationStream:
    """Streams application progress to Telegram in real-time."""
    
    async def stream_start(self, job: dict, company_research: CompanyResearch):
        """Send initial message with company intel."""
        msg = (
            f"🔄 Applying: {job['role']} at {job['company']}\n"
            f"📊 {company_research.size} | {company_research.industry}\n"
            f"💰 {company_research.salary_range}\n"
            f"🔧 {', '.join(company_research.tech_stack[:5])}"
        )
        self._msg_id = await self._send(msg)
    
    async def stream_field(self, label: str, value: str, tier: int, confident: bool):
        """Update message with field progress."""
        icon = "✅" if confident else "⚠️"
        tier_label = ["Pattern", "Nano", "LLM", "Vision"][tier - 1]
        self._lines.append(f"{icon} {label}: {value[:50]} [{tier_label}]")
        await self._edit(self._msg_id, self._format())
    
    async def stream_uncertain(self, label: str, draft: str) -> str:
        """Pause for human review. Returns approved/edited answer."""
        msg = (
            f"⚠️ Uncertain answer for:\n"
            f"Q: {label}\n"
            f"Draft: {draft}\n\n"
            f"Reply: ✅ to approve, or type a better answer"
        )
        await self._send(msg)
        # Poll for reply (30 second timeout, then use draft)
        reply = await self._wait_for_reply(timeout=30)
        if reply == "✅" or reply is None:
            return draft
        return reply
    
    async def stream_complete(self, success: bool, gate_score: float):
        """Final status."""
        icon = "✅" if success else "❌"
        await self._edit(self._msg_id, self._format() + f"\n\n{icon} Score: {gate_score}/10")
```

### 1.12 Side Panel Dashboard

**sidepanel.html** — Real-time UI alongside the job page:

Sections:
1. **Connection status** — green/red indicator, Python backend connected/disconnected
2. **Current application** — company, role, progress bar, current state in state machine
3. **Company intel** — Perplexity research summary (industry, size, tech stack, red flags)
4. **Field log** — each filled field with tier indicator and value
5. **Controls** — Pause, Resume, Skip Field, Edit Answer, Abort
6. **Queue** — upcoming applications waiting

**Communication:** Side panel receives updates from background.js via `chrome.runtime.onMessage`. No direct WebSocket — background.js relays Python messages to both content script and side panel.

### 1.13 Config Changes

New environment variables in `jobpulse/config.py`:

```python
# Perplexity
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

# Extension bridge
EXT_BRIDGE_HOST = os.getenv("EXT_BRIDGE_HOST", "localhost")
EXT_BRIDGE_PORT = int(os.getenv("EXT_BRIDGE_PORT", "8765"))

# Application engine mode
APPLICATION_ENGINE = os.getenv("APPLICATION_ENGINE", "extension")  # "extension" | "playwright"
```

When `APPLICATION_ENGINE=extension`, `applicator.py` uses `ExtensionAdapter` instead of platform-specific Playwright adapters. When `APPLICATION_ENGINE=playwright`, existing behavior is preserved (backwards compatible).

## Phase 2: Get Smarter Over Time

### 2.1 Semantic Answer Cache

Replace exact-match caching in `screening_answers.py` with embedding-based semantic matching:

```python
class SemanticAnswerCache:
    """Cache answers by meaning, not exact text."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._embedder = None  # Lazy load all-MiniLM-L6-v2
    
    def find_similar(self, question: str, threshold: float = 0.85) -> CachedAnswer | None:
        """Find semantically similar cached question."""
        embedding = self._embed(question)
        # SQLite + cosine similarity on stored embeddings
        ...
    
    def store(self, question: str, answer: str, company: str, outcome: str | None = None):
        """Store answer with embedding for future matching."""
        ...
    
    def personalize(self, cached_answer: str, company_research: CompanyResearch) -> str:
        """Re-personalize a cached answer with new company context."""
        ...
```

**Integration:** Inserted as Tier 1.5 in `get_answer()` resolution:
1. Pattern match (COMMON_ANSWERS)
2. **Semantic cache** (new)
3. Gemini Nano (via extension)
4. LLM API
5. Vision model

### 2.2 Outcome Learning Loop

Track which answers lead to interviews:

```python
class OutcomeTracker:
    """Tracks application outcomes and correlates with answer quality."""
    
    def record_application(self, job_id: str, answers: dict[str, str], company: str):
        """Store all answers for this application."""
    
    def record_outcome(self, job_id: str, outcome: Literal["interview", "rejected", "ghosted"]):
        """Record what happened — triggered by Gmail agent detecting response."""
    
    def get_best_answers(self, question_embedding: list[float], top_k: int = 5) -> list[ScoredAnswer]:
        """Find answers to similar questions that led to interviews."""
    
    def get_answer_stats(self) -> AnswerStats:
        """Which answer patterns have the highest interview conversion rate."""
```

**Data flow:**
1. Application submitted → `record_application()` stores all answers
2. Days later → Gmail agent detects "interview invitation" or "unfortunately" email
3. `record_outcome()` links the outcome to stored answers
4. Future applications → `get_best_answers()` biases toward proven answers
5. After 50+ applications with outcomes → statistical significance kicks in

### 2.3 ATS Keyword Injection

Before submitting, check every open-ended answer against JD keywords:

```python
def inject_keywords(answer: str, jd_keywords: list[str], max_additions: int = 3) -> str:
    """Naturally weave missing JD keywords into the answer."""
    present = {kw for kw in jd_keywords if kw.lower() in answer.lower()}
    missing = [kw for kw in jd_keywords if kw not in present][:max_additions]
    
    if not missing:
        return answer
    
    # LLM rewrites answer incorporating missing keywords
    prompt = (
        f"Rewrite this answer to naturally include these keywords: {', '.join(missing)}\n"
        f"Original: {answer}\n"
        f"Rules: Keep the same meaning and tone. Don't keyword-stuff. "
        f"One mention per keyword is enough."
    )
    return llm_rewrite(prompt)
```

### 2.4 Smart Retry with Diagnosis

```python
class RetryDiagnostic:
    """Diagnoses application failures and determines retry strategy."""
    
    def diagnose(self, error: dict, snapshot: PageSnapshot | None) -> Diagnosis:
        """Analyze why an application failed."""
        if error.get("wall"):
            return Diagnosis(
                cause="verification_wall",
                retryable=True,
                strategy="wait_and_retry",
                wait_hours=4,
                adjustments={"reduce_speed": True, "increase_delays": True},
            )
        if "validation" in error.get("error", "").lower():
            return Diagnosis(
                cause="form_validation",
                retryable=True,
                strategy="fix_and_retry",
                field_fixes=self._analyze_validation_error(error, snapshot),
            )
        ...
    
    def apply_adjustments(self, diagnosis: Diagnosis):
        """Adjust behavior for retry attempt."""
        ...
```

### 2.5 Multi-Tab Parallel Applications

For simple applications (single-page forms with no screening questions):

```python
class ParallelApplicationManager:
    """Manage multiple simultaneous applications in different tabs."""
    
    MAX_PARALLEL = 3  # Conservative — more tabs = more suspicious
    
    async def apply_batch(self, jobs: list[Job], bridge: ExtensionBridge):
        """Apply to multiple simple jobs in parallel tabs."""
        simple = [j for j in jobs if self._is_simple(j)]
        complex_ = [j for j in jobs if not self._is_simple(j)]
        
        # Simple jobs: parallel tabs
        for batch in chunks(simple, self.MAX_PARALLEL):
            tasks = [self._apply_in_tab(j, bridge) for j in batch]
            await asyncio.gather(*tasks)
        
        # Complex jobs: sequential (need human oversight)
        for job in complex_:
            await self._apply_in_tab(job, bridge)
    
    def _is_simple(self, job: Job) -> bool:
        """Single-page form, no screening questions, standard fields."""
        return job.ats_platform in ("greenhouse", "lever") and not job.has_screening_questions
```

## Phase 3: Full Autopilot

### 3.1 Recruiter & Hiring Manager Detection

```python
class RecruiterFinder:
    """Find the hiring manager or recruiter for a role via Perplexity."""
    
    def find(self, company: str, role: str) -> RecruiterInfo | None:
        perplexity = PerplexityClient()
        response = perplexity._query(
            f"Who is the recruiter or hiring manager for {role} at {company}? "
            f"Check LinkedIn. Return name, title, and LinkedIn URL if available."
        )
        return self._parse(response)
```

Stored in Notion Job Tracker "Recruiter" column. Telegram notification: "Found: Sarah Chen (Senior Recruiter). Send follow-up?"

### 3.2 Application Timing Optimizer

```python
class TimingOptimizer:
    """Queue applications for optimal send times based on historical data."""
    
    # Research-backed defaults (overridden by personal outcome data)
    DEFAULT_WINDOWS = {
        "linkedin": {"days": [1, 2, 3], "hours": (9, 11)},     # Tue-Thu 9-11am
        "greenhouse": {"days": [1, 2, 3, 4], "hours": (8, 12)}, # Mon-Fri 8am-12pm
        "indeed": {"days": [1, 2], "hours": (10, 14)},           # Tue-Wed 10am-2pm
    }
    
    def should_apply_now(self, platform: str, job_posted_hours_ago: int) -> bool:
        """Apply immediately if fresh (< 24h) regardless of timing."""
        if job_posted_hours_ago < 24:
            return True
        return self._in_optimal_window(platform)
    
    def next_optimal_slot(self, platform: str) -> datetime:
        """When to schedule this application."""
        ...
```

### 3.3 Follow-Up Automation

```python
class FollowUpManager:
    """Schedule and draft follow-up messages after applications."""
    
    SCHEDULE = [
        {"days_after": 5, "type": "initial", "channel": "email"},
        {"days_after": 12, "type": "second", "channel": "email"},
        {"days_after": 20, "type": "final", "channel": "linkedin"},
    ]
    
    def draft_followup(self, job: dict, company_research: CompanyResearch, attempt: int) -> str:
        """Draft a follow-up message using Perplexity company context."""
        ...
    
    def check_pending_followups(self) -> list[PendingFollowUp]:
        """Called by daily cron — returns follow-ups due today."""
        ...
```

Telegram notification: "Follow-up due for {company}. Draft ready. Send/Edit/Skip?"

### 3.4 Referral Network Scanner

```python
class ReferralScanner:
    """Check if user has connections at target company."""
    
    def scan(self, company: str) -> list[Connection]:
        connections = []
        
        # 1. LinkedIn connections (extension reads the page)
        # Navigate to linkedin.com/search/results/people/?company={company_id}
        # Extension reports connection names
        
        # 2. Gmail history
        # Search for emails from @company.com domain
        
        # 3. Past applications in Notion
        # Check if previously applied to same company
        
        return connections
```

Telegram: "You know James Park at {company} (connected 2024). Ask for referral? Referrals get 10x interview rate."

### 3.5 Application Strength Predictor

```python
class StrengthPredictor:
    """Predict interview probability before starting an application."""
    
    def predict(self, job: Job, profile: dict, company_research: CompanyResearch) -> Prediction:
        signals = {
            "skill_match": job.match_score / 100,
            "company_response_rate": self._estimate_response_rate(company_research),
            "role_freshness": self._freshness_score(job.posted_at),
            "competition": self._competition_score(job.applicant_count),
            "referral": 1.0 if self._has_referral(job.company) else 0.0,
            "salary_fit": self._salary_alignment(job, company_research),
            "past_success": self._past_success_rate(job.company, job.role),
        }
        
        score = sum(w * signals[k] for k, w in self.WEIGHTS.items())
        tier = "strong" if score > 0.7 else "worth_it" if score > 0.4 else "long_shot"
        return Prediction(score=score, tier=tier, signals=signals)
```

### 3.6 Post-Apply Monitoring

```python
class PostApplyMonitor:
    """Monitor for application status changes via Gmail."""
    
    def verify_submission(self, job_id: str):
        """Check for confirmation email within 5 minutes of submission."""
        # Gmail agent searches for "thank you for applying" from company domain
        ...
    
    def daily_status_check(self):
        """Scan Gmail for interview invites, rejections, status updates."""
        # Parse emails, update Notion status
        # Extract interview date/time → add to Calendar
        ...
```

### 3.7 Job Freshness Prioritizer

```python
def prioritize_queue(jobs: list[Job], company_cache: dict) -> list[Job]:
    """Sort job queue by composite priority score."""
    for job in jobs:
        freshness = max(0, 1.0 - (hours_since_posted(job) / 168))  # Decays over 7 days
        job.priority_score = (
            job.match_score / 100 * 0.35 +
            freshness * 0.30 +
            company_quality(job.company, company_cache) * 0.20 +
            (0.15 if has_referral(job.company) else 0.0)
        )
    return sorted(jobs, key=lambda j: j.priority_score, reverse=True)
```

### 3.8 Cross-Platform Intelligence

```python
def choose_best_platform(job: Job, platforms: list[str], stats: dict) -> str:
    """Route application through highest-success-rate platform."""
    PRIORITY = {
        "referral": 5,          # Internal referral (if available)
        "company_direct": 4,    # Company careers page
        "greenhouse": 3,        # Direct ATS
        "lever": 3,
        "linkedin": 2,          # Aggregator
        "indeed": 1,            # Lowest priority
        "generic": 0,
    }
    
    available = [(p, PRIORITY.get(p, 0)) for p in platforms]
    # Adjust by personal success rate data
    for p, score in available:
        if p in stats:
            score *= stats[p].get("interview_rate", 0.5) / 0.5
    
    return max(available, key=lambda x: x[1])[0]
```

## File Structure

```
jobpulse/
├── ext_bridge.py              # WebSocket server for extension communication
├── ext_adapter.py             # ExtensionAdapter (replaces Playwright adapters)
├── perplexity.py              # Perplexity API client + cache
├── pre_submit_gate.py         # Pre-submit quality review
├── telegram_stream.py         # Live application progress streaming
├── state_machines/
│   ├── __init__.py            # PlatformStateMachine base + registry
│   ├── linkedin.py            # LinkedIn Easy Apply flow
│   ├── greenhouse.py          # Greenhouse flow
│   ├── lever.py               # Lever flow
│   ├── indeed.py              # Indeed flow
│   ├── workday.py             # Workday flow
│   └── generic.py             # Generic fallback flow
├── semantic_cache.py          # Phase 2: Embedding-based answer cache
├── outcome_tracker.py         # Phase 2: Interview outcome tracking
├── keyword_injector.py        # Phase 2: ATS keyword optimization
├── retry_diagnostic.py        # Phase 2: Failure diagnosis + retry
├── parallel_manager.py        # Phase 2: Multi-tab applications
├── recruiter_finder.py        # Phase 3: Recruiter detection
├── timing_optimizer.py        # Phase 3: Optimal send time
├── followup_manager.py        # Phase 3: Follow-up automation
├── referral_scanner.py        # Phase 3: Network scanning
├── strength_predictor.py      # Phase 3: Pre-application prediction
├── post_apply_monitor.py      # Phase 3: Status monitoring
├── freshness_prioritizer.py   # Phase 3: Queue optimization
└── platform_router.py         # Phase 3: Cross-platform routing

extension/
├── manifest.json
├── background.js              # Service worker + WebSocket client
├── content.js                 # Page scanner + form filler + behavior profile
├── sidepanel.html             # Dashboard layout
├── sidepanel.js               # Dashboard logic
├── popup.html                 # Quick status popup
├── popup.js                   # Popup logic
├── protocol.js                # Message type definitions
├── icons/
│   ├── icon16.png
│   ├── icon48.png
│   └── icon128.png
└── styles/
    ├── sidepanel.css
    └── popup.css

tests/
├── test_ext_bridge.py
├── test_ext_adapter.py
├── test_perplexity.py
├── test_pre_submit_gate.py
├── test_state_machines.py
├── test_semantic_cache.py
├── test_outcome_tracker.py
└── ...
```

## Migration Plan

1. **Extension + bridge built first** — `APPLICATION_ENGINE=extension` env var to opt-in
2. **Existing Playwright adapters untouched** — `APPLICATION_ENGINE=playwright` (default) keeps current behavior
3. **Gradual migration** — Test extension on Greenhouse/Lever first (simplest flows), then LinkedIn, then Indeed/Workday
4. **Once stable** — Flip default to `extension`, deprecate Playwright adapters
5. **Playwright adapters kept** as fallback for edge cases (e.g., headless CI testing)

## Cost Estimate

| Component | Cost | Frequency |
|-----------|------|-----------|
| Perplexity company research (`sonar`) | ~$0.002/company | Per unique company (cached 7 days) |
| Perplexity deep research (`sonar-pro`) | ~$0.01/company | Dream companies only |
| Perplexity salary research (`sonar`) | ~$0.002/lookup | Per role+company (cached 30 days) |
| Pre-submit gate (GPT-4.1-mini) | ~$0.002/app | Per application |
| Tier 3 LLM answers (GPT-4.1-mini) | ~$0.002/question | ~4 questions per complex app |
| Chrome Prompt/Writer API (Tier 2) | Free | Local Gemini Nano, unlimited |
| Pattern match (Tier 1) | Free | Instant |
| Vision diagnosis (Tier 4) | ~$0.01/screenshot | Rare (~5% of apps) |
| Perplexity citation tokens | Free | 2026 update — no longer billed |
| **Daily total (30 apps, 20 companies)** | **~$0.20** | |
| **Monthly total** | **~$6.00** | |

## Dependencies

- `websockets>=14.0` — Python WebSocket server (latest stable as of April 2026)
- Perplexity API — No SDK needed, raw httpx POST to OpenAI-compatible REST API (`https://api.perplexity.ai/chat/completions`)
- Chrome 137+ for Prompt API / Writer API / Rewriter API origin trials (Gemini Nano)
- Chrome flag: `chrome://flags/#optimization-guide-on-device-model` enabled
- macOS 13+ with 22 GB free disk space (for Gemini Nano model download)
- No new pip packages required beyond websockets

## References (April 2026)

- [Perplexity Sonar API Models](https://docs.perplexity.ai/getting-started/models/models/sonar) — sonar, sonar-pro, sonar-reasoning, sonar-reasoning-pro, sonar-deep-research
- [Perplexity API Pricing](https://docs.perplexity.ai/docs/getting-started/pricing) — $1/M tokens (sonar), citation tokens free
- [Chrome Built-in AI APIs](https://developer.chrome.com/docs/ai/built-in-apis) — Prompt API, Writer API, Rewriter API, Summarizer API
- [Chrome Prompt API](https://developer.chrome.com/docs/ai/prompt-api) ��� self.ai.languageModel interface
- [Chrome Writer API](https://developer.chrome.com/docs/ai/writer-api) — self.ai.writer interface
- [Chrome Rewriter API](https://developer.chrome.com/docs/ai/rewriter-api) — self.ai.rewriter interface
- [WebSockets in MV3 Service Workers](https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets) — Keepalive heartbeat requirement
- [MV3 Service Worker Lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle) — 30s idle timeout
- [Playwright Stealth 2026 Limitations](https://dicloak.com/blog-detail/playwright-stealth-what-works-in-2026-and-where-it-falls-short) — Why stealth plugins fail
- [DataDome LLM Crawler Detection](https://moonito.net/comparisons/best-bot-detection-tools) — AI agent traffic now a first-class detection category
- [PerimeterX Per-Customer ML Models](https://scrapfly.io/blog/posts/how-to-bypass-perimeterx-human-anti-scraping) — Custom models per website
- [Chrome Gemini Integration (Jan 2026)](https://techcrunch.com/2026/01/28/chrome-takes-on-ai-browsers-with-tighter-gemini-integration-agentic-features-for-autonomous-tasks/) — Agentic browsing features

## Tests

```
tests/
├── test_ext_bridge.py          # WebSocket server, connection, reconnection, message ordering
├── test_ext_adapter.py         # fill_and_submit via mock bridge, state machine transitions
├── test_perplexity.py          # API calls, caching, parsing, error handling
├── test_pre_submit_gate.py     # Score calculation, weak answer detection, keyword injection
├── test_state_machines.py      # Per-platform state detection and transitions
├── test_semantic_cache.py      # Embedding matching, personalization, cache eviction
├── test_outcome_tracker.py     # Record/retrieve, statistical significance, answer ranking
├── test_telegram_stream.py     # Message formatting, uncertain answer flow, timeout
├── test_keyword_injector.py    # Keyword detection, natural rewriting
├── test_retry_diagnostic.py    # Failure classification, strategy selection
├── test_parallel_manager.py    # Simple/complex classification, batch execution
└── conftest.py                 # Mock WebSocket, mock Perplexity API, sample snapshots
```

All tests use `tmp_path` for databases. Never touch `data/*.db`.
