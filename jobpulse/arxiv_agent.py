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
from shared.db import get_db_conn
from shared.agents import get_openai_client
from jobpulse.config import OPENAI_API_KEY, PROJECT_DIR, DATA_DIR
from jobpulse import telegram_agent, event_logger
from jobpulse.paper_discovery import discover_trending_papers

logger = get_logger(__name__)

# Categories to scan
CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.MA", "stat.ML"]

DB_PATH = DATA_DIR / "papers.db"


def _get_conn() -> sqlite3.Connection:
    return get_db_conn(DB_PATH)


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
    # Migration: add fact-check columns if missing
    for col, col_type, default in [
        ("fact_check_score", "REAL", "0"),
        ("fact_check_claims", "INTEGER", "0"),
        ("fact_check_verified", "INTEGER", "0"),
        ("fact_check_issues", "TEXT", "''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {col_type} DEFAULT {default}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()


_init_db()


# ── Fetching ──

def fetch_papers(max_results: int = 200) -> list[dict]:
    """Fetch recent papers from arXiv API."""
    import httpx

    import time

    cat_query = "+OR+".join(f"cat:{c}" for c in CATEGORIES)
    url = (
        f"https://export.arxiv.org/api/query?"
        f"search_query={cat_query}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&start=0&max_results={max_results}"
    )

    # arXiv requires descriptive User-Agent per API policy
    headers = {"User-Agent": "JobPulse/1.0 (mailto:bishnoiyash274@gmail.com)"}

    resp = None
    for attempt in range(3):
        try:
            resp = httpx.get(url, timeout=30, headers=headers, follow_redirects=True)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)  # 5s, 10s, 15s — arXiv needs longer backoff
                logger.warning("arXiv rate limited (429), retrying in %ds (attempt %d/3)", wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except Exception as e:
            if attempt < 2:
                logger.warning("arXiv API error (attempt %d/3): %s", attempt + 1, e)
                time.sleep(30 * (2 ** attempt))  # 30s, 60s, 120s
            else:
                logger.error("arXiv API error after 3 attempts: %s", e)
                return []

    if resp is None or resp.status_code != 200:
        logger.error("arXiv API: failed to get 200 after 3 attempts")
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

def _extract_json_array(raw: str) -> list:
    """Extract JSON array from LLM response, handling markdown wrappers and text prefixes."""
    # Strip markdown code blocks
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    # Find the first [ ... ] block
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("_extract_json_array: found [...] but invalid JSON: %s", e)
            return []
    return []


def llm_rank_broad(papers: list[dict], top_n: int = 5) -> list[dict]:
    """Rank papers by BROAD AI impact using multi-criteria scoring.

    Each paper scored on 4 dimensions (0-10):
    - Novelty (30%): genuinely new idea, architecture, or technique
    - Significance (25%): could change how people build AI systems
    - Practical (30%): useful for practitioners today
    - Breadth (15%): relevant across multiple AI subfields
    """
    if not OPENAI_API_KEY:
        return papers[:top_n]

    from shared.agents import get_model_name, is_local_llm
    client = get_openai_client()
    _local = is_local_llm()

    # Send top 30 by recency (most recent = most likely to be today's papers)
    candidates = papers[:30]

    _abs_len = 800 if _local else 400
    paper_texts = []
    for i, p in enumerate(candidates):
        paper_texts.append(
            f"{i+1}. \"{p['title']}\"\n"
            f"   Categories: {', '.join(p['categories'][:3])}\n"
            f"   Abstract: {p['abstract'][:_abs_len]}"
        )

    prompt = f"""You are an AI research curator for a daily digest. Your audience is
AI/ML engineers and researchers who want to stay on top of the field.

From these {len(candidates)} recent arXiv papers, pick the TOP {top_n} most impactful.

Score each paper on 4 dimensions (0-10 each):
1. NOVELTY — genuinely new idea, architecture, or technique (not incremental)
2. SIGNIFICANCE — could change how people build AI systems
3. PRACTICAL — useful for practitioners today, not just theoretical
4. BREADTH — relevant across multiple AI subfields

Avoid: survey papers, minor incremental improvements, dataset-only papers.
Prefer: breakthrough techniques, new architectures, surprising results, open-source releases.

Papers:
{chr(10).join(paper_texts)}

Return ONLY a JSON array. Compute overall as: (novelty*0.3 + significance*0.25 + practical*0.3 + breadth*0.15)
[{{"rank": 1, "paper_num": X, "scores": {{"novelty": N, "significance": N, "practical": N, "breadth": N}}, "overall": weighted_avg, "reason": "One sentence on why this matters to AI", "key_technique": "The main technique or contribution in 5 words", "category_tag": "e.g. LLM, Agents, Vision, RL, Efficiency, Safety, Reasoning"}}]"""

    try:
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2400 if _local else 1200,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        rankings = _extract_json_array(raw)

        ranked = []
        for r in rankings[:top_n]:
            idx = r.get("paper_num", 1) - 1
            if 0 <= idx < len(candidates):
                # Support both old flat score and new multi-criteria format
                if "overall" in r:
                    candidates[idx]["impact_score"] = r["overall"]
                elif "scores" in r:
                    s = r["scores"]
                    candidates[idx]["impact_score"] = (
                        s.get("novelty", 5) * 0.3 + s.get("significance", 5) * 0.25
                        + s.get("practical", 5) * 0.3 + s.get("breadth", 5) * 0.15
                    )
                else:
                    candidates[idx]["impact_score"] = r.get("score", 5)

                candidates[idx]["scores"] = r.get("scores", {})
                candidates[idx]["impact_reason"] = r.get("reason", "")
                candidates[idx]["key_technique"] = r.get("key_technique", "")
                candidates[idx]["category_tag"] = r.get("category_tag", "")
                ranked.append(candidates[idx])

        return ranked

    except Exception as e:
        logger.warning("LLM ranking failed: %s", e)
        return candidates[:top_n]


def _find_repo_url(paper: dict) -> str | None:
    """Try to find a GitHub repo URL from paper metadata."""
    abstract = paper.get("abstract", "")
    match = re.search(r"https?://github\.com/[^\s)]+", abstract)
    if match:
        return match.group(0).rstrip(".")
    return None


def summarize_and_verify_paper(paper: dict) -> dict:
    """Summarize a paper and fact-check claims using multi-source verification.

    Uses Semantic Scholar for attribution/date claims, quality web search for
    benchmark/comparison claims, and abstract-check for technical claims.
    Checks repo health if GitHub URL found.
    """
    from shared.fact_checker import (
        extract_claims, verify_claims, compute_accuracy_score,
        generate_fact_check_explanation,
    )
    from shared.external_verifiers import check_repo_health

    summary = summarize_paper(paper)

    # Extract claims from summary
    try:
        claims = extract_claims(summary, paper["title"])
    except Exception as e:
        logger.warning("Claim extraction failed for %s: %s", paper["arxiv_id"], e)
        claims = []

    # Check repo health
    repo_url = _find_repo_url(paper)
    try:
        repo_health = check_repo_health(repo_url)
    except Exception as e:
        logger.warning("Repo health check failed: %s", e)
        repo_health = {"status": "REPO_NA", "score_adjustment": 0.0, "summary": "Check failed"}

    if not claims:
        score = max(0.0, min(10.0, 10.0 + repo_health.get("score_adjustment", 0.0)))
        explanation = generate_fact_check_explanation(score, [], repo_health)
        return {
            "summary": summary,
            "fact_check": {
                "score": score,
                "explanation": explanation,
                "total_claims": 0,
                "verified_count": 0,
                "issues": [],
                "repo_health": repo_health,
            },
        }

    # Multi-source verification
    try:
        verifications = verify_claims(
            claims, sources=[], paper_abstract=paper["abstract"],
            arxiv_id=paper["arxiv_id"],
        )
        score = compute_accuracy_score(
            verifications,
            repo_adjustment=repo_health.get("score_adjustment", 0.0),
        )
    except Exception as e:
        logger.warning("Verification failed for %s: %s", paper["arxiv_id"], e)
        verifications = []
        score = 0.0

    explanation = generate_fact_check_explanation(score, verifications, repo_health)

    issues = [
        {"claim": v["claim"], "verdict": v["verdict"], "fix": v.get("fix_suggestion")}
        for v in verifications
        if v.get("verdict") in ("INACCURATE", "EXAGGERATED")
    ]

    return {
        "summary": summary,
        "fact_check": {
            "score": score,
            "explanation": explanation,
            "total_claims": len(verifications),
            "verified_count": sum(1 for v in verifications if v["verdict"] == "VERIFIED"),
            "issues": issues,
            "repo_health": repo_health,
        },
    }


def summarize_paper(paper: dict) -> str:
    """Generate a summary with: what it does, why it matters, practical takeaway."""
    if not OPENAI_API_KEY:
        return paper["abstract"][:200]

    from shared.agents import get_model_name, is_local_llm
    client = get_openai_client()
    _local = is_local_llm()

    _abs_limit = len(paper['abstract']) if _local else 1000
    try:
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": f"""Summarize this AI paper for a practitioner in 3-4 sentences:

1. WHAT: What does the paper propose/discover? (1 sentence)
2. WHY: Why does this matter for the AI field? (1 sentence)
3. HOW: Key technical insight or method (1 sentence)
4. USE: One practical way someone could apply this today (1 sentence, start with "Practical takeaway:")

Title: {paper['title']}
Abstract: {paper['abstract'][:_abs_limit]}"""}],
            max_tokens=800 if _local else 250,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Summary failed for %s: %s", paper["arxiv_id"], e)
        return paper["abstract"][:300]


# ── Paper Storage ──

def store_papers(papers: list[dict], digest_date: str):
    """Store ranked papers in SQLite (including fact-check results)."""
    conn = _get_conn()
    for p in papers:
        fc = p.get("fact_check", {})
        conn.execute(
            "INSERT OR REPLACE INTO papers (arxiv_id, title, authors, abstract, categories, "
            "pdf_url, arxiv_url, published_at, impact_score, impact_reason, summary, "
            "key_technique, practical_takeaway, status, digest_date, discovered_at, "
            "fact_check_score, fact_check_claims, fact_check_verified, fact_check_issues) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                p["arxiv_id"], p["title"], json.dumps(p["authors"]),
                p["abstract"], json.dumps(p["categories"]),
                p.get("pdf_url", ""), p["arxiv_url"], p.get("published", ""),
                p.get("impact_score", 0), p.get("impact_reason", ""),
                p.get("summary", ""), p.get("key_technique", ""),
                p.get("practical_takeaway", ""),
                "sent", digest_date, datetime.now().isoformat(),
                fc.get("accuracy_score", 0), fc.get("total_claims", 0),
                fc.get("verified_count", 0), json.dumps(fc.get("issues", [])),
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


# ── Notion Paper Pages ──

def create_notion_paper_pages(summaries: list[tuple[dict, str]], digest_date: str) -> dict:
    """Create a Notion page for each paper's summary + a daily index page.

    Returns: {arxiv_id: notion_url, ...}
    """
    from jobpulse.notion_agent import _notion_api
    from jobpulse.config import NOTION_RESEARCH_DB_ID

    if not NOTION_RESEARCH_DB_ID:
        return {}

    urls = {}

    # 1. Create the daily index page
    index_title = f"AI Papers — {digest_date}"
    index_data = {
        "parent": {"database_id": NOTION_RESEARCH_DB_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": index_title}}]},
        },
        "icon": {"emoji": "📚"},
        "children": [
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"text": {"content": f"Top 5 AI Papers — {digest_date}"}}]
            }},
            {"object": "block", "type": "divider", "divider": {}},
            # Papers table
            {"object": "block", "type": "table", "table": {
                "table_width": 4,
                "has_column_header": True,
                "has_row_header": False,
                "children": [
                    {"object": "block", "type": "table_row", "table_row": {
                        "cells": [
                            [{"type": "text", "text": {"content": "#"}}],
                            [{"type": "text", "text": {"content": "Paper"}}],
                            [{"type": "text", "text": {"content": "Category"}}],
                            [{"type": "text", "text": {"content": "Score"}}],
                        ]
                    }},
                ]
            }},
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_3", "heading_3": {
                "rich_text": [{"text": {"content": "Blog Posts"}}]
            }},
            {"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"text": {"content": "Say \"blog 1\" to generate a 2000-word blog post for any paper above."}}]
            }},
        ],
    }

    index_result = _notion_api("POST", "/pages", index_data)
    index_page_id = index_result.get("id", "")

    if not index_page_id:
        logger.warning("Failed to create daily index page")
        return {}

    index_url = f"https://www.notion.so/{index_page_id.replace('-', '')}"
    logger.info("Created daily index page: %s", index_url)

    # Find the table block to append rows
    index_blocks = _notion_api("GET", f"/blocks/{index_page_id}/children?page_size=100")
    table_id = None
    for block in index_blocks.get("results", []):
        if block.get("type") == "table":
            table_id = block["id"]
            break

    # 2. Create a sub-page for each paper's summary
    for i, (paper, summary) in enumerate(summaries, 1):
        authors = ", ".join(paper["authors"][:3]) if isinstance(paper["authors"], list) else paper["authors"]
        tag = paper.get("category_tag", "AI")
        score = paper.get("impact_score", 0)

        # Create the paper's summary page
        paper_data = {
            "parent": {"page_id": index_page_id},
            "properties": {
                "title": {"title": [{"text": {"content": paper["title"][:100]}}]},
            },
            "icon": {"emoji": "📄"},
            "children": [
                {"object": "block", "type": "callout", "callout": {
                    "rich_text": [{"text": {"content":
                        f"Score: {score:.0f}/10 | Category: {tag} | Authors: {authors}"
                    }}],
                    "icon": {"emoji": "📊"},
                }},
                {"object": "block", "type": "divider", "divider": {}},
            ],
        }

        # Add summary paragraphs
        for para in summary.split("\n"):
            para = para.strip()
            if not para:
                continue
            if para.startswith("1.") or para.startswith("2.") or para.startswith("3.") or para.startswith("4."):
                paper_data["children"].append({
                    "object": "block", "type": "paragraph", "paragraph": {
                        "rich_text": [{"text": {"content": para[:2000]}}]
                    }
                })
            else:
                paper_data["children"].append({
                    "object": "block", "type": "paragraph", "paragraph": {
                        "rich_text": [{"text": {"content": para[:2000]}}]
                    }
                })

        # Add links
        paper_data["children"].append({"object": "block", "type": "divider", "divider": {}})
        paper_data["children"].append({
            "object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [
                    {"text": {"content": "arXiv: ", "link": None}},
                    {"text": {"content": paper.get("arxiv_url", ""), "link": {"url": paper.get("arxiv_url", "")}}},
                    {"text": {"content": " | PDF: "}},
                    {"text": {"content": paper.get("pdf_url", ""), "link": {"url": paper.get("pdf_url", paper.get("arxiv_url", ""))}}},
                ]
            }
        })

        paper_result = _notion_api("POST", "/pages", paper_data)
        paper_page_id = paper_result.get("id", "")

        if paper_page_id:
            paper_url = f"https://www.notion.so/{paper_page_id.replace('-', '')}"
            urls[paper["arxiv_id"]] = paper_url

            # Add row to the index table with linked paper name
            if table_id:
                _notion_api("PATCH", f"/blocks/{table_id}/children", {
                    "children": [
                        {"object": "block", "type": "table_row", "table_row": {
                            "cells": [
                                [{"type": "text", "text": {"content": str(i)}}],
                                [{"type": "text", "text": {"content": paper["title"][:80], "link": {"url": paper_url}}}],
                                [{"type": "text", "text": {"content": tag}}],
                                [{"type": "text", "text": {"content": f"{score:.0f}/10"}}],
                            ]
                        }},
                    ]
                })

    logger.info("Created %d paper pages under %s", len(urls), index_url)
    return urls


# ── Digest Builder ──

def build_digest(top_n: int = 5) -> str:
    """Full pipeline: fetch -> LLM rank by broad AI impact -> summarize -> format."""
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("arxiv_agent", "daily_digest")
    today = datetime.now().strftime("%Y-%m-%d")

    # Step 1: Community-first discovery, fallback to arXiv API
    with trail.step("api_call", "Discover trending papers (community-first)") as s:
        papers = discover_trending_papers()
        s["output"] = f"Community discovery: {len(papers)} papers"
        if not papers:
            # Fallback to direct arXiv API fetch
            papers = fetch_papers(max_results=200)
            s["output"] = f"Fallback to arXiv API: {len(papers)} papers"
        if not papers:
            trail.finalize("No papers fetched")
            return "Could not fetch papers from arXiv. Try again later."

    # Step 2: LLM rank by BROAD AI IMPACT
    with trail.step("llm_call", "LLM ranking by broad AI impact") as s:
        ranked = llm_rank_broad(papers, top_n=top_n)
        s["output"] = f"Selected {len(ranked)} papers"

    # Step 3: Summarize + fact-check each
    summaries = []
    for i, paper in enumerate(ranked):
        with trail.step("llm_call", f"Summarize + verify paper {i+1}",
                         step_input=paper["title"][:100]) as s:
            result = summarize_and_verify_paper(paper)
            paper["summary"] = result["summary"]
            paper["fact_check"] = result["fact_check"]
            summaries.append((paper, result["summary"]))
            fc = result["fact_check"]
            s["output"] = (f"{result['summary'][:80]}... "
                          f"[FC: {fc['verified_count']}/{fc['total_claims']}]")

    # Step 4: Store in database
    with trail.step("api_call", "Store papers in database") as s:
        store_papers(ranked, today)
        s["output"] = f"Stored {len(ranked)} papers"

    # Step 4b: Create Notion pages for each paper + daily index page
    notion_urls = {}
    with trail.step("api_call", "Create Notion paper pages") as s:
        try:
            notion_urls = create_notion_paper_pages(summaries, today)
            s["output"] = f"Created {len(notion_urls)} Notion pages"
        except Exception as e:
            logger.warning("Notion paper pages failed: %s", e)
            s["output"] = f"Notion failed: {e}"

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

        # Fact-check with honest explanation
        fc = paper.get("fact_check", {})
        if fc and fc.get("explanation"):
            lines.append(f"Fact-check: {fc['explanation']}")
        elif fc and fc.get("total_claims", 0) > 0:
            verified = fc.get("verified_count", 0)
            total = fc["total_claims"]
            lines.append(f"Fact-check: {fc.get('score', 0):.1f}/10 — {verified}/{total} claims checked")

        lines.append(f"PDF: {paper.get('pdf_url', paper['arxiv_url'])}")
        notion_link = notion_urls.get(paper['arxiv_id'], '')
        if notion_link:
            lines.append(f"Summary: {notion_link}")

    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Scanned {len(papers)} papers from {', '.join(CATEGORIES)}")
    lines.append(f"\nCommands: \"paper 3\" full abstract | \"blog 1\" generate blog post | \"read 1\" mark as read")

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
            except Exception as e:
                logger.debug("KG extraction failed for paper: %s", e)
        logger.info("KG extraction complete for %d papers", len(summaries))

    threading.Thread(target=_extract_bg, daemon=True).start()

    # Schedule a 20-minute reminder to blog papers
    def _blog_reminder():
        import time
        time.sleep(20 * 60)  # 20 minutes
        try:
            from jobpulse.telegram_bots import send_research
            paper_titles = [f"{i}. \"{p['title'][:60]}\"" for i, (p, _) in enumerate(summaries, 1)]
            reminder = (
                "\U0001f4dd PAPER BLOG REMINDER (20 min)\n\n"
                "Today's papers:\n" + "\n".join(paper_titles) + "\n\n"
                "Want to generate a blog post?\n"
                "Reply: \"blog 1\" through \"blog 5\"\n"
                "Or \"skip\" to pass today."
            )
            send_research(reminder)
            logger.info("Blog reminder sent (20 min after digest)")
        except Exception as e:
            logger.debug("Blog reminder failed: %s", e)

    threading.Thread(target=_blog_reminder, daemon=True).start()

    # Store verification experiences for experiential learning
    try:
        from jobpulse.swarm_dispatcher import store_experience
        for paper, summary in summaries:
            fc = paper.get("fact_check", {})
            if fc.get("total_claims", 0) > 0:
                store_experience(
                    intent=f"arxiv_verification_{paper['arxiv_id']}",
                    experience={
                        "paper_title": paper["title"][:100],
                        "arxiv_id": paper["arxiv_id"],
                        "score": fc.get("score", 0),
                        "total_claims": fc.get("total_claims", 0),
                        "verified_count": fc.get("verified_count", 0),
                        "issues": fc.get("issues", []),
                        "repo_status": fc.get("repo_health", {}).get("status", "REPO_NA"),
                    },
                    score=fc.get("score", 0) / 10.0,
                )
    except Exception as e:
        logger.debug("Experience storage failed: %s", e)

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

    from jobpulse.telegram_bots import send_with_retry, send_research
    success = send_with_retry(send_research, digest, retries=2, label="arxiv_digest")

    logger.info("arXiv digest %s (%d chars)", "sent" if success else "FAILED", len(digest))
    return success
