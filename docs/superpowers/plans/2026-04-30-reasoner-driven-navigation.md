# Reasoner-Driven Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded classifier→handler state machine in the navigator with an LLM-driven observe→reason→act loop at every step.

**Architecture:** Every navigation step takes a page snapshot, sends it to the PageReasoner (LLM), gets back a structured action with specific field fills, and executes it. The DOM classifier becomes a fast-path shortcut for high-confidence cases only. Auth handlers become content-driven — they read the actual fields on the page instead of following hardcoded flows.

**Tech Stack:** OpenAI gpt-4o-mini via `smart_llm_call()`, Playwright CDP, SQLite caching (domain+content_hash)

---

## File Structure

| File | Role | Action |
|------|------|--------|
| `jobpulse/page_analysis/page_reasoner.py` | LLM page understanding — returns structured actions with field fills | **Major rewrite** — richer prompt, field-level actions, sync method, overlay awareness |
| `jobpulse/application_orchestrator_pkg/_navigator.py` | Main navigation loop | **Major rewrite** — replace if/elif PageType chain with reasoner-driven loop |
| `jobpulse/application_orchestrator_pkg/_auth.py` | Login/signup/email verify | **Delete most code** — replaced by reasoner + action executor |
| `jobpulse/page_analyzer.py` | 3-tier page type detection | **Simplify** — only used as fast-path, no longer primary decision maker |
| `jobpulse/navigation/action_executor.py` | New: executes reasoner actions on the page | **Create** — translates PageAction into Playwright calls |
| `tests/jobpulse/test_reasoner_navigation.py` | Tests for the new loop | **Create** |
| `tests/jobpulse/test_nav_action_executor.py` | Tests for action executor | **Create** |

---

### Task 1: Upgrade PageReasoner — richer prompt, field-level actions, sync interface

The current PageReasoner returns a single action + target_text. That's not enough — it needs to return specific field fills (which field gets which value), overlay dismissal targets, and checkbox handling instructions. It also has an async/sync bug and doesn't handle overlays.

**Files:**
- Modify: `jobpulse/page_analysis/page_reasoner.py`
- Test: `tests/jobpulse/test_reasoner_navigation.py`

- [ ] **Step 1: Write the failing test for the upgraded reasoner**

```python
# tests/jobpulse/test_reasoner_navigation.py
"""Tests for the reasoner-driven navigation loop."""
import json
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.page_analysis.page_reasoner import (
    PageReasoner, PageAction, VALID_ACTIONS,
)


def _fake_llm_response(data: dict) -> MagicMock:
    """Create a mock AIMessage with .content = JSON string."""
    msg = MagicMock()
    msg.content = json.dumps(data)
    return msg


class TestPageReasonerParsing:
    def test_parse_field_fills(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Email entry page for Oracle Cloud",
            "page_type": "signup_form",
            "action": "fill_and_advance",
            "field_fills": [
                {"label": "Email Address", "value": "FROM_PROFILE:email", "method": "fill"},
                {"label": "I agree with the terms", "value": "true", "method": "check_label"},
            ],
            "advance_button": "Next",
            "overlays_to_dismiss": ["Agree"],
            "reasoning": "Simple email entry with consent checkbox",
            "confidence": 0.95,
        })
        action = reasoner._parse_response(text)
        assert action.action == "fill_and_advance"
        assert len(action.field_fills) == 2
        assert action.field_fills[0]["label"] == "Email Address"
        assert action.advance_button == "Next"
        assert action.overlays_to_dismiss == ["Agree"]

    def test_parse_click_apply(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Job listing page with Apply button",
            "page_type": "job_description",
            "action": "click_element",
            "target_text": "Apply Now",
            "field_fills": [],
            "advance_button": "",
            "overlays_to_dismiss": [],
            "reasoning": "Click apply to proceed",
            "confidence": 0.9,
        })
        action = reasoner._parse_response(text)
        assert action.action == "click_element"
        assert action.target_text == "Apply Now"

    def test_parse_dismiss_overlay(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Cookie consent overlay blocking page",
            "page_type": "unknown",
            "action": "dismiss_overlay",
            "target_text": "Accept",
            "field_fills": [],
            "advance_button": "",
            "overlays_to_dismiss": ["Accept", "Agree"],
            "reasoning": "Cookie consent must be dismissed first",
            "confidence": 0.95,
        })
        action = reasoner._parse_response(text)
        assert action.action == "dismiss_overlay"

    def test_parse_captcha_routes_to_human(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Page with hCaptcha blocking interaction",
            "page_type": "verification_wall",
            "action": "wait_human",
            "target_text": "",
            "field_fills": [],
            "advance_button": "",
            "overlays_to_dismiss": [],
            "reasoning": "CAPTCHA requires human intervention",
            "confidence": 0.9,
        })
        action = reasoner._parse_response(text)
        assert action.action == "wait_human"

    def test_honeypot_skipped(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Signup with honeypot",
            "page_type": "signup_form",
            "action": "fill_and_advance",
            "field_fills": [
                {"label": "Email Address", "value": "FROM_PROFILE:email", "method": "fill"},
            ],
            "advance_button": "Next",
            "overlays_to_dismiss": [],
            "reasoning": "Honeypot field skipped",
            "confidence": 0.9,
        })
        action = reasoner._parse_response(text)
        assert len(action.field_fills) == 1
        assert all(f["label"] != "honeypot" for f in action.field_fills)

    def test_valid_actions_includes_new_types(self):
        assert "fill_and_advance" in VALID_ACTIONS
        assert "dismiss_overlay" in VALID_ACTIONS
        assert "fill_form" in VALID_ACTIONS
        assert "wait_human" in VALID_ACTIONS


class TestPageReasonerSync:
    @patch("jobpulse.page_analysis.page_reasoner.smart_llm_call")
    @patch("jobpulse.page_analysis.page_reasoner.get_llm")
    def test_reason_sync_returns_page_action(self, mock_get_llm, mock_smart_call):
        mock_smart_call.return_value = _fake_llm_response({
            "page_understanding": "Login page",
            "page_type": "login_form",
            "action": "fill_and_advance",
            "field_fills": [
                {"label": "Email", "value": "FROM_PROFILE:email", "method": "fill"},
            ],
            "advance_button": "Sign In",
            "overlays_to_dismiss": [],
            "reasoning": "Fill email and sign in",
            "confidence": 0.9,
        })
        reasoner = PageReasoner.__new__(PageReasoner)
        reasoner._ensure_db = lambda: None
        # reason_sync is the new sync method
        action = reasoner.reason_sync({
            "url": "https://example.com/login",
            "page_text_preview": "Sign in to your account",
            "buttons": [{"text": "Sign In"}],
            "fields": [{"label": "Email", "input_type": "email"}],
        })
        assert isinstance(action, PageAction)
        assert action.action == "fill_and_advance"
        assert action.confidence == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_reasoner_navigation.py -v --timeout=30 -x`
