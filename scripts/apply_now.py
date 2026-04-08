"""Apply to a job using the full ApplicationOrchestrator pipeline.

Usage:
    python -m scripts.apply_now <url>

Everything is derived dynamically from what's on the page:
    navigate → analyze page → extract JD/company/title → generate CV → fill forms → submit
"""
import asyncio
import sys
import logging
from urllib.parse import urlparse

import httpx

from jobpulse.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def _send_photo(data: bytes, caption: str) -> int:
    """Send photo to Telegram, return message_id."""
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
        data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
        files={"photo": ("screenshot.png", data, "image/png")},
        timeout=15,
    )
    return resp.json().get("result", {}).get("message_id", 0)


def _send_msg(text: str) -> None:
    httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=10,
    )


def _drain_updates() -> int:
    """Drain pending Telegram updates, return last update_id."""
    resp = httpx.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
        params={"offset": -1},
        timeout=5,
    )
    updates = resp.json().get("result", [])
    return max((u["update_id"] for u in updates), default=0)


async def _wait_for_reply(last_id: int, max_wait: int = 180) -> tuple[str | None, int]:
    """Poll Telegram for a short text reply. Returns (text, last_update_id)."""
    for _ in range(max_wait // 2):
        await asyncio.sleep(2)
        try:
            resp = httpx.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"offset": last_id + 1, "timeout": 1},
                timeout=10,
            )
            for u in resp.json().get("result", []):
                last_id = u["update_id"]
                msg = u.get("message", {})
                if str(msg.get("chat", {}).get("id", "")) == str(TELEGRAM_CHAT_ID):
                    text = (msg.get("text", "") or "").strip()
                    if text:
                        return text, last_id
        except Exception:
            pass
    return None, last_id


# ---------------------------------------------------------------------------
# URL analysis helpers
# ---------------------------------------------------------------------------

_ATS_HOSTS = {
    "greenhouse.io", "lever.co", "zohorecruit.eu", "zohorecruit.com",
    "workday.com", "ashbyhq.com", "smartrecruiters.com", "icims.com",
    "successfactors.com", "taleo.net", "bamboohr.com", "jazz.co",
    "breezy.hr", "recruitee.com", "jobvite.com", "applytojob.com",
}

_PLATFORM_PATTERNS = [
    ("linkedin.com", "linkedin"),
    ("indeed.com", "indeed"), ("indeed.co", "indeed"),
    ("reed.co.uk", "reed"),
    ("totaljobs.com", "totaljobs"),
    ("glassdoor.com", "glassdoor"), ("glassdoor.co", "glassdoor"),
]


