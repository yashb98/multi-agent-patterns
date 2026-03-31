# Verification Wall Learning System — Design Spec

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement.

**Goal:** Build a universal, self-improving verification/CAPTCHA wall detection and avoidance system that learns from every block event across all platforms.

**Architecture:** Event-sourced learning with statistical correlation (zero LLM cost) + periodic LLM pattern analysis (every 5th block). Universal module used by all scanners. 2-hour cooldown with exponential backoff on blocks. Human-like page interaction with full DOM load waits.

**Tech Stack:** SQLite (scan_learning.db), Playwright, GPT-5o-mini (periodic analysis only)

---

## 1. Verification Detector (`jobpulse/verification_detector.py`)

Universal block page detection that runs after every `page.goto()` and after every card click in any scanner.

### Detection Targets

| Wall Type | Detection Method |
|-----------|-----------------|
| `cloudflare` | Selectors: `#challenge-running`, `.cf-turnstile`, iframe src containing `challenges.cloudflare.com` |
| `recaptcha` | Selectors: `.g-recaptcha`, `#recaptcha-anchor`, iframe src containing `google.com/recaptcha` |
| `hcaptcha` | Selectors: `.h-captcha`, iframe src containing `hcaptcha.com` |
| `text_challenge` | Page body text matches: "verify you are human", "please verify", "are you a robot", "unusual traffic", "automated requests" (case-insensitive) |
| `http_block` | HTTP status 403, 429, or 503 + page content containing "access denied", "blocked", "forbidden" |
| `empty_anomaly` | 0 job cards found when query is expected to return results (soft signal, confidence 0.5) |

### Interface

```python
@dataclass
class VerificationResult:
    wall_type: str          # "cloudflare" | "recaptcha" | "hcaptcha" | "text_challenge" | "http_block" | "empty_anomaly"
    confidence: float       # 0.0-1.0
    page_url: str
    page_title: str
    screenshot_path: str | None  # saved to data/screenshots/
    detected_at: datetime

def detect_verification_wall(page) -> VerificationResult | None:
    """Check current page for verification walls. Returns None if clean."""
```

### Screenshot Capture

On block detection with confidence >= 0.7, save a screenshot to `data/screenshots/{platform}_{timestamp}.png`. Auto-cleanup screenshots older than 30 days.

---

## 2. Scan Event Recorder (`jobpulse/scan_learning.py`)

Records every scan session with 17 signals for correlation analysis.

### ScanEvent Schema

```python
@dataclass
class ScanEvent:
    id: str                        # UUID4
    platform: str                  # "indeed", "linkedin", "reed", etc.
    timestamp: datetime            # UTC
    time_of_day_bucket: str        # "morning" (6-12) | "afternoon" (12-17) | "evening" (17-22) | "night" (22-6)
    requests_in_session: int       # total pages loaded this session
    avg_delay_between_requests: float  # seconds between requests
    session_age_seconds: float     # how long browser has been open
    user_agent_hash: str           # SHA256[:8] of UA string
    was_fresh_session: bool        # new cookies vs reused profile
    used_vpn: bool                 # True if IP differs from baseline
    simulated_mouse: bool          # did we scroll/move mouse before block
    referrer_chain: str            # "direct" | "homepage_first" | "search_to_detail"
    search_query: str              # job title searched when event occurred
    pages_before_block: int        # cards/pages visited before wall appeared
    browser_fingerprint: str       # SHA256[:8] of browser config (viewport + UA + args)
    waited_for_page_load: bool     # did we wait for full DOM load
    page_load_time_ms: int         # how long the page took to load
    outcome: str                   # "success" | "blocked" | "timeout" | "empty"
    wall_type: str | None          # from VerificationResult, None if success
```

### SQLite Tables

**Table: `scan_events`**

All 17 signal columns + `id`, `outcome`, `wall_type`, `created_at`. Primary key on `id`.

**Table: `learned_rules`**

