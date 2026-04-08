# Task 9: Python Thin Orchestrator Methods

**Files:**
- Modify: `jobpulse/ext_bridge.py` — add convenience methods for new extension commands

**Why:** Python should have thin wrappers to call the new extension commands (`scan_validation_errors`, `fill_contenteditable`). No fill logic on the Python side — extension handles all DOM interaction.

**Dependencies:** Task 3 (extension must handle these commands)

---

- [ ] **Step 1: Read ext_bridge.py to understand the pattern**

Look at existing methods like `send_command`, `fill`, `click`, `screenshot` etc. to follow the same pattern.

The bridge uses `await self.send_command(action, payload)` which sends a WebSocket message to the extension and awaits the response.

- [ ] **Step 2: Add `scan_validation_errors` method**

Add to the `ExtensionBridge` class, following the same pattern as other methods:

```python
async def scan_validation_errors(self) -> dict:
    """Ask the extension to scan for validation errors on the current page.

    Returns dict with keys: errors (list), has_errors (bool), count (int).
    """
    return await self.send_command("scan_validation_errors", {})
```

- [ ] **Step 3: Add `fill_contenteditable` method**

```python
async def fill_contenteditable(self, selector: str, value: str) -> dict:
    """Fill a contenteditable element (rich text editors in Lever/Workday)."""
    return await self.send_command("fill_contenteditable", {
        "selector": selector,
        "value": value,
    })
```

- [ ] **Step 4: Commit**

```bash
git add jobpulse/ext_bridge.py
git commit -m "feat(bridge): scan_validation_errors + fill_contenteditable wrappers

Thin Python methods for new extension capabilities. Extension handles
all DOM interaction; Python only orchestrates what to fill."
```
