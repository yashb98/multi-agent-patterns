# Task 5: Bezier Mouse Trajectories

**Files:**
- Modify: `extension/content.js` — replace `moveCursorTo` (~line 269-278) + add `bezierCurve` utility

**Why:** Current `moveCursorTo()` uses a CSS transition = straight-line teleport. Real mouse movement follows slight curves with overshoot. Playwright's `mouse.move(steps)` is also linear. This gives us an anti-detection edge neither has.

**Dependencies:** None (standalone utility)

---

- [ ] **Step 1: Add `bezierCurve` utility**

Add after the `hideCursor` function:

```javascript
/**
 * Generate points along a cubic Bezier curve with randomized control
 * points. Creates natural-looking mouse trajectories that overshoot
 * slightly and curve, unlike Playwright's straight lines.
 *
 * @param {number} x0, y0 — start position
 * @param {number} x1, y1 — end position
 * @param {number} steps — number of intermediate points (default 18)
 * @returns {Array<{x: number, y: number}>}
 */
function bezierCurve(x0, y0, x1, y1, steps = 18) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const distance = Math.sqrt(dx * dx + dy * dy);

  // Control points offset perpendicular to the line, with randomness
  const perpX = -dy / (distance || 1);
  const perpY = dx / (distance || 1);
  const curvature = (Math.random() - 0.5) * distance * 0.3;
  const overshoot = 1.0 + (Math.random() * 0.08 - 0.02);

  const cp1x = x0 + dx * 0.3 + perpX * curvature;
  const cp1y = y0 + dy * 0.3 + perpY * curvature;
  const cp2x = x0 + dx * 0.7 * overshoot + perpX * curvature * 0.3;
  const cp2y = y0 + dy * 0.7 * overshoot + perpY * curvature * 0.3;

  const points = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const u = 1 - t;
    const x = u*u*u*x0 + 3*u*u*t*cp1x + 3*u*t*t*cp2x + t*t*t*x1;
    const y = u*u*u*y0 + 3*u*u*t*cp1y + 3*u*t*t*cp2y + t*t*t*y1;
    points.push({ x, y });
  }
  return points;
}
```

- [ ] **Step 2: Rewrite `moveCursorTo` to use Bezier path**

Replace the existing `moveCursorTo` function (~line 269-278):

```javascript
/** Smoothly move the visual cursor along a Bezier curve to an element's center. */
async function moveCursorTo(el) {
  const cursor = ensureCursor();
  const rect = el.getBoundingClientRect();
  const targetX = rect.left + rect.width / 2;
  const targetY = rect.top + rect.height / 2;

  // Get current cursor position
  const currentX = parseFloat(cursor.style.left) || -100;
  const currentY = parseFloat(cursor.style.top) || -100;

  // Short distances: just snap (not worth animating <30px moves)
  const dist = Math.sqrt((targetX - currentX) ** 2 + (targetY - currentY) ** 2);
  if (dist < 30) {
    cursor.style.left = targetX + "px";
    cursor.style.top = targetY + "px";
    cursor.style.display = "block";
    await delay(50);
    return;
  }

  // Remove CSS transition for manual animation
  cursor.style.transition = "transform 0.15s ease";

  const points = bezierCurve(currentX, currentY, targetX, targetY);
  cursor.style.display = "block";

  // Ease-in-out: slower at start/end, faster in middle
  for (let i = 0; i < points.length; i++) {
    cursor.style.left = points[i].x + "px";
    cursor.style.top = points[i].y + "px";
    const t = i / points.length;
    const easeMs = 8 + 20 * (1 - Math.abs(2 * t - 1));
    await delay(easeMs + Math.random() * 5);
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add extension/content.js
git commit -m "feat(extension): Bezier mouse trajectories with natural curvature

Replaces straight-line CSS transition with cubic Bezier curve animation.
Randomized control points, slight overshoot, ease-in-out timing. Beats
Playwright mouse.move(steps) which only does linear interpolation."
```
