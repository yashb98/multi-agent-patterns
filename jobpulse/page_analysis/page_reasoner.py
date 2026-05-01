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


def _lazy_import_agents():
    from shared.agents import get_llm as _get_llm, smart_llm_call as _smart_llm_call
    return _get_llm, _smart_llm_call


def get_llm(*args, **kwargs):  # noqa: ANN
    """Lazy proxy — enables patch('jobpulse.page_analysis.page_reasoner.get_llm')."""
    _fn, _ = _lazy_import_agents()
    return _fn(*args, **kwargs)


def smart_llm_call(*args, **kwargs):  # noqa: ANN
    """Lazy proxy — enables patch('jobpulse.page_analysis.page_reasoner.smart_llm_call')."""
    _, _fn = _lazy_import_agents()
    return _fn(*args, **kwargs)

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

VALID_OUTCOMES = frozenset({
    "url_changes",        # we expect the URL to change after this action
    "fields_filled",      # we expect specific fields to become non-empty
    "dialog_dismissed",   # we expect a dialog/overlay to disappear
    "page_unchanged",     # we expect to stay on this page (e.g. consent acknowledgement only)
    "unknown",            # default — no specific expectation
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
    expected_outcome: str = "unknown"

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
            "expected_outcome": self.expected_outcome,
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
            existing = {r[1] for r in conn.execute("PRAGMA table_info(reasoning_cache)").fetchall()}
            if "page_understanding_text" not in existing:
                conn.execute("ALTER TABLE reasoning_cache ADD COLUMN page_understanding_text TEXT DEFAULT ''")

    def _cache_key(
        self, url: str, page_text: str, dialog_text: str,
        fields: list[dict] | None = None, buttons: list[dict] | None = None,
    ) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(url) if url else None
        domain = parsed.netloc.lower().removeprefix("www.") if parsed else ""
        path = parsed.path.rstrip("/") if parsed else ""
        field_sig = ""
        if fields:
            labels = sorted(f.get("label", "")[:30] for f in fields[:15] if f.get("label"))
            field_sig = f"|fields={len(fields)}:{','.join(labels)}"
        button_sig = ""
        if buttons:
            btn_texts = sorted(b.get("text", "")[:20] for b in buttons[:10] if b.get("text"))
            button_sig = f"|buttons={','.join(btn_texts)}"
        content_hash = hashlib.sha256(
            (path + "|" + page_text[:500] + "|" + dialog_text[:300]
             + field_sig + button_sig).encode()
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

    def _get_cached_semantic(self, domain: str, page_text: str) -> PageAction | None:
        """Semantic near-miss: find cached entries with similar page understanding."""
        try:
            from shared.semantic_utils import best_semantic_match
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT cache_key, result_json, created_at, page_understanding_text "
                    "FROM reasoning_cache WHERE cache_key LIKE ? AND page_understanding_text != ''",
                    (f"{domain}:%",),
                ).fetchall()
            if not rows:
                return None
            valid = [(r[0], r[1], r[2], r[3]) for r in rows if (time.time() - r[2]) < 3600]
            if not valid:
                return None
            understandings = [r[3] for r in valid]
            match, score = best_semantic_match(page_text[:200], understandings, min_score=0.90)
            if match is not None:
                idx = understandings.index(match)
                data = json.loads(valid[idx][1])
                logger.info("PageReasoner: semantic near-miss hit (score=%.3f)", score)
                return PageAction(**data)
        except Exception as exc:
            logger.debug("Semantic cache lookup failed: %s", exc)
        return None

    def _set_cache(self, key: str, action: PageAction) -> None:
        if action.action == "abort" and action.confidence < 0.5:
            return
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO reasoning_cache "
                    "(cache_key, result_json, created_at, page_understanding_text) VALUES (?, ?, ?, ?)",
                    (key, json.dumps(action.to_dict()), time.time(), action.page_understanding),
                )
        except Exception:
            pass

    @staticmethod
    def _apply_field_count_guard(
        action: "PageAction", snapshot_fields: list[dict],
    ) -> "PageAction":
        """If the LLM dropped required fields, lower confidence and annotate.

        Only applies when action is fill-related. Honeypots and skip-marked
        fills do not count toward coverage.
        """
        if action.action not in ("fill_and_advance", "fill_form", "login", "signup"):
            return action

        required = [
            f for f in snapshot_fields
            if f.get("required") and f.get("label")
            and "honeypot" not in (f.get("label") or "").lower()
        ]
        if not required:
            return action

        filled_labels = {
            (f.get("label") or "").strip().lower()
            for f in action.field_fills
            if f.get("method") != "skip"
        }
        required_labels = {(f.get("label") or "").strip().lower() for f in required}
        covered = required_labels & filled_labels
        coverage = len(covered) / len(required_labels) if required_labels else 1.0

        if coverage < 0.8:
            new_confidence = min(action.confidence, coverage)
            return PageAction(
                page_understanding=action.page_understanding,
                action=action.action,
                target_text=action.target_text,
                reasoning=(
                    f"{action.reasoning} | field_coverage={coverage:.0%} "
                    f"({len(covered)}/{len(required_labels)} required fields)"
                ),
                confidence=new_confidence,
                page_type=action.page_type,
                field_fills=action.field_fills,
                advance_button=action.advance_button,
                overlays_to_dismiss=action.overlays_to_dismiss,
                expected_outcome=action.expected_outcome,
            )
        return action

    def reason_sync(self, snapshot: dict[str, Any]) -> PageAction:
        """Synchronous page reasoning — primary entry point."""
        url = snapshot.get("url", "")
        page_text = snapshot.get("page_text_preview", "")[:800]
        dialog_text = snapshot.get("dialog_text", "")[:500]
        buttons = snapshot.get("buttons", [])
        fields = snapshot.get("fields", [])
        wall = snapshot.get("verification_wall")

        cache_key = self._cache_key(url, page_text, dialog_text, fields, buttons)
        cached = self._get_cached(cache_key)
        if cached:
            logger.info("PageReasoner: cache hit for %s → %s", cache_key[:30], cached.action)
            return cached

        # Semantic near-miss lookup
        from urllib.parse import urlparse
        parsed = urlparse(url) if url else None
        domain = parsed.netloc.lower().removeprefix("www.") if parsed else ""
        semantic_hit = self._get_cached_semantic(domain, page_text)
        if semantic_hit:
            return semantic_hit

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

        action = self._apply_field_count_guard(action, fields)
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
            from langchain_core.messages import SystemMessage, HumanMessage
            msgs = [
                SystemMessage(content=self._system_prompt()),
                HumanMessage(content=prompt),
            ]
            llm = get_llm(temperature=0, max_tokens=500, agent_name="page_reasoner")
            try:
                response = smart_llm_call(llm, msgs)
            except Exception as local_err:
                from shared.agents import is_local_llm
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
            'email_verification|confirmation|verification_wall|consent_gate|session_expired|expired_job|unknown",\n'
            '  "action": "fill_and_advance|click_element|dismiss_overlay|wait_human|fill_form|done|abort",\n'
            '  "target_text": "button/link text to click (if action is click_element)",\n'
            '  "field_fills": [\n'
            '    {"label": "field label", "value": "what to put", "method": "fill|check_label|check_input|select|skip"}\n'
            "  ],\n"
            '  "advance_button": "text of Next/Submit/Continue button to click after filling",\n'
            '  "overlays_to_dismiss": ["button text to click to dismiss cookie/session overlays"],\n'
            '  "reasoning": "why this action",\n'
            '  "confidence": 0.0-1.0,\n'
            '  "expected_outcome": "url_changes|fields_filled|dialog_dismissed|page_unchanged|unknown"\n'
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
            "- If the page says the job is no longer available, expired, closed, removed, or filled, "
            "page_type = \"expired_job\" and action = \"abort\"\n"
            "- If application was submitted successfully, action = \"done\"\n"
            "- action \"fill_and_advance\" = fill the listed fields + click advance_button\n"
            "- expected_outcome MUST be one of: url_changes, fields_filled, dialog_dismissed, page_unchanged, unknown\n"
            "- Pick url_changes for navigation/login/submit actions\n"
            "- Pick dialog_dismissed for overlay/consent dismissals\n"
            "- Pick fields_filled for fill_form when no advance is expected on this page\n"
            "- Pick page_unchanged ONLY when no visible state change is expected\n"
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
            outcome = data.get("expected_outcome", "unknown")
            if outcome not in VALID_OUTCOMES:
                outcome = "unknown"
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
                expected_outcome=outcome,
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
