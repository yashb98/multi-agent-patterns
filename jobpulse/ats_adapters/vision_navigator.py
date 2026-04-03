"""Vision Navigator — GPT-4.1-mini guided page interaction for any ATS portal.

Screenshots the page, sends to vision LLM, gets back structured actions
(click, fill, select, upload, scroll, next), executes them, repeats.

Works on ANY career portal — Oracle Cloud, Bending Spoons, SuccessFactors,
custom company sites. The LLM sees the page and decides what to do.

**Learning mode**: Every successful navigation is recorded per-domain.
On repeat visits to the same domain, we replay the learned sequence
directly — zero LLM cost, instant execution.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from shared.logging_config import get_logger
from jobpulse.utils.safe_io import safe_openai_call
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_MAX_STEPS = 12  # Safety cap — never interact more than 12 times per page
_VISION_DB_PATH = str(DATA_DIR / "vision_learning.db")

_SYSTEM_PROMPT = """\
You are a job application form automation assistant. You see a screenshot of a career portal page.
Your job is to determine what action to take next to progress through the application.

The applicant's profile:
- Name: Yash Bishnoi
- Email: bishnoiyash274@gmail.com
- Phone: 07909445288
- LinkedIn: https://linkedin.com/in/yash-bishnoi-2ab36a1a5
- GitHub: https://github.com/yashb98
- Location: Dundee, UK
- Education: MSc Computer Science, University of Dundee (Jan 2025 - Jan 2026)
- Visa: Student Visa, converting to Graduate Visa from May 2026 (no sponsorship needed)
- Salary expectation: £28,000
- Notice period: Immediate

You MUST return ONLY valid JSON with this exact structure:
{
    "page_state": "jd_page | form_page | login_page | success_page | error_page | unknown",
    "description": "Brief description of what you see on the page",
    "actions": [
        {
            "type": "click | fill | select | scroll | wait | done | blocked",
            "target": "CSS selector or text description of the element",
            "value": "text to fill (for fill/select actions)",
            "reason": "why this action"
        }
    ],
    "confidence": 0.0-1.0
}

Action types:
- click: Click a button, link, or element. Target should be a CSS selector or button text.
- fill: Type text into an input field. Target = selector, value = text.
- select: Choose an option from a dropdown. Target = selector, value = option text.
- scroll: Scroll down to reveal more content. Target = "down".
- wait: Wait for page to load. Target = "2000" (milliseconds).
- done: Application form is fully filled and ready for review/submit.
- blocked: Page requires something we can't automate (CAPTCHA, manual verification).

Rules:
- If you see a job description page with an "Apply" button, click it.
- If you see a login/signup page, try "Apply without account" or "Continue as guest" first.
  If no guest option exists, return blocked.
- If you see form fields, fill them with the applicant's profile data.
- For dropdowns, pick the most appropriate option.
- For "Yes/No" questions about work authorization, answer Yes.
- For salary fields, use 28000 (plain number, no symbols).
- Never click "Submit" — use "done" when the form is filled and ready for review.
- Return at most 3 actions per response (to allow re-screenshotting between batches).
- If the page looks like a success/confirmation, return done.
"""


# ---------------------------------------------------------------------------
# Vision Learning Store — SQLite-backed
# ---------------------------------------------------------------------------

def _init_learning_db(db_path: str | None = None) -> str:
    """Create the learning DB tables if they don't exist."""
    path = db_path or _VISION_DB_PATH
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS learned_sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            page_pattern TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            times_replayed INTEGER DEFAULT 0,
            times_succeeded INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT,
            UNIQUE(domain, page_pattern)
        );

        CREATE TABLE IF NOT EXISTS vision_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            url TEXT NOT NULL,
            mode TEXT NOT NULL,
            actions_taken INTEGER DEFAULT 0,
            outcome TEXT NOT NULL,
            llm_calls INTEGER DEFAULT 0,
            total_time_ms INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    return path


def _extract_domain(url: str) -> str:
    """Extract the domain from a URL for learning key."""
    parsed = urlparse(url)
    return parsed.netloc.lower().replace("www.", "")


