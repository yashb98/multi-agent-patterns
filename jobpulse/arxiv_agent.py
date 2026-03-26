"""arXiv Research Agent — daily top 5 AI papers, broadly ranked by impact.

Scans cs.AI, cs.LG, cs.CL, cs.MA, stat.ML for the most impactful papers
of the day. Ranks by BROAD AI significance (not just your projects).
Summarizes with practical takeaways. Tracks in SQLite + Notion.

Cost: ~$0.02/day for ranking + summarizing.
"""

import re
import json
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from shared.logging_config import get_logger
from jobpulse.config import OPENAI_API_KEY, PROJECT_DIR, DATA_DIR
from jobpulse import telegram_agent, event_logger

logger = get_logger(__name__)

# Categories to scan
CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.MA", "stat.ML"]

DB_PATH = DATA_DIR / "papers.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            arxiv_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            authors TEXT NOT NULL,
            abstract TEXT NOT NULL,
            categories TEXT NOT NULL,
            pdf_url TEXT DEFAULT '',
            arxiv_url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            impact_score REAL DEFAULT 0,
            impact_reason TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            key_technique TEXT DEFAULT '',
            practical_takeaway TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            digest_date TEXT DEFAULT '',
            discovered_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);
        CREATE INDEX IF NOT EXISTS idx_papers_date ON papers(digest_date);
        CREATE INDEX IF NOT EXISTS idx_papers_score ON papers(impact_score DESC);
    """)
    conn.commit()
    conn.close()


_init_db()


# ── Fetching ──

def fetch_papers(max_results: int = 200) -> list[dict]:
    """Fetch recent papers from arXiv API."""
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


# ── Ranking (Broad AI Impact) ──

def llm_rank_broad(papers: list[dict], top_n: int = 5) -> list[dict]:
    """Rank papers by BROAD AI impact — not project-specific.

    Focuses on: novelty, significance, practical applicability,
    and how much the AI community would care about it.
    """
    if not OPENAI_API_KEY:
        return papers[:top_n]

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    # Send top 30 by recency (most recent = most likely to be today's papers)
    candidates = papers[:30]

    paper_texts = []
    for i, p in enumerate(candidates):
        paper_texts.append(
            f"{i+1}. \"{p['title']}\"\n"
            f"   Categories: {', '.join(p['categories'][:3])}\n"
            f"   Abstract: {p['abstract'][:400]}"
        )

    prompt = f"""You are an AI research curator for a daily digest. Your audience is
AI/ML engineers and researchers who want to stay on top of the field.

From these {len(candidates)} recent arXiv papers, pick the TOP {top_n} most impactful.

Rank by:
1. NOVELTY — introduces a genuinely new idea, architecture, or technique
2. SIGNIFICANCE — could change how people build AI systems
3. PRACTICAL VALUE — useful for practitioners, not just theoretical
4. BREADTH — relevant to many subfields of AI, not just one niche

Avoid: survey papers, minor incremental improvements, dataset-only papers.
Prefer: breakthrough techniques, new architectures, surprising results, open-source releases.

Papers:
{chr(10).join(paper_texts)}

Return ONLY a JSON array:
[{{"rank": 1, "paper_num": X, "score": 0-10, "reason": "One sentence on why this matters to AI", "key_technique": "The main technique or contribution in 5 words", "category_tag": "e.g. LLM, Agents, Vision, RL, Efficiency, Safety, Reasoning"}}]"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]

        rankings = json.loads(raw)

        ranked = []
        for r in rankings[:top_n]:
            idx = r.get("paper_num", 1) - 1
            if 0 <= idx < len(candidates):
                candidates[idx]["impact_score"] = r.get("score", 5)
                candidates[idx]["impact_reason"] = r.get("reason", "")
                candidates[idx]["key_technique"] = r.get("key_technique", "")
                candidates[idx]["category_tag"] = r.get("category_tag", "")
                ranked.append(candidates[idx])

        return ranked

    except Exception as e:
        logger.warning("LLM ranking failed: %s", e)
        return candidates[:top_n]


def summarize_paper(paper: dict) -> str:
    """Generate a summary with: what it does, why it matters, practical takeaway."""
    if not OPENAI_API_KEY:
        return paper["abstract"][:200]

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""Summarize this AI paper for a practitioner in 3-4 sentences:

