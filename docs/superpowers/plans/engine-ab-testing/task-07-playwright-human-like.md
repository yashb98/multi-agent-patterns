# Task 7: PlaywrightDriver — Human-Like Enhancements

**Files:**
- Modify: `jobpulse/playwright_driver.py`

**Why:** Add Bezier mouse curves, scroll-aware timing, and smart field gaps to the PlaywrightDriver. These make Playwright fills indistinguishable from human interaction.

**Dependencies:** Task 6 (fill methods must exist)

---

- [ ] **Step 1: Add timing helpers at module level**

```python
import math
import random

def _get_field_gap(label_text: str = "") -> float:
    """Return delay in seconds based on label length (simulates reading)."""
    length = len(label_text)
    if length < 10:
        return 0.3 + random.uniform(0, 0.15)
    if length < 30:
        return 0.5 + random.uniform(0, 0.3)
    if length < 60:
        return 0.8 + random.uniform(0, 0.4)
    return 1.2 + random.uniform(0, 0.5)

def _scroll_delay(distance_px: float) -> float:
    """Return delay in seconds proportional to scroll distance."""
    if distance_px < 50:
        return 0.05
    if distance_px < 300:
        return 0.15 + random.uniform(0, 0.1)
    return 0.4 + random.uniform(0, 0.4)
```

- [ ] **Step 2: Add Bezier curve helper**

```python
def _bezier_points(x0, y0, x1, y1, steps=15):
    """Generate cubic Bezier curve points with randomized curvature."""
    dx, dy = x1 - x0, y1 - y0
    dist = math.sqrt(dx * dx + dy * dy)
    if dist < 5:
        return [(x1, y1)]

    # Perpendicular offset for control points
    px, py = -dy / dist, dx / dist
    curve = random.uniform(30, 80) * random.choice([-1, 1])

    cx1 = x0 + dx * 0.3 + px * curve
    cy1 = y0 + dy * 0.3 + py * curve
    cx2 = x0 + dx * 0.7 + px * curve * 0.5
    cy2 = y0 + dy * 0.7 + py * curve * 0.5

    points = []
    for i in range(1, steps + 1):
        t = i / steps
        it = 1 - t
        bx = it**3 * x0 + 3 * it**2 * t * cx1 + 3 * it * t**2 * cx2 + t**3 * x1
        by = it**3 * y0 + 3 * it**2 * t * cy1 + 3 * it * t**2 * cy2 + t**3 * y1
        points.append((bx, by))
    return points
```

- [ ] **Step 3: Add `_move_mouse_to` method to PlaywrightDriver**

```python
    async def _move_mouse_to(self, el) -> None:
        """Move mouse to element along a Bezier curve."""
        box = await el.bounding_box()
        if not box:
            return
        target_x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
        target_y = box["y"] + box["height"] / 2 + random.uniform(-2, 2)

        # Get current mouse position (approximate from viewport center)
        vp = self._page.viewport_size or {"width": 1280, "height": 720}
        start_x = getattr(self, "_mouse_x", vp["width"] / 2)
        start_y = getattr(self, "_mouse_y", vp["height"] / 2)

        points = _bezier_points(start_x, start_y, target_x, target_y)
        for px, py in points:
            await self._page.mouse.move(px, py)
            await asyncio.sleep(random.uniform(0.008, 0.025))

        self._mouse_x = target_x
        self._mouse_y = target_y
```

- [ ] **Step 4: Add `_smart_scroll` method**

```python
    async def _smart_scroll(self, el) -> None:
        """Scroll element into view and wait proportionally to distance."""
        box_before = await el.bounding_box()
        await el.scroll_into_view_if_needed()
        box_after = await el.bounding_box()
        if box_before and box_after:
            dist = abs(box_after["y"] - box_before["y"])
            delay = _scroll_delay(dist)
            await asyncio.sleep(delay)
```

- [ ] **Step 5: Integrate into fill() and click()**

Update the `fill()` method to use smart scroll + field gap + mouse:

```python
    async def fill(self, selector: str, value: str, label: str = "") -> dict:
        async def _do():
            el = await self._page.query_selector(selector)
            if not el:
                return {"success": False, "error": f"Element {selector} not found"}
            await self._smart_scroll(el)
            await asyncio.sleep(_get_field_gap(label))
            await self._move_mouse_to(el)
            await el.fill(value)
            actual = await el.evaluate("el => el.value || ''")
            verified = actual == value or value[:10] in actual
            return {"success": True, "value_set": value, "value_verified": verified}
        return await _with_retry(_do)
```

Update `click()` similarly — add `_smart_scroll` + `_move_mouse_to` before click.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/playwright_driver.py
git commit -m "feat: PlaywrightDriver human-like — Bezier mouse, scroll timing, field gaps

Fills now move the mouse along randomized cubic Bezier curves, wait
proportionally after scrolling, and pause based on label length."
```