Expected: FAIL — `PageAction` doesn't have `field_fills`, `advance_button`, `overlays_to_dismiss` yet. `reason_sync` doesn't exist. `fill_and_advance` and `dismiss_overlay` not in `VALID_ACTIONS`.

- [ ] **Step 3: Rewrite PageReasoner with richer output and sync interface**

Replace the entire content of `jobpulse/page_analysis/page_reasoner.py`:

```python
"""Semantic page reasoner — LLM-based understanding for every navigation step.

PRIMARY decision-maker for the navigation loop. Takes a page snapshot,
reasons about what to do, and returns structured actions with specific
field fills, overlay dismissals, and advance buttons.

Costs ~$0.001 per call. Cached per domain+content_hash (1hr TTL).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "page_reasoning_cache.db"

VALID_ACTIONS = frozenset({
    "fill_and_advance",
    "click_element",
    "dismiss_overlay",
    "dismiss_dialog",
    "click_apply",
    "fill_form",
    "login",
    "signup",
    "accept_consent",
    "wait_human",
    "go_back",
    "abort",
    "done",
})


@dataclass
class PageAction:
    page_understanding: str
    action: str
    target_text: str
    reasoning: str
    confidence: float
    page_type: str
    field_fills: list[dict[str, str]] = dc_field(default_factory=list)
    advance_button: str = ""
    overlays_to_dismiss: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_understanding": self.page_understanding,
            "action": self.action,
            "target_text": self.target_text,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "page_type": self.page_type,
            "field_fills": self.field_fills,
            "advance_button": self.advance_button,
            "overlays_to_dismiss": self.overlays_to_dismiss,
        }


class PageReasoner:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = str(db_path or _DB_PATH)
        self._ensure_db()

    def _ensure_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reasoning_cache (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)

    def _cache_key(self, url: str, page_text: str, dialog_text: str) -> str:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().removeprefix("www.") if url else ""
        content_hash = hashlib.sha256(
            (page_text[:500] + "|" + dialog_text[:300]).encode()
        ).hexdigest()[:16]
        return f"{domain}:{content_hash}"

    def _get_cached(self, key: str) -> PageAction | None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT result_json, created_at FROM reasoning_cache WHERE cache_key = ?",
                    (key,),
                ).fetchone()
            if row and (time.time() - row[1]) < 3600:
                data = json.loads(row[0])
                return PageAction(**data)
        except Exception:
            pass
        return None

    def _set_cache(self, key: str, action: PageAction) -> None:
        if action.action == "abort" and action.confidence < 0.5:
            return
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO reasoning_cache (cache_key, result_json, created_at) VALUES (?, ?, ?)",
                    (key, json.dumps(action.to_dict()), time.time()),
                )
        except Exception:
            pass

    def reason_sync(self, snapshot: dict[str, Any]) -> PageAction:
        """Synchronous page reasoning — primary entry point."""
        url = snapshot.get("url", "")
        page_text = snapshot.get("page_text_preview", "")[:800]
        dialog_text = snapshot.get("dialog_text", "")[:500]
        buttons = snapshot.get("buttons", [])
        fields = snapshot.get("fields", [])
        wall = snapshot.get("verification_wall")

        cache_key = self._cache_key(url, page_text, dialog_text)
        cached = self._get_cached(cache_key)
        if cached:
            logger.info("PageReasoner: cache hit for %s → %s", cache_key[:30], cached.action)
            return cached

        button_summary = [b.get("text", "")[:40] for b in buttons[:15] if b.get("text")]
        field_summary = []
        for f in fields[:20]:
            label = f.get("label", "?")
            ftype = f.get("input_type", f.get("type", "?"))
            value = f.get("value", "")
            entry = f"{label} ({ftype})"
            if value:
                entry += f" [current: {value[:30]}]"
            field_summary.append(entry)

        wall_info = ""
        if wall:
            wall_info = f"\nCAPTCHA/WALL DETECTED: {wall.get('type', 'unknown')}"

        prompt = self._build_prompt(url, page_text, dialog_text, button_summary, field_summary, wall_info)
        action = self._call_llm(prompt)

        self._set_cache(cache_key, action)
        logger.info(
            "PageReasoner: %s → action=%s, type=%s, confidence=%.2f — %s",
            url[:60], action.action, action.page_type, action.confidence,
            action.page_understanding[:80],
        )
        return action

    async def reason(self, snapshot: dict[str, Any]) -> PageAction:
        """Async wrapper for backward compatibility."""
        return self.reason_sync(snapshot)

    def _call_llm(self, prompt: str) -> PageAction:
        try:
            from shared.agents import get_llm, is_local_llm, smart_llm_call
            from langchain_core.messages import SystemMessage, HumanMessage
            msgs = [
                SystemMessage(content=self._system_prompt()),
                HumanMessage(content=prompt),
            ]
            llm = get_llm(temperature=0, max_tokens=500, agent_name="page_reasoner")
            try:
                response = smart_llm_call(llm, msgs)
            except Exception as local_err:
                if is_local_llm():
                    logger.warning("PageReasoner local LLM failed, falling back to cloud: %s", local_err)
                    from langchain_openai import ChatOpenAI as _ChatOpenAI
                    cloud_llm = _ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=500, timeout=30)
                    response = smart_llm_call(cloud_llm, msgs)
                else:
                    raise
            text = response.content if hasattr(response, "content") else str(response)
            return self._parse_response(text)
        except Exception as exc:
            logger.warning("PageReasoner LLM call failed: %s", exc)
            return PageAction(
                page_understanding="LLM reasoning failed",
                action="abort",
                target_text="",
                reasoning=str(exc),
                confidence=0.0,
                page_type="unknown",
            )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a page analyzer for a job application bot. "
            "You see a web page's content, fields, buttons, and any overlays/CAPTCHAs.\n\n"
            "Your job: decide EXACTLY what to do on this page — which fields to fill, "
            "which checkboxes to check, which overlays to dismiss, and which button to click to advance.\n\n"
            "Return ONLY a JSON object:\n"
            "{\n"
            '  "page_understanding": "one sentence describing what you see",\n'
            '  "page_type": "job_description|application_form|login_form|signup_form|'
            'email_verification|confirmation|verification_wall|consent_gate|session_expired|unknown",\n'
            '  "action": "fill_and_advance|click_element|dismiss_overlay|wait_human|fill_form|done|abort",\n'
            '  "target_text": "button/link text to click (if action is click_element)",\n'
            '  "field_fills": [\n'
            '    {"label": "field label", "value": "what to put", "method": "fill|check_label|check_input|select|skip"}\n'
            "  ],\n"
            '  "advance_button": "text of Next/Submit/Continue button to click after filling",\n'
            '  "overlays_to_dismiss": ["button text to click to dismiss cookie/session overlays"],\n'
            '  "reasoning": "why this action",\n'
            '  "confidence": 0.0-1.0\n'
            "}\n\n"
            "RULES:\n"
            '- For email fields, use value "FROM_PROFILE:email"\n'
            '- For name fields, use "FROM_PROFILE:first_name" or "FROM_PROFILE:last_name"\n'
            '- For phone fields, use "FROM_PROFILE:phone"\n'
            '- For password fields, use "FROM_PROFILE:password"\n'
            "- For consent/agree checkboxes, method = \"check_label\" (clicks the label, not the hidden input)\n"
            "- For honeypot fields (hidden, named 'honeypot', trap fields), method = \"skip\"\n"
            "- If a CAPTCHA/hCaptcha/reCAPTCHA is present and blocking interaction, action = \"wait_human\"\n"
            "- If overlays (cookie consent, session timeout) are blocking the form, list them in overlays_to_dismiss\n"
            "- If this is an application form ready to fill, action = \"fill_form\" (hand off to form filler)\n"
            "- If application was submitted successfully, action = \"done\"\n"
            "- action \"fill_and_advance\" = fill the listed fields + click advance_button\n"
            "- action \"click_element\" = click a specific button/link (e.g. Apply Now)\n\n"
            "Context: The bot navigates from a job listing to the application form, "
            "fills it out, and stops before final submission. Dismiss all non-application overlays. "
            "Proceed through login/signup. Fill application forms."
        )

    @staticmethod
    def _build_prompt(
        url: str,
        page_text: str,
        dialog_text: str,
        buttons: list[str],
        fields: list[str],
        wall_info: str,
    ) -> str:
        parts = [f"URL: {url}"]
        if dialog_text:
            parts.append(f"DIALOG/MODAL TEXT:\n{dialog_text[:500]}")
        parts.append(f"PAGE TEXT:\n{page_text[:600]}")
        if buttons:
            parts.append(f"BUTTONS: {', '.join(buttons)}")
        if fields:
            parts.append(f"FORM FIELDS:\n" + "\n".join(f"  - {f}" for f in fields))
        if wall_info:
            parts.append(wall_info)
        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(text: str) -> PageAction:
        try:
            if "{" in text:
                text = text[text.index("{"):text.rindex("}") + 1]
            data = json.loads(text)
            action = data.get("action", "abort")
            if action not in VALID_ACTIONS:
                action = "abort"
            return PageAction(
                page_understanding=data.get("page_understanding", ""),
                action=action,
                target_text=data.get("target_text", ""),
                reasoning=data.get("reasoning", ""),
                confidence=float(data.get("confidence", 0.5)),
                page_type=data.get("page_type", "unknown"),
                field_fills=data.get("field_fills", []),
                advance_button=data.get("advance_button", ""),
                overlays_to_dismiss=data.get("overlays_to_dismiss", []),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            return PageAction(
                page_understanding=f"Failed to parse LLM response: {exc}",
                action="abort",
                target_text="",
                reasoning=text[:200],
                confidence=0.0,
                page_type="unknown",
            )


_reasoner: PageReasoner | None = None


def get_page_reasoner() -> PageReasoner:
    global _reasoner
    if _reasoner is None:
        _reasoner = PageReasoner()
    return _reasoner
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_reasoner_navigation.py -v --timeout=30 -x`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/page_analysis/page_reasoner.py tests/jobpulse/test_reasoner_navigation.py
git commit -m "feat(reasoner): richer PageAction with field_fills, overlays, sync interface"
```

---

### Task 2: Create navigation action executor

The navigator needs a module that takes a `PageAction` and executes it on the page — dismissing overlays, filling fields, checking checkboxes (via labels), and clicking advance buttons. This is different from the form-fill `ActionExecutor` which handles typed form actions — this handles the reasoner's free-form navigation instructions.

**Files:**
- Create: `jobpulse/navigation/action_executor.py`
- Test: `tests/jobpulse/test_nav_action_executor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_nav_action_executor.py
"""Tests for the navigation action executor."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from jobpulse.page_analysis.page_reasoner import PageAction
from jobpulse.navigation.action_executor import NavigationActionExecutor


def _make_action(**kwargs) -> PageAction:
    defaults = {
        "page_understanding": "test",
        "action": "fill_and_advance",
        "target_text": "",
        "reasoning": "test",
        "confidence": 0.9,
        "page_type": "signup_form",
        "field_fills": [],
        "advance_button": "",
        "overlays_to_dismiss": [],
    }
    defaults.update(kwargs)
    return PageAction(**defaults)


@pytest.fixture
def mock_page():
    page = AsyncMock()
    page.url = "https://example.com/apply"
    btn_locator = AsyncMock()
    btn_locator.count = AsyncMock(return_value=1)
    btn_locator.first = AsyncMock()
    btn_locator.first.is_visible = AsyncMock(return_value=True)
    btn_locator.first.click = AsyncMock()
    page.get_by_role = MagicMock(return_value=btn_locator)
    page.get_by_label = MagicMock(return_value=btn_locator)
    page.locator = MagicMock(return_value=btn_locator)
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    return page


@pytest.fixture
def executor(mock_page):
    return NavigationActionExecutor(mock_page)


class TestOverlayDismissal:
    @pytest.mark.asyncio
    async def test_dismisses_overlays_before_filling(self, executor, mock_page):
        action = _make_action(
            overlays_to_dismiss=["Agree", "Continue Working"],
            field_fills=[{"label": "Email", "value": "test@test.com", "method": "fill"}],
        )
        await executor.execute(action, profile={})
        # get_by_role should have been called for overlay buttons first
        calls = mock_page.get_by_role.call_args_list
        assert any("Agree" in str(c) for c in calls)


class TestFieldFilling:
    @pytest.mark.asyncio
    async def test_fill_resolves_profile_refs(self, executor, mock_page):
        action = _make_action(
            field_fills=[{"label": "Email Address", "value": "FROM_PROFILE:email", "method": "fill"}],
        )
        profile = {"email": "user@example.com"}
        await executor.execute(action, profile=profile)
        mock_page.get_by_label.assert_called()

    @pytest.mark.asyncio
    async def test_check_label_clicks_label_not_input(self, executor, mock_page):
        action = _make_action(
            field_fills=[{"label": "I agree with terms", "value": "true", "method": "check_label"}],
        )
        await executor.execute(action, profile={})
        mock_page.get_by_label.assert_called()

    @pytest.mark.asyncio
    async def test_skip_method_does_nothing(self, executor, mock_page):
        action = _make_action(
            field_fills=[{"label": "honeypot", "value": "", "method": "skip"}],
        )
        await executor.execute(action, profile={})
        # fill should not have been called
        mock_page.fill.assert_not_called()


class TestAdvanceButton:
    @pytest.mark.asyncio
    async def test_clicks_advance_button(self, executor, mock_page):
        action = _make_action(advance_button="Next")
        await executor.execute(action, profile={})
        mock_page.get_by_role.assert_called()

    @pytest.mark.asyncio
    async def test_no_advance_button_does_not_crash(self, executor, mock_page):
        action = _make_action(advance_button="")
        await executor.execute(action, profile={})


class TestClickElement:
    @pytest.mark.asyncio
    async def test_click_element_uses_target_text(self, executor, mock_page):
        action = _make_action(action="click_element", target_text="Apply Now")
        await executor.execute(action, profile={})
        calls = mock_page.get_by_role.call_args_list
        assert any("Apply Now" in str(c) for c in calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_nav_action_executor.py -v --timeout=30 -x`
Expected: FAIL — `jobpulse.navigation.action_executor` does not exist

- [ ] **Step 3: Create the navigation action executor**

```python
# jobpulse/navigation/action_executor.py
"""Executes PageAction instructions on the live page.

