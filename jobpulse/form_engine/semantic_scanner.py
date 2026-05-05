"""Semantic-first form scanner.

Reads the visible page text and identifies form questions, then matches
each question to the nearest interactive widget. Complements the
shape-based detectors in field_scanner.py — catches questions whose
widget is a custom React component the shape detectors don't recognize.

Three pieces:
    1. extract_visible_questions(page) -> list[Question]
    2. match_question_to_widget(question, page) -> Widget | None
    3. classify_widget(meta) -> str
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


# A question is text that:
#   - Ends with '?', OR
#   - Starts with: Are/Do/Did/Have/Will you, What is/are/were, How long/many,
#     Which, Where, When, Why, Please describe, Tell us
QUESTION_STARTERS = re.compile(
    r"^(are\s+you|do\s+you|did\s+you|have\s+you|will\s+you|"
    r"what\s+(is|was|are|were)|how\s+(long|many|much|often)|"
    r"which\s+|where\s+|when\s+|why\s+|please\s+(describe|provide|tell|share)|"
    r"tell\s+us)\b",
    re.IGNORECASE,
)

# Buttons / nav / non-question phrases
NON_QUESTION_PHRASES = re.compile(
    r"^(apply|submit|next|back|continue|review|save|go to|learn more|"
    r"click\s+here|upload|sign\s+(in|up)|log\s+(in|out)|return|cancel)\b",
    re.IGNORECASE,
)

# Field labels (without surrounding question context) — short, no verb
FIELD_LABEL_HEURISTIC = re.compile(
    r"^(first|last|full|preferred)\s*name$|"
    r"^email(\s+address)?$|"
    r"^phone(\s+number)?$|"
    r"^(post|zip)\s*code$|"
    r"^address(\s+line\s+\d)?$",
    re.IGNORECASE,
)

MIN_QUESTION_LEN = 12
MAX_QUESTION_LEN = 500


@dataclass
class Question:
    text: str
    y: int
    dom_path: str


def _is_question_shaped(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < MIN_QUESTION_LEN or len(s) > MAX_QUESTION_LEN:
        return False
    if NON_QUESTION_PHRASES.match(s):
        return False
    if FIELD_LABEL_HEURISTIC.match(s):
        return False
    if s.endswith("?"):
        return True
    if QUESTION_STARTERS.match(s):
        return True
    return False


async def extract_visible_questions(page: Any) -> list[Question]:
    """Walk every visible text node, return question-shaped fragments."""
    raw = await page.evaluate(
        """() => {
            const out = [];
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT,
                { acceptNode: n => {
                    const p = n.parentElement;
                    if (!p || p.offsetParent === null) return NodeFilter.FILTER_REJECT;
                    const t = (n.textContent || '').trim();
                    if (t.length < 8 || t.length > 500) return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                }},
            );
            function cssPath(el) {
                const parts = [];
                let n = el;
                for (let i = 0; n && n.nodeType === 1 && i < 6; i++, n = n.parentElement) {
                    let p = n.tagName.toLowerCase();
                    if (n.id) { p += '#' + n.id; parts.unshift(p); break; }
                    parts.unshift(p);
                }
                return parts.join(' > ');
            }
            let n;
            while (n = walker.nextNode()) {
                const p = n.parentElement;
                const r = p.getBoundingClientRect();
                out.push({
                    text: (n.textContent || '').trim(),
                    y: Math.round(r.top + window.scrollY),
                    dom_path: cssPath(p),
                });
            }
            return out;
        }"""
    )
    seen: set[str] = set()
    qs: list[Question] = []
    for item in (raw or []):
        text = (item.get("text") or "").strip()
        if text in seen:
            continue
        if not _is_question_shaped(text):
            continue
        seen.add(text)
        qs.append(Question(
            text=text,
            y=int(item.get("y") or 0),
            dom_path=item.get("dom_path") or "",
        ))
    return qs


async def match_question_to_widget(
    question: Question, page: Any
) -> dict | None:
    """Find the nearest interactive element to a question.

    Two-tier search:
      1. Ancestor match — find the question's text node, walk up to a
         <fieldset>/<section>/[role=group]. If that ancestor contains an
         interactive element (input/select/textarea/[role]), use it.
      2. Pixel proximity — visible interactive element within 400px below
         the question's bounding box. Tie-break by smallest distance.

    Returns a dict with selector + match metadata, or None.
    """
    result = await page.evaluate(
        """(args) => {
            const { questionText, questionY } = args;

            // 1. Ancestor match
            const xpath = `//*[contains(text(), ${JSON.stringify(questionText.slice(0, 50))})]`;
            const all = document.evaluate(
                xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null,
            ).singleNodeValue;

            function widgetIn(scope) {
                return scope.querySelector(
                    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]),' +
                    'select, textarea,' +
                    '[role="combobox"], [role="switch"], [role="radio"],' +
                    '[role="checkbox"], [role="listbox"], [role="button"][aria-haspopup]'
                );
            }

            function selectorOf(el) {
                if (!el) return '';
                if (el.id) return `#${el.id}`;
                if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
                if (el.getAttribute('data-qa')) return `[data-qa="${el.getAttribute('data-qa')}"]`;
                const parts = [];
                let n = el;
                for (let i = 0; n && n.nodeType === 1 && i < 5; i++, n = n.parentElement) {
                    let p = n.tagName.toLowerCase();
                    if (n.className && typeof n.className === 'string') {
                        const cls = n.className.split(/\\s+/).filter(c => c).slice(0, 2).join('.');
                        if (cls) p += '.' + cls;
                    }
                    parts.unshift(p);
                }
                return parts.join(' > ');
            }

            if (all) {
                let scope = all.parentElement;
                for (let i = 0; scope && i < 4; i++, scope = scope.parentElement) {
                    if (['FIELDSET', 'SECTION'].includes(scope.tagName) ||
                        ['group', 'region'].includes(scope.getAttribute('role'))) {
                        const w = widgetIn(scope);
                        if (w && w.offsetParent !== null) {
                            return {
                                matched: true,
                                y: w.getBoundingClientRect().top + window.scrollY,
                                tag: w.tagName,
                                role: w.getAttribute('role') || '',
                                aria_haspopup: w.getAttribute('aria-haspopup') || '',
                                aria_pressed: w.getAttribute('aria-pressed'),
                                aria_checked: w.getAttribute('aria-checked'),
                                selector: selectorOf(w),
                                ancestor_classes: scope.className || '',
                                match_kind: 'ancestor',
                                distance_px: 0,
                            };
                        }
                    }
                }
            }

            // 2. Pixel proximity within 400px below
            const candidates = [...document.querySelectorAll(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]),' +
                'select, textarea,' +
                '[role="combobox"], [role="switch"], [role="radio"],' +
                '[role="checkbox"], [role="listbox"]'
            )].filter(el => el.offsetParent !== null);

            let best = null;
            for (const w of candidates) {
                const r = w.getBoundingClientRect();
                const wy = r.top + window.scrollY;
                const dy = wy - questionY;
                if (dy < 0 || dy > 400) continue;
                if (!best || dy < best.distance_px) {
                    best = {
                        matched: true,
                        y: wy,
                        tag: w.tagName,
                        role: w.getAttribute('role') || '',
                        aria_haspopup: w.getAttribute('aria-haspopup') || '',
                        aria_pressed: w.getAttribute('aria-pressed'),
                        aria_checked: w.getAttribute('aria-checked'),
                        selector: selectorOf(w),
                        ancestor_classes: (w.parentElement && w.parentElement.className) || '',
                        match_kind: 'proximity',
                        distance_px: dy,
                    };
                }
            }
            return best || { matched: false };
        }""",
        {"questionText": question.text, "questionY": question.y},
    )
    if not result or not result.get("matched"):
        return None
    return result


def classify_widget(meta: dict) -> str:
    """Map the matched element's tag/role/aria to a fill-handler input_type.

    Returns one of: text, textarea, select, combobox, switch, radio_group,
    checkbox. The dispatcher in NativeFormFiller._fill_by_label has handlers
    for all of these.
    """
    tag = (meta.get("tag") or "").upper()
    role = (meta.get("role") or "").lower()
    haspopup = (meta.get("aria_haspopup") or "").lower()

    if role == "switch":
        return "switch"
    if role == "checkbox":
        return "checkbox"
    if role == "radio":
        return "radio_group"
    if role == "combobox":
        return "combobox"
    if role == "listbox":
        return "combobox"
    if tag == "SELECT":
        return "select"
    if tag == "TEXTAREA":
        return "textarea"
    if tag == "BUTTON" and haspopup in ("listbox", "true", "menu"):
        return "combobox"
    if tag == "INPUT":
        return "text"
    return "text"
