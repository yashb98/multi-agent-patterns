"""Browser Intelligence — real-time signal capture from browser during form filling.

Attaches passive listeners (console, network, DOM mutations, CDP logs) to a
Playwright page. Captures validation errors, HTTP failures, and DOM alerts
into a ring buffer that the fill pipeline queries after each field interaction.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import ConsoleMessage, Page, Response

logger = get_logger(__name__)

_BUFFER_MAX = 50

_CONSOLE_NOISE = frozenset({
    "each child in a list should have",
    "warning: failed prop type",
    "[hmr]",
    "[wds]",
    "webpack",
    "hot update",
    "gtag",
    "analytics",
    "fbq(",
    "hotjar",
    "deprecated",
    "will be removed",
    "download the react devtools",
    "third-party cookie",
    "mixed content",
    "favicon.ico",
    "source map",
    "devtools",
})

_MUTATION_OBSERVER_JS = """() => {
    if (window.__bi_errors) return;
    window.__bi_errors = [];
    window.__bi_observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
            if (m.type === 'attributes' && m.attributeName === 'aria-invalid') {
                const val = m.target.getAttribute('aria-invalid');
                if (val === 'true') {
                    const label = m.target.getAttribute('aria-label')
                        || m.target.getAttribute('name')
                        || m.target.getAttribute('placeholder') || '';
                    window.__bi_errors.push({
                        type: 'aria_invalid',
                        text: 'Field marked invalid: ' + label,
                        label: label,
                        selector: m.target.tagName + (m.target.id ? '#' + m.target.id : ''),
                        ts: performance.now(),
                    });
                }
                continue;
            }
            for (const node of m.addedNodes) {
                if (node.nodeType !== 1) continue;
                const el = node;
                const isError = el.getAttribute('role') === 'alert'
                    || el.classList.contains('error')
                    || el.classList.contains('field-error')
                    || el.classList.contains('validation-error')
                    || el.classList.contains('invalid-feedback')
                    || el.className.toString().includes('error')
                    || el.closest('[aria-invalid="true"]');
                const text = (el.textContent || '').trim();
                if (isError && text.length > 0 && text.length < 500) {
                    const parent = el.closest(
                        '.form-group, .field-wrapper, [class*=field], [class*=form]'
                    );
                    let fieldLabel = '';
                    if (parent) {
                        const input = parent.querySelector('input, select, textarea');
                        if (input) {
                            fieldLabel = input.getAttribute('aria-label')
                                || input.getAttribute('name')
                                || input.getAttribute('placeholder') || '';
                        }
                    }
                    window.__bi_errors.push({
                        type: 'dom_error',
                        text: text,
                        label: fieldLabel,
                        selector: el.tagName + '.' + [...el.classList].join('.'),
                        ts: performance.now(),
                    });
                }
            }
        }
    });
    window.__bi_observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['aria-invalid', 'class'],
    });
}"""


@dataclass
class CapturedSignal:
    """A single captured browser signal."""

    source: str
    level: str
    text: str
    timestamp_ms: float
    url: str
    metadata: dict = field(default_factory=dict)


class BrowserIntelligence:
    """Passive browser signal capture for form-filling intelligence."""

    def __init__(self) -> None:
        self._page: Page | None = None
        self._buffer: deque[CapturedSignal] = deque(maxlen=_BUFFER_MAX)
        self._cdp: Any = None
        self._attached = False
        self._mutation_injected = False

    async def attach(self, page: Page) -> None:
        """Wire up all listeners on a Playwright page."""
        if self._attached:
            await self.detach()
        self._page = page
        self._attached = True

        page.on("console", self._on_console)
        page.on("response", self._on_response)

        try:
            self._cdp = await page.context.new_cdp_session(page)
            await self._cdp.send("Log.enable")
            self._cdp.on("Log.entryAdded", self._on_log_entry)
        except Exception as exc:
            logger.debug("CDP Log.enable failed (non-critical): %s", exc)
            self._cdp = None

        await self._inject_mutation_observer()
        logger.info("BrowserIntelligence attached — console + network + mutation + CDP log")

    async def detach(self) -> None:
        """Remove all listeners."""
        if not self._attached or not self._page:
            return
        try:
            self._page.remove_listener("console", self._on_console)
            self._page.remove_listener("response", self._on_response)
        except Exception:
            pass
        if self._cdp:
            try:
                await self._cdp.detach()
            except Exception:
                pass
            self._cdp = None
        self._attached = False
        self._mutation_injected = False
        logger.debug("BrowserIntelligence detached")

    def get_signals(self, since_ms: float | None = None) -> list[CapturedSignal]:
        """Return captured signals, optionally filtered by timestamp."""
        if since_ms is None:
            return list(self._buffer)
        return [s for s in self._buffer if s.timestamp_ms >= since_ms]

    def clear(self) -> None:
        """Flush the signal buffer (call between form pages)."""
        self._buffer.clear()
        self._mutation_injected = False

    async def poll_mutations(self) -> None:
        """Pull DOM mutation errors from the injected MutationObserver."""
        if not self._page or not self._mutation_injected:
            return
        try:
            errors = await self._page.evaluate("() => { const e = window.__bi_errors || []; window.__bi_errors = []; return e; }")
            now = time.monotonic() * 1000
            for err in errors:
                self._buffer.append(CapturedSignal(
                    source="mutation",
                    level="error",
                    text=err.get("text", ""),
                    timestamp_ms=now,
                    url=self._page.url,
                    metadata={
                        "mutation_type": err.get("type", ""),
                        "field_label": err.get("label", ""),
                        "selector": err.get("selector", ""),
                    },
                ))
        except Exception:
            pass

    async def inject_on_new_page(self) -> None:
        """Re-inject MutationObserver after page navigation."""
        if not self._page:
            return
        self._mutation_injected = False
        await self._inject_mutation_observer()

    async def _inject_mutation_observer(self) -> None:
        if self._mutation_injected or not self._page:
            return
        try:
            await self._page.evaluate(_MUTATION_OBSERVER_JS)
            self._mutation_injected = True
        except Exception as exc:
            logger.debug("MutationObserver injection failed: %s", exc)

    def _on_console(self, msg: ConsoleMessage) -> None:
        if msg.type not in ("error", "warning"):
            return
        text = msg.text
        text_lower = text.lower()
        if any(noise in text_lower for noise in _CONSOLE_NOISE):
            return
        if len(text) < 3:
            return
        self._buffer.append(CapturedSignal(
            source="console",
            level=msg.type,
            text=text[:1000],
            timestamp_ms=time.monotonic() * 1000,
            url=getattr(self._page, "url", ""),
            metadata={},
        ))

    def _on_response(self, response: Response) -> None:
        try:
            method = response.request.method
        except Exception:
            return
        if method not in ("POST", "PUT", "PATCH"):
            return
        if response.status < 400:
            return
        # response.text() is async in Playwright; this is a sync event handler,
        # so body fetch would return an un-awaitable coroutine. Downstream
        # consumers (signal_interpreter) only read status_code + source, so
        # capture metadata and leave text empty rather than crash on slicing.
        self._buffer.append(CapturedSignal(
            source="network",
            level="error",
            text="",
            timestamp_ms=time.monotonic() * 1000,
            url=response.url,
            metadata={
                "status_code": response.status,
                "method": method,
            },
        ))

    def _on_log_entry(self, params: dict) -> None:
        entry = params.get("entry", {})
        level = entry.get("level", "")
        if level not in ("error", "warning"):
            return
        text = entry.get("text", "")
        if not text or len(text) < 3:
            return
        text_lower = text.lower()
        if any(noise in text_lower for noise in _CONSOLE_NOISE):
            return
        self._buffer.append(CapturedSignal(
            source="browser_log",
            level=level,
            text=text[:1000],
            timestamp_ms=time.monotonic() * 1000,
            url=entry.get("url", ""),
            metadata={},
        ))