def _company_from_url(url: str) -> str:
    """Extract a human-readable company name from the URL domain."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    parts = host.split(".")
    domain_suffix = ".".join(parts[-2:])
    if domain_suffix in _ATS_HOSTS and len(parts) >= 3:
        return parts[0].replace("-", " ").title()
    if len(parts) >= 2:
        return parts[-2].replace("-", " ").title()
    return parts[0].title()


def _platform_from_url(url: str) -> str:
    """Detect the job platform from the URL and query params."""
    u = url.lower()
    for pattern, platform in _PLATFORM_PATTERNS:
        if pattern in u:
            return platform
    # Check ?source= query param (ZohoRecruit, Greenhouse etc. embed the source)
    from urllib.parse import parse_qs
    qs = parse_qs(urlparse(url).query)
    source = (qs.get("source") or qs.get("src") or qs.get("utm_source") or [""])[0]
    if source:
        return source.lower()
    return "generic"


# ---------------------------------------------------------------------------
# CAPTCHA handler
# ---------------------------------------------------------------------------

async def handle_captcha(bridge, company: str, title: str) -> bool:
    """Screenshot CAPTCHA, send to Telegram, wait for reply, fill it."""
    # Try cropped element screenshot first, fall back to full page
    _CAPTCHA_SELECTORS = [
        "div.crc-captcha",
        "[class*='captcha']",
        "[id*='captcha']",
        "div.captcha-container",
    ]
    data = None
    for sel in _CAPTCHA_SELECTORS:
        try:
            data = await bridge.element_screenshot(sel, timeout_ms=10000)
            if data and len(data) > 100:
                logger.info("CAPTCHA element screenshot via %s (%d bytes)", sel, len(data))
                break
            data = None
        except Exception:
            continue
    # Fallback: full page screenshot
    if not data:
        try:
            data = await bridge.screenshot(timeout_ms=8000)
        except Exception:
            logger.error("Failed to take CAPTCHA screenshot")
            return False

    _send_photo(data, "CAPTCHA detected. Reply with the exact text (case-sensitive):")
    logger.info("CAPTCHA sent to Telegram, waiting for reply...")

    last_id = _drain_updates()
    reply, last_id = await _wait_for_reply(last_id, max_wait=180)

    if not reply:
        _send_msg("No CAPTCHA reply received. Application paused.")
        return False

    logger.info("Filling CAPTCHA: %r", reply)
    try:
        for sel in [
            "div.crc-captcha input",
            "input[name*='captcha']",
            "input[placeholder*='captcha' i]",
            "input[placeholder*='image text' i]",
        ]:
            try:
                r = await bridge.fill(sel, reply, timeout_ms=5000)
                if r.success:
                    logger.info("CAPTCHA filled via %s", sel)
                    return True
            except Exception:
                continue
    except Exception as exc:
        logger.error("CAPTCHA fill failed: %s", exc)

    return False


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def apply_to_job(url: str) -> dict:
    """Fully dynamic application: navigate → analyze page → generate CV → fill → submit."""
    from jobpulse.relay_bridge import RelayBridge
    from jobpulse.application_orchestrator import ApplicationOrchestrator
    from jobpulse.jd_analyzer import analyze_jd
    from jobpulse.skill_graph_store import SkillGraphStore
    from jobpulse.project_portfolio import get_best_projects_for_jd
    from jobpulse.cv_templates.generate_cv import (
        generate_cv_pdf, build_extra_skills, get_role_profile,
    )

    rb = RelayBridge()
    if not await rb.connect(timeout=10):
        return {"success": False, "error": "No bridge connection"}

    print(f"Connected. Applying to: {url[:80]}")

    # ── Step 1: Navigate to the page (single navigation) ──
    print("Step 1: Navigating to page...")
    snap = None
    try:
        snap = await rb.navigate(url, timeout_ms=30000)
    except Exception as exc:
        logger.info("Navigate raised %s — waiting for reconnect", type(exc).__name__)
        await asyncio.sleep(5)
        snap = await rb.get_snapshot(force_refresh=True)

    if not snap:
        await rb.stop()
        return {"success": False, "error": "Could not load page"}

    # ── Step 1b: Check for verification wall / CAPTCHA ──
    captcha_signals = [
        snap.verification_wall is not None,
        "captcha" in (snap.title or "").lower(),
        "captcha" in (snap.page_text_preview or "").lower(),
    ]
    if any(captcha_signals):
        print("CAPTCHA/verification wall detected — attempting to solve...")
        company = _company_from_url(url)
        captcha_ok = await handle_captcha(rb, company, snap.title or "")
        if not captcha_ok:
            await rb.stop()
            return {"success": False, "error": "CAPTCHA — manual intervention needed"}
        # Re-grab snapshot after CAPTCHA solved
        await asyncio.sleep(3)
        snap = await rb.get_snapshot(force_refresh=True)
        if not snap:
            await rb.stop()
            return {"success": False, "error": "No page after CAPTCHA"}

    # ── Step 2: Analyze what's on screen ──
    print("Step 2: Analyzing page content...")

    # Try to get full JD text from extension's scan_jd command
    jd_text = ""
    try:
        jd_text = await rb.scan_jd(timeout_ms=8000)
    except Exception:
        pass

    # Fall back to page text from snapshot
    if not jd_text:
        jd_text = snap.page_text_preview or ""

    # Derive everything from URL + page content
    company = _company_from_url(url)
    title = snap.title or ""
    platform = _platform_from_url(url)

    # If title is a generic browser tab title, try to parse something useful
    if not title or title.lower() in ("apply", "application", "job"):
        title = jd_text[:100].split("\n")[0].strip() if jd_text else ""

    print(f"  Company: {company}")
    print(f"  Title: {title[:80]}")
    print(f"  Platform: {platform}")
    print(f"  JD length: {len(jd_text)} chars")

    if not jd_text and not title:
        await rb.stop()
        return {"success": False, "error": "No JD text or title found on page"}

    # ── Step 3: Generate tailored CV from page analysis ──
    print("Step 3: Generating CV...")

    listing = analyze_jd(
        url=url, title=title,
        company=company, platform=platform,
        jd_text=jd_text or title, apply_url=url,
    )

    store = SkillGraphStore()
    screen = store.pre_screen_jd(listing)
    print(f"  Tier: {screen.tier} | Score: {screen.gate3_score}")

    matched_projects = get_best_projects_for_jd(
        listing.required_skills, listing.preferred_skills, top_n=4,
    )
    role_profile = get_role_profile(listing.title)
    extra_skills = build_extra_skills(
        required_skills=listing.required_skills,
        preferred_skills=listing.preferred_skills,
    )

    location = listing.location or "UK"
    # Sanitize location — never allow JD text to leak into CV header
    # Max 40 chars, strip after common delimiters that indicate JD content
    for _delim in ["·", "•", " - ", ". ", "\n"]:
        if _delim in location:
            location = location.split(_delim)[0].strip()
    if len(location) > 40:
        location = location[:40].rsplit(",", 1)[0].strip()
    cv_path = generate_cv_pdf(
        company=company, location=location,
        tagline=role_profile.get("tagline"),
        summary=role_profile.get("summary"),
        projects=matched_projects,
        extra_skills=extra_skills if extra_skills else None,
    )
    print(f"  CV: {cv_path.name}")

    # ── Step 4: Run orchestrator with pre-navigated snapshot (no double navigate) ──
    print("Step 4: Running ApplicationOrchestrator...")

    # Convert snapshot to dict for the orchestrator
    snap_dict = snap.model_dump() if hasattr(snap, "model_dump") else snap

    # Wire up 5-tier form intelligence (pattern → cache → Gemini Nano → LLM → fallback)
    from jobpulse.form_intelligence import FormIntelligence
    fi = FormIntelligence(bridge=rb)

    orch = ApplicationOrchestrator(bridge=rb)
    result = await orch.apply(
        url=url,
        platform=platform,
        cv_path=cv_path,
        dry_run=False,
        form_intelligence=fi,
        pre_navigated_snapshot=snap_dict,
        custom_answers={
            "_job_context": {
                "job_title": listing.title,
                "company": company,
                "location": listing.location or "UK",
                "platform": platform,
                "source_url": url,
            }
        },
    )

    print(f"Orchestrator result: success={result.get('success')}, error={result.get('error', '')}")

    # ── Step 5: Handle CAPTCHA if needed ──
    # Detect CAPTCHA: explicit error OR stuck (inline CAPTCHA blocks form submission)
    err = result.get("error", "")
    needs_captcha = not result.get("success") and ("CAPTCHA" in err or "Stuck" in err)
    if needs_captcha:
        print("Step 5: Handling CAPTCHA...")
        captcha_ok = await handle_captcha(rb, company, title)
        if captcha_ok:
            _send_msg("CAPTCHA filled. Reply 'submit' to submit.")
            last_id = _drain_updates()
            reply, last_id = await _wait_for_reply(last_id, max_wait=120)
            if reply and reply.lower() == "submit":
                try:
                    await rb.click("button.lyte-button:last-of-type", timeout_ms=8000)
                except Exception:
                    pass
                await asyncio.sleep(3)
                post_snap = await rb.get_snapshot(force_refresh=True)
                from jobpulse.ext_models import PageType
                from jobpulse.page_analyzer import PageAnalyzer
                analyzer = PageAnalyzer(rb)
                submit_page = await analyzer.detect(post_snap) if post_snap else PageType.UNKNOWN
                if submit_page == PageType.CONFIRMATION:
                    _send_msg(f"Application submitted! {company} — {title}")
                    result = {"success": True}
                else:
                    _send_msg(f"Submit clicked. Page: {submit_page}. Check manually.")
                    result = {"success": True, "needs_check": True}
            else:
                _send_msg("No submit confirmation received. Application paused.")
        else:
            _send_msg("CAPTCHA handling failed. Application paused.")

    # Notify on Telegram
    if result.get("success"):
        print("SUCCESS")
        _send_msg(f"Application complete: {company} — {title}")
    else:
        print(f"FAILED: {result.get('error', 'unknown')}")
        ss = result.get("screenshot")
        if ss:
            _send_photo(ss, f"Application failed ({company}): {result.get('error', 'unknown')}")
        else:
            _send_msg(f"Application failed ({company}): {result.get('error', 'unknown')}")

    await rb.stop()
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.apply_now <url>")
        sys.exit(1)
    url = sys.argv[1]
    asyncio.run(apply_to_job(url))
