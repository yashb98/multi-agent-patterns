"""3-agent journal summary pipeline: Extract → Write → Hallucination Guard."""

from __future__ import annotations

import json as _json
import re
from typing import Optional  # noqa: F401 — forward-reference for Tasks 22-25

import httpx

from jobpulse.papers.models import Paper
from research_journal.models import BenchResult, ExtractedFacts, VerificationBadge  # noqa: F401
from shared.logging_config import get_logger

logger = get_logger(__name__)


def extract_facts(paper: Paper) -> ExtractedFacts:
    """Pull structured facts from the paper PDF (or abstract fallback)."""
    pdf_text = _download_pdf_text(paper.pdf_url) if paper.pdf_url else ""
    if not pdf_text:
        logger.info("PDF unavailable for %s; falling back to abstract", paper.arxiv_id)
        pdf_text = paper.abstract
    return _llm_extract(pdf_text[:30_000])  # cap to avoid context overflow


def _download_pdf_text(url: str) -> str:
    if not url:
        return ""
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            content = r.content
    except Exception as exc:
        logger.warning("PDF download failed for %s: %s", url, exc)
        return ""
    try:
        from pypdf import PdfReader
        from io import BytesIO
        reader = PdfReader(BytesIO(content))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages[:30])
    except Exception as exc:
        logger.warning("PDF parse failed for %s: %s", url, exc)
        return ""


def _llm_extract(paper_text: str) -> ExtractedFacts:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt

    prompt = get_prompt("journal", "extract_facts")
    call_params = prompt.render(paper_text=paper_text)
    task = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in call_params["messages"]
    )
    raw = cognitive_llm_call(
        task=task,
        domain="journal_extract",
        stakes="medium",
        fallback_messages=call_params["messages"],
        response_format={"type": "json_object"},
    ) or "{}"
    data = _json.loads(_strip_codefence(raw))
    bench = [BenchResult(**b) if isinstance(b, dict) else b for b in data.get("benchmarks", [])]
    data["benchmarks"] = bench
    if not data.get("raw_excerpts"):
        data["raw_excerpts"] = ["[no excerpts extracted — guard will block ungrounded claims]"]
    return ExtractedFacts(**data)


def _strip_codefence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    return s.strip()


# ---------------------------------------------------------------------------
# Writer agent (Task 22)
# ---------------------------------------------------------------------------

_SECTION_HEADERS = ("TL;DR", "Problem", "Method", "Key insight", "Results", "Limitations")
_TARGETS = {"TL;DR": 50, "Problem": 200, "Method": 450, "Key insight": 100, "Results": 350, "Limitations": 100}
_TOLERANCE = 0.25


def write_summary(paper: Paper, facts: ExtractedFacts, max_attempts: int = 2) -> str:
    """Generate 1100-1300w summary with one regen on word-count violation."""
    last_md = ""
    for attempt in range(max_attempts):
        last_md = _llm_write(paper, facts)
        if _word_count_compliant(last_md):
            return last_md
        logger.info("write_summary attempt %d failed word-count check; retrying", attempt + 1)
    logger.warning("write_summary accepted off-target output after %d attempts", max_attempts)
    return last_md


def _llm_write(paper: Paper, facts: ExtractedFacts) -> str:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt

    prompt = get_prompt("journal", "write_summary")
    call_params = prompt.render(
        title=paper.title,
        authors=", ".join(paper.authors[:5]),
        facts_json=facts.model_dump_json(indent=2),
    )
    task = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in call_params["messages"]
    )
    return cognitive_llm_call(
        task=task,
        domain="journal_write",
        stakes="medium",
        fallback_messages=call_params["messages"],
    ) or ""


def _word_count_compliant(md: str) -> bool:
    sections = _split_sections(md)
    for header, target in _TARGETS.items():
        words = len(sections.get(header, "").split())
        lo, hi = int(target * (1 - _TOLERANCE)), int(target * (1 + _TOLERANCE))
        if not (lo <= words <= hi):
            return False
    return True


def _split_sections(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m and m.group(1).strip() in _SECTION_HEADERS:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out
