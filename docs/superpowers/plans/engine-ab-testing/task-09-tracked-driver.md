# Task 9: TrackedDriver Wrapper

**Files:**
- Modify: `jobpulse/tracked_driver.py` (add TrackedDriver class)
- Test: `tests/jobpulse/test_tracked_driver.py`

**Why:** Wraps any DriverProtocol implementation and logs every call to ABTracker. The orchestrator uses `TrackedDriver(PlaywrightDriver())` or `TrackedDriver(ExtensionBridge())` — transparent instrumentation.

**Dependencies:** Task 8 (ABTracker must exist in same file)

---

- [ ] **Step 1: Write failing test**

```python
"""tests/jobpulse/test_tracked_driver.py"""
import asyncio, pytest
from unittest.mock import AsyncMock
from jobpulse.tracked_driver import TrackedDriver, ABTracker

@pytest.fixture
def mock_driver():
    driver = AsyncMock()
    driver.fill.return_value = {"success": True, "value_set": "test", "value_verified": True}
    driver.click.return_value = {"success": True}
    driver.close.return_value = None
    return driver

def test_tracked_fill_logs_event(tmp_path, mock_driver):
    db_path = str(tmp_path / "ab.db")
    tracked = TrackedDriver(mock_driver, engine="playwright", application_id="app1", db_path=db_path)
    result = asyncio.run(tracked.fill("#email", "test@test.com"))

    assert result["success"] is True
    mock_driver.fill.assert_called_once_with("#email", "test@test.com")

    tracker = ABTracker(db_path=db_path)
    stats = tracker.get_engine_stats("playwright", days=1)
    assert stats["total_fields"] == 1
    assert stats["fields_verified"] == 1

def test_tracked_click_logs_event(tmp_path, mock_driver):
    db_path = str(tmp_path / "ab.db")
    tracked = TrackedDriver(mock_driver, engine="extension", application_id="app2", db_path=db_path)
    result = asyncio.run(tracked.click("#submit"))
    assert result["success"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_tracked_driver.py -v`
Expected: FAIL — `ImportError: cannot import name 'TrackedDriver'`

- [ ] **Step 3: Implement TrackedDriver in tracked_driver.py**

Add to the bottom of `jobpulse/tracked_driver.py` (after ABTracker):

```python
import time

class TrackedDriver:
    """Transparent wrapper that logs every driver call to ABTracker."""

    def __init__(self, inner, engine: str, application_id: str, db_path: str | None = None):
        self._inner = inner
        self._engine = engine
        self._app_id = application_id
        self._tracker = ABTracker(db_path=db_path)
        self._platform: str | None = None

    def set_platform(self, platform: str) -> None:
        self._platform = platform

    async def _tracked_call(self, action: str, method, *args, **kwargs):
        start = time.monotonic()
        result = await method(*args, **kwargs)
        duration = int((time.monotonic() - start) * 1000)
        selector = args[0] if args else kwargs.get("selector")
        self._tracker.log_field(
            application_id=self._app_id, engine=self._engine,
            platform=self._platform, action=action, selector=selector,
            success=result.get("success", False),
            value_verified=result.get("value_verified"),
            duration_ms=duration, error=result.get("error"),
            retry_count=result.get("retry_count", 0),
        )
        return result

    async def fill(self, selector, value, **kw):
        return await self._tracked_call("fill", self._inner.fill, selector, value, **kw)

    async def click(self, selector):
        return await self._tracked_call("click", self._inner.click, selector)

    async def select_option(self, selector, value):
        return await self._tracked_call("select", self._inner.select_option, selector, value)

    async def check_box(self, selector, checked):
        return await self._tracked_call("checkbox", self._inner.check_box, selector, checked)

    async def fill_radio(self, selector, value):
        return await self._tracked_call("radio", self._inner.fill_radio, selector, value)

    async def fill_date(self, selector, value):
        return await self._tracked_call("date", self._inner.fill_date, selector, value)

    async def fill_autocomplete(self, selector, value):
        return await self._tracked_call("autocomplete", self._inner.fill_autocomplete, selector, value)

    async def fill_contenteditable(self, selector, value):
        return await self._tracked_call("contenteditable", self._inner.fill_contenteditable, selector, value)

    async def upload_file(self, selector, path):
        return await self._tracked_call("upload", self._inner.upload_file, selector, path)

    # Pass-through (no tracking needed for non-fill operations)
    async def navigate(self, url): return await self._inner.navigate(url)
    async def screenshot(self): return await self._inner.screenshot()
    async def get_snapshot(self, **kw): return await self._inner.get_snapshot(**kw)
    async def scan_validation_errors(self): return await self._inner.scan_validation_errors()
    async def close(self): return await self._inner.close()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_tracked_driver.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/tracked_driver.py tests/jobpulse/test_tracked_driver.py
git commit -m "feat: TrackedDriver — transparent instrumentation wrapper for A/B metrics"
```