def _extract_page_pattern(url: str) -> str:
    """Extract a generalizable URL pattern (strip IDs, keep structure).

    e.g. jobs.bendingspoons.com/positions/12345 → /positions/*
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    # Replace numeric segments with wildcard
    parts = path.split("/")
    generalized = []
    for part in parts:
        if re.match(r"^\d+$", part) or re.match(r"^[0-9a-f-]{8,}$", part):
            generalized.append("*")
        else:
            generalized.append(part)
    return "/".join(generalized) or "/"


def save_learned_sequence(
    url: str,
    actions: list[dict],
    db_path: str | None = None,
) -> None:
    """Save a successful action sequence for future replay."""
    path = _init_learning_db(db_path)
    domain = _extract_domain(url)
    page_pattern = _extract_page_pattern(url)
    actions_json = json.dumps(actions)

    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO learned_sequences (domain, page_pattern, actions_json)
           VALUES (?, ?, ?)
           ON CONFLICT(domain, page_pattern) DO UPDATE SET
               actions_json = excluded.actions_json,
               last_used_at = datetime('now')
        """,
        (domain, page_pattern, actions_json),
    )
    conn.commit()
    conn.close()
    logger.info(
        "Vision learning: saved %d actions for %s %s",
        len(actions), domain, page_pattern,
    )


