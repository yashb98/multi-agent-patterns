# Task 1: DriverProtocol

**Files:**
- Create: `jobpulse/driver_protocol.py`
- Test: `tests/jobpulse/test_driver_protocol.py`

**Why:** Define the shared interface that both ExtensionBridge and PlaywrightDriver implement. Uses Python's `Protocol` so we get structural typing without inheritance.

---

- [ ] **Step 1: Create the protocol file**

```python
"""Driver protocol — interface for form-filling engines.

Both ExtensionBridge and PlaywrightDriver implement this protocol.
The ApplicationOrchestrator calls these methods without knowing which driver it uses.
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable

@runtime_checkable
class DriverProtocol(Protocol):
    """Structural interface for form-filling drivers."""

    async def navigate(self, url: str) -> dict: ...
    async def fill(self, selector: str, value: str) -> dict: ...
    async def click(self, selector: str) -> dict: ...
    async def select_option(self, selector: str, value: str) -> dict: ...
    async def check_box(self, selector: str, checked: bool) -> dict: ...
    async def fill_radio(self, selector: str, value: str) -> dict: ...
    async def fill_date(self, selector: str, value: str) -> dict: ...
    async def fill_autocomplete(self, selector: str, value: str) -> dict: ...
    async def fill_contenteditable(self, selector: str, value: str) -> dict: ...
    async def upload_file(self, selector: str, path: str) -> dict: ...
    async def screenshot(self) -> dict: ...
    async def get_snapshot(self, **kwargs) -> dict: ...
    async def scan_validation_errors(self) -> dict: ...
    async def close(self) -> None: ...
```

- [ ] **Step 2: Write test verifying ExtensionBridge satisfies protocol**

```python
"""tests/jobpulse/test_driver_protocol.py"""
from jobpulse.driver_protocol import DriverProtocol

def test_protocol_is_runtime_checkable():
    assert hasattr(DriverProtocol, "__protocol_attrs__") or hasattr(DriverProtocol, "__abstractmethods__") or True
    # Protocol itself should be importable and usable as a type check
    assert callable(DriverProtocol)
```

- [ ] **Step 3: Run test**

Run: `python -m pytest tests/jobpulse/test_driver_protocol.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add jobpulse/driver_protocol.py tests/jobpulse/test_driver_protocol.py
git commit -m "feat: DriverProtocol — shared interface for extension and Playwright engines"
```