```sql
CREATE TABLE learned_rules (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    rule_text TEXT NOT NULL,          -- human-readable pattern
    confidence FLOAT NOT NULL,        -- 0.0-1.0
    recommendation TEXT NOT NULL,     -- what to change
    source TEXT NOT NULL,             -- "statistical" | "llm"
    created_at TEXT NOT NULL,
    times_applied INT DEFAULT 0,
    times_successful INT DEFAULT 0    -- applied and no block occurred
);
```

**Table: `cooldowns`**

```sql
CREATE TABLE cooldowns (
    platform TEXT PRIMARY KEY,
    blocked_at TEXT NOT NULL,
    cooldown_until TEXT NOT NULL,     -- UTC timestamp
    consecutive_blocks INT DEFAULT 1,
    last_wall_type TEXT
);
```

**Database:** `data/scan_learning.db` (separate from other DBs).

---

## 3. Statistical Correlation Engine

Pure Python, zero LLM calls. Runs after every block event.

### Algorithm

1. Query all `scan_events` for the blocked platform (last 90 days)
2. For each signal, bucket the values and compute:
   - `block_rate = count(outcome="blocked") / count(all)` per bucket
3. Signals with `block_rate > 0.50` and `sample_size >= 3` become **risk factors**
4. Store risk factors as `learned_rules` with `source="statistical"`

### Bucketed Signals

| Signal | Buckets |
|--------|---------|
| `time_of_day_bucket` | morning, afternoon, evening, night |
| `requests_in_session` | 1-3, 4-6, 7-10, 11+ |
| `avg_delay_between_requests` | <2s, 2-4s, 4-8s, 8s+ |
| `session_age_seconds` | <300, 300-600, 600-900, 900+ |
| `user_agent_hash` | per distinct UA |
| `was_fresh_session` | true, false |
| `simulated_mouse` | true, false |
| `referrer_chain` | direct, homepage_first, search_to_detail |
| `pages_before_block` | 1-3, 4-6, 7-10, 11+ |
| `waited_for_page_load` | true, false |

### Risk Factor Example

After 10 events:
- `time_of_day=morning`: 4 blocks / 5 scans = 80% block rate → **RISK FACTOR**
- `time_of_day=afternoon`: 1 block / 5 scans = 20% → safe
- `requests_in_session=7-10`: 3 blocks / 3 scans = 100% → **RISK FACTOR**

---

## 4. LLM Pattern Analyzer

Triggered after every 5th block event across all platforms. Uses GPT-5o-mini.

### Input

Last 20 `scan_events` for the platform, formatted as a table with all signals + outcomes.

### Prompt

```
You are analyzing job scraping session data to find patterns that trigger verification walls.

Here are the last 20 scan sessions for {platform}:
{events_table}

Identify the pattern that most likely triggers blocks. Return JSON:
{
  "pattern": "human-readable description of the trigger pattern",
  "confidence": 0.0-1.0,
  "recommendation": "specific parameter changes to avoid the pattern",
  "risk_signals": ["signal1", "signal2"]
}
```

### Output

Stored in `learned_rules` table with `source="llm"`. Cost: ~$0.002 per call, estimated 1-2 calls/month.

---

## 5. Adaptive Parameters

Before each scan, queries `learned_rules` and current conditions to build scan parameters.

### Interface

```python
@dataclass
class AdaptiveParams:
    delay_range: tuple[float, float]    # seconds between requests
    max_requests: int                    # per session
    user_agent: str                      # selected UA (avoiding blocked ones)
    wait_for_load: bool                  # always True (user requirement)
    simulate_human: bool                 # scroll, mouse movement
    session_max_age_seconds: int         # restart browser after this
    referrer_strategy: str               # "direct" | "homepage_first"
    cooldown_active: bool                # True if in cooldown
    cooldown_until: datetime | None      # when cooldown ends
    risk_level: str                      # "low" | "medium" | "high"

def get_adaptive_params(platform: str) -> AdaptiveParams:
    """Build scan parameters based on learned rules + current conditions."""
```

### Default Parameters (before any learning)

