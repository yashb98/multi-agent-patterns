"""Reasoning-LLM field analyzer (cache-llm Step C).

The static field scanner (``field_scanner.py``) reads the a11y tree and
returns a ``label`` + ``type`` + ``options`` per field. It works for plain
HTML forms but misses dynamic widgets:

  - React Select (Greenhouse) — scanner sees ``combobox`` but ``options=[]``
    because the dropdown list is hidden until clicked.
  - Custom radio groups rendered as buttons — scanner sees ``button``,
    not the underlying choice semantic.
  - File uploads disguised as text fields — scanner sees ``text`` but the
    label says "Upload your CV".
  - Checkboxes labelled as questions — scanner sees ``checkbox`` but the
    nearby label is the prompt, not the choice.

This module sends the scanned form to a **reasoning** model
(``cognitive_llm_call`` with domain ``field_type_analysis``) and asks it
to correct the per-field metadata: true type, real options, fill method.
The result is cached per ``(domain, page_signature)`` so the same form
on the same domain incurs the analysis cost once and serves cache
thereafter.

Architecture (per the user's request during cache-llm-S8 step 3):

    DBs (profile, learned mappings, screening cache)
        → factual data (the WHAT)

    Reasoning LLM (this module)
        → decision per field (the HOW: type + options + fill_method)

    Deterministic semantic_matcher / form_engine
        → applies the decision (clicks, types, selects, uploads)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


_CACHE_TTL_DAYS = 14
_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Cache helpers (page-level, keyed by (domain, page_signature))
# ---------------------------------------------------------------------------


def _page_signature(fields: list[dict], page_text: str = "") -> str:
    """Stable fingerprint for the form. Same shape as PageReasoner's
    cache key so a form whose layout hasn't changed doesn't re-analyse.
    Order-independent on field labels."""
    labels = sorted((f.get("label", "") or "")[:60] for f in fields)
    types = sorted((f.get("type", "") or "") for f in fields)
    raw = json.dumps(
        {"labels": labels, "types": types, "text": (page_text or "")[:400]},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _cache_init(db) -> None:
    """Lazily create the analysis cache table inside applications.db."""
    conn = db._connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS field_analysis_cache ("
        "domain TEXT NOT NULL, page_signature TEXT NOT NULL, "
        "payload TEXT NOT NULL, generated_at TEXT NOT NULL, "
        "hit_count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (domain, page_signature))"
    )
    conn.commit()


def _cache_lookup(domain: str, page_sig: str, *, db=None) -> list[dict] | None:
    if not (domain and page_sig):
        return None
    if db is None and os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return None
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    with _CACHE_LOCK:
        _cache_init(db)
        conn = db._connect()
        row = conn.execute(
            "SELECT payload, generated_at FROM field_analysis_cache "
            "WHERE domain = ? AND page_signature = ?",
            (domain, page_sig),
        ).fetchone()
        if not row:
            return None
        try:
            generated = datetime.fromisoformat(row["generated_at"])
            if (datetime.now() - generated).days > _CACHE_TTL_DAYS:
                return None
            payload = json.loads(row["payload"])
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        conn.execute(
            "UPDATE field_analysis_cache SET hit_count = hit_count + 1 "
            "WHERE domain = ? AND page_signature = ?",
            (domain, page_sig),
        )
        conn.commit()
        return payload


def _cache_store(
    domain: str, page_sig: str, payload: list[dict], *, db=None,
) -> None:
    if not (domain and page_sig) or not payload:
        return
    if db is None and os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    with _CACHE_LOCK:
        _cache_init(db)
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO field_analysis_cache "
            "(domain, page_signature, payload, generated_at, hit_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (domain, page_sig, json.dumps(payload, ensure_ascii=False),
             datetime.now().isoformat()),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Prompt — system + user, few-shot, reasoning hints, strict JSON schema
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """\
You are an expert form-fill agent who analyses scanned web-form fields and
returns the **true** widget type, real options, and the correct fill method
per field. You receive the output of a static a11y-tree scanner that often
misses dynamic widgets — your job is to correct it using reasoning.

Return ONLY a JSON object matching this schema (no markdown fences, no prose):

{
  "fields": [
    {
      "label": "<exact label from input — must match input verbatim>",
      "true_type": "text | textarea | select | combobox | radio | checkbox | file | button | url | email | phone | tel | number | date",
      "options": ["..."],          // [] for free-text fields; full list for select/combobox/radio
      "fill_method": "fill | select | check_label | check_input | click_label | upload | skip",
      "reasoning": "<one short sentence explaining the decision>"
    }
  ]
}

Rules:
1. The output ``fields`` array MUST contain ONE entry per input field, in the
   same order. Do NOT add or drop fields. Do NOT rename labels.
2. ``options`` MUST be the actual choices the user can pick. For React Select
   / hidden dropdowns where the scanner reports ``options=[]`` but the label
   strongly implies a closed set (e.g. "Yes/No", country list, gender,
   sponsorship status), infer the options list. Keep the reasoning grounded.
