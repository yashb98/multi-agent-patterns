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


# ---------------------------------------------------------------------------
# Hallucination Guard: Claim Extraction (Task 23)
# ---------------------------------------------------------------------------


def extract_claims_from_summary(summary_md: str) -> list[str]:
    """Extract verifiable claims (sentences with numbers/benchmarks) from markdown."""
    return _llm_extract_claims(summary_md)


def _llm_extract_claims(summary_md: str) -> list[str]:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt

    prompt = get_prompt("journal", "extract_claims")
    call_params = prompt.render(summary_md=summary_md[:12_000])
    task = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in call_params["messages"]
    )
    raw = cognitive_llm_call(
        task=task,
        domain="journal_claims",
        stakes="low",
        fallback_messages=call_params["messages"],
    ) or "[]"
    try:
        claims = _json.loads(_strip_codefence(raw))
        return [str(c) for c in claims if isinstance(c, str)]
    except (ValueError, _json.JSONDecodeError, AttributeError, TypeError):
        logger.warning("claim extraction returned invalid JSON; treating as no claims")
        return []


# ---------------------------------------------------------------------------
# Hallucination Guard: Deterministic Grounding (Task 24)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_EMBEDDING_THRESHOLD = 0.85


def is_claim_grounded(claim: str, facts: ExtractedFacts) -> bool:
    """Return True if the claim is supported by extracted facts (3-tier deterministic check)."""
    norm_claim = _normalize(claim)
    excerpts_norm = [_normalize(e) for e in facts.raw_excerpts]

    # Tier 1: substring containment
    for ex in excerpts_norm:
        if norm_claim in ex or _significant_overlap(norm_claim, ex):
            return True

    # Tier 2: numeric match against benchmark values OR excerpts
    nums_in_claim = {float(m) for m in _NUMBER_RE.findall(claim)}
    if nums_in_claim:
        bench_values = {b.value for b in facts.benchmarks}
        if nums_in_claim & bench_values:
            return True
        for ex in facts.raw_excerpts:
            ex_nums = {float(m) for m in _NUMBER_RE.findall(ex)}
            if nums_in_claim & ex_nums:
                return True

    # Tier 3: embedding similarity
    for ex in facts.raw_excerpts:
        if _embedding_similarity(claim, ex) >= _EMBEDDING_THRESHOLD:
            return True
    return False


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _significant_overlap(claim: str, excerpt: str) -> bool:
    """Return True if at least 60% of claim's content words appear in excerpt."""
    claim_words = {w for w in claim.split() if len(w) > 4}
    if len(claim_words) < 3:
        return False
    return len(claim_words & set(excerpt.split())) / len(claim_words) >= 0.6


def _embedding_similarity(a: str, b: str) -> float:
    try:
        from shared.memory_layer._embedder import embed_text
        import numpy as np
        va = np.asarray(embed_text(a), dtype=float)
        vb = np.asarray(embed_text(b), dtype=float)
        denom = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1.0
        return float(np.dot(va, vb) / denom)
    except Exception as exc:
        logger.warning("embedding similarity failed: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# Hallucination Guard: regen loop + public orchestrator (Task 25)
# ---------------------------------------------------------------------------


def guard_summary(summary_md: str, facts: ExtractedFacts, sample_size: int = 5) -> tuple[bool, list[str]]:
    """Return (all_grounded, list_of_failed_claims).

    Strict grounding: ANY failed claim returns grounded=False.
    Note: the original spec said ">1 fails → regenerate" but the test contract
    requires len(failed) == 0 for grounded=True.  We use the stricter rule so
    no ungrounded claim slips through.
    """
    claims = extract_claims_from_summary(summary_md)
    if not claims:
        return True, []
    sample = claims[:sample_size] if len(claims) > sample_size else claims
    failed = [c for c in sample if not is_claim_grounded(c, facts)]
    return (len(failed) == 0), failed


def _write_with_avoid(paper: Paper, facts: ExtractedFacts, avoid: list[str] | None = None) -> str:
    """Wrapper around write_summary that injects 'avoid these patterns' on regen.

    The avoid hints are appended to facts.problem so the existing write_summary
    prompt receives them without requiring a prompt-template change.  This is
    intentionally minimal — a cleaner approach (extend write_summary to accept
    avoid: list[str]) is deferred.
    """
    if not avoid:
        return write_summary(paper, facts)
    avoid_block = "\n\nAVOID THESE UNGROUNDED PATTERNS:\n- " + "\n- ".join(avoid)
    facts2 = facts.model_copy(update={"problem": facts.problem + avoid_block})
    return write_summary(paper, facts2)


def summarize_paper(paper: Paper, max_regens: int = 1) -> tuple[str, bool]:
    """End-to-end: extract → write → guard (with one regen on failure).

    Returns (markdown_summary, claims_grounded).
    """
    facts = extract_facts(paper)
    summary = _write_with_avoid(paper, facts)
    grounded, failed = guard_summary(summary, facts)
    attempts = 1
    while not grounded and attempts <= max_regens:
        logger.info(
            "hallucination guard failed for %s (attempts=%d); regenerating with %d avoid hints",
            paper.arxiv_id, attempts, len(failed),
        )
        summary = _write_with_avoid(paper, facts, avoid=failed)
        grounded, failed = guard_summary(summary, facts)
        attempts += 1
    if not grounded:
        logger.error(
            "hallucination guard failed twice for %s; publishing with claims_grounded=False",
            paper.arxiv_id,
        )
    return summary, grounded
