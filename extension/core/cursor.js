// extension/core/cursor.js — Visual cursor overlay for automation feedback
//
// Depends on: core/dom.js (uses window.JobPulse.dom.delay)

window.JobPulse = window.JobPulse || {};

const { delay } = window.JobPulse.dom;

let _cursor = null;

function ensureCursor() {
  if (_cursor && document.body.contains(_cursor)) return _cursor;
  _cursor = document.createElement("div");
  _cursor.id = "jobpulse-cursor";
  _cursor.style.cssText = `
    position: fixed; z-index: 2147483647; pointer-events: none;
    width: 20px; height: 20px; border-radius: 50%;
    background: rgba(59, 130, 246, 0.7);
    border: 2px solid rgba(255, 255, 255, 0.9);
    box-shadow: 0 0 12px rgba(59, 130, 246, 0.5), 0 0 4px rgba(0,0,0,0.3);
    transition: left 0.4s cubic-bezier(0.25, 0.1, 0.25, 1),
                top 0.4s cubic-bezier(0.25, 0.1, 0.25, 1),
                transform 0.15s ease;
    left: -100px; top: -100px;
    transform: translate(-50%, -50%);
  `;
  document.body.appendChild(_cursor);
  return _cursor;
}

/**
 * Generate points along a cubic Bezier curve with randomized control
 * points. Creates natural-looking mouse trajectories that overshoot
 * slightly and curve, unlike Playwright's straight lines.
 */
function bezierCurve(x0, y0, x1, y1, steps = 18) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const distance = Math.sqrt(dx * dx + dy * dy);

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

/** Smoothly move the visual cursor along a Bezier curve to an element's center. */
async function moveCursorTo(el) {
  const cursor = ensureCursor();
  const rect = el.getBoundingClientRect();
  const targetX = rect.left + rect.width / 2;
  const targetY = rect.top + rect.height / 2;

  const currentX = parseFloat(cursor.style.left) || -100;
  const currentY = parseFloat(cursor.style.top) || -100;

  const dist = Math.sqrt((targetX - currentX) ** 2 + (targetY - currentY) ** 2);
  if (dist < 30) {
    cursor.style.left = targetX + "px";
    cursor.style.top = targetY + "px";
    cursor.style.display = "block";
    await delay(50);
    return;
  }

  cursor.style.transition = "transform 0.15s ease";

  const points = bezierCurve(currentX, currentY, targetX, targetY);
  cursor.style.display = "block";

  for (let i = 0; i < points.length; i++) {
    cursor.style.left = points[i].x + "px";
    cursor.style.top = points[i].y + "px";
    const t = i / points.length;
    const easeMs = 8 + 20 * (1 - Math.abs(2 * t - 1));
    await delay(easeMs + Math.random() * 5);
  }
}

/** Flash a click animation on the cursor. */
async function cursorClickFlash() {
  const cursor = ensureCursor();
  cursor.style.transform = "translate(-50%, -50%) scale(0.6)";
  await delay(100);
  cursor.style.transform = "translate(-50%, -50%) scale(1.0)";
  await delay(100);
}

/** Highlight an element briefly (glow effect). */
function highlightElement(el) {
  const prev = el.style.outline;
  const prevTransition = el.style.transition;
  el.style.transition = "outline 0.2s ease";
  el.style.outline = "2px solid rgba(59, 130, 246, 0.8)";
  setTimeout(() => {
    el.style.outline = prev;
    el.style.transition = prevTransition;
  }, 1500);
}

/** Hide the cursor after automation is done. */
function hideCursor() {
  if (_cursor) _cursor.style.display = "none";
}

window.JobPulse.cursor = { ensureCursor, bezierCurve, moveCursorTo, cursorClickFlash, highlightElement, hideCursor };