1. WHAT: What does the paper propose/discover? (1 sentence)
2. WHY: Why does this matter for the AI field? (1 sentence)
3. HOW: Key technical insight or method (1 sentence)
4. USE: One practical way someone could apply this today (1 sentence, start with "Practical takeaway:")

Title: {paper['title']}
Abstract: {paper['abstract'][:1000]}"""}],
            max_tokens=250,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Summary failed for %s: %s", paper["arxiv_id"], e)
        return paper["abstract"][:300]


# ── Paper Storage ──

def store_papers(papers: list[dict], digest_date: str):
    """Store ranked papers in SQLite."""
    conn = _get_conn()
    for p in papers:
        conn.execute(
            "INSERT OR REPLACE INTO papers (arxiv_id, title, authors, abstract, categories, "
            "pdf_url, arxiv_url, published_at, impact_score, impact_reason, summary, "
            "key_technique, practical_takeaway, status, digest_date, discovered_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                p["arxiv_id"], p["title"], json.dumps(p["authors"]),
                p["abstract"], json.dumps(p["categories"]),
                p.get("pdf_url", ""), p["arxiv_url"], p.get("published", ""),
                p.get("impact_score", 0), p.get("impact_reason", ""),
                p.get("summary", ""), p.get("key_technique", ""),
                p.get("practical_takeaway", ""),
                "sent", digest_date, datetime.now().isoformat(),
            )
        )
    conn.commit()
    conn.close()


def get_paper_by_index(digest_date: str, index: int) -> dict | None:
    """Get a paper from today's digest by its display number (1-5)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM papers WHERE digest_date=? ORDER BY impact_score DESC",
        (digest_date,)
    ).fetchall()
    conn.close()
    if 1 <= index <= len(rows):
        return dict(rows[index - 1])
    return None


def mark_as_read(arxiv_id: str):
    """Mark a paper as read."""
    conn = _get_conn()
    conn.execute("UPDATE papers SET status='read' WHERE arxiv_id=?", (arxiv_id,))
    conn.commit()
    conn.close()


def get_reading_stats() -> dict:
    """Get paper reading statistics."""
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    read = conn.execute("SELECT COUNT(*) FROM papers WHERE status='read'").fetchone()[0]
    this_week = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE discovered_at >= date('now', '-7 days')"
    ).fetchone()[0]
    conn.close()
    return {"total": total, "read": read, "unread": total - read, "this_week": this_week}


# ── Digest Builder ──