def get_learned_sequence(
    url: str,
    db_path: str | None = None,
) -> list[dict] | None:
    """Look up a learned action sequence for this domain+pattern.

    Returns None if no learned sequence exists.
    """
    path = _init_learning_db(db_path)
    domain = _extract_domain(url)
    page_pattern = _extract_page_pattern(url)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT actions_json, success_rate, times_replayed
           FROM learned_sequences
           WHERE domain = ? AND page_pattern = ?""",
        (domain, page_pattern),
    ).fetchone()
    conn.close()

    if row is None:
        return None

    # Only replay if success rate is decent (or never tried replay yet)
    if row["times_replayed"] > 2 and row["success_rate"] < 0.5:
        logger.info(
            "Vision learning: skipping learned sequence for %s (success_rate=%.2f)",
            domain, row["success_rate"],
        )
        return None

    logger.info(
        "Vision learning: found learned sequence for %s (%d actions, %.0f%% success)",
        domain, len(json.loads(row["actions_json"])),
        row["success_rate"] * 100,
    )
    return json.loads(row["actions_json"])


def mark_replay_outcome(
    url: str,
    success: bool,
    db_path: str | None = None,
) -> None:
    """Update success stats after replaying a learned sequence."""
    path = _init_learning_db(db_path)
    domain = _extract_domain(url)
    page_pattern = _extract_page_pattern(url)

    conn = sqlite3.connect(path)
    if success:
        conn.execute(
            """UPDATE learned_sequences
               SET times_replayed = times_replayed + 1,
                   times_succeeded = times_succeeded + 1,
                   success_rate = CAST(times_succeeded + 1 AS REAL) / (times_replayed + 1),
                   last_used_at = datetime('now')
               WHERE domain = ? AND page_pattern = ?""",
            (domain, page_pattern),
        )
    else:
        conn.execute(
            """UPDATE learned_sequences
               SET times_replayed = times_replayed + 1,
                   success_rate = CAST(times_succeeded AS REAL) / (times_replayed + 1),
                   last_used_at = datetime('now')
               WHERE domain = ? AND page_pattern = ?""",
            (domain, page_pattern),
        )
    conn.commit()
    conn.close()


def record_vision_session(
    url: str,
    mode: str,
    actions_taken: int,
    outcome: str,
    llm_calls: int = 0,
    total_time_ms: int = 0,
    db_path: str | None = None,
) -> None:
    """Record a vision session for analytics."""
    path = _init_learning_db(db_path)
    domain = _extract_domain(url)

    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO vision_sessions
           (domain, url, mode, actions_taken, outcome, llm_calls, total_time_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (domain, url, mode, actions_taken, outcome, llm_calls, total_time_ms),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Vision LLM interaction
# ---------------------------------------------------------------------------

def _screenshot_to_b64(page: Any) -> str:
    """Capture page screenshot and return as base64 string."""
    try:
        raw = page.screenshot(full_page=False)
        return base64.b64encode(raw).decode("ascii")
    except Exception as exc:
        logger.warning("Vision navigator: screenshot failed: %s", exc)
        return ""


def _get_visible_text(page: Any) -> str:
    """Get visible text from the page (truncated for context)."""
    try:
        text = page.inner_text("body")
        return text[:3000]
    except Exception:
        return ""


def _ask_vision(screenshot_b64: str, visible_text: str, page_url: str, step: int) -> dict | None:
    """Send screenshot to GPT-4.1-mini and get structured actions back."""
    import openai

    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Step {step}/{_MAX_STEPS}. Current URL: {page_url}\n\n"
                f"Visible text (first 3000 chars):\n{visible_text}\n\n"
                "What actions should I take on this page?"
            ),
        },
    ]
    if screenshot_b64:
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{screenshot_b64}",
                "detail": "high",
            },
        })

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    client = openai.OpenAI()
    response = safe_openai_call(
        client,
        model="gpt-4.1-mini",
        messages=messages,
        temperature=0.2,
        timeout=90.0,
        caller="vision_navigator",
    )

    if not response:
        return None

    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Vision navigator: invalid JSON: %s", response[:200])
        return None


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------

def _execute_action(page: Any, action: dict) -> bool:
    """Execute a single action on the page. Returns True if successful."""
    action_type = action.get("type", "")
    target = action.get("target", "")
    value = action.get("value", "")
    reason = action.get("reason", "")

    logger.info("Vision navigator: %s target='%s' value='%s' reason='%s'",
                action_type, target[:60], value[:40], reason[:60])

    try:
        if action_type == "click":
            # Try CSS selector first, then text-based
            el = None
            if target.startswith((".", "#", "[", "button", "a", "input", "div", "span")):
                el = page.query_selector(target)
            if not el:
                # Try :has-text selector
                clean_text = target.replace("'", "\\'")
                for tag in ["button", "a", "[role='button']", "span", "div"]:
                    el = page.query_selector(f"{tag}:has-text('{clean_text}')")
                    if el and el.is_visible():
                        break
                    el = None
            if el:
                if el.is_visible():
                    el.click(timeout=10000)
                else:
                    # JS click for invisible elements
                    el.evaluate("el => el.click()")
                time.sleep(2)
                return True
            else:
                # Last resort: JS click by text content
                escaped = target.lower()[:30].replace("'", "\\'")
                clicked = page.evaluate(f"""() => {{
                    const els = document.querySelectorAll('a, button, [role="button"], input[type="submit"]');
                    for (const el of els) {{
                        if (el.textContent.trim().toLowerCase().includes('{escaped}')) {{
                            el.click();
                            return true;
                        }}
                    }}
                    return false;
                }}""")
                if clicked:
                    time.sleep(2)
                return clicked

        elif action_type == "fill":
            el = page.query_selector(target)
            if not el:
                # Try by placeholder or label text
                escaped = target.replace("'", "\\'")
                el = page.query_selector(f"input[placeholder*='{escaped}' i], input[name*='{escaped}' i]")
            if el:
                el.fill(value)
                return True

        elif action_type == "select":
            el = page.query_selector(target)
            if el:
                # Try selecting by label text
                options = el.query_selector_all("option")
                for opt in options:
                    opt_text = opt.text_content().strip()
                    if value.lower() in opt_text.lower():
                        el.select_option(label=opt_text)
                        return True
                # Fallback: select by value
                el.select_option(value=value)
                return True

        elif action_type == "scroll":
            page.evaluate("window.scrollBy(0, 500)")
            time.sleep(1)
            return True

        elif action_type == "wait":
            wait_ms = int(target) if target.isdigit() else 2000
            time.sleep(min(wait_ms / 1000, 5))
            return True

        elif action_type in ("done", "blocked"):
            return True  # Signal handled by caller

    except Exception as exc:
        logger.warning("Vision navigator: action %s failed: %s", action_type, str(exc)[:120])

    return False


# ---------------------------------------------------------------------------
# Replay mode — execute learned sequence without vision LLM
# ---------------------------------------------------------------------------

def _replay_learned(
    page: Any,
    learned_actions: list[dict],
    cv_path: Path,
    dry_run: bool = True,
    screenshot_callback: Any = None,
) -> dict:
    """Replay a previously learned action sequence without calling vision LLM.

    Returns same dict format as vision_navigate().
    """
    actions_taken = []
    last_screenshot_path = None

    logger.info("Vision replay: executing %d learned actions", len(learned_actions))

    for i, action in enumerate(learned_actions):
        action_type = action.get("type", "")

        # Terminal signals
        if action_type == "done":
            ss_path = cv_path.parent / f"replay_done.png"
            try:
                page.screenshot(path=str(ss_path), full_page=False)
                last_screenshot_path = ss_path
            except Exception:
                pass
            return {
                "success": True,
                "page_state": "form_filled",
                "actions_taken": actions_taken,
                "screenshot": last_screenshot_path,
                "error": None,
                "dry_run": dry_run,
                "mode": "replay",
            }

        if action_type == "blocked":
            return {
                "success": False,
                "page_state": "blocked",
                "actions_taken": actions_taken,
                "screenshot": last_screenshot_path,
                "error": f"Blocked: {action.get('reason', 'unknown')}",
                "mode": "replay",
            }

        # Execute the action
        success = _execute_action(page, action)
        actions_taken.append({
            "step": i + 1,
            "action": action_type,
            "target": action.get("target", "")[:60],
            "success": success,
        })

        if not success:
            logger.warning(
                "Vision replay: action %d failed (%s) — falling back to vision mode",
                i + 1, action_type,
            )
            return {
                "success": False,
                "page_state": "replay_failed",
                "actions_taken": actions_taken,
                "screenshot": last_screenshot_path,
                "error": f"Replay failed at action {i + 1}: {action_type}",
                "mode": "replay",
                "fallback_to_vision": True,
            }

        # Screenshot every 3rd action or last action
        if (i + 1) % 3 == 0 or i == len(learned_actions) - 1:
            ss_path = cv_path.parent / f"replay_step_{i + 1:02d}.png"
            try:
                page.screenshot(path=str(ss_path), full_page=False)
                last_screenshot_path = ss_path
                if screenshot_callback:
                    screenshot_callback(i + 1, ss_path.read_bytes())
            except Exception:
                pass

        # Wait for page to settle
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        time.sleep(0.5)

    # Exhausted all actions without hitting "done"
    return {
        "success": False,
        "page_state": "replay_incomplete",
        "actions_taken": actions_taken,
        "screenshot": last_screenshot_path,
        "error": "Replay finished but no 'done' action found",
        "mode": "replay",
        "fallback_to_vision": True,
    }


# ---------------------------------------------------------------------------
# Main entry point — vision navigate with learning
# ---------------------------------------------------------------------------

def vision_navigate(
    page: Any,
    cv_path: Path,
    profile: dict,
    dry_run: bool = True,
    screenshot_callback: Any = None,
    db_path: str | None = None,
) -> dict:
    """Drive a career portal page using vision LLM guidance, with learning.

    Flow:
    1. Check if we have a learned sequence for this domain
    2. If yes → replay it (zero LLM cost). If replay fails → fall back to vision.
    3. If no → use vision LLM to navigate. On success → save the sequence.

    Args:
        page: Playwright page object (already navigated to the career portal).
        cv_path: Path to CV PDF for upload.
        profile: Applicant profile dict.
        dry_run: If True, stops before submitting.
        screenshot_callback: Optional fn(step, screenshot_bytes) for Telegram.
        db_path: Optional override for learning DB path (for tests).

    Returns:
        dict with keys: success, page_state, actions_taken, screenshot, error, mode
    """
    start_time = time.time()
    page_url = page.url

    # --- Try replay first ---
    learned = get_learned_sequence(page_url, db_path=db_path)
    if learned:
        logger.info("Vision navigator: REPLAY MODE for %s (%d learned actions)",
                     _extract_domain(page_url), len(learned))
        result = _replay_learned(
            page, learned, cv_path, dry_run=dry_run,
            screenshot_callback=screenshot_callback,
        )

        elapsed = int((time.time() - start_time) * 1000)

        if result.get("success"):
            mark_replay_outcome(page_url, success=True, db_path=db_path)
            record_vision_session(
                page_url, mode="replay",
                actions_taken=len(result.get("actions_taken", [])),
                outcome="success", llm_calls=0,
                total_time_ms=elapsed, db_path=db_path,
            )
            return result

        # Replay failed — mark and fall through to vision mode
        mark_replay_outcome(page_url, success=False, db_path=db_path)
        if not result.get("fallback_to_vision"):
            record_vision_session(
                page_url, mode="replay",
                actions_taken=len(result.get("actions_taken", [])),
                outcome="failed", llm_calls=0,
                total_time_ms=elapsed, db_path=db_path,
            )
            return result

        logger.info("Vision navigator: replay failed — falling back to VISION MODE")

    # --- Vision mode ---
    logger.info("Vision navigator: VISION MODE for %s", _extract_domain(page_url))
    all_actions_for_learning: list[dict] = []
    actions_taken = []
    last_screenshot_path = None
    llm_calls = 0

    for step in range(1, _MAX_STEPS + 1):
        # Screenshot current state
        screenshot_b64 = _screenshot_to_b64(page)
        visible_text = _get_visible_text(page)
        current_url = page.url

        # Save screenshot to disk
        ss_path = cv_path.parent / f"vision_step_{step:02d}.png"
        try:
            page.screenshot(path=str(ss_path), full_page=False)
            last_screenshot_path = ss_path
        except Exception:
            pass

        # Send to Telegram if callback provided
        if screenshot_callback and ss_path.exists():
            try:
                screenshot_callback(step, ss_path.read_bytes())
            except Exception:
                pass

        # Ask vision LLM
        logger.info("Vision navigator: step %d — asking LLM (url=%s)", step, current_url[:80])
        result = _ask_vision(screenshot_b64, visible_text, current_url, step)
        llm_calls += 1

        if not result:
            logger.warning("Vision navigator: LLM returned no result at step %d", step)
            continue

        page_state = result.get("page_state", "unknown")
        description = result.get("description", "")
        actions = result.get("actions", [])
        confidence = result.get("confidence", 0.5)

        logger.info(
            "Vision navigator: step %d — state=%s conf=%.2f desc='%s' actions=%d",
            step, page_state, confidence, description[:80], len(actions),
        )

        # Handle terminal states
        if page_state == "success_page":
            elapsed = int((time.time() - start_time) * 1000)
            # Save learned sequence
            all_actions_for_learning.append({"type": "done", "reason": "success page detected"})
            save_learned_sequence(page_url, all_actions_for_learning, db_path=db_path)
            record_vision_session(
                page_url, mode="vision", actions_taken=len(actions_taken),
                outcome="success", llm_calls=llm_calls,
                total_time_ms=elapsed, db_path=db_path,
            )
            return {
                "success": True,
                "page_state": "success",
                "actions_taken": actions_taken,
                "screenshot": last_screenshot_path,
                "error": None,
                "mode": "vision",
            }

        # Execute actions
        for action in actions:
            action_type = action.get("type", "")

            if action_type == "done":
                elapsed = int((time.time() - start_time) * 1000)
                logger.info("Vision navigator: form filled — ready for review")
                # Save the full sequence including "done" for learning
                all_actions_for_learning.append(action)
                save_learned_sequence(page_url, all_actions_for_learning, db_path=db_path)
                record_vision_session(
                    page_url, mode="vision", actions_taken=len(actions_taken),
                    outcome="success", llm_calls=llm_calls,
                    total_time_ms=elapsed, db_path=db_path,
                )
                return {
                    "success": True,
                    "page_state": "form_filled",
                    "actions_taken": actions_taken,
                    "screenshot": last_screenshot_path,
                    "error": None,
                    "dry_run": dry_run,
                    "mode": "vision",
                }

            if action_type == "blocked":
                elapsed = int((time.time() - start_time) * 1000)
                reason = action.get("reason", "Unknown blocker")
                logger.warning("Vision navigator: BLOCKED — %s", reason)
                # Don't save blocked sequences — not useful for replay
                record_vision_session(
                    page_url, mode="vision", actions_taken=len(actions_taken),
                    outcome="blocked", llm_calls=llm_calls,
                    total_time_ms=elapsed, db_path=db_path,
                )
                return {
                    "success": False,
                    "page_state": "blocked",
                    "actions_taken": actions_taken,
                    "screenshot": last_screenshot_path,
                    "error": f"Blocked: {reason}",
                    "mode": "vision",
                }

            success = _execute_action(page, action)
            actions_taken.append({
                "step": step,
                "action": action_type,
                "target": action.get("target", "")[:60],
                "success": success,
            })

            # Record for learning (only successful actions)
            if success:
                all_actions_for_learning.append(action)

        # Wait for page to settle after actions
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        time.sleep(1)

    # Exhausted steps
    elapsed = int((time.time() - start_time) * 1000)
    record_vision_session(
        page_url, mode="vision", actions_taken=len(actions_taken),
        outcome="exhausted", llm_calls=llm_calls,
        total_time_ms=elapsed, db_path=db_path,
    )
    return {
        "success": False,
        "page_state": "exhausted",
        "actions_taken": actions_taken,
        "screenshot": last_screenshot_path,
        "error": f"Vision navigator exhausted {_MAX_STEPS} steps without completing",
        "mode": "vision",
    }
