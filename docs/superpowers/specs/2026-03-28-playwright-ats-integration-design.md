# Playwright ATS Integration — Design Spec

## Goal

Add browser automation via Playwright to the existing Job Autopilot pipeline, enabling auto-submission of job applications across 5 platforms with human-like timing, per-platform rate limits, and hybrid approval flow.

## Constraints

- **40 applications/day total** across all platforms
- Per-platform caps: LinkedIn 15, Indeed 10, Reed 4, TotalJobs 4, Direct ATS 7
- Human-like timing: 2-8s random delays, 50-150ms typing, Bezier mouse paths
- Session breaks: 5-min pause every 10 applications
- Real Chrome profile (not headless) — uses existing login sessions
- Screenshot on failure, skip on captcha, notify via Telegram
- Hybrid approval: 95%+ auto-submit, 85-94% batch review, <85% skip

## 6 Scan Windows

| Window | Time | Platforms |
|--------|------|-----------|
| 1 | 7:00 AM | LinkedIn, Indeed |
| 2 | 10:00 AM | Reed, TotalJobs |
| 3 | 1:00 PM | LinkedIn, Glassdoor |
| 4 | 4:30 PM | Indeed, Reed |
| 5 | 7:00 PM | LinkedIn, TotalJobs |
| 6 | 2:00 AM | Greenhouse/Lever/Workday |

## Architecture

### Browser Manager (`jobpulse/browser_manager.py`)

Single shared Playwright browser instance that:
- Launches Chromium with persistent user data dir (real cookies/sessions)
- Creates new pages per application (not new browsers)
- Provides human-like action primitives: `human_type()`, `human_click()`, `random_delay()`, `human_scroll()`
- Screenshots on error to `data/applications/{job_id}/error.png`
- Closes browser between scan windows (not kept alive 24/7)

```python
class BrowserManager:
    async def launch() -> None
    async def new_page() -> Page
    async def close() -> None
    async def human_type(page, selector, text) -> None  # 50-150ms/char, occasional typo
    async def human_click(page, selector) -> None  # Bezier mouse path + random delay
    async def random_delay(min_s=2, max_s=8) -> None
    async def screenshot_error(page, job_id, step) -> Path
```

### Rate Limiter (`jobpulse/rate_limiter.py`)

Per-platform daily quota tracking in SQLite:

```python
class RateLimiter:
    def can_apply(platform: str) -> bool  # Check quota
    def record_application(platform: str) -> None  # Decrement quota
    def get_remaining() -> dict[str, int]  # All platforms
    def reset_daily() -> None  # Called at midnight
    def should_take_break() -> bool  # True every 10 apps
    def record_break_taken() -> None
```

Caps stored in config, tracked in `data/rate_limits.db`:
```python
DAILY_CAPS = {
    "linkedin": 15, "indeed": 10, "reed": 4,
    "totaljobs": 4, "greenhouse": 7, "lever": 7, "workday": 7,
}
TOTAL_DAILY_CAP = 40
SESSION_BREAK_EVERY = 10
SESSION_BREAK_MINUTES = 5
```

### ATS Adapter Base (`jobpulse/ats_adapters/base.py`)

```python
class ATSAdapter(ABC):
    def __init__(self, browser: BrowserManager, rate_limiter: RateLimiter)

    @abstractmethod
    async def submit(self, application: ApplicationRecord) -> SubmitResult:
        """Fill and submit the application form. Returns success/failure with details."""

    @abstractmethod
    async def detect_platform(self, page: Page) -> bool:
        """Check if this page is handled by this adapter."""

    async def fill_common_fields(self, page, app) -> None:
        """Shared logic: name, email, phone, LinkedIn, resume upload."""
```

`SubmitResult`:
```python
class SubmitResult(BaseModel):
    success: bool
    platform: str
    job_id: str
    screenshot_path: str | None = None
    error: str | None = None
    captcha_detected: bool = False
    fields_filled: list[str] = []
```

### Platform Adapters