def build_digest(top_n: int = 5) -> str:
    """Full pipeline: fetch -> LLM rank by broad AI impact -> summarize -> format."""
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("arxiv_agent", "daily_digest")
    today = datetime.now().strftime("%Y-%m-%d")

    # Step 1: Fetch
    with trail.step("api_call", "Fetch papers from arXiv") as s:
        papers = fetch_papers(max_results=200)
        s["output"] = f"Fetched {len(papers)} papers"
        if not papers:
            trail.finalize("No papers fetched")
            return "Could not fetch papers from arXiv. Try again later."

    # Step 2: LLM rank by BROAD AI IMPACT
    with trail.step("llm_call", "LLM ranking by broad AI impact") as s:
        ranked = llm_rank_broad(papers, top_n=top_n)
        s["output"] = f"Selected {len(ranked)} papers"

    # Step 3: Summarize each
    summaries = []
    for i, paper in enumerate(ranked):
        with trail.step("llm_call", f"Summarize paper {i+1}",
                         step_input=paper["title"][:100]) as s:
            summary = summarize_paper(paper)
            paper["summary"] = summary
            summaries.append((paper, summary))
            s["output"] = summary[:100]

    # Step 4: Store in database
    with trail.step("api_call", "Store papers in database") as s:
        store_papers(ranked, today)
        s["output"] = f"Stored {len(ranked)} papers"

    # Step 5: Format
    date_display = datetime.now().strftime("%B %d, %Y")
    lines = [f"📚 TOP 5 AI PAPERS — {date_display}\n"]

    for i, (paper, summary) in enumerate(summaries, 1):
        score = paper.get("impact_score", 0)
        tag = paper.get("category_tag", "AI")
        technique = paper.get("key_technique", "")
        authors = ", ".join(paper["authors"][:3])
        if len(paper["authors"]) > 3:
            authors += " et al."
        cats = ", ".join(paper["categories"][:3])

        lines.append(f"━━━━━━━━━━━━━━━━━━━━\n")
        lines.append(f"{i}. [{tag}] ({score:.0f}/10)")
        lines.append(f"\"{paper['title']}\"")
        lines.append(f"Authors: {authors}")
        if technique:
            lines.append(f"Key: {technique}")
        lines.append(f"")
        lines.append(f"{summary}\n")
        lines.append(f"PDF: {paper.get('pdf_url', paper['arxiv_url'])}")

    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Scanned {len(papers)} papers from {', '.join(CATEGORIES)}")
    lines.append(f"\nCommands: \"paper 3\" for full abstract | \"read 1\" to mark as read | \"papers stats\" for reading stats")

    digest = "\n".join(lines)

    event_logger.log_event(
        event_type="research_paper",
        agent_name="arxiv_agent",
        action="daily_digest",
        content=digest[:500],
        metadata={"papers_scanned": len(papers), "papers_shown": len(summaries)},
    )

    trail.finalize(f"Digest: {len(summaries)} papers from {len(papers)} scanned")

    # Extract to knowledge graph in background (don't block the reply)
    import threading
    def _extract_bg():
        for paper, summary in summaries:
            try:
                from jobpulse.auto_extract import extract_from_paper_summary
                extract_from_paper_summary(
                    title=paper["title"],
                    authors=", ".join(paper["authors"][:3]),
                    summary=summary,
                    arxiv_id=paper["arxiv_id"],
                )
            except Exception:
                pass
        logger.info("KG extraction complete for %d papers", len(summaries))

    threading.Thread(target=_extract_bg, daemon=True).start()

    return digest


def get_paper_detail(index: int) -> str:
    """Get full abstract for paper N from today's digest."""
    today = datetime.now().strftime("%Y-%m-%d")
    paper = get_paper_by_index(today, index)
    if not paper:
        return f"No paper #{index} in today's digest. Say \"papers\" to get today's digest first."

    authors = json.loads(paper["authors"]) if isinstance(paper["authors"], str) else paper["authors"]
    authors_str = ", ".join(authors[:5])
    cats = json.loads(paper["categories"]) if isinstance(paper["categories"], str) else paper["categories"]

    return (f"📄 Paper #{index}: {paper['title']}\n\n"
            f"Authors: {authors_str}\n"
            f"Categories: {', '.join(cats[:5])}\n"
            f"Score: {paper['impact_score']:.0f}/10\n"
            f"Key: {paper.get('key_technique', '-')}\n\n"
            f"ABSTRACT:\n{paper['abstract']}\n\n"
            f"PDF: {paper.get('pdf_url', paper['arxiv_url'])}\n"
            f"arXiv: {paper['arxiv_url']}\n\n"
            f"Reply \"read {index}\" to mark as read.")


def mark_paper_read(index: int) -> str:
    """Mark paper N as read and extract to knowledge graph."""
    today = datetime.now().strftime("%Y-%m-%d")
    paper = get_paper_by_index(today, index)
    if not paper:
        return f"No paper #{index} in today's digest."

    mark_as_read(paper["arxiv_id"])

    return (f"✅ Marked as read: \"{paper['title'][:60]}...\"\n\n"
            f"Reading stats: {json.dumps(get_reading_stats())}")


def get_stats_text() -> str:
    """Get formatted reading stats."""
    stats = get_reading_stats()
    return (f"📊 PAPER READING STATS:\n\n"
            f"  Total papers tracked: {stats['total']}\n"
            f"  Read: {stats['read']}\n"
            f"  Unread: {stats['unread']}\n"
            f"  This week: {stats['this_week']}")


def send_daily_digest(trigger: str = "cron_morning"):
    """Build digest and send to Telegram research bot."""
    digest = build_digest()

    # Send to research bot if available, else main bot
    try:
        from jobpulse.telegram_bots import send_research
        success = send_research(digest)
    except ImportError:
        success = telegram_agent.send_message(digest)

    logger.info("arXiv digest %s (%d chars)", "sent" if success else "FAILED", len(digest))
    return success
