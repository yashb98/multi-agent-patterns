"""Navigation — traverse redirect chains to reach the application form.

Handles: learned sequence replay, cookie dismissal, apply button detection,
LinkedIn direct-apply shortcut, and page-type-based routing.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from enum import Enum
from typing import Any

from dataclasses import dataclass, field as dc_field

from shared.logging_config import get_logger

from jobpulse.form_models import PageType
from jobpulse.cookie_dismisser import dismiss_cookie_banner_playwright
from jobpulse.navigation.action_executor import NavigationActionExecutor, ExecutorResult
from jobpulse.navigation.overlay_dismisser import OverlayDismisser
from jobpulse.navigation.wait_conditions import wait_for_modal_open, wait_for_page_stable
from jobpulse.page_analysis.page_reasoner import PageAction, get_page_reasoner
from jobpulse.page_analysis.classifier import PageTypeClassifier

logger = get_logger(__name__)


class TabState(Enum):
    NORMAL = "normal"
    NEW_TAB = "new_tab"
    POPUP = "popup"
    CLOSED = "closed"
    REDIRECTED = "redirected"


@dataclass
class PageFingerprint:
    field_count: int
    button_texts: tuple[str, ...]
    content_hash: str
    has_dialog: bool
    has_file_inputs: bool
    page_type: str
    dom_confidence: float
    url_path_pattern: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_count": self.field_count,
            "button_texts": list(self.button_texts),
            "content_hash": self.content_hash,
            "has_dialog": self.has_dialog,
            "has_file_inputs": self.has_file_inputs,
            "page_type": self.page_type,
            "dom_confidence": self.dom_confidence,
            "url_path_pattern": self.url_path_pattern,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PageFingerprint":
        return cls(
            field_count=d.get("field_count", 0),
            button_texts=tuple(d.get("button_texts", ())),
            content_hash=d.get("content_hash", ""),
            has_dialog=d.get("has_dialog", False),
            has_file_inputs=d.get("has_file_inputs", False),
            page_type=d.get("page_type", "unknown"),
            dom_confidence=d.get("dom_confidence", 0.0),
            url_path_pattern=d.get("url_path_pattern", ""),
        )


@dataclass
class StepContext:
    snapshot: dict[str, Any]
    url: str
    tab_state: TabState

    tab_recovered: bool = False

    dom_type: PageType = dc_field(default=PageType.UNKNOWN)
    dom_confidence: float = 0.0
    browser_signals: list[dict] | None = None
    wall_detected: dict | None = None
    page_fingerprint: PageFingerprint | None = None

    learned_step: dict | None = None
    match_score: float = 0.0
    match_source: str = ""

    planned_action: PageAction | None = None
    plan_source: str = ""

    action_executed: bool = False
    post_snapshot: dict | None = None
    ghost_click: bool = False
    executor_result: ExecutorResult | None = None
    reflected_action: Any = None
    vision_disagreement: Any = None


@dataclass
class ActionVerification:
    pre_url: str
    pre_hash: str
    pre_dialog: bool
    post_url: str
    post_hash: str
    post_dialog: bool
    ghost_click: bool = False
    expected_outcome_met: bool | None = None  # populated in Task 8

    # url_changed and content_changed are consumed by _check_expected_outcome
    # in Task 8 (mapping PageAction.expected_outcome to verification predicates).
    @property
    def url_changed(self) -> bool:
        return self.pre_url != self.post_url

    @property
    def content_changed(self) -> bool:
        return self.url_changed or self.pre_hash != self.post_hash or self.pre_dialog != self.post_dialog


def _maybe_reflect_on_failure(
    verification: "ActionVerification",
    snapshot: dict[str, Any],
    trigger: str,
    context_extra: dict[str, Any] | None = None,
) -> Any:
    """Invoke PageReasoner.reason_with_failure and return the fresh PageAction.

    Centralizes reflection so all failure-mode triggers (ghost_click,
    expected_outcome_violation, vision_disagreement, persistent_fill_failure)
    use the same construction. Returns None on any failure — caller's existing
    plan continues unmodified.
    """
    try:
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner

        ctx_lines = [f"trigger={trigger}"]
        if context_extra:
            for k, v in context_extra.items():
                ctx_lines.append(f"{k}={str(v)[:100]}")
        ctx_lines.append(f"pre_url={verification.pre_url}")
        ctx_lines.append(f"post_url={verification.post_url}")
        ctx_lines.append(f"ghost_click={verification.ghost_click}")
        ctx_lines.append(f"expected_outcome_met={verification.expected_outcome_met}")

        failure_context = " | ".join(ctx_lines)
        reflected = get_page_reasoner().reason_with_failure(
            snapshot, failure_context=failure_context,
        )
        logger.info(
            "Reflection (trigger=%s) produced: %s (confidence=%.2f)",
            trigger, reflected.action, reflected.confidence,
        )
        return reflected
    except Exception as exc:
        logger.debug("Reflection failed for trigger=%s: %s", trigger, exc)
        return None


# Actions that hand control back to the orchestrator's NativeFormFiller
# pipeline (which scans + maps + fills + uploads CV/CL + clicks Continue).
#
# fill_and_advance was previously NON-terminal — the navigator handled it
# inline via NavigationActionExecutor, which only executes the reasoner's
# pre-planned `field_fills` (text-only). On pages where the reasoner's
# field plan is empty (e.g. Revolut welovealfa.com /apply/upload-cv has
# only a hidden file input + a Drop Zone, no text fields), the executor
# did nothing — the verifier saw no change → reflection looped to
# dismiss_overlay → wait_human → abort. CV never uploaded.
#
# Treating fill_and_advance as terminal hands control to NativeFormFiller
# which always calls upload_files() regardless of scanned-field count, so
# pages with only file inputs are now handled correctly.
TERMINAL_ACTIONS = frozenset({"fill_form", "fill_and_advance", "done", "abort"})

# Apply-button click actions — _phase_act and _verify_learned_action route
# all three to the same `click_apply_button` handler.
APPLY_CLICK_ACTIONS = frozenset({"click_apply", "click_apply_guess", "linkedin_direct_apply"})

# Actions that consume their own outcome (no separate vision-gate cross-
# check needed). Used by `_phase_act` to skip the screenshot-based vision
# disagreement probe on terminal/no-progress actions.
NO_VISION_GATE_ACTIONS = frozenset({"done", "abort", "wait_human"})

MAX_NAVIGATION_STEPS = 10

_NUMERIC_ID_RE = re.compile(r"/\d{3,}")


def _normalize_url_path(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") if parsed.path else ""
    return _NUMERIC_ID_RE.sub("/{id}", path)


def _compute_content_hash(url_path: str, page_text: str, field_labels: list[str], button_texts: list[str]) -> str:
    raw = "|".join([url_path, page_text[:500], ",".join(sorted(field_labels)), ",".join(sorted(button_texts))])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_page_fingerprint(snapshot: dict[str, Any], page_type: str, dom_confidence: float) -> PageFingerprint:
    url = snapshot.get("url", "")
    buttons = snapshot.get("buttons", [])
    fields = snapshot.get("fields", [])
    page_text = snapshot.get("page_text_preview", "")

    btn_texts = sorted({b.get("text", "")[:20] for b in buttons if b.get("text", "").strip()})
    field_labels = [f.get("label", "") for f in fields if f.get("label")]
    url_path = _normalize_url_path(url)

    return PageFingerprint(
        field_count=len(fields),
        button_texts=tuple(btn_texts),
        content_hash=_compute_content_hash(url_path, page_text, field_labels, btn_texts),
        has_dialog=bool(snapshot.get("has_dialog") or snapshot.get("modal_detected")),
        has_file_inputs=bool(snapshot.get("has_file_inputs")),
        page_type=page_type,
        dom_confidence=dom_confidence,
        url_path_pattern=url_path,
    )


def score_fingerprint_match(current: PageFingerprint, learned_fp: "dict[str, Any] | None") -> float:
    if not learned_fp:
        return 0.0

    score = 0.0

    if current.page_type == learned_fp.get("page_type"):
        score += 0.30
    if current.content_hash == learned_fp.get("content_hash"):
        score += 0.25

    learned_fc = learned_fp.get("field_count", 0)
    diff = abs(current.field_count - learned_fc)
    score += 0.15 * (1.0 - min(diff / 10.0, 1.0))

    learned_btns = set(learned_fp.get("button_texts", []))
    current_btns = set(current.button_texts)
    if learned_btns or current_btns:
        union = learned_btns | current_btns
        intersection = learned_btns & current_btns
        score += 0.15 * (len(intersection) / len(union))
    else:
        score += 0.15

    if current.url_path_pattern == learned_fp.get("url_path_pattern"):
        score += 0.15

    return round(score, 4)


@dataclass
class ApplyButtonPatterns:
    """Single source of truth for apply-button text patterns."""

    primary: tuple[str, ...] = (
        "easy apply", "apply now", "apply for this job", "start application",
        "apply on company website", "apply for this",
    )
    secondary: tuple[str, ...] = (
        "i'm interested", "submit interest", "begin application", "apply",
    )
    exclude: tuple[str, ...] = (
        "submit application", "submit my application", "save",
    )


def score_apply_button(text: str) -> float:
    """Score a button text for how likely it is an apply button.

    Returns 0.0-1.0. Higher = stronger apply signal.
    """
    lower = text.lower().strip()
    patterns = ApplyButtonPatterns()

    for pat in patterns.exclude:
        if pat in lower:
            return 0.0

    for pat in patterns.primary:
        if pat in lower:
            return 1.0

    for pat in patterns.secondary:
        if pat in lower:
            return 0.7

    if "apply" in lower:
        return 0.4

    return 0.0


class FormNavigator:
    """Navigates through redirect chains to reach the application form."""

    def __init__(self, orch, auth_handler):
        self._orch = orch
        self.auth = auth_handler
        self._classifier = PageTypeClassifier()

    @property
    def driver(self):
        return self._orch.driver

    @property
    def analyzer(self):
        return self._orch.analyzer

    @property
    def cookie_dismisser(self):
        return self._orch.cookie_dismisser

    @property
    def sso(self):
        return self._orch.sso

    @property
    def learner(self):
        return self._orch.learner

    @staticmethod
    def _as_dict(snapshot: Any) -> dict:
        if hasattr(snapshot, "model_dump"):
            return snapshot.model_dump()
        return snapshot

    @staticmethod
    def _detect_ghost_click(
        pre_url: str, pre_content_hash: str, pre_dialog: bool,
        post_url: str, post_content_hash: str, post_dialog: bool,
    ) -> bool:
        return (pre_url == post_url
                and pre_content_hash == post_content_hash
                and pre_dialog == post_dialog)

    async def _verify_action(
        self,
        pre_snapshot: dict[str, Any],
        post_snapshot: dict[str, Any],
        action_kind: str,
    ) -> ActionVerification:
        """Compute pre/post verification — shared between _phase_act and auth handlers.

        Async to keep the call signature stable for Task 8, where the
        verification path may await _check_expected_outcome work that
        consults the page asynchronously.
        """
        pre_url = pre_snapshot.get("url", "")
        pre_hash = self._snapshot_content_hash(pre_snapshot)
        pre_dialog = bool(pre_snapshot.get("has_dialog"))
        post_url = post_snapshot.get("url", "")
        post_hash = self._snapshot_content_hash(post_snapshot)
        post_dialog = bool(post_snapshot.get("has_dialog"))
        is_click = action_kind in (
            "click_apply", "click_apply_guess", "click_element",
            "linkedin_direct_apply", "dismiss_overlay", "dismiss_dialog",
            "accept_consent",
        )
        ghost = is_click and self._detect_ghost_click(
            pre_url, pre_hash, pre_dialog, post_url, post_hash, post_dialog,
        )
        return ActionVerification(
            pre_url=pre_url, pre_hash=pre_hash, pre_dialog=pre_dialog,
            post_url=post_url, post_hash=post_hash, post_dialog=post_dialog,
            ghost_click=ghost,
        )

    def _check_expected_outcome(
        self, action: PageAction, verification: ActionVerification,
    ) -> ActionVerification:
        """Populate verification.expected_outcome_met based on action.expected_outcome.

        Returns the same ActionVerification (mutated). The mapping:
        - url_changes      → True iff verification.url_changed
        - dialog_dismissed → True iff a dialog was present pre and absent post
        - page_unchanged   → True iff no content changed
        - fields_filled    → None (caller checks ExecutorResult, not verification)
        - unknown          → None (no expectation declared)
        """
        outcome = getattr(action, "expected_outcome", "unknown")
        if outcome == "unknown":
            verification.expected_outcome_met = None
            return verification
        if outcome == "url_changes":
            verification.expected_outcome_met = verification.url_changed
        elif outcome == "dialog_dismissed":
            verification.expected_outcome_met = (
                verification.pre_dialog and not verification.post_dialog
            )
        elif outcome == "page_unchanged":
            verification.expected_outcome_met = not verification.content_changed
        elif outcome == "fields_filled":
            verification.expected_outcome_met = None
        else:
            verification.expected_outcome_met = None
        return verification

    @staticmethod
    def _snapshot_content_hash(snapshot: dict[str, Any]) -> str:
        text = snapshot.get("page_text_preview", "")[:300]
        fc = str(len(snapshot.get("fields", [])))
        bc = str(len(snapshot.get("buttons", [])))
        return hashlib.sha256(f"{text}|{fc}|{bc}".encode()).hexdigest()[:16]

    @staticmethod
    def _make_result(ctx: "StepContext") -> dict[str, Any]:
        action = ctx.planned_action
        act = action.action if action else "abort"
        pt = action.page_type if action else "unknown"

        # Surface the reasoner's PageAction so downstream consumers
        # (NativeFormFiller._click_navigation, _is_submit_page) can
        # consume advance_button + action='done' instead of running
        # their own hardcoded button-text lookups. Defensive: tests
        # may stub planned_action with a types.SimpleNamespace.
        if action is None:
            planned_action_dict = None
        elif hasattr(action, "to_dict"):
            planned_action_dict = action.to_dict()
        else:
            planned_action_dict = {
                "action": getattr(action, "action", "abort"),
                "page_type": getattr(action, "page_type", "unknown"),
                "advance_button": getattr(action, "advance_button", ""),
                "confidence": getattr(action, "confidence", 0.0),
                "expected_outcome": getattr(action, "expected_outcome", "unknown"),
            }

        if act in ("fill_form", "fill_and_advance"):
            result: dict[str, Any] = {
                "page_type": PageType.APPLICATION_FORM,
                "snapshot": ctx.snapshot,
                "planned_action": planned_action_dict,
            }
        elif act == "done":
            result = {
                "page_type": PageType.CONFIRMATION,
                "snapshot": ctx.snapshot,
                "planned_action": planned_action_dict,
            }
        else:
            result = {
                "page_type": PageType.UNKNOWN,
                "snapshot": ctx.snapshot,
                "planned_action": planned_action_dict,
            }

        if pt == "expired_job":
            result["expired"] = True
            result["error"] = (action.page_understanding if action else "") or "Job is no longer available"

        return result

    async def _phase_observe(self, ctx: StepContext) -> StepContext:
        page = getattr(self.driver, "page", None)
        if page is None:
            return ctx

        if hasattr(page, "is_closed") and page.is_closed():
            ctx.tab_state = TabState.CLOSED
            return ctx

        # Capture pre-observe URL so we can detect cross-domain transitions
        # below and clear stale reflection state planned against the old host.
        prev_url = ctx.url

        browser_ctx = getattr(page, "context", None)
        if browser_ctx is not None:
            pages = browser_ctx.pages
            if len(pages) > 1 and self._should_auto_switch_tab(page):
                newest = self._pick_target_tab(pages, page)
                if newest is not None:
                    try:
                        await newest.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    logger.info("OBSERVE: new tab detected — switching to %s", newest.url[:80])
                    self.driver._page = newest
                    ctx.tab_state = TabState.NEW_TAB
                    ctx.tab_recovered = True
                    intelligence = getattr(self.driver, "intelligence", None)
                    if intelligence:
                        intelligence.clear()
                        await intelligence.inject_on_new_page()
                    ctx.snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                    ctx.url = ctx.snapshot.get("url", "")
                    self._clear_stale_plan_on_host_change(ctx, prev_url, ctx.url, "new_tab")
                    return ctx

        current_url = page.url or ""
        if current_url and current_url != ctx.url:
            logger.info("OBSERVE: redirect detected — %s → %s", ctx.url[:50], current_url[:50])
            ctx.tab_state = TabState.REDIRECTED
            ctx.tab_recovered = True
            ctx.snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
            ctx.url = ctx.snapshot.get("url", "")
            intelligence = getattr(self.driver, "intelligence", None)
            if intelligence:
                intelligence.clear()
                await intelligence.inject_on_new_page()
            self._clear_stale_plan_on_host_change(ctx, prev_url, ctx.url, "redirect")
            return ctx

        ctx.snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        ctx.url = ctx.snapshot.get("url", "")
        return ctx

    @staticmethod
    def _is_apply_path_url(url: str) -> bool:
        """Does this URL look like an in-progress ATS application page?

        Mirrors the path patterns used by
        `live_review_applicator._find_in_progress_apply_tab` so the
        navigator's tab-switch heuristic agrees with the session's
        explicit tab pick. Structural URL validation only — regex is
        appropriate here per `.claude/rules/jobpulse.md`.
        """
        if not url:
            return False
        return bool(re.search(
            r"/apply(/|$|\?)|/section/|/application(/|$|\?)|/candidate/|"
            r"/jobs/[^/]+/apply|/job/[^/]+/apply",
            url, re.IGNORECASE,
        ))

    def _should_auto_switch_tab(self, current_page: Any) -> bool:
        """Decide whether `_phase_observe` may switch to a different tab.

        Bug 2026-05-05 (regression): with multiple tabs open (Indeed JD +
        ATS JD + ATS apply form), the original heuristic blindly switched
        to `pages[-1]`, clobbering the tab the session had deliberately
        attached to via `_find_in_progress_apply_tab` + `prefer_url`.

        Two locks:
          1. If the driver attached via `prefer_url` match
             (`_attached_existing_url`), don't auto-switch — the
             attachment was intentional.
          2. If the current page is already on an apply-path URL, don't
             auto-switch — we're already on the right page.

        Auto-switch is still allowed when current is on a non-apply page
        (e.g., Indeed JD that just opened a new ATS tab via "Apply on
        company site") so we can follow the SSO/redirect chain.
        """
        if getattr(self.driver, "_attached_existing_url", False):
            return False
        try:
            current_url = current_page.url or ""
        except Exception:
            current_url = ""
        if self._is_apply_path_url(current_url):
            return False
        return True

    def _pick_target_tab(self, pages: list[Any], current: Any) -> Any | None:
        """Pick the right tab to switch to from candidate `pages`.

        Prefer: a tab on an apply-path URL, then the newest non-current,
        non-closed page. This ensures that when "Apply on company site"
        opens a new ATS tab while leaving the original Indeed JD tab in
        the list, we pick the ATS tab — not whichever happens to be at
        `pages[-1]`.
        """
        candidates: list[Any] = []
        for p in pages:
            if p is current:
                continue
            try:
                if hasattr(p, "is_closed") and p.is_closed():
                    continue
            except Exception:
                continue
            candidates.append(p)
        if not candidates:
            return None
        for p in reversed(candidates):
            try:
                if self._is_apply_path_url(p.url or ""):
                    return p
            except Exception:
                continue
        return candidates[-1]

    @staticmethod
    def _domain_has_prior_success(url: str) -> bool:
        """Has this domain had at least one successful application fill?

        Used by `_phase_plan` to decide whether the DOM-only fast path to
        fill_form is safe. Trust comes from FormExperienceDB: any record
        with `success=1` means the agent has previously navigated this site
        end-to-end, so the DOM classifier's confidence is well-calibrated
        for it. New/unknown domains fall through to PageReasoner regardless
        of DOM confidence.

        Resolved at runtime via FormExperienceDB lookup — no hardcoded
        domain allow-list, fully dynamic.
        """
        from urllib.parse import urlparse
        try:
            domain = urlparse(url or "").netloc.lower().removeprefix("www.")
        except Exception:
            return False
        if not domain:
            return False
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            fe = FormExperienceDB()
            record = fe.lookup(domain)
            if not record:
                return False
            return bool(record.get("success", 0)) and int(record.get("apply_count") or 0) >= 1
        except Exception as exc:
            logger.debug("_domain_has_prior_success: lookup failed for %s: %s", domain, exc)
            return False

    @staticmethod
    def _clear_stale_plan_on_host_change(
        ctx: StepContext, prev_url: str, new_url: str, reason: str,
    ) -> None:
        """Drop carried reflection state when navigating to a different host.

        Without this, an action planned against the previous host (e.g. clicking
        a LinkedIn premium-overlay close button) gets executed against a new
        host (e.g. Greenhouse), which produces guaranteed ghost clicks because
        the target element doesn't exist on the new page. Forcing a fresh plan
        on host transitions breaks that loop.
        """
        try:
            from urllib.parse import urlparse
            prev_host = urlparse(prev_url or "").netloc.lower().removeprefix("www.")
            new_host = urlparse(new_url or "").netloc.lower().removeprefix("www.")
        except Exception:
            return
        if prev_host and new_host and prev_host != new_host:
            if ctx.reflected_action is not None:
                logger.info(
                    "OBSERVE: host change %s→%s (%s) — clearing stale reflected_action",
                    prev_host, new_host, reason,
                )
                ctx.reflected_action = None

    async def _phase_analyze(self, ctx: StepContext) -> StepContext:
        dom_type, dom_confidence = self._classifier.classify(ctx.snapshot)
        ctx.dom_type = dom_type
        ctx.dom_confidence = dom_confidence

        ctx.page_fingerprint = build_page_fingerprint(
            ctx.snapshot,
            page_type=dom_type.value if hasattr(dom_type, "value") else str(dom_type),
            dom_confidence=dom_confidence,
        )

        intelligence = getattr(self.driver, "intelligence", None)
        if intelligence:
            try:
                signals = intelligence.get_signals()
                ctx.browser_signals = [
                    {"source": s.source, "level": s.level, "text": s.text,
                     "timestamp_ms": s.timestamp_ms, "url": s.url}
                    for s in signals
                ]
            except Exception:
                pass

        wall = ctx.snapshot.get("verification_wall")
        if wall:
            ctx.wall_detected = wall

        pre_dismiss_hash = self._snapshot_content_hash(ctx.snapshot)

        await self.cookie_dismisser.dismiss(ctx.snapshot)
        page = getattr(self.driver, "page", None)
        if page is not None:
            await dismiss_cookie_banner_playwright(page)

        ctx.snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        ctx.snapshot = await self._dismiss_site_prompt_if_present(ctx.snapshot)

        post_dismiss_hash = self._snapshot_content_hash(ctx.snapshot)
        if post_dismiss_hash != pre_dismiss_hash:
            dom_type, dom_confidence = self._classifier.classify(ctx.snapshot)
            ctx.dom_type = dom_type
            ctx.dom_confidence = dom_confidence
            ctx.page_fingerprint = build_page_fingerprint(
                ctx.snapshot,
                page_type=dom_type.value if hasattr(dom_type, "value") else str(dom_type),
                dom_confidence=dom_confidence,
            )

        return ctx

    def _phase_match(self, ctx: StepContext, domain: str, platform: str, step_index: int) -> StepContext:
        if ctx.page_fingerprint is None:
            ctx.match_source = "none"
            return ctx

        sequence = self.learner.get_sequence(domain)
        source = "domain"
        if not sequence and platform:
            sequence = self.learner.get_platform_pattern(platform, exclude_domain=domain)
            source = "platform"
        if not sequence:
            content_hash = ctx.page_fingerprint.content_hash if ctx.page_fingerprint else ""
            sequence = self.learner.get_sequence_by_content_hash(content_hash, exclude_domain=domain) if content_hash else None
            source = "content_hash"

        if not sequence:
            ctx.match_source = "none"
            return ctx

        if step_index >= len(sequence):
            ctx.match_source = "none"
            return ctx

        learned_step = sequence[step_index]
        learned_fp = learned_step.get("fingerprint")

        if not learned_fp:
            page_type_match = (ctx.page_fingerprint.page_type == learned_step.get("page_type", ""))
            ctx.match_score = 0.3 if page_type_match else 0.0
            ctx.match_source = "none"
            return ctx

        ctx.match_score = score_fingerprint_match(ctx.page_fingerprint, learned_fp)

        if ctx.match_score >= 0.7:
            ctx.learned_step = learned_step
            ctx.match_source = source
            logger.info("MATCH: score=%.2f from %s — using learned step: %s",
                         ctx.match_score, source, learned_step.get("action"))
        else:
            ctx.match_source = "none"
            logger.info("MATCH: score=%.2f (below 0.7) — falling through to reasoner", ctx.match_score)

        return ctx

    def _phase_plan(self, ctx: StepContext, visited_states: dict[str, int], wall_bypass_attempts: int) -> StepContext:
        # Carried reflection: if the previous iteration's action failed and the
        # reasoner pivoted (e.g. fill_and_advance → wait_human), use that
        # pivoted action this iteration instead of re-asking the primary
        # reasoner. Without this, the primary returns the same failed action,
        # the reflection pivots again, and we burn 3 LLM calls before the
        # loop detector aborts.
        if ctx.reflected_action is not None:
            ctx.planned_action = ctx.reflected_action
            ctx.plan_source = "reflection_carryover"
            ra = ctx.reflected_action
            logger.info(
                "PLAN: reflection carryover → %s (type=%s, conf=%.2f)",
                ra.action, ra.page_type, ra.confidence,
            )
            # Clear so it doesn't carry across more than one iteration.
            ctx.reflected_action = None
            return ctx

        if ctx.wall_detected:
            ctx.planned_action = PageAction(
                page_understanding="Verification wall detected",
                action="wait_human",
                target_text="",
                reasoning=f"Wall type: {ctx.wall_detected.get('type', 'unknown')}",
                confidence=1.0,
                page_type="verification_wall",
            )
            ctx.plan_source = "fast_path"
            return ctx

        if ctx.dom_confidence >= 0.8 and ctx.dom_type == PageType.CONFIRMATION:
            ctx.planned_action = PageAction(
                page_understanding="Confirmation page detected",
                action="done",
                target_text="",
                reasoning=f"DOM confidence {ctx.dom_confidence:.2f}",
                confidence=ctx.dom_confidence,
                page_type="confirmation",
            )
            ctx.plan_source = "fast_path"
            return ctx

        # DOM-fast-path to fill_form is only safe on domains we've successfully
        # filled before. The DOM classifier mis-labels job-description pages
        # with embedded contact/search forms as APPLICATION_FORM at high
        # confidence — verified live on pls-solicitors.co.uk where the listing
        # URL had visible form elements but the actual apply form was behind
        # an "Apply Now" button further down. Untrusted domains: skip the
        # fast path and let PageReasoner re-evaluate. Trusted domains
        # (has at least one successful fill in FormExperienceDB): keep
        # fast path for speed.
        domain_trusted = FormNavigator._domain_has_prior_success(ctx.url)
        if (
            ctx.dom_confidence >= 0.8
            and ctx.dom_type == PageType.APPLICATION_FORM
            and domain_trusted
        ):
            ctx.planned_action = PageAction(
                page_understanding="Application form detected",
                action="fill_form",
                target_text="",
                reasoning=f"DOM confidence {ctx.dom_confidence:.2f}",
                confidence=ctx.dom_confidence,
                page_type="application_form",
            )
            ctx.plan_source = "fast_path"
            return ctx

        if ctx.learned_step and ctx.match_score >= 0.7:
            learned_action = ctx.learned_step.get("action", "")
            if self._verify_learned_action(learned_action, ctx.snapshot):
                ctx.planned_action = PageAction(
                    page_understanding=f"Learned step (score={ctx.match_score:.2f})",
                    action=learned_action,
                    target_text="",
                    reasoning=f"Matched from {ctx.match_source}",
                    confidence=ctx.match_score,
                    page_type=ctx.learned_step.get("page_type", "unknown"),
                )
                ctx.plan_source = "learned_verified"
                logger.info("PLAN: using verified learned action '%s' (score=%.2f)", learned_action, ctx.match_score)
                try:
                    self.learner.increment_replay(extract_domain(ctx.url))
                except Exception:
                    pass
                return ctx
            logger.info("PLAN: learned action '%s' failed verification — falling to reasoner", learned_action)

        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(ctx.snapshot)

        state_key = f"{action.page_type}:{action.action}"
        visited_states[state_key] = visited_states.get(state_key, 0) + 1
        if visited_states[state_key] >= 3:
            logger.warning("PLAN: loop detected — %s x%d — aborting", state_key, visited_states[state_key])
            ctx.planned_action = PageAction(
                page_understanding="Navigation loop detected",
                action="abort",
                target_text="",
                reasoning=f"State {state_key} repeated {visited_states[state_key]} times",
                confidence=0.0,
                page_type="unknown",
            )
            ctx.plan_source = "fast_path"
            return ctx

        if action.page_type == "expired_job":
            action = PageAction(
                page_understanding=action.page_understanding,
                action="abort",
                target_text="",
                reasoning=action.reasoning,
                confidence=action.confidence,
                page_type="expired_job",
            )

        # Stuck-state telemetry. The cognitive-escalation block previously
        # here was silently broken: `engine.think(...)` is async, so calling
        # it without `await` returned a coroutine, and `cog_result.get(...)`
        # raised AttributeError on every trigger. Removed in S3 audit
        # (2026-05-07) — there is no `ThinkResult` → `PageAction` translator
        # yet, so reviving the call would only burn LLM cost without
        # changing the planner output. Re-enable when a translator exists.
        if action.confidence < 0.3 and sum(1 for v in visited_states.values() if v >= 2) >= 2:
            logger.info(
                "PLAN: stuck (conf=%.2f, visited=%s) — cognitive escalation "
                "is not wired; the reasoner's action stands.",
                action.confidence, dict(visited_states),
            )

        ctx.planned_action = action
        ctx.plan_source = "reasoner"
        logger.info("PLAN: reasoner → %s (type=%s, conf=%.2f)",
                    action.action, action.page_type, action.confidence)
        return ctx

    def _verify_learned_action(self, action: str, snapshot: dict) -> bool:
        if action in APPLY_CLICK_ACTIONS:
            return find_apply_button(snapshot) is not None
        if action.startswith("sso_"):
            provider = action[len("sso_"):]
            sso = self.sso.detect_sso(snapshot)
            return sso is not None and sso.get("provider") == provider
        if action in ("login", "signup", "fill_login", "fill_signup"):
            fields = snapshot.get("fields", [])
            has_password = any(f.get("input_type") == "password" for f in fields)
            has_email = any(
                f.get("input_type") == "email" or "email" in f.get("label", "").lower()
                for f in fields
            )
            return has_password and has_email
        if action == "verify_email":
            text = (snapshot.get("page_text_preview") or "").lower()
            return "verify" in text or "check your email" in text
        return True

    async def _phase_act(
        self, ctx: "StepContext", platform: str, steps: list[dict],
        wall_bypass_attempts: int, job: dict | None = None,
    ) -> "StepContext":
        action = ctx.planned_action
        if not action:
            return ctx

        pre_url = ctx.snapshot.get("url", "")
        pre_hash = self._snapshot_content_hash(ctx.snapshot)
        pre_dialog = bool(ctx.snapshot.get("has_dialog"))
        post_snap: dict[str, Any] | None = None

        act = action.action

        # Stamp reasoner hints on the page so downstream scanners can consult
        # them without an import cycle. Cheap — these are plain attributes,
        # not Playwright-managed state. Used by field_scanner.scan_fields to
        # decide whether to force a vision augment on sparse scans.
        try:
            _page = getattr(self.driver, "page", None)
            if _page is not None:
                _page._jp_page_type_hint = action.page_type
                _page._jp_reasoner_confidence = float(getattr(action, "confidence", 0.9))
        except Exception:
            pass

        if act in APPLY_CLICK_ACTIONS:
            post_snap = await self.click_apply_button(ctx.snapshot)
            ctx.action_executed = True
        elif act.startswith("sso_"):
            provider = act[len("sso_"):]
            sso = self.sso.detect_sso(ctx.snapshot)
            if sso and sso.get("provider") == provider:
                await self.sso.click_sso(sso)
            ctx.action_executed = True
            post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        elif act == "verify_email":
            post_snap = await self.auth.handle_email_verification(
                ctx.snapshot, platform, pre_url,
            )
            ctx.action_executed = True
        elif act == "wait_human":
            wall_info = ctx.wall_detected or {"type": "unknown"}

            if wall_bypass_attempts > 2:
                try:
                    from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                    get_page_reasoner().invalidate(ctx.snapshot)
                except Exception:
                    pass
                if job:
                    pb_result = await self._try_platform_bypass(ctx.snapshot, job, steps)
                    if pb_result is not None:
                        ctx.post_snapshot = pb_result
                        ctx.action_executed = True
                        return ctx

            bypass_result = await self._bypass_verification_wall(ctx.snapshot, wall_info)
            ctx.action_executed = True
            # _bypass_verification_wall returns solved=True whenever the page
            # is no longer a VERIFICATION_WALL — but for reflection-carryover
            # wait_human on a non-wall page (e.g. login credentials rejected),
            # the page was never a wall to begin with, so "solved" is a
            # false positive. Detect that case via URL stability and treat
            # it as not-solved.
            bypass_post_url = (bypass_result.get("snapshot") or {}).get("url", "")
            carryover_made_no_progress = (
                ctx.plan_source == "reflection_carryover"
                and bypass_post_url == pre_url
            )
            if bypass_result["solved"] and not carryover_made_no_progress:
                post_snap = bypass_result["snapshot"]
            else:
                if job:
                    pb_result = await self._try_platform_bypass(ctx.snapshot, job, steps)
                    if pb_result is not None:
                        ctx.post_snapshot = pb_result
                        return ctx
                # When wait_human came from a reflection carryover and the
                # bypass didn't actually move the page, the primary reasoner
                # would just return the same failed action next iteration.
                # Escalate to abort via the same carryover machinery.
                if ctx.plan_source == "reflection_carryover":
                    logger.info(
                        "ACT: wait_human via reflection_carryover did not resolve — "
                        "escalating to abort"
                    )
                    ctx.reflected_action = PageAction(
                        page_understanding="Reflection escalated to wait_human but no resolution within bypass window",
                        action="abort",
                        target_text="",
                        reasoning="wait_human via reflection carryover did not resolve; primary reasoner would re-run the failed action",
                        confidence=0.7,
                        page_type=action.page_type if action else "unknown",
                    )
                ctx.post_snapshot = bypass_result["snapshot"]
                return ctx
        elif act == "go_back":
            page = getattr(self.driver, "page", None)
            if page:
                await page.go_back(wait_until="domcontentloaded")
                await wait_for_page_stable(page, timeout_ms=5000)
            ctx.action_executed = True
            post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        else:
            page = getattr(self.driver, "page", None)
            if page is not None:
                from jobpulse.applicator import PROFILE
                from jobpulse.navigation.action_executor import emit_fill_failures
                nav_executor = NavigationActionExecutor(page)
                exec_result = await nav_executor.execute(action, profile=PROFILE)
                ctx.executor_result = exec_result
                domain = extract_domain(pre_url)
                emit_fill_failures(exec_result, domain=domain, source="navigator")
            ctx.action_executed = True
            await asyncio.sleep(1.0)
            post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

        if post_snap is None:
            post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

        verification = await self._verify_action(
            pre_snapshot=ctx.snapshot,
            post_snapshot=post_snap,
            action_kind=act,
        )
        post_url = verification.post_url
        post_hash = verification.post_hash
        post_dialog = verification.post_dialog
        if verification.ghost_click:
            logger.warning("ACT: ghost click detected for action '%s'", act)
            page = getattr(self.driver, "page", None)
            retry_recovered = False
            # Retry needs an explicit target string, so we only run it when
            # action.target_text is set. Learned-replay actions hardcode this
            # to "" — they fall straight through to the recovery block below.
            if page is not None and action.target_text:
                for role in ("button", "link"):
                    try:
                        loc = page.get_by_role(role, name=action.target_text, exact=False)
                        if await loc.count() and await loc.first.is_visible():
                            await loc.first.click(force=True)
                            logger.info("ACT: force-click retry on '%s'", action.target_text[:40])
                            await asyncio.sleep(1.0)
                            post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                            retry_hash = self._snapshot_content_hash(post_snap)
                            if not self._detect_ghost_click(pre_url, pre_hash, pre_dialog,
                                                             post_snap.get("url", ""), retry_hash,
                                                             bool(post_snap.get("has_dialog"))):
                                retry_recovered = True
                                break
                    except Exception:
                        continue
            if not retry_recovered:
                ctx.ghost_click = True
                _target_safe = (action.target_text or "")[:40]
                try:
                    from shared.optimization import get_optimization_engine
                    from datetime import UTC, datetime
                    get_optimization_engine().emit(
                        signal_type="failure",
                        source_loop="navigator",
                        domain=extract_domain(pre_url),
                        agent_name="navigator",
                        payload={"param": "ghost_click", "action": act, "target": _target_safe},
                        session_id=f"gc_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
                    )
                except Exception:
                    pass
                try:
                    from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                    removed = get_page_reasoner().invalidate(ctx.snapshot)
                    if removed:
                        logger.info("Invalidated cached reasoning for ghost-click page")
                except Exception:
                    pass
                try:
                    from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                    reflected = get_page_reasoner().reason_with_failure(
                        ctx.snapshot,
                        failure_context=(
                            f"ghost_click on action={act}, "
                            f"target='{(action.target_text or '')[:60]}', "
                            f"pre_url={pre_url}, post_url={post_url}"
                        ),
                    )
                    ctx.reflected_action = reflected
                    logger.info(
                        "Reflection produced: %s (confidence=%.2f)",
                        reflected.action, reflected.confidence,
                    )
                except Exception as exc:
                    logger.debug("Reflection failed: %s", exc)

            # Re-verify against the (possibly retry-updated) post_snap so
            # _check_expected_outcome and the post_url comparison below see the
            # current URL/hash/dialog state, not the pre-retry stale values.
            verification = await self._verify_action(
                pre_snapshot=ctx.snapshot,
                post_snapshot=post_snap,
                action_kind=act,
            )
            post_url = verification.post_url
            post_hash = verification.post_hash
            post_dialog = verification.post_dialog

        verification = self._check_expected_outcome(action, verification)
        if verification.expected_outcome_met is False:
            logger.warning(
                "ACT: expected_outcome '%s' not met for action '%s'",
                action.expected_outcome, act,
            )
            try:
                from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                get_page_reasoner().invalidate(ctx.snapshot)
            except Exception:
                pass
            reflected = _maybe_reflect_on_failure(
                verification=verification,
                snapshot=ctx.snapshot,
                trigger="expected_outcome_violation",
                context_extra={
                    "expected": action.expected_outcome,
                    "action": act,
                },
            )
            if reflected is not None:
                ctx.reflected_action = reflected

        intelligence = getattr(self.driver, "intelligence", None)
        if intelligence and post_url != pre_url:
            intelligence.clear()
            await intelligence.inject_on_new_page()

        logger.info(
            "THRESHOLD_OBS: vision_gate threshold=0.7 confidence=%.2f decision=%s",
            action.confidence,
            "fired" if action.confidence < 0.7 and act not in NO_VISION_GATE_ACTIONS else "skipped",
        )
        if action.confidence < 0.7 and act not in NO_VISION_GATE_ACTIONS:
            try:
                from jobpulse.vision_tier import classify_page_type_from_screenshot
                page = getattr(self.driver, "page", None)
                if page is not None:
                    shot = await page.screenshot(type="png")
                    vision_type = await classify_page_type_from_screenshot(shot)
                    if vision_type and vision_type != "unknown" and vision_type != action.page_type:
                        logger.warning(
                            "Vision-DOM disagreement: reasoner=%s vision=%s — escalating",
                            action.page_type, vision_type,
                        )
                        ctx.vision_disagreement = {
                            "reasoner_type": action.page_type,
                            "vision_type": vision_type,
                        }
                        try:
                            from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                            get_page_reasoner().invalidate(ctx.snapshot)
                        except Exception:
                            pass
                        reflected = _maybe_reflect_on_failure(
                            verification=verification,
                            snapshot=ctx.snapshot,
                            trigger="vision_disagreement",
                            context_extra={
                                "reasoner_type": action.page_type,
                                "vision_type": vision_type,
                            },
                        )
                        if reflected is not None:
                            ctx.reflected_action = reflected
            except Exception as exc:
                logger.debug("Vision gate failed: %s", exc)

        step_record: dict[str, Any] = {
            "page_type": action.page_type,
            "action": act,
        }
        if ctx.page_fingerprint:
            step_record["fingerprint"] = ctx.page_fingerprint.to_dict()
        steps.append(step_record)

        await self.cookie_dismisser.dismiss(post_snap)
        page = getattr(self.driver, "page", None)
        if page is not None:
            await dismiss_cookie_banner_playwright(page)
            post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

        ctx.post_snapshot = post_snap
        return ctx

    @staticmethod
    async def _dismiss_linkedin_discard(page) -> bool:
        """Dismiss LinkedIn 'Save this application?' overlay — delegates to OverlayDismisser."""
        dismisser = OverlayDismisser(page)
        return await dismisser.dismiss_linkedin_discard()

    async def navigate_to_form(
        self, url: str, platform: str, steps: list[dict],
        skip_initial_navigate: bool = False,
        job: dict | None = None,
    ) -> dict:
        """Navigate through redirect chain to reach application form.

        If *skip_initial_navigate* is True, the caller has already loaded the
        page and injected the snapshot into the bridge cache — we skip the
        initial ``bridge.navigate(url)`` to avoid a redundant MV3 restart.
        """
        # If LinkedIn Easy Apply modal is already open, skip ALL navigation to avoid
        # triggering LinkedIn's "Save this application?" dialog.
        # Only check on LinkedIn pages — generic dialog selectors cause false positives.
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

        # When the driver attached to an existing tab matching the target URL
        # (set via PlaywrightDriver.connect(prefer_url=...)), the page is
        # already loaded. Calling driver.navigate(url) here would force a
        # page.goto re-load, destroying any in-progress SPA state — exactly
        # what wiped the user's manually-logged-in JPMC tab on 2026-05-04.
        # Skip the initial navigate when the current URL already matches.
        already_on_target = False
        try:
            current_url = (current_page.url or "") if current_page is not None else ""
            if current_url and (current_url == url or current_url.startswith(url) or url.startswith(current_url)):
                already_on_target = True
            elif getattr(self.driver, "_attached_existing_url", False):
                already_on_target = True
        except Exception:
            already_on_target = False

        if not skip_initial_navigate and not already_on_target:
            try:
                await self.driver.navigate(url)
            except (TimeoutError, ConnectionError):
                logger.info("Navigate lost (MV3 restart) — waiting for extension to reconnect")
                await wait_for_page_stable(self.driver.page, timeout_ms=8000)
        elif already_on_target:
            logger.info(
                "navigate_to_form: skipping initial navigate — already on %s",
                (current_page.url if current_page else url)[:100],
            )
        snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        if not snapshot or not snapshot.get("url"):
            # Still no snapshot — wait longer
            await wait_for_page_stable(self.driver.page, timeout_ms=8000)
            snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

        # ── 5-Phase Navigation Loop ──
        domain = extract_domain(url)
        visited_states: dict[str, int] = {}
        wall_bypass_attempts = 0
        prev_url = snapshot.get("url", "")
        # Carry the reflection's pivoted action from one iteration to the next
        # so _phase_plan can act on it before the primary reasoner runs again.
        pending_reflected_action: Any = None
        # Cap consecutive ghost-clicks at GHOST_LOOP_BUDGET (3). After that,
        # additional reflection on the same page is futile — it just burns
        # LLM cost cycling between dismiss_overlay and click_element on a page
        # whose Apply button doesn't change URL (e.g. Greenhouse inline forms).
        consecutive_ghost_clicks = 0
        GHOST_LOOP_BUDGET = 3

        for step_idx in range(MAX_NAVIGATION_STEPS):
            ctx = StepContext(snapshot=snapshot, url=prev_url, tab_state=TabState.NORMAL)
            ctx.reflected_action = pending_reflected_action
            pending_reflected_action = None

            ctx = await self._phase_observe(ctx)
            if ctx.tab_state == TabState.CLOSED:
                logger.warning("Page closed during navigation — aborting")
                return {"page_type": PageType.UNKNOWN, "snapshot": ctx.snapshot}

            # Tab/redirect transitions reveal genuinely new pages — the prior
            # ghost-click streak no longer applies.
            if ctx.tab_recovered:
                consecutive_ghost_clicks = 0

            ctx = await self._phase_analyze(ctx)

            ctx = self._phase_match(ctx, domain, platform, len(steps))

            ctx = self._phase_plan(ctx, visited_states, wall_bypass_attempts)

            if ctx.planned_action and ctx.planned_action.action in TERMINAL_ACTIONS:
                return self._make_result(ctx)

            ctx = await self._phase_act(ctx, platform, steps, wall_bypass_attempts, job=job)

            if ctx.ghost_click:
                consecutive_ghost_clicks += 1
                if consecutive_ghost_clicks >= GHOST_LOOP_BUDGET:
                    logger.warning(
                        "ACT: ghost-click budget exhausted (%d consecutive) — "
                        "page state isn't changing on click. URL=%s. Aborting "
                        "navigation; caller should retry via platform handoff "
                        "or human bypass.",
                        consecutive_ghost_clicks, (ctx.url or "")[:80],
                    )
                    return {"page_type": PageType.UNKNOWN, "snapshot": ctx.snapshot}
            else:
                consecutive_ghost_clicks = 0

            if ctx.planned_action and ctx.planned_action.action == "wait_human":
                wall_bypass_attempts += 1
            else:
                wall_bypass_attempts = 0

            # Capture the reflection (if _phase_act produced one) for the next
            # iteration's plan. The primary reasoner's cache returns the same
            # failed action otherwise — the pivot is lost.
            pending_reflected_action = ctx.reflected_action

            snapshot = ctx.post_snapshot or ctx.snapshot
            prev_url = snapshot.get("url", "")

        return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

    async def click_apply_button(self, snapshot: dict) -> dict:
        buttons = snapshot.get("buttons", [])
        button_texts = [b.get("text", "")[:60] for b in buttons]
        logger.info("Apply button search: %d buttons found — %s", len(buttons), button_texts[:10])

        # Score all buttons using unified scoring
        scored: list[tuple[float, dict]] = []
        for btn in buttons:
            text = btn.get("text", "")
            if btn.get("enabled") is False:
                continue
            if len(text) > 50:
                continue
            score = score_apply_button(text)
            if score > 0:
                scored.append((score, btn))

        if not scored:
            logger.warning(
                "No apply button found in snapshot — consulting reasoner for "
                "target_text (Plan F1-2: dynamic-over-hardcoded fallback)"
            )
            current_page = getattr(self.driver, "page", None)
            if current_page is not None:
                # Plan F1-2: ask the reasoner for the exact button text to
                # click. The reasoner emits action='click_apply' with
                # target_text on JD pages. No hardcoded button-name list —
                # whatever string ladder we wrote here would always lag a
                # site we hadn't seen yet.
                try:
                    from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                    snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                    action = get_page_reasoner().reason_sync(snap)
                    target_text = (action.target_text or "").strip()
                    if (action.action == "click_apply"
                            and target_text):
                        for matcher in (
                            current_page.get_by_role("link", name=target_text, exact=True),
                            current_page.get_by_role("button", name=target_text, exact=True),
                            current_page.get_by_role("link", name=target_text, exact=False),
                            current_page.get_by_role("button", name=target_text, exact=False),
                        ):
                            try:
                                loc = matcher.first
                                if await loc.count() and await loc.is_visible():
                                    logger.info(
                                        "click_apply: clicked %r via reasoner-named target_text",
                                        target_text,
                                    )
                                    await loc.click()
                                    await wait_for_page_stable(current_page, timeout_ms=8000)
                                    return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                            except Exception:
                                continue
                except Exception as exc:
                    logger.debug("click_apply: reasoner fallback failed: %s", exc)
            return snapshot

        # Rank by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        ranked = [btn for _, btn in scored]

        current_page = getattr(self.driver, "page", None)

        # If LinkedIn Easy Apply modal is already open (from a previous attempt),
        # skip navigation — going to a URL while the modal is open triggers
        # LinkedIn's "Save this application?" dialog.
        # Only check on LinkedIn pages — generic [role="dialog"] matches cookie
        # consent dialogs on external ATS sites, causing false positives.
        if current_page is not None:
            try:
                page_url = current_page.url or ""
                if "linkedin.com" in page_url:
                    modal = current_page.locator('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]')
                    if await modal.count():
                        logger.info("Easy Apply modal already open — skipping navigation")
                        return self._as_dict(await self.driver.get_snapshot())
            except Exception:
                pass

        before_pages = []
        if current_page is not None:
            with_pages = getattr(current_page, "context", None)
            before_pages = list(with_pages.pages) if with_pages is not None else []

        # Strategy: if the match is a normal link with href, navigate directly.
        # But LinkedIn outbound apply links (`/safety/go`) must be clicked on-page
        # so LinkedIn can open the external ATS tab correctly.
        for btn in ranked:
            href = btn.get("href", "")
            if href and href.startswith("http") and "linkedin.com/safety/go" not in href:
                logger.info("Apply link found: '%s' → navigating to %s", btn["text"][:40], href[:100])
                await self.driver.navigate(href)
                await wait_for_page_stable(current_page or self.driver.page, timeout_ms=8000)
                if current_page is not None:
                    # LinkedIn draft dialog may take time to render — try twice
                    dismissed = await self._dismiss_linkedin_discard(current_page)
                    if not dismissed:
                        await wait_for_modal_open(current_page, timeout_ms=2000)
                        await self._dismiss_linkedin_discard(current_page)
                return self._as_dict(await self.driver.get_snapshot())

        # Fallback: click the button directly (Easy Apply modals, non-link buttons)
        btn = ranked[0]
        logger.info("Clicking apply button: '%s' via %s", btn["text"][:60], btn["selector"])
        button_text = (btn.get("text") or "").strip()
        try:
            clicked = False
            if current_page is not None and button_text:
                for role in ("link", "button"):
                    locator = current_page.get_by_role(role, name=button_text).first
                    try:
                        if await locator.count():
                            await locator.click()
                            clicked = True
                            break
                    except Exception:
                        continue
            if not clicked:
                await self.driver.click(btn["selector"])
        except (TimeoutError, Exception) as exc:
            logger.warning("Click timed out (%s) — trying force_click", exc)
            try:
                if current_page is not None and button_text:
                    forced = False
                    for role in ("link", "button"):
                        locator = current_page.get_by_role(role, name=button_text).first
                        try:
                            if await locator.count():
                                await locator.click(force=True)
                                forced = True
                                break
                        except Exception:
                            continue
                    if not forced:
                        await self.driver.force_click(btn["selector"])
                else:
                    await self.driver.force_click(btn["selector"])
            except Exception as e:
                logger.debug("Force click also failed: %s", e)

        # Wait for modal or new form fields
        modal_found = await wait_for_modal_open(self.driver.page, timeout_ms=8000)
        if not modal_found:
            await wait_for_page_stable(self.driver.page, timeout_ms=3000)

        if current_page is not None:
            await self._dismiss_linkedin_discard(current_page)

        # Follow external applications that open in a new tab/window.
        if current_page is not None:
            context = getattr(current_page, "context", None)
            if context is not None:
                new_pages = [page for page in context.pages if page not in before_pages]
                if new_pages:
                    newest = new_pages[-1]
                    try:
                        await newest.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    logger.info("Apply click opened a new page: %s", newest.url)
                    self.driver._page = newest

        return self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    async def _bypass_verification_wall(self, snapshot: dict, wall_info: dict) -> dict:
        """Multi-stage Cloudflare/CAPTCHA bypass using full Playwright capabilities.

        Stages:
        1. Auto-wait — Cloudflare JS challenges auto-resolve in 3-10s
        2. Human interaction simulation — mouse movement, scroll, click
        3. Page reload — clears transient challenges
        4. Turnstile checkbox click — Cloudflare's interactive challenge
        5. Human fallback (MANDATORY) — Telegram alert, wait 120s
        """
        page = getattr(self.driver, "page", None)
        wall_type = wall_info.get("type", "unknown")
        wall_url = snapshot.get("url", "?")

        async def _check_cleared() -> dict | None:
            try:
                snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
            except Exception:
                await asyncio.sleep(2)
                try:
                    snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                except Exception:
                    return None
            re_type = await self.analyzer.detect(snap)
            if re_type != PageType.VERIFICATION_WALL:
                return snap
            return None

        # ── Stage 1: Auto-wait (Cloudflare JS challenge typically resolves in 3-10s) ──
        logger.info("Bypass stage 1: waiting for JS challenge auto-resolve (up to 15s)")
        for _poll in range(5):
            await asyncio.sleep(3)
            cleared = await _check_cleared()
            if cleared:
                logger.info("Bypass stage 1 succeeded: wall cleared after %ds", (_poll + 1) * 3)
                return {"solved": True, "snapshot": cleared}

        if page is None:
            logger.warning("Bypass: no page object — skipping interactive stages")
            return {"solved": False, "snapshot": snapshot}

        # ── Stage 2: Simulate human interaction ──
        logger.info("Bypass stage 2: simulating human interaction")
        try:
            import random
            await page.mouse.move(random.randint(100, 600), random.randint(100, 400))
            await asyncio.sleep(0.3)
            await page.mouse.move(random.randint(200, 700), random.randint(200, 500))
            await asyncio.sleep(0.5)
            await page.evaluate("window.scrollBy(0, 100)")
            await asyncio.sleep(1)
            await page.evaluate("window.scrollBy(0, -50)")
            await asyncio.sleep(1)
        except Exception as exc:
            logger.debug("Stage 2 interaction failed: %s", exc)

        cleared = await _check_cleared()
        if cleared:
            logger.info("Bypass stage 2 succeeded: wall cleared after human simulation")
            return {"solved": True, "snapshot": cleared}

        # ── Stage 3: Turnstile/checkbox click ──
        logger.info("Bypass stage 3: attempting Turnstile/checkbox click")
        try:
            for selector in (
                "iframe[src*='challenges.cloudflare.com']",
                "iframe[src*='turnstile']",
                ".cf-turnstile iframe",
            ):
                frame_el = page.locator(selector)
                if await frame_el.count():
                    frame = await frame_el.first.content_frame()
                    if frame:
                        checkbox = frame.locator("input[type='checkbox'], .cb-i, #challenge-stage")
                        if await checkbox.count():
                            await checkbox.first.click()
                            logger.info("Clicked Turnstile checkbox")
                            await asyncio.sleep(5)
                            cleared = await _check_cleared()
                            if cleared:
                                logger.info("Bypass stage 3 succeeded: Turnstile cleared")
                                return {"solved": True, "snapshot": cleared}
        except Exception as exc:
            logger.debug("Stage 3 Turnstile click failed: %s", exc)

        # ── Stage 4: Page reload ──
        logger.info("Bypass stage 4: reloading page")
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
        except Exception as exc:
            logger.debug("Stage 4 reload failed: %s", exc)

        cleared = await _check_cleared()
        if cleared:
            logger.info("Bypass stage 4 succeeded: wall cleared after reload")
            return {"solved": True, "snapshot": cleared}

        # ── Stage 5: Second reload with networkidle ──
        logger.info("Bypass stage 5: second reload with networkidle wait")
        try:
            await page.reload(wait_until="networkidle", timeout=20000)
            await asyncio.sleep(5)
        except Exception as exc:
            logger.debug("Stage 5 reload failed: %s", exc)

        cleared = await _check_cleared()
        if cleared:
            logger.info("Bypass stage 5 succeeded: wall cleared after second reload")
            return {"solved": True, "snapshot": cleared}

        # ── Stage 6: MANDATORY human fallback ──
        logger.warning("All auto-bypass stages failed — requesting human intervention (MANDATORY)")
        try:
            from jobpulse.telegram_agent import send_message as _send_tg
            from jobpulse.config import TELEGRAM_CHAT_ID as _chat_id
            _send_tg(
                f"🔒 Security wall ({wall_type}) on:\n{wall_url}\n\n"
                "Auto-bypass failed after 5 attempts.\n"
                "Please solve the challenge manually in Chrome — I'll wait up to 120 seconds.",
                chat_id=_chat_id,
            )
        except Exception:
            pass

        for _poll in range(24):
            await asyncio.sleep(5)
            cleared = await _check_cleared()
            if cleared:
                logger.info("Human solved the wall after %ds", (_poll + 1) * 5)
                try:
                    from jobpulse.telegram_agent import send_message as _send_tg2
                    from jobpulse.config import TELEGRAM_CHAT_ID as _chat_id2
                    _send_tg2("✅ Security wall cleared — continuing application.", chat_id=_chat_id2)
                except Exception:
                    pass
                return {"solved": True, "snapshot": cleared}

        logger.error("Verification wall not cleared after all bypass stages + 120s human wait")
        try:
            from jobpulse.telegram_agent import send_message as _send_tg3
            from jobpulse.config import TELEGRAM_CHAT_ID as _chat_id3
            _send_tg3(
                f"❌ Could not bypass security wall on {wall_url}. Skipping this job.",
                chat_id=_chat_id3,
            )
        except Exception:
            pass
        try:
            snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        except Exception:
            snap = snapshot
        return {"solved": False, "snapshot": snap}

    async def _dismiss_site_prompt_if_present(self, snapshot: dict) -> dict:
        """Detect and dismiss non-application dialogs (site prompts, surveys, alerts)."""
        if not snapshot.get("has_dialog"):
            return snapshot

        dialog_text = snapshot.get("dialog_text", "").lower()
        if not dialog_text:
            return snapshot

        prompt_signals = (
            "are you interested", "not interested", "maybe later",
            "save application", "rate your experience", "take a survey",
            "subscribe", "newsletter", "job alert", "similar jobs",
            "how did you hear", "recommended for you",
        )
        is_prompt = any(sig in dialog_text for sig in prompt_signals)
        if not is_prompt:
            return snapshot

        logger.info("Site prompt dialog detected — attempting to dismiss: %s", dialog_text[:80])
        page = getattr(self.driver, "page", None)
        if page is None:
            return snapshot

        dismiss_texts = ("Close", "No thanks", "Not now", "Dismiss", "Skip", "Maybe later", "Not interested")
        for text in dismiss_texts:
            try:
                btn = page.get_by_role("button", name=text, exact=False)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click()
                    logger.info("Dismissed site prompt via '%s'", text)
                    await asyncio.sleep(0.5)
                    return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
            except Exception:
                continue

        for selector in ('[aria-label="Close"]', '[aria-label="Dismiss"]', 'button.close', '[data-dismiss]'):
            try:
                loc = page.locator(selector)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click()
                    logger.info("Dismissed site prompt via selector %s", selector)
                    await asyncio.sleep(0.5)
                    return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
            except Exception:
                continue

        logger.warning("Could not dismiss site prompt dialog — proceeding anyway")
        return snapshot

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
                return await self._navigate_to_direct_url(
                    pb_result.direct_url, wall_url, pb_result.strategy_used, steps,
                )
        except Exception as exc:
            logger.debug("Platform bypass failed: %s", exc)

        # Fallback: scrape the direct URL on-the-fly via python-jobspy
        direct = self._scrape_direct_url(job)
        if direct:
            return await self._navigate_to_direct_url(direct, wall_url, "live_scrape", steps)

        return None

    async def _navigate_to_direct_url(
        self, direct_url: str, wall_url: str, strategy: str, steps: list[dict],
    ) -> dict | None:
        """Navigate to a resolved direct ATS URL and return the new snapshot."""
        try:
            logger.info("Platform bypass: %s → %s (strategy=%s)", wall_url[:40], direct_url[:60], strategy)
            await self.driver.page.goto(direct_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)
            new_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
            steps.append({
                "page_type": "platform_bypass",
                "action": "redirect_to_ats",
                "from_url": wall_url,
                "to_url": direct_url,
                "strategy": strategy,
            })
            return new_snap
        except Exception as exc:
            logger.warning("Failed to navigate to direct URL %s: %s", direct_url[:60], exc)
            return None

    @staticmethod
    def _scrape_direct_url(job: dict) -> str | None:
        """Re-scrape the job via python-jobspy to get job_url_direct.

        Only works for Indeed. Returns the direct ATS URL or None.
        """
        platform = (job.get("platform") or "").lower()
        if platform not in ("indeed",):
            return None

        title = job.get("title", "")
        company = job.get("company", "")
        if not company:
            return None

        try:
            from jobspy import scrape_jobs
        except ImportError:
            logger.debug("python-jobspy not installed — cannot scrape direct URL")
            return None

        search_term = f"{company} {title}".strip()
        logger.info("Scraping direct URL for %r via python-jobspy", search_term[:60])
        try:
            results = scrape_jobs(
                site_name=["indeed"],
                search_term=search_term,
                location="UK",
                results_wanted=5,
                country_indeed="UK",
            )
            for _, row in results.iterrows():
                row_company = (row.get("company") or "").strip().lower()
                if row_company and (company.lower() in row_company or row_company in company.lower()):
                    direct = row.get("job_url_direct") or ""
                    if direct:
                        logger.info("Scraped direct URL: %s → %s", company, direct[:60])
                        # Cache for future use
                        try:
                            from jobpulse.platform_bypass import get_platform_bypass
                            pb = get_platform_bypass()
                            pb._store_cached(company, direct, ats_platform="", strategy="live_scrape")
                        except Exception:
                            pass
                        return direct
        except Exception as exc:
            logger.warning("python-jobspy scrape failed: %s", exc)

        return None

    async def verify_submission(self) -> dict:
        """Wait for and verify the confirmation page after submit click."""
        await wait_for_page_stable(self.driver.page, timeout_ms=5000)
        snapshot = await self.driver.get_snapshot(force_refresh=True)
        if not snapshot:
            return {"verified": False, "reason": "no_snapshot"}
        snapshot = self._as_dict(snapshot)
        text = (snapshot.get("page_text_preview") or "").lower()

        # Success indicators
        success_patterns = [
            r"application.*(?:submitted|received|complete|sent)",
            r"thank\s*you\s*for\s*(?:applying|your\s*application)",
            r"we.ll\s*(?:be\s*in\s*touch|review|get\s*back)",
            r"application\s*(?:reference|confirmation|id)\s*[\w-]+",
            r"successfully\s*(?:applied|submitted)",
            r"you\s*(?:have\s*)?applied",
        ]
        for pat in success_patterns:
            if re.search(pat, text):
                return {"verified": True, "pattern": pat}

        # URL-based confirmation
        url = (snapshot.get("url") or "").lower()
        for path in ("/confirmation", "/thank-you", "/success", "/applied", "/complete"):
            if path in url:
                return {"verified": True, "url_match": path}

        # Error indicators (form rejected submission)
        error_patterns = [
            r"please\s*(?:fix|correct|review)\s*(?:the\s*)?(?:errors|fields)",
            r"required\s*field",
            r"there\s*(?:was|were)\s*(?:an?\s*)?error",
            r"submission\s*failed",
        ]
        for pat in error_patterns:
            if re.search(pat, text):
                return {"verified": False, "reason": "form_error", "pattern": pat}

        return {"verified": False, "reason": "unknown_state"}


# ── Module-level utilities ──

def extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.lower().removeprefix("www.") if parsed.netloc else url


def find_apply_button(snapshot: dict) -> dict | None:
    """Find the best apply button in a snapshot using unified scoring."""
    best: dict | None = None
    best_score = 0.0
    for btn in snapshot.get("buttons", []):
        if not btn.get("enabled"):
            continue
        score = score_apply_button(btn.get("text", ""))
        if score > best_score:
            best_score = score
            best = btn
    return best if best_score >= 0.4 else None