**LinkedIn Easy Apply** — Multi-step modal:
1. Navigate to job URL
2. Click "Easy Apply" button
3. Fill contact info (pre-filled from profile usually)
4. Upload resume PDF
5. Answer screening questions (use `ats_answer_cache` for common Qs)
6. Click through review steps
7. Submit

**Indeed Quick Apply** — Single-page form:
1. Navigate to job URL
2. Click "Apply now"
3. Upload resume
4. Fill additional questions
5. Submit

**Greenhouse/Lever** — Standard web forms:
1. Navigate to application URL
2. Fill name, email, phone, LinkedIn
3. Upload resume + cover letter
4. Answer custom questions
5. Submit

**Workday** — Multi-step wizard:
1. Navigate to job URL
2. Click "Apply"
3. Create account or sign in (use cached credentials)
4. Fill each step: personal info → work history → education → resume → review
5. Submit

### Screening Question Handler

Common questions auto-answered from user profile:
- "Are you authorized to work in the UK?" → Yes
- "Do you require visa sponsorship?" → No (Graduate Visa from May 2026)
- "What is your expected salary?" → £27,000-32,000
- "Years of experience?" → Based on work history
- "Are you willing to relocate?" → Yes (within UK)
- Unknown questions → LLM generates answer, caches for reuse

### Hybrid Approval Flow in Applicator

```python
async def process_batch(jobs: list[ApplicationRecord]):
    auto_apply = [j for j in jobs if j.ats_score >= 95]
    review = [j for j in jobs if 85 <= j.ats_score < 95]
    skip = [j for j in jobs if j.ats_score < 85]

    # Auto-submit slam dunks
    for job in auto_apply:
        if rate_limiter.can_apply(job.platform):
            result = await adapter.submit(job)
            # Notify on Telegram after

    # Send review batch to Telegram
    if review:
        send_jobs_for_review(review)
        # User replies "apply 1,3,5" → those get submitted

    # Log skips
    for job in skip:
        update_status(job, "Skipped", reason=f"ATS {job.ats_score}%")
```

### Error Handling

- **Captcha detected** → Screenshot + skip + Telegram alert "Captcha on LinkedIn for [Company]. Apply manually: [URL]"
- **Login expired** → Screenshot + pause platform + Telegram "LinkedIn session expired. Please log in and reply 'resume jobs'"
- **Form field not found** → Screenshot + skip + log error + continue to next job
- **Rate limit hit** → Stop that platform for today + reallocate quota to others
- **Network error** → Retry once after 30s, then skip

## Files

| File | Action |
|------|--------|
| `jobpulse/browser_manager.py` | Create |
| `jobpulse/rate_limiter.py` | Create |
| `jobpulse/ats_adapters/base.py` | Modify (add Playwright integration) |
| `jobpulse/ats_adapters/linkedin.py` | Modify (implement Easy Apply) |
| `jobpulse/ats_adapters/indeed.py` | Modify (implement Quick Apply) |
| `jobpulse/ats_adapters/greenhouse.py` | Modify (implement form fill) |
| `jobpulse/ats_adapters/lever.py` | Modify (implement form fill) |
| `jobpulse/ats_adapters/workday.py` | Modify (implement wizard) |
| `jobpulse/ats_adapters/generic.py` | Modify (best-effort fallback) |
| `jobpulse/applicator.py` | Modify (wire Playwright + hybrid flow) |
| `jobpulse/screening_answers.py` | Create (question handler + cache) |
| `scripts/install_cron.py` | Modify (add 6 scan windows) |
| `tests/test_rate_limiter.py` | Create |
| `tests/test_browser_manager.py` | Create |
| `tests/test_screening_answers.py` | Create |

## Dependencies

```
playwright>=1.40.0
```

Install browsers: `playwright install chromium`

## Cost

- Playwright: $0 (open source)
- LLM for screening questions: ~$0.001/question (gpt-4o-mini)
- Total per day (40 apps): ~$0.04 for screening question LLM calls