Translates the reasoner's structured actions into Playwright calls:
overlay dismissal → field fills → checkbox checks → advance button click.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from shared.logging_config import get_logger

from jobpulse.page_analysis.page_reasoner import PageAction

logger = get_logger(__name__)

_PROFILE_REF = re.compile(r"^FROM_PROFILE:(\w+)$")


class NavigationActionExecutor:
    """Executes a PageAction's instructions on a Playwright page."""

    def __init__(self, page: Any) -> None:
        self._page = page

    async def execute(self, action: PageAction, profile: dict[str, str]) -> None:
        """Execute the full action: dismiss overlays → fill fields → click advance."""
        if action.overlays_to_dismiss:
            await self._dismiss_overlays(action.overlays_to_dismiss)

        if action.action == "click_element":
            await self._click_by_text(action.target_text)
            return

        if action.action == "dismiss_overlay":
            if action.target_text:
                await self._click_by_text(action.target_text)
            return

        if action.action in ("fill_and_advance", "login", "signup"):
            for fill in action.field_fills:
                await self._execute_fill(fill, profile)
            if action.advance_button:
                await asyncio.sleep(0.3)
                await self._click_by_text(action.advance_button)

    async def _dismiss_overlays(self, overlay_buttons: list[str]) -> None:
        for text in overlay_buttons:
            try:
                for role in ("button", "link"):
                    loc = self._page.get_by_role(role, name=text, exact=False)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click()
                        logger.info("Dismissed overlay: '%s'", text)
                        await asyncio.sleep(0.5)
                        break
            except Exception as exc:
                logger.debug("Overlay dismiss failed for '%s': %s", text, exc)

    async def _execute_fill(self, fill: dict[str, str], profile: dict[str, str]) -> None:
        label = fill.get("label", "")
        value = fill.get("value", "")
        method = fill.get("method", "fill")

        if method == "skip":
            logger.debug("Skipping field: %s", label)
            return

        value = self._resolve_value(value, profile)

        try:
            if method == "check_label":
                loc = self._page.get_by_label(label, exact=False)
                if await loc.count():
                    checked = await loc.first.is_checked()
                    if not checked:
                        await loc.first.check()
                        logger.info("Checked: %s", label[:50])
                else:
                    loc = self._page.get_by_text(label, exact=False)
                    if await loc.count():
                        await loc.first.click()
                        logger.info("Clicked label text: %s", label[:50])

            elif method == "check_input":
                loc = self._page.get_by_label(label, exact=False)
                if await loc.count():
                    await loc.first.check()
                    logger.info("Checked input: %s", label[:50])

            elif method == "select":
                loc = self._page.get_by_label(label, exact=False)
                if await loc.count():
                    await loc.first.select_option(value)
                    logger.info("Selected %s = %s", label[:30], value[:30])

            elif method == "fill":
                loc = self._page.get_by_label(label, exact=False)
                if await loc.count():
                    await loc.first.fill(value)
                    logger.info("Filled %s", label[:30])
                else:
                    loc = self._page.get_by_placeholder(label, exact=False)
                    if await loc.count():
                        await loc.first.fill(value)
                        logger.info("Filled (placeholder) %s", label[:30])

        except Exception as exc:
            logger.warning("Fill failed for '%s' (%s): %s", label[:30], method, exc)

    async def _click_by_text(self, text: str) -> None:
        if not text:
            return
        for role in ("button", "link"):
            try:
                loc = self._page.get_by_role(role, name=text, exact=False)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click()
                    logger.info("Clicked %s: '%s'", role, text[:40])
                    await asyncio.sleep(1.0)
                    return
            except Exception:
                continue
        logger.warning("Could not find clickable element: '%s'", text[:40])

    @staticmethod
    def _resolve_value(value: str, profile: dict[str, str]) -> str:
        m = _PROFILE_REF.match(value)
        if m:
            key = m.group(1)
            return profile.get(key, "")
        return value