| Parameter | Default | After Learning (example) |
|-----------|---------|--------------------------|
| `delay_range` | (2.0, 8.0) | (5.0, 12.0) |
| `max_requests` | 50 | 5 |
| `simulate_human` | False | True |
| `session_max_age_seconds` | 1800 | 480 |
| `referrer_strategy` | "direct" | "homepage_first" |
| `wait_for_load` | True | True (always) |

### Risk Level Calculation

- **Low:** 0 active risk factors → use defaults
- **Medium:** 1 risk factor → increase delays 50%, reduce max_requests by half
- **High:** 2+ risk factors → maximum delays, minimum requests, full human simulation

---

## 6. Cooldown Manager

Handles post-block cooldown with exponential backoff.

### Flow

1. **First block:** 2-hour cooldown
2. **Second block (same day):** 4-hour cooldown (doubles)
3. **Third consecutive block:** 48-hour skip + Telegram alert
4. **Max cooldown:** 48 hours
5. **Cooldown reset:** After a successful scan, `consecutive_blocks` resets to 0

### Persistence

Cooldowns stored in `cooldowns` table. Survives process restarts. Checked by `can_scan_now(platform) -> bool` before every scan.

### Telegram Alert (on 3rd consecutive block)

```
⚠️ {Platform} blocked 3 times consecutively.
Skipping for 48 hours (until {datetime}).
Last wall type: {wall_type}
Top risk factors: {risk_factors}
```

---

## 7. Human-Like Page Interaction

Added to all scanners, not just Indeed. Runs before extracting data from any page.

### Sequence

1. `page.goto(url, wait_until="networkidle")` — wait for full DOM + network settle
2. Random delay 1-3s (reading time)
3. Scroll down 300-600px slowly (50px increments with 100ms between)
4. Random mouse movement to a visible element
5. Wait 0.5-1.5s
6. Then extract data

### Page Load Wait

Every `page.goto()` uses `wait_until="networkidle"` instead of `"domcontentloaded"`. Yes, slower — but matches real user behavior and is what the user requested.

---

## 8. Scanner Integration Points

Each scanner (`scan_indeed`, `scan_linkedin`, `scan_reed`) gets 3 additions:

### Pre-Scan Gate

```python
params = get_adaptive_params("indeed")
if params.cooldown_active:
    logger.info("scan_indeed: in cooldown until %s, skipping", params.cooldown_until)
    return []
```

### Post-Page Verification Check

```python
wall = detect_verification_wall(page)
if wall:
    record_block_event(platform="indeed", wall=wall, session_signals=signals)
    start_cooldown("indeed", wall)
    logger.warning("scan_indeed: verification wall detected (%s), aborting", wall.wall_type)
    return results_so_far  # return whatever we got before the block
```

### Human Interaction Before Extraction

```python
await simulate_human_interaction(page)  # scroll, mouse, wait
# then extract job cards
```

---

## 9. Testing Strategy

- **Unit tests:** Detection patterns (mock HTML pages with each wall type)
- **Unit tests:** Statistical correlation (synthetic events → verify risk factors)
- **Unit tests:** Cooldown logic (consecutive blocks → correct backoff)
- **Unit tests:** Adaptive params (risk factors → correct param adjustment)
- **Integration test:** Full flow — block detected → event recorded → params adapted → cooldown set
- All tests use `tmp_path` for `scan_learning.db` — never touch production DB

---

## 10. File Map

| File | Purpose |
|------|---------|
| `jobpulse/verification_detector.py` | Universal block page detection |
| `jobpulse/scan_learning.py` | Event recording, stat engine, LLM analyzer, adaptive params, cooldown |
| `data/scan_learning.db` | SQLite for events, rules, cooldowns |
| `data/screenshots/` | Block page screenshots (auto-cleanup 30 days) |
| `tests/test_verification_detector.py` | Detection unit tests |
| `tests/test_scan_learning.py` | Learning engine + cooldown tests |

No changes to existing files except adding 3 integration points (pre-scan gate, post-page check, human interaction) to each scanner in `job_scanner.py`.
