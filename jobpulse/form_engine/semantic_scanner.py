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