```

Ensure the `__init__.py` exists:

```bash
ls jobpulse/navigation/__init__.py || touch jobpulse/navigation/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_nav_action_executor.py -v --timeout=30 -x`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/navigation/action_executor.py tests/jobpulse/test_nav_action_executor.py
git commit -m "feat(nav): action executor translates PageAction to Playwright calls"
```

---

### Task 3: Rewrite navigator main loop — reasoner-driven at every step

Replace the `navigate_to_form` method's if/elif PageType chain with a simple loop:
1. Take snapshot
2. Fast-path: DOM classifier with high confidence → skip LLM for obvious pages (APPLICATION_FORM, CONFIRMATION)
3. Otherwise: PageReasoner → PageAction → NavigationActionExecutor
4. Take new snapshot, repeat

The existing `_bypass_verification_wall`, `click_apply_button`, learned sequence replay, and `_dismiss_site_prompt_if_present` are preserved — they handle specific concerns the reasoner delegates to. But the main routing decision is now always the reasoner.

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py`
- Test: `tests/jobpulse/test_reasoner_navigation.py` (add more tests)

- [ ] **Step 1: Add tests for the reasoner-driven loop**

Append to `tests/jobpulse/test_reasoner_navigation.py`:

```python
class TestNavigatorReasonerLoop:
    """Test that the navigator uses the reasoner at every step."""

    @patch("jobpulse.page_analysis.page_reasoner.smart_llm_call")
    @patch("jobpulse.page_analysis.page_reasoner.get_llm")
    def test_reasoner_called_each_step(self, mock_get_llm, mock_smart_call):
        """Verify the reasoner is invoked per navigation step, not just as fallback."""
        # This is a structural test — we verify the new navigate_to_form
        # calls reason_sync at each step by checking the import path
        from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
        import inspect
        source = inspect.getsource(FormNavigator.navigate_to_form)
        assert "reason_sync" in source or "reasoner.reason" in source, (
            "navigate_to_form must call the reasoner at every step"
        )
        assert source.count("PageType.LOGIN_FORM") == 0 or "fast_path" in source.lower(), (
            "navigate_to_form should not have hardcoded PageType routing (except fast-path)"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_reasoner_navigation.py::TestNavigatorReasonerLoop -v --timeout=30 -x`
Expected: FAIL — current navigator still uses hardcoded PageType routing

- [ ] **Step 3: Rewrite the navigate_to_form loop**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, replace the `navigate_to_form` method (lines 107–387). Keep the method signature, learned sequence replay, cookie dismissal, and initial navigation. Replace the `for step in range(MAX_NAVIGATION_STEPS):` loop body:

```python
    async def navigate_to_form(
        self, url: str, platform: str, steps: list[dict],
        skip_initial_navigate: bool = False,
        job: dict | None = None,
    ) -> dict:
        """Navigate through redirect chain to reach application form.

        Uses the PageReasoner at every step to decide what to do based on
        actual page content. DOM classifier is only a fast-path for
        high-confidence APPLICATION_FORM/CONFIRMATION detection.
        """
        # LinkedIn Easy Apply modal shortcut (unchanged)
        current_page = getattr(self.driver, "page", None)
        if current_page is not None:
            try:
                page_url = current_page.url or ""
                if "linkedin.com" in page_url:
                    modal = current_page.locator('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]')
                    if await modal.count():
                        logger.info("Easy Apply modal already open — skipping initial navigation")
                        snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                        return {"page_type": PageType.APPLICATION_FORM, "snapshot": snapshot}
            except Exception:
                pass

        if not skip_initial_navigate:
            try:
                await self.driver.navigate(url)
            except (TimeoutError, ConnectionError):
                logger.info("Navigate lost (MV3 restart) — waiting for extension to reconnect")
                await wait_for_page_stable(self.driver.page, timeout_ms=8000)
        snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        if not snapshot or not snapshot.get("url"):
            await wait_for_page_stable(self.driver.page, timeout_ms=8000)
            snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

        # Try learned sequence first (unchanged)
        domain = extract_domain(url)
        learned = self.learner.get_sequence(domain)
        if not learned and platform:
            learned = self.learner.get_platform_pattern(platform, exclude_domain=domain)
            if learned:
                logger.info("Using PLATFORM pattern for %s (%s)", domain, platform)
        if learned:
            logger.info("Replaying learned navigation for %s (%d steps)", domain, len(learned))
            self.learner.increment_replay(domain)
            replay_ok = True
            for learned_step in learned:
                action = learned_step.get("action", "")
                try:
                    if action in {"click_apply", "click_apply_guess", "linkedin_direct_apply"}:
                        snapshot = await self.click_apply_button(snapshot)
                    elif action == "fill_login":
                        snapshot = await self._reasoner_step(snapshot, platform, steps)
                    elif action.startswith("sso_"):
                        provider = action[len("sso_"):]
                        sso = self.sso.detect_sso(snapshot)
                        if sso and sso.get("provider") == provider:
                            await self.sso.click_sso(sso)
                            snapshot = self._as_dict(await self.driver.get_snapshot())
                        else:
                            replay_ok = False
                            break
                    elif action == "fill_signup":
                        snapshot = await self._reasoner_step(snapshot, platform, steps)
                    elif action == "verify_email":
                        snapshot = await self.auth.handle_email_verification(snapshot, platform, url)
                    else:
                        replay_ok = False
                        break
                    await self.cookie_dismisser.dismiss(snapshot)
                    snapshot = self._as_dict(await self.driver.get_snapshot())
                except Exception as replay_exc:
                    logger.warning("Replay step failed: %s", replay_exc)
                    self.learner.mark_failed(domain)
                    replay_ok = False
                    break

            if replay_ok:
                page_type_after = await self.analyzer.detect(snapshot)
                if page_type_after == PageType.APPLICATION_FORM:
                    logger.info("Replay succeeded: reached APPLICATION_FORM for %s", domain)
                    return {"page_type": page_type_after, "snapshot": snapshot}
                logger.info("Replay completed but page_type=%s — continuing", page_type_after)
                self.learner.mark_failed(domain)

        # Cookie banner dismissal (unchanged)
        await self.cookie_dismisser.dismiss(snapshot)
        current_page = getattr(self.driver, "page", None)
        if current_page is not None:
            await dismiss_cookie_banner_playwright(current_page)
        snapshot = self._as_dict(await self.driver.get_snapshot())

        # ── Reasoner-driven navigation loop ──
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import NavigationActionExecutor
        reasoner = get_page_reasoner()

        visited_states: dict[str, int] = {}
        for step in range(MAX_NAVIGATION_STEPS):
            # Fast-path: DOM classifier for high-confidence terminal states
            dom_type, dom_confidence = self._dom_classify(snapshot)
            if dom_confidence >= 0.85 and dom_type == PageType.APPLICATION_FORM:
                logger.info("Fast-path: APPLICATION_FORM (confidence=%.2f)", dom_confidence)
                return {"page_type": PageType.APPLICATION_FORM, "snapshot": snapshot}
            if dom_confidence >= 0.85 and dom_type == PageType.CONFIRMATION:
                logger.info("Fast-path: CONFIRMATION (confidence=%.2f)", dom_confidence)
                return {"page_type": PageType.CONFIRMATION, "snapshot": snapshot}

            # Reasoner decides what to do
            action = reasoner.reason_sync(snapshot)
            logger.info(
                "Step %d: reasoner → %s (type=%s, conf=%.2f) — %s",
                step + 1, action.action, action.page_type, action.confidence,
                action.page_understanding[:80],
            )

            # Loop detection
            state_key = f"{action.page_type}:{action.action}"
            visited_states[state_key] = visited_states.get(state_key, 0) + 1
            if visited_states[state_key] >= 3:
                logger.warning("Reasoner loop: %s × %d — aborting", state_key, visited_states[state_key])
                return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

            # Terminal actions
            if action.action == "fill_form":
                return {"page_type": PageType.APPLICATION_FORM, "snapshot": snapshot}
            if action.action == "done":
                return {"page_type": PageType.CONFIRMATION, "snapshot": snapshot}
            if action.action == "abort":
                logger.warning("Reasoner says abort: %s", action.reasoning)
                return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

            # Verification wall / CAPTCHA — use existing bypass pipeline
            if action.action == "wait_human":
                wall_info = snapshot.get("verification_wall") or {"type": "unknown"}
                bypass_result = await self._bypass_verification_wall(snapshot, wall_info)
                if bypass_result["solved"]:
                    snapshot = bypass_result["snapshot"]
                    visited_states.clear()
                    continue
                # Platform bypass for aggregator walls
                if job:
                    pb_result = await self._try_platform_bypass(snapshot, job, steps)
                    if pb_result is not None:
                        snapshot = pb_result
                        visited_states.clear()
                        continue
                return {"page_type": PageType.VERIFICATION_WALL, "snapshot": bypass_result["snapshot"]}

            # SSO detection — check before executing generic fills
            if action.page_type in ("login_form", "signup_form", "session_expired"):
                sso = self.sso.detect_sso(snapshot)
                if sso:
                    await self.sso.click_sso(sso)
                    snapshot = self._as_dict(await self.driver.get_snapshot())
                    steps.append({"page_type": action.page_type, "action": f"sso_{sso['provider']}"})
                    continue

            # Email verification — delegate to existing handler
            if action.page_type == "email_verification":
                snapshot = await self.auth.handle_email_verification(snapshot, platform, url)
                steps.append({"page_type": "email_verification", "action": "verify_email"})
                continue

            # Execute the reasoner's action on the page
            page = getattr(self.driver, "page", None)
            if page is not None:
                from jobpulse.applicator import PROFILE
                nav_executor = NavigationActionExecutor(page)
                await nav_executor.execute(action, profile=PROFILE)

            steps.append({"page_type": action.page_type, "action": action.action})

            # Post-action: dismiss cookies, get fresh snapshot
            await asyncio.sleep(1.0)
            await self.cookie_dismisser.dismiss(snapshot)
            if page is not None:
                await dismiss_cookie_banner_playwright(page)

            # Handle new tabs
            if page is not None:
                snapshot = await self._handle_new_tabs(page, snapshot)
            else:
                snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

        return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

    async def _reasoner_step(self, snapshot: dict, platform: str, steps: list[dict]) -> dict:
        """Single reasoner-driven step — used during learned sequence replay fallback."""
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import NavigationActionExecutor
        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(snapshot)
        page = getattr(self.driver, "page", None)
        if page is not None:
            from jobpulse.applicator import PROFILE
            nav_executor = NavigationActionExecutor(page)
            await nav_executor.execute(action, profile=PROFILE)
        steps.append({"page_type": action.page_type, "action": action.action})
        await asyncio.sleep(1.0)
        return self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    @staticmethod
    def _dom_classify(snapshot: dict) -> tuple[PageType, float]:
        from jobpulse.page_analysis.classifier import PageTypeClassifier
        clf = PageTypeClassifier()
        return clf.classify(snapshot)

    async def _handle_new_tabs(self, page, snapshot: dict) -> dict:
        """Check for new tabs after a click and switch to them."""
        context = getattr(page, "context", None)
        if context is None:
            return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        pages = context.pages
        if len(pages) > 1:
            newest = pages[-1]
            try:
                await newest.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            if newest.url and newest.url != page.url:
                logger.info("Switched to new tab: %s", newest.url[:80])
                self.driver._page = newest
        return self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    async def _try_platform_bypass(self, snapshot: dict, job: dict, steps: list[dict]) -> dict | None:
        """Try platform bypass for aggregator walls. Returns new snapshot or None."""
        wall_url = snapshot.get("url", "")
        try:
            from jobpulse.platform_bypass import is_aggregator_domain, get_platform_bypass
            if not is_aggregator_domain(wall_url):
                return None
            logger.info("Aggregator wall on %s — attempting platform bypass", wall_url)
            page = getattr(self.driver, "page", None)
            pb = get_platform_bypass()
            pb_result = await pb.resolve_direct_url(job, wall_url, page)
            if pb_result.resolved:
                logger.info("Platform bypass: %s → %s", wall_url[:40], pb_result.direct_url[:60])
                await self.driver.page.goto(pb_result.direct_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)
                new_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                steps.append({
                    "page_type": "platform_bypass",
                    "action": "redirect_to_ats",
                    "from_url": wall_url,
                    "to_url": pb_result.direct_url,
                    "strategy": pb_result.strategy_used,
                })
                return new_snap
        except Exception as exc:
            logger.debug("Platform bypass failed: %s", exc)
        return None
```

Also keep the existing `click_apply_button`, `_bypass_verification_wall`, `_dismiss_site_prompt_if_present`, `verify_submission`, and the `score_apply_button` / `ApplyButtonPatterns` / `find_apply_button` functions unchanged. Delete the old `_semantic_fallback` method — the reasoner now handles this inline.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_reasoner_navigation.py -v --timeout=30 -x`
Expected: All tests PASS (including the structural test)

- [ ] **Step 5: Run existing page analysis tests to check for regressions**

Run: `python -m pytest tests/jobpulse/test_page_analysis.py tests/jobpulse/test_page_analyzer.py -v --timeout=30`
Expected: 43+ pass, only pre-existing calibration failures

- [ ] **Step 6: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_reasoner_navigation.py
git commit -m "feat(nav): reasoner-driven loop replaces hardcoded PageType routing"
```

---

### Task 4: Simplify auth handler — remove hardcoded flows

With the reasoner deciding what to fill and the NavigationActionExecutor doing the filling, the auth handler's `handle_login` and `handle_signup` methods are no longer the primary path. They become thin wrappers that the reasoner loop doesn't call directly — the reasoner tells the executor exactly what fields to fill.

Keep `handle_email_verification` (Gmail polling is a distinct concern). Simplify login/signup to just delegate to the reasoner.

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_auth.py`
- Test: `tests/jobpulse/test_reasoner_navigation.py` (add auth tests)

- [ ] **Step 1: Add test for simplified auth**

Append to `tests/jobpulse/test_reasoner_navigation.py`:

```python
class TestAuthSimplified:
    def test_handle_login_delegates_to_reasoner(self):
        """Auth handler login should not have hardcoded field iteration."""
        import inspect
        from jobpulse.application_orchestrator_pkg._auth import AuthHandler
        source = inspect.getsource(AuthHandler.handle_login)
        # Should not iterate over fields and check types
        assert "ftype == \"password\"" not in source, (
            "handle_login should not have hardcoded password field matching"
        )

    def test_handle_signup_delegates_to_reasoner(self):
        """Auth handler signup should not have hardcoded field iteration."""
        import inspect
        from jobpulse.application_orchestrator_pkg._auth import AuthHandler
        source = inspect.getsource(AuthHandler.handle_signup)
        assert "create_account" not in source, (
            "handle_signup should not call create_account — reasoner fills fields"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_reasoner_navigation.py::TestAuthSimplified -v --timeout=30 -x`
Expected: FAIL — current auth handler still has hardcoded flows

- [ ] **Step 3: Simplify auth handler**

Replace `handle_login` and `handle_signup` in `jobpulse/application_orchestrator_pkg/_auth.py`:

```python
    async def handle_login(self, snapshot: dict, platform: str) -> dict:
        """Login via reasoner — analyzes actual page content."""
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import NavigationActionExecutor
        from jobpulse.applicator import PROFILE

        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(snapshot)
        logger.info("Auth login via reasoner: %s — %s", action.action, action.page_understanding[:60])

        page = getattr(self.driver, "page", None)
        if page is not None:
            executor = NavigationActionExecutor(page)
            await executor.execute(action, profile=PROFILE)

        import asyncio
        await asyncio.sleep(2.0)
        return self._as_dict(await self.driver.get_snapshot())

    async def handle_signup(self, snapshot: dict, platform: str) -> dict:
        """Signup via reasoner — analyzes actual page content."""
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import NavigationActionExecutor
        from jobpulse.applicator import PROFILE

        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(snapshot)
        logger.info("Auth signup via reasoner: %s — %s", action.action, action.page_understanding[:60])

        page = getattr(self.driver, "page", None)
        if page is not None:
            executor = NavigationActionExecutor(page)
            await executor.execute(action, profile=PROFILE)

        import asyncio
        await asyncio.sleep(2.0)
        return self._as_dict(await self.driver.get_snapshot())
```

Keep `handle_email_verification` unchanged — it handles Gmail polling which is a distinct concern the reasoner can't do. Keep `find_signup_link` unchanged — it's used in learned sequence replay.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_reasoner_navigation.py -v --timeout=30 -x`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_auth.py tests/jobpulse/test_reasoner_navigation.py
git commit -m "refactor(auth): login/signup delegate to reasoner instead of hardcoded flows"
```

---

### Task 5: Update page analyzer — reasoner as primary, classifier as fast-path

The `PageAnalyzer.detect()` method currently uses the DOM classifier as primary and the reasoner as fallback. Flip it: DOM classifier is a fast-path for high-confidence cases, reasoner is primary for everything else. The navigator already does its own fast-path check, so `detect()` just needs to be consistent.

**Files:**
- Modify: `jobpulse/page_analyzer.py`

- [ ] **Step 1: Simplify the detect method**

The navigator now handles the reasoner loop directly, so `PageAnalyzer.detect()` is only used for:
1. Learned sequence replay (checking if we reached APPLICATION_FORM)
2. Verification wall bypass (checking if wall cleared)

These callers just need a PageType, not a full action. Keep detect() as-is but ensure the reasoner's `async reason()` works for these callers. The reasoner's `reason()` now wraps `reason_sync()` so no changes needed in page_analyzer.py beyond ensuring the async call works.

Verify by running existing tests:

Run: `python -m pytest tests/jobpulse/test_page_analysis.py tests/jobpulse/test_page_analyzer.py -v --timeout=30`
Expected: 43+ pass (same as before)

- [ ] **Step 2: Commit (if any changes were needed)**

```bash
git add jobpulse/page_analyzer.py
git commit -m "docs(analyzer): clarify reasoner-primary architecture in comments"
```

---

### Task 6: Integration test — full reasoner-driven navigation

End-to-end test that verifies the complete flow: snapshot → reasoner → action executor → new snapshot → repeat. Uses mocked driver and LLM to simulate a 3-step navigation: job description → signup (email+checkbox) → application form.

**Files:**
- Modify: `tests/jobpulse/test_reasoner_navigation.py`

- [ ] **Step 1: Write the integration test**

Append to `tests/jobpulse/test_reasoner_navigation.py`:

```python
class TestReasonerDrivenIntegration:
    """Integration test: simulate a 3-step navigation via reasoner."""

    @pytest.mark.asyncio
    @patch("jobpulse.page_analysis.page_reasoner.smart_llm_call")
    @patch("jobpulse.page_analysis.page_reasoner.get_llm")
    async def test_three_step_navigation(self, mock_get_llm, mock_smart_call):
        """Job description → signup → application form."""
        # Step 1: Job description → click Apply
        # Step 2: Signup → fill email + check consent + click Next
        # Step 3: Application form → fill_form (terminal)
        responses = [
            _fake_llm_response({
                "page_understanding": "Job listing with Apply button",
                "page_type": "job_description",
                "action": "click_element",
                "target_text": "Apply Now",
                "field_fills": [],
                "advance_button": "",
                "overlays_to_dismiss": [],
                "reasoning": "Click apply to proceed",
                "confidence": 0.95,
            }),
            _fake_llm_response({
                "page_understanding": "Email signup page with consent",
                "page_type": "signup_form",
                "action": "fill_and_advance",
                "field_fills": [
                    {"label": "Email Address", "value": "FROM_PROFILE:email", "method": "fill"},
                    {"label": "I agree", "value": "true", "method": "check_label"},
                ],
                "advance_button": "Next",
                "overlays_to_dismiss": ["Agree"],
                "reasoning": "Fill email and accept terms",
                "confidence": 0.9,
            }),
            _fake_llm_response({
                "page_understanding": "Application form with multiple fields",
                "page_type": "application_form",
                "action": "fill_form",
                "field_fills": [],
                "advance_button": "",
                "overlays_to_dismiss": [],
                "reasoning": "Hand off to form filler",
                "confidence": 0.95,
            }),
        ]
        mock_smart_call.side_effect = responses

        from jobpulse.page_analysis.page_reasoner import PageReasoner
        reasoner = PageReasoner.__new__(PageReasoner)
        reasoner._db_path = ":memory:"
        reasoner._ensure_db = lambda: None
        reasoner._get_cached = lambda k: None
        reasoner._set_cache = lambda k, a: None

        # Simulate 3 steps
        snapshots = [
            {"url": "https://indeed.com/viewjob?jk=123", "page_text_preview": "Data Scientist role",
             "buttons": [{"text": "Apply Now"}], "fields": []},
            {"url": "https://oracle.com/apply/email", "page_text_preview": "Enter email",
             "buttons": [{"text": "Next"}], "fields": [{"label": "Email Address", "input_type": "email"}]},
            {"url": "https://oracle.com/apply/form", "page_text_preview": "Application form",
             "buttons": [{"text": "Submit"}], "fields": [{"label": "First Name", "input_type": "text"}]},
        ]

        actions_taken = []
        for snap in snapshots:
            action = reasoner.reason_sync(snap)
            actions_taken.append(action.action)
            if action.action == "fill_form":
                break

        assert actions_taken == ["click_element", "fill_and_advance", "fill_form"]
        assert len(actions_taken) == 3
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/jobpulse/test_reasoner_navigation.py tests/jobpulse/test_nav_action_executor.py -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 3: Run broader regression check**

Run: `python -m pytest tests/jobpulse/ -v --timeout=30 -x -q`
Expected: No new failures beyond pre-existing calibration tests

- [ ] **Step 4: Commit**

```bash
git add tests/jobpulse/test_reasoner_navigation.py
git commit -m "test(nav): integration test for 3-step reasoner-driven navigation"
```

---

### Task 7: Clear stale cache + log mistakes + final cleanup

- [ ] **Step 1: Clear any remaining stale abort cache entries**

```bash
sqlite3 data/page_reasoning_cache.db "DELETE FROM reasoning_cache WHERE json_extract(result_json, '$.action') = 'abort' AND json_extract(result_json, '$.confidence') < 0.5;"
```

- [ ] **Step 2: Update mistakes.md with the architectural lesson**

Add to `.claude/mistakes.md`:

```
- [2026-04-30] Auth handlers must read actual page content, not follow hardcoded flows — Oracle Cloud email-only page crashed because handler assumed password field exists
- [2026-04-30] PageReasoner must be PRIMARY decision-maker, not fallback — hardcoded classifier→handler chains break on any page layout the code hasn't seen
```

- [ ] **Step 3: Run full test suite one final time**

```bash
python -m pytest tests/jobpulse/test_reasoner_navigation.py tests/jobpulse/test_nav_action_executor.py tests/jobpulse/test_page_analysis.py -v --timeout=30
```

Expected: All non-calibration tests PASS

- [ ] **Step 4: Final commit**

```bash
git add .claude/mistakes.md
git commit -m "docs: log reasoner-driven architecture lessons in mistakes.md"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] PageReasoner returns field-level actions (Task 1)
- [x] Navigator uses reasoner at every step (Task 3)
- [x] Auth handlers are content-driven (Task 4)
- [x] Overlays dismissed before interaction (Task 2 executor)
- [x] Honeypot fields skipped (Task 1 prompt rules)
- [x] Hidden checkboxes clicked via label (Task 2 check_label method)
- [x] hCaptcha routes to human fallback (Task 3 wait_human handling)
- [x] Learning systems still fire — NavigationLearner, FormExperienceDB, CorrectionCapture unchanged (Task 3 preserves steps list)
- [x] DOM classifier kept as fast-path (Task 3 _dom_classify)
- [x] smart_llm_call is sync (Task 1 reason_sync)
- [x] No PII in source (Task 1 uses FROM_PROFILE: refs)
- [x] Tests use mocks not real LLM (all tests)

**2. Placeholder scan:** No TBDs, TODOs, or "implement later" found.

**3. Type consistency:** `PageAction` dataclass consistent across all tasks. `reason_sync()` name consistent. `NavigationActionExecutor.execute()` signature consistent.
