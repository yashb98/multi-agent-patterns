"""arXiv Research Agent — fetches daily papers, ranks by relevance, summarizes top 5.

Replaces the old scripts/arxiv-daily.sh (which used claude -p).
Uses arXiv API directly — no dependencies, no extra cost for fetching.
LLM cost: ~$0.01/day for ranking + summarizing.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from shared.logging_config import get_logger
from jobpulse.config import OPENAI_API_KEY, PROJECT_DIR
from jobpulse import telegram_agent, event_logger

logger = get_logger(__name__)

# Categories to scan
CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.MA", "stat.ML"]

# Keywords and weights for relevance scoring (fast filter)
INTEREST_WEIGHTS = {
    "multi-agent": 3.0, "multi agent": 3.0, "orchestration": 3.0,
    "llm agent": 3.0, "language model agent": 3.0, "agentic": 2.5,
    "reinforcement learning": 2.5, "grpo": 3.0, "rlhf": 2.0,
    "prompt optimization": 2.5, "prompt tuning": 2.0,
    "knowledge graph": 2.5, "graph rag": 3.0, "graphrag": 3.0,
    "rag": 2.0, "retrieval augmented": 2.0,
    "reasoning": 2.0, "chain of thought": 2.0, "tree of thought": 2.5,
    "tool use": 2.5, "function calling": 2.0,
    "code generation": 2.0, "code agent": 2.5,
    "fine-tuning": 1.5, "fine tuning": 1.5,
    "transformer": 1.0, "attention": 1.0,
    "swarm": 3.0, "persona": 2.0, "self-improving": 2.5,
    "evaluation": 1.5, "benchmark": 1.0,
    "mixture of experts": 2.0, "moe": 2.0,
    "recursive": 2.0, "self-play": 2.0,
}


def fetch_papers(max_results: int = 150) -> list[dict]:
    """Fetch recent papers from arXiv API. Returns parsed paper dicts."""
    import httpx

    cat_query = "+OR+".join(f"cat:{c}" for c in CATEGORIES)
    url = (
        f"http://export.arxiv.org/api/query?"
        f"search_query={cat_query}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&start=0&max_results={max_results}"
    )

    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.error("arXiv API error: %s", e)
        return []

    # Parse Atom XML
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        logger.error("arXiv XML parse error: %s", e)
        return []

    papers = []
    for entry in root.findall("atom:entry", ns):
        title = entry.findtext("atom:title", "", ns).strip().replace("\n", " ")
        abstract = entry.findtext("atom:summary", "", ns).strip().replace("\n", " ")
        arxiv_id_url = entry.findtext("atom:id", "", ns)
        arxiv_id = arxiv_id_url.split("/abs/")[-1] if "/abs/" in arxiv_id_url else arxiv_id_url
        published = entry.findtext("atom:published", "", ns)

        authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]

        categories = [c.get("term", "") for c in entry.findall("atom:category", ns)]

        pdf_url = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")

        papers.append({
            "title": title,
            "abstract": abstract,
            "arxiv_id": arxiv_id,
            "authors": authors[:5],
            "categories": categories,
            "published": published,
            "pdf_url": pdf_url,
            "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
        })

    logger.info("Fetched %d papers from arXiv", len(papers))
    return papers


def fast_score(paper: dict) -> float:
    """Score paper by keyword match — no LLM, instant."""
    text = (paper["title"] + " " + paper["abstract"]).lower()
    score = 0.0

    for keyword, weight in INTEREST_WEIGHTS.items():
        if keyword in text:
            score += weight

    # Category bonus
    if "cs.MA" in paper["categories"]:
        score += 2.0
    if "cs.AI" in paper["categories"]:
        score += 0.5

    return score


def llm_rank(papers: list[dict], top_n: int = 5) -> list[dict]:
    """Use LLM to rank top candidates by relevance. Returns top_n with scores + reasons."""
    if not OPENAI_API_KEY:
        # No LLM available — return by fast score
        return papers[:top_n]

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    # Build paper list for LLM
    paper_texts = []
    for i, p in enumerate(papers):
        paper_texts.append(
            f"{i+1}. \"{p['title']}\"\n"
            f"   Categories: {', '.join(p['categories'][:3])}\n"
            f"   Abstract: {p['abstract'][:300]}"
        )

    prompt = f"""You are ranking arXiv papers for a researcher working on:
- Multi-agent orchestration (LangGraph, GRPO, persona evolution)
- Knowledge graphs (GraphRAG, entity extraction)
- LLM agents (tool use, prompt optimization)
- RAG architecture

Rank these {len(papers)} papers by relevance (most relevant first).
For the top {top_n}, explain in ONE sentence why it's relevant.

Papers:
{chr(10).join(paper_texts)}

