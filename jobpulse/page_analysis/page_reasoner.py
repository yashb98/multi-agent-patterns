"""Semantic page reasoner — LLM-based understanding for every navigation step.

PRIMARY decision-maker for the navigation loop. Takes a page snapshot,
reasons about what to do, and returns structured actions with specific
field fills, overlay dismissals, and advance buttons.

Costs ~$0.001 per call. Cached per domain+content_hash (1hr TTL).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from shared.db_observability import observe_lookup
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


def get_optimization_engine():  # noqa: ANN
    """Lazy proxy for OptimizationEngine — enables `patch(
    'jobpulse.page_analysis.page_reasoner.get_optimization_engine')`
    in S2 unit tests. Slice S2 / TP-3."""
    from shared.optimization import get_optimization_engine as _fn
    return _fn()

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

    @observe_lookup("page_reasoning_cache", "reasoning_cache", key_arg=1)
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

    @observe_lookup("page_reasoning_cache", "reasoning_cache.semantic", key_arg=1)
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
        # Skip caching only for low-confidence aborts (confidence < 0.5),
        # which usually mean the LLM was uncertain and we should re-ask
        # next time. Notably this means an `abort` with confidence ≥ 0.5
        # IS cached and will be returned on every subsequent visit to
        # the same page+content_hash for the cache TTL (1h). Callers
        # that need a fresh decision after a known-bad cached abort
        # must call `invalidate(snapshot)` first.
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

    def invalidate(self, snapshot: dict[str, Any]) -> int:
        """Delete the cached PageAction for this snapshot. Returns rows removed.

        Called by FormNavigator when verification fails so the next visit
        re-runs the LLM rather than reusing a wrong cached plan.
        """
        url = snapshot.get("url", "")
        page_text = snapshot.get("page_text_preview", "")[:800]
        dialog_text = snapshot.get("dialog_text", "")[:500]
        fields = snapshot.get("fields", []) or []
        buttons = snapshot.get("buttons", []) or []
        cache_key = self._cache_key(url, page_text, dialog_text, fields, buttons)
        try:
            with sqlite3.connect(self._db_path) as conn:
                cur = conn.execute(
                    "DELETE FROM reasoning_cache WHERE cache_key = ?", (cache_key,),
                )
                return cur.rowcount
        except Exception as exc:
            logger.debug("PageReasoner.invalidate failed: %s", exc)
            return 0

    @staticmethod
    def _apply_zero_fields_guard(
        action: "PageAction",
        snapshot_fields: list[dict],
        snapshot_buttons: list[dict],
    ) -> "PageAction":
        """If LLM says fill_form but page has zero fields, override.

        The LLM sometimes hallucinates 'application_form' on pages that are
        actually job listings or pre-application landing pages (e.g. Workday
        job descriptions). The page text mentions the role and has an Apply
        button, but no actual form inputs. Trusting the LLM here causes the
        form filler to spin in a hydration loop.

        Override logic:
        - If a button/link contains "apply" → click_element with that target
        - If a button/link contains "sign in"/"login" → click_element with that target
        - Otherwise → unknown with low confidence (forces re-classification)
        """
        if action.action not in ("fill_and_advance", "fill_form"):
            return action
        # Count fillable fields (not honeypots, not display-only)
        fillable = [
            f for f in snapshot_fields
            if f.get("label") and "honeypot" not in (f.get("label") or "").lower()
        ]
        if fillable:
            return action

        button_texts = [
            (b.get("text") or "").strip()
            for b in snapshot_buttons
            if b.get("text")
        ]
        apply_btn = next(
            (t for t in button_texts if "apply" in t.lower()), "",
        )
        login_btn = next(
            (t for t in button_texts
             if any(kw in t.lower() for kw in ("sign in", "log in", "login"))), "",
        )
        target = apply_btn or login_btn

        if target:
            return PageAction(
                page_understanding=action.page_understanding,
                action="click_element",
                target_text=target,
                reasoning=(
                    f"Override: LLM said {action.action} but page has 0 fillable "
                    f"fields. Falling back to click '{target}' to navigate to "
                    "the actual form."
                ),
                confidence=0.7,
                page_type="job_description",
                field_fills=[],
                advance_button="",
                overlays_to_dismiss=action.overlays_to_dismiss,
                expected_outcome="url_changes",
            )
        return PageAction(
            page_understanding=action.page_understanding,
            action="abort",
            target_text="",
            reasoning=(
                f"Override: LLM said {action.action} but page has 0 fillable "
                "fields and no Apply/Sign In button found. Cannot proceed."
            ),
            confidence=0.2,
            page_type="unknown",
            field_fills=[],
            advance_button="",
            overlays_to_dismiss=action.overlays_to_dismiss,
            expected_outcome="unknown",
        )

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

        logger.info(
            "THRESHOLD_OBS: field_count_guard threshold=0.8 coverage=%.2f covered=%d/%d action=%s decision=%s",
            coverage, len(covered), len(required_labels), action.action,
            "lowered_confidence" if coverage < 0.8 else "passed",
        )
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

    @staticmethod
    def _apply_advance_button_guard(action: "PageAction") -> "PageAction":
        """Plan D: when action is fill_and_advance but advance_button is
        empty, the consumer (NativeFormFiller._click_navigation) has
        nothing to click. Lower confidence so the orchestrator either
        re-plans or routes through human bypass — instead of silently
        failing or falling back to a hardcoded button-text list.
        """
        if action.action != "fill_and_advance":
            return action
        if (action.advance_button or "").strip():
            return action
        logger.info(
            "advance_button_guard: fill_and_advance with empty advance_button "
            "→ confidence downgraded to 0.0 (forces re-plan)"
        )
        return PageAction(
            page_understanding=action.page_understanding,
            action=action.action,
            target_text=action.target_text,
            reasoning=(
                f"{action.reasoning} | advance_button_missing — consumer "
                f"has no button name to click"
            ),
            confidence=0.0,
            page_type=action.page_type,
            field_fills=action.field_fills,
            advance_button="",
            overlays_to_dismiss=action.overlays_to_dismiss,
            expected_outcome=action.expected_outcome,
        )

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

        # Option 2: if the LLM produced unparseable JSON twice AND the page
        # snapshot clearly shows form fields, default to fill_form with low
        # confidence. The downstream confidence-gate cross-check will run
        # vision classification to verify, so we don't blindly trust the
        # heuristic — but we also don't abort a page that obviously needs
        # filling. Verified live (Kimi malformed-JSON regression on Anthropic
        # Greenhouse, 2026-05-10): the page has 50 form fields, but the
        # reasoner aborts on parse failure → navigator gives up before fill.
        fillable = [f for f in fields if f.get("label") and "honeypot" not in (f.get("label") or "").lower()]
        if self._is_parse_failure(action) and len(fillable) >= 3:
            logger.warning(
                "PageReasoner: parse failed after retry, but %d fillable fields detected — "
                "defaulting to fill_form (confidence=0.3)",
                len(fillable),
            )
            action = PageAction(
                page_understanding=(
                    f"LLM JSON parse failed twice; defaulting to fill_form "
                    f"because the page has {len(fillable)} fillable fields"
                ),
                action="fill_form",
                target_text="",
                reasoning=action.reasoning,
                confidence=0.3,
                page_type="application_form",
                expected_outcome="fields_filled",
            )

        action = self._apply_zero_fields_guard(action, fields, buttons)
        action = self._apply_field_count_guard(action, fields)
        action = self._apply_advance_button_guard(action)
        self._set_cache(cache_key, action)
        logger.info(
            "PageReasoner: %s → action=%s, type=%s, confidence=%.2f — %s",
            url[:60], action.action, action.page_type, action.confidence,
            action.page_understanding[:80],
        )
        return action

    def reason_with_failure(
        self, snapshot: dict[str, Any], failure_context: str,
    ) -> PageAction:
        """Re-call the LLM with a failure context appended — does NOT use cache.

        Called by FormNavigator when a previously-cached action led to a
        ghost click, expected_outcome violation, or persistent fill failure.
        Returns a fresh PageAction the caller can route on.
        """
        url = snapshot.get("url", "")
        page_text = snapshot.get("page_text_preview", "")[:800]
        dialog_text = snapshot.get("dialog_text", "")[:500]
        buttons = snapshot.get("buttons", [])
        fields = snapshot.get("fields", [])
        wall = snapshot.get("verification_wall")

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

        base_prompt = self._build_prompt(
            url, page_text, dialog_text, button_summary, field_summary, wall_info,
        )
        # Pull the failed action out of the failure_context so we can forbid it
        # explicitly. Caller emits "action=X" inside the pipe-delimited context;
        # if absent we fall back to a generic "different action" instruction.
        prior_action = ""
        for part in failure_context.split("|"):
            part = part.strip()
            if part.startswith("action="):
                prior_action = part[len("action="):].strip()
                break

        forbidden_clause = (
            f"DO NOT return action='{prior_action}' again — that exact action "
            f"was just tried on this page and did not produce the expected "
            f"outcome. Pick a different action.\n\n"
            if prior_action
            else "DO NOT return the same action that just failed — pick a different one.\n\n"
        )
        prompt = (
            base_prompt
            + "\n\nPRIOR ATTEMPT FAILED:\n"
            + failure_context
            + "\n\n"
            + forbidden_clause
            + "Choose a DIFFERENT recovery strategy. Concrete options:\n"
            + "  - 'wait_human' if the page is blocked by auth, CAPTCHA, "
              "session expiry, MFA, or anything that needs the user\n"
            + "  - 'go_back' if the navigation landed on the wrong page\n"
            + "  - 'dismiss_overlay' if a modal/banner is intercepting clicks "
              "or stealing focus\n"
            + "  - 'click_element' with a DIFFERENT target_text if the previous "
              "click hit the wrong element (e.g. promotional or hidden)\n"
            + "  - 'abort' if there is no way forward (job closed, account "
              "locked, jurisdiction blocked, page is a 404)\n\n"
            + "Also reconsider the page_type: a login_form that won't accept "
              "credentials may actually be a session_expired page, an SSO-only "
              "page, or an account-creation page that requires email "
              "verification first."
        )
        action = self._call_llm(prompt)
        action = self._apply_zero_fields_guard(action, fields, buttons)
        action = self._apply_advance_button_guard(action)
        # Do not cache reflection results — they are situational.
        logger.info(
            "PageReasoner.reflect: %s → action=%s, type=%s, confidence=%.2f",
            url[:60], action.action, action.page_type, action.confidence,
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
            # Audit 2026-05-10 / Slice S2 / TP-3 — bind response_format so
            # Moonshot returns parseable JSON without prose/markdown wrappers.
            # Per orchestration-agents.md: "Use response_format=
            # {'type':'json_object'} when expecting JSON from OpenAI" and
            # "Never rely on markdown stripping to extract JSON". Live
            # verified twice on Graphcore: first parse failed every time,
            # safety net (Fix D) was load-bearing.
            # Live evidence 2026-05-12: Moonshot returned LengthFinishReasonError
            # with `completion_tokens=500` because Kimi's reasoning trace +
            # JSON response together exceed the 500-token budget on long ATS
            # pages (Graphcore had 53 fields). Raising to 4046 covers the
            # reasoning-trace overhead while staying well under Kimi's 8k
            # cap. The system prompt also instructs the model to be terse
            # so the JSON itself stays small — semantic analysis only needs
            # the action + targets, not prose.
            base_llm = get_llm(temperature=0, max_tokens=4046, agent_name="page_reasoner")
            llm = base_llm.bind(response_format={"type": "json_object"})
            try:
                response = smart_llm_call(llm, msgs)
            except Exception as local_err:
                from shared.agents import is_local_llm
                if is_local_llm():
                    logger.warning("PageReasoner local LLM failed, falling back to cloud: %s", local_err)
                    cloud_llm = get_llm(
                        model="gpt-4o-mini",
                        temperature=0,
                        max_tokens=4046,
                        timeout=30,
                        agent_name="page_reasoner",
                        force_cloud=True,
                    ).bind(response_format={"type": "json_object"})
                    response = smart_llm_call(cloud_llm, msgs)
                else:
                    raise
            text = response.content if hasattr(response, "content") else str(response)
            action = self._parse_response(text)

            # Option 1: on parse failure, retry once with a stricter "JSON only"
            # instruction. Verified live (run3/5/6 2026-05-10): Kimi occasionally
            # emits prose+JSON or structurally broken JSON; a second strict pass
            # usually returns clean output.
            if self._is_parse_failure(action):
                logger.info("PageReasoner: first parse failed, retrying with strict-JSON prompt")
                # S2 — emit a `failure` signal so cleanup-retry engagement-rate
                # is observable in data/optimization.db. Wrapped in try/except
                # because observability MUST NOT block the apply pipeline.
                try:
                    engine = get_optimization_engine()
                    engine.emit(
                        "failure",
                        source_loop="page_reasoner",
                        domain="page_reasoner",
                        agent_name="page_reasoner",
                        severity="warning",
                        payload={
                            "reason": "parse_failure_strict_retry",
                            "raw_snippet": (text or "")[:200],
                            "error": (action.reasoning or "")[:200],
                        },
                    )
                except Exception as sig_exc:  # noqa: BLE001
                    logger.debug("PageReasoner: failure signal emit failed: %s", sig_exc)

                strict_msgs = [
                    SystemMessage(content=self._system_prompt()),
                    HumanMessage(content=prompt),
                    HumanMessage(content=(
                        "Your last response could not be parsed. "
                        "Return ONLY a valid JSON object — no prose, no markdown fences, "
                        "no comments, no trailing commas. Use double quotes for strings. "
                        "Escape control characters (use \\\\n not raw newline inside strings)."
                    )),
                ]
                try:
                    retry_response = smart_llm_call(llm, strict_msgs)
                    retry_text = retry_response.content if hasattr(retry_response, "content") else str(retry_response)
                    action = self._parse_response(retry_text)
                except Exception as retry_exc:
                    logger.warning("PageReasoner retry failed: %s", retry_exc)
            return action
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
    def _is_parse_failure(action: "PageAction") -> bool:
        """True when ``action`` came from the parse-failure branch in
        ``_parse_response`` (confidence 0, abort, page_understanding starts
        with the failure prefix). Exposed so callers can apply targeted
        fallbacks instead of treating all aborts the same.
        """
        return (
            action.action == "abort"
            and action.confidence == 0.0
            and (action.page_understanding or "").startswith("Failed to parse LLM response")
        )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a page analyzer for a job application bot. "
            "You see a web page's content, fields, buttons, and any overlays/CAPTCHAs.\n\n"
            "Your job: decide EXACTLY what to do on this page — which fields to fill, "
            "which checkboxes to check, which overlays to dismiss, and which button to click to advance.\n\n"
            "BE CONCISE: keep every string value short (≤80 chars). "
            "page_understanding + reasoning must each be ONE short sentence. "
            "Do not pad. Semantic analysis only needs action + targets; "
            "verbose prose wastes tokens and risks truncation.\n\n"
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
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Common Kimi/Moonshot reasoning model JSON glitches:
                # trailing commas, // comments, /* */ block comments, and
                # raw control chars (\x00-\x1f) leaking from the model's
                # internal token stream into string values. Apply
                # progressively more aggressive cleanup, then parse with
                # strict=False which permits raw control chars inside
                # strings — Kimi's `reasoning` field often contains raw
                # tabs/newlines that strict mode rejects.
                cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
                cleaned = re.sub(r"//[^\n]*", "", cleaned)
                cleaned = re.sub(r"/\*[\s\S]*?\*/", "", cleaned)
                data = json.loads(cleaned, strict=False)
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