3. ``fill_method`` semantics:
     - ``fill`` — type into a text/textarea input
     - ``select`` — open a dropdown and pick the matching option
     - ``check_label`` — click the label of a checkbox/radio group (preferred)
     - ``check_input`` — click the input directly (when label-click won't work)
     - ``click_label`` — click a labelled button/area (e.g. consent banner)
     - ``upload`` — file input requiring a path
     - ``skip`` — honeypot / decorative / pre-filled, do not touch
4. Think step by step before deciding each field's type:
     a. What does the LABEL imply? (a question → answer-as-text, or a Yes/No?)
     b. What did the SCANNER observe? (combobox, text, button, …)
     c. Does the scanner's options list match the question? If a Yes/No
        question reports options=[], FILL THEM IN ("Yes", "No").
     d. Is this a custom widget the scanner can't see (React Select,
        Material UI radio group, drag-and-drop upload)?
5. Common forms:
     - "Have you ever interviewed at X before?" → combobox, options=["Yes","No"], select
     - "Why X?" with no options → textarea, fill
     - "Upload your CV" → file, upload
     - "I agree to receive marketing" → checkbox (single, with check_label)
     - "Pronouns" with no options → combobox, options=["He/Him","She/Her","They/Them","Prefer not to say"], select
     - "Country" with no options → combobox, options=[full country list], select