Return ONLY a JSON array of the top {top_n}:
[{{"rank": 1, "paper_num": X, "score": 0-10, "reason": "..."}}]"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]

        rankings = __import__("json").loads(raw)

        # Map back to papers
        ranked = []
        for r in rankings[:top_n]:
            idx = r.get("paper_num", 1) - 1
            if 0 <= idx < len(papers):
                papers[idx]["llm_score"] = r.get("score", 5)
                papers[idx]["relevance_reason"] = r.get("reason", "")
                ranked.append(papers[idx])

        return ranked

    except Exception as e:
        logger.warning("LLM ranking failed: %s — using fast scores", e)
        return papers[:top_n]


def summarize_paper(paper: dict) -> str:
    """Generate a concise summary + actionable takeaway."""
    if not OPENAI_API_KEY:
        return paper["abstract"][:200]

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""Summarize this paper in 2-3 sentences.
Then add ONE sentence: "You could use this: ..." explaining how a developer
building multi-agent systems with LangGraph could apply this technique.

Title: {paper['title']}
Abstract: {paper['abstract'][:800]}"""}],
            max_tokens=200,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Summary failed for %s: %s", paper["arxiv_id"], e)
        return paper["abstract"][:200]


def build_digest(top_n: int = 5) -> str:
    """Full pipeline: fetch → fast score → LLM rank → summarize → format."""
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("arxiv_agent", "daily_digest")

    # Step 1: Fetch
    with trail.step("api_call", "Fetch papers from arXiv") as s:
        papers = fetch_papers(max_results=150)
        s["output"] = f"Fetched {len(papers)} papers"
        if not papers:
            trail.finalize("No papers fetched")
            return "Could not fetch papers from arXiv. Try again later."

    # Step 2: Fast score
    with trail.step("decision", "Fast keyword scoring") as s:
        for p in papers:
            p["fast_score"] = fast_score(p)
        papers.sort(key=lambda p: p["fast_score"], reverse=True)
        top_candidates = [p for p in papers[:20] if p["fast_score"] > 0]
        s["output"] = f"Top 20 candidates from {len(papers)} papers"
        s["metadata"] = {"candidates": len(top_candidates)}

    if not top_candidates:
        trail.finalize("No relevant papers found")
        return "No papers matching your interests today."

    # Step 3: LLM rank top 20 → pick top N
    with trail.step("llm_call", f"LLM ranking top {len(top_candidates)} candidates") as s:
        ranked = llm_rank(top_candidates, top_n=top_n)
        s["output"] = f"Selected {len(ranked)} papers"

    # Step 4: Summarize each
    summaries = []
    for i, paper in enumerate(ranked):
        with trail.step("llm_call", f"Summarize paper {i+1}",
                         step_input=paper["title"][:100]) as s:
            summary = summarize_paper(paper)
            summaries.append((paper, summary))
            s["output"] = summary[:100]

    # Step 5: Format
    today = datetime.now().strftime("%B %d, %Y")
    lines = [f"📚 AI RESEARCH DIGEST — {today}\n"]

    for i, (paper, summary) in enumerate(summaries, 1):
        score = paper.get("llm_score", paper.get("fast_score", 0))
        score_label = "STRONG MATCH" if score >= 8 else "HIGH MATCH" if score >= 6 else "MATCH"
        authors = ", ".join(paper["authors"][:3])
        if len(paper["authors"]) > 3:
            authors += " et al."
        cats = ", ".join(paper["categories"][:3])

        lines.append(f"━━━━━━━━━━━━━━━━━━━━\n")
        lines.append(f"{i}️⃣ {score_label} ({score:.0f}/10)")
        lines.append(f"\"{paper['title']}\"")
        lines.append(f"Authors: {authors}")
        lines.append(f"🏷️ {cats}\n")
        lines.append(f"{summary}\n")
        lines.append(f"🔗 {paper['arxiv_url']}")

    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 Scanned: {len(papers)} papers | Matched: {len(top_candidates)} | Shown: {len(summaries)}")

    digest = "\n".join(lines)

    # Step 6: Extract to knowledge graph
    with trail.step("extraction", "Extract papers to knowledge graph") as s:
        extracted = 0
        for paper, summary in summaries:
            try:
                from jobpulse.auto_extract import extract_from_paper_summary
                extract_from_paper_summary(
                    title=paper["title"],
                    authors=", ".join(paper["authors"][:3]),
                    summary=summary,
                    arxiv_id=paper["arxiv_id"],
                )
                extracted += 1
            except Exception:
                pass
        s["output"] = f"Extracted {extracted}/{len(summaries)} papers"

    # Log event
    event_logger.log_event(
        event_type="research_paper",
        agent_name="arxiv_agent",
        action="daily_digest",
        content=digest[:500],
        metadata={"papers_scanned": len(papers), "papers_shown": len(summaries)},
    )

    trail.finalize(f"Digest: {len(summaries)} papers from {len(papers)} scanned")
    return digest


def send_daily_digest(trigger: str = "cron_morning"):
    """Build digest and send to Telegram."""
    digest = build_digest()
    success = telegram_agent.send_message(digest)
    logger.info("arXiv digest %s (%d chars)", "sent" if success else "FAILED", len(digest))
    return success