6. Never invent labels. Never hallucinate fields not in the input.\
"""


def _few_shot_examples() -> str:
    """Inline few-shots demonstrating the rules with realistic Greenhouse / Workday / iCIMS shapes."""
    examples = [
        # 1. React Select with empty options (Greenhouse)
        {
            "input": [
                {"label": "Have you ever interviewed at Anthropic before?*",
                 "type": "combobox", "options": []},
            ],
            "output": {"fields": [
                {"label": "Have you ever interviewed at Anthropic before?*",
                 "true_type": "combobox",
                 "options": ["Yes", "No"],
                 "fill_method": "select",
                 "reasoning": "Yes/No question; scanner missed the React Select option list."},
            ]},
        },
        # 2. Free-text essay
        {
            "input": [
                {"label": "Why Anthropic?", "type": "textarea", "options": []},
            ],
            "output": {"fields": [
                {"label": "Why Anthropic?",
                 "true_type": "textarea",
                 "options": [],
                 "fill_method": "fill",
                 "reasoning": "Open-ended essay — free text input."},
            ]},
        },
        # 3. File upload
        {
            "input": [
                {"label": "Resume / CV", "type": "file", "options": []},
            ],
            "output": {"fields": [
                {"label": "Resume / CV",
                 "true_type": "file",
                 "options": [],
                 "fill_method": "upload",
                 "reasoning": "File input — needs a path, not text."},
            ]},
        },
        # 4. Consent checkbox (single)
        {
            "input": [
                {"label": "I agree to the privacy policy*", "type": "checkbox", "options": []},
            ],
            "output": {"fields": [
                {"label": "I agree to the privacy policy*",
                 "true_type": "checkbox",
                 "options": [],
                 "fill_method": "check_label",
                 "reasoning": "Mandatory consent checkbox — click the label."},
            ]},
        },
        # 5. Marketing opt-in (skip)
        {
            "input": [
                {"label": "Send me marketing updates", "type": "checkbox", "options": []},
            ],
            "output": {"fields": [
                {"label": "Send me marketing updates",
                 "true_type": "checkbox",
                 "options": [],
                 "fill_method": "skip",
                 "reasoning": "Optional marketing opt-in — leave unchecked per consent_policy."},
            ]},
        },
    ]
    return json.dumps(examples, ensure_ascii=False, indent=2)


def _build_user_prompt(fields: list[dict], page_text: str, url: str) -> str:
    fields_view = []
    for f in fields:
        fields_view.append({
            "label": f.get("label", ""),
            "type": f.get("type", ""),
            "options": (f.get("options") or [])[:30],
            "value": f.get("value", "")[:40] if isinstance(f.get("value"), str) else "",
            "required": bool(f.get("required")),
        })
    return (
        f"URL: {url}\n\n"
        f"PAGE TEXT (first 600 chars, for context):\n{(page_text or '')[:600]}\n\n"
        f"SCANNED FIELDS (output of the static scanner):\n"
        f"{json.dumps(fields_view, ensure_ascii=False, indent=2)}\n\n"
        f"REFERENCE EXAMPLES (the kind of correction expected):\n"
        f"{_few_shot_examples()}\n\n"
        f"Now correct the SCANNED FIELDS list. Return ONE JSON object with "
        f"a top-level 'fields' array, in the same order as the scan, with "
        f"every field's true_type / options / fill_method / reasoning filled."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_fields(
    fields: list[dict],
    *,
    url: str = "",
    page_text: str = "",
    db=None,
) -> list[dict]:
    """Analyse a scanned field list with a reasoning LLM.

    Returns an enriched copy of ``fields`` where each field gets a
    ``true_type`` / ``analyzed_options`` / ``fill_method`` / ``analyzer_reasoning``
    overlay. Falls back to the input list (with empty overlay fields) on
    LLM failure or cache miss + LLM unavailable — never raises.

    Cached per ``(domain, page_signature)``; the same form on the same
    domain pays the LLM cost once.
    """
    if not fields:
        return []

    domain = _domain_of(url)
    page_sig = _page_signature(fields, page_text)

    # 1. Cache lookup
    cached = _cache_lookup(domain, page_sig, db=db)
    if cached is not None:
        logger.info(
            "field_analyzer: cache hit on (%s, %s) — %d fields, skipping LLM",
            domain[:30], page_sig[:8], len(cached),
        )
        return _merge(fields, cached)

    # 2. LLM analyse — reasoning model via cognitive_llm_call(domain=field_type_analysis)
    try:
        from shared.agents import cognitive_llm_call
    except ImportError:
        return _attach_empty_overlay(fields)

    user_prompt = _build_user_prompt(fields, page_text, url)
    task = f"SYSTEM: {_SYSTEM_PROMPT}\n\nUSER: {user_prompt}"

    # Budget: each field's JSON entry costs ~80–120 tokens (label,
    # true_type, options array, fill_method, reasoning). 44 fields → ~5k
    # tokens, 100-field forms → ~12k. Ask for 16k so the response can't
    # truncate mid-array.
    out_budget = max(2000, 200 * len(fields))
    raw = cognitive_llm_call(
        task=task,
        domain="field_type_analysis",
        stakes="medium",
        response_format={"type": "json_object"},
        max_tokens=out_budget,
    )
    if not raw:
        logger.warning(
            "field_analyzer: LLM returned empty for (%s, %s) — %d fields, falling back",
            domain[:30], page_sig[:8], len(fields),
        )
        return _attach_empty_overlay(fields)

    try:
        parsed = json.loads(raw.strip())
        analyzed = parsed.get("fields", [])
        if not isinstance(analyzed, list) or len(analyzed) != len(fields):
            logger.warning(
                "field_analyzer: LLM returned %d fields, expected %d — falling back",
                len(analyzed) if isinstance(analyzed, list) else -1, len(fields),
            )
            return _attach_empty_overlay(fields)
    except json.JSONDecodeError as exc:
        logger.warning("field_analyzer: JSON parse failed: %s", exc)
        return _attach_empty_overlay(fields)

    # 3. Persist + return enriched
    _cache_store(domain, page_sig, analyzed, db=db)
    logger.info(
        "field_analyzer: analysed %d fields on %s — corrections logged "
        "(see field['analyzer_reasoning'])",
        len(analyzed), domain[:30],
    )
    return _merge(fields, analyzed)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attach_empty_overlay(fields: list[dict]) -> list[dict]:
    """Fall-through: return fields with empty analyser keys so downstream
    code can rely on the keys existing without a crash."""
    out = []
    for f in fields:
        new = dict(f)
        new.setdefault("true_type", new.get("type", ""))
        new.setdefault("analyzed_options", new.get("options", []) or [])
        new.setdefault("fill_method", "fill")
        new.setdefault("analyzer_reasoning", "(analyzer unavailable)")
        out.append(new)
    return out


def _merge(scanned: list[dict], analyzed: list[dict]) -> list[dict]:
    """Overlay LLM analysis onto the scanned field list. Preserves the
    scanner's keys; the analyser's keys (true_type, analyzed_options,
    fill_method, analyzer_reasoning) are additive — downstream code that
    reads ``f["type"]`` continues to work, but new code can read
    ``f["true_type"]`` and ``f["analyzed_options"]`` for the corrected
    metadata."""
    # Build by-label index so order mismatch (LLM occasionally re-orders)
    # doesn't corrupt the merge.
    by_label = {a.get("label", ""): a for a in analyzed if isinstance(a, dict)}
    out = []
    for f in scanned:
        new = dict(f)
        a = by_label.get(f.get("label", ""))
        if a:
            new["true_type"] = a.get("true_type", new.get("type", ""))
            new["analyzed_options"] = a.get("options") or new.get("options", []) or []
            new["fill_method"] = a.get("fill_method", "fill")
            new["analyzer_reasoning"] = a.get("reasoning", "")
            # If the scanner missed options but the analyzer found them,
            # promote them onto the canonical ``options`` key so existing
            # downstream code (semantic_matcher, OptionAligner) sees them.
            if not new.get("options") and new["analyzed_options"]:
                new["options"] = list(new["analyzed_options"])
        else:
            new["true_type"] = new.get("type", "")
            new["analyzed_options"] = new.get("options", []) or []
            new["fill_method"] = "fill"
            new["analyzer_reasoning"] = "(analyzer skipped this field)"
        out.append(new)
    return out
