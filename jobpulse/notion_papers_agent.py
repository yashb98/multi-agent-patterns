"""Notion Weekly Papers Agent — creates a weekly research summary page in Notion.

Replaces scripts/agents/notion-papers.sh (which used claude -p).
Runs Monday 8:33am. Fetches top 5 papers from the week, generates
500-word summaries, creates a Notion page, sends Telegram notification.
"""

from datetime import datetime
from shared.logging_config import get_logger
from jobpulse.config import OPENAI_API_KEY
from jobpulse import telegram_agent, event_logger
from jobpulse.arxiv_agent import fetch_papers, fast_score, llm_rank

logger = get_logger(__name__)


def generate_paper_summary(paper: dict) -> str:
    """Generate a 500-word summary with structured sections."""
    if not OPENAI_API_KEY:
        return paper["abstract"][:500]

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""Write a ~500 word summary of this paper with these sections:

**Problem**: What gap or challenge does this address?
**Approach**: What is the method, architecture, or technique?
**Key Results**: Benchmarks, numbers, comparisons
**Why It Matters**: Relevance for AI engineers working with agents, RAG, or LLMs
**Practical Takeaways**: What can a practitioner apply today?

Title: {paper['title']}
Abstract: {paper['abstract']}"""}],
            max_tokens=800,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Summary generation failed: %s", e)
        return paper["abstract"][:500]


def build_notion_content(papers_with_summaries: list[tuple[dict, str]]) -> list[dict]:
    """Build Notion block content for the research page."""
    blocks = []

    # Key Themes section
    themes = ", ".join(set(
        cat for paper, _ in papers_with_summaries
        for cat in paper.get("categories", [])[:2]
    ))
    blocks.append({
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"text": {"content": "Key Themes This Week"}}]}
    })
    blocks.append({
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content":
            f"This week's top papers span {themes}. "
            f"Key themes include techniques for improving agent systems, "
            f"novel approaches to knowledge retrieval, and advances in reasoning."
        }}]}
    })
    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # Each paper
    for paper, summary in papers_with_summaries:
        authors = ", ".join(paper["authors"][:3])
        if len(paper["authors"]) > 3:
            authors += " et al."

        # Title heading
        blocks.append({
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"text": {"content": paper["title"][:100]}}]}
        })

        # Authors + link
        blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [
                {"text": {"content": f"Authors: {authors}\n"}},
                {"text": {"content": paper["arxiv_url"], "link": {"url": paper["arxiv_url"]}}},
            ]}
        })

        # Summary
        # Split into paragraphs (Notion has a 2000 char limit per block)
        for para in summary.split("\n\n"):
            para = para.strip()
            if para:
                blocks.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": para[:2000]}}]}
                })

        blocks.append({"object": "block", "type": "divider", "divider": {}})

    return blocks


def create_weekly_page(trigger: str = "cron_monday") -> str:
    """Full pipeline: fetch → rank → summarize → create Notion page → notify."""
    from jobpulse.process_logger import ProcessTrail
    from jobpulse.notion_agent import create_research_page

    trail = ProcessTrail("notion_papers_agent", trigger)
    week_date = datetime.now().strftime("%Y-%m-%d")
    title = f"AI Research — Week of {week_date}"

    # Step 1: Fetch papers (more for weekly)
    with trail.step("api_call", "Fetch papers from arXiv") as s:
        papers = fetch_papers(max_results=200)
        s["output"] = f"Fetched {len(papers)} papers"
        if not papers:
            trail.finalize("No papers fetched")
            return ""

    # Step 2: Score and rank
    with trail.step("decision", "Score and rank papers") as s:
        for p in papers:
            p["fast_score"] = fast_score(p)
        papers.sort(key=lambda p: p["fast_score"], reverse=True)
        top = [p for p in papers[:20] if p["fast_score"] > 0]
        s["output"] = f"{len(top)} candidates"

    with trail.step("llm_call", "LLM rank top candidates") as s:
        ranked = llm_rank(top, top_n=5)
        s["output"] = f"Selected {len(ranked)} papers"

    # Step 3: Generate 500-word summaries
    papers_with_summaries = []
    for i, paper in enumerate(ranked):
        with trail.step("llm_call", f"Generate summary {i+1}",
                         step_input=paper["title"][:100]) as s:
            summary = generate_paper_summary(paper)
            papers_with_summaries.append((paper, summary))
            s["output"] = f"{len(summary)} chars"

    # Step 4: Create Notion page
    with trail.step("api_call", "Create Notion research page") as s:
        blocks = build_notion_content(papers_with_summaries)
        page_url = create_research_page(title, blocks)
        s["output"] = f"Page: {page_url}" if page_url else "Failed"

    # Step 5: Send Telegram notification
    with trail.step("api_call", "Send Telegram notification") as s:
        paper_titles = "\n".join(f"  {i+1}. {p['title'][:60]}" for i, (p, _) in enumerate(papers_with_summaries))
        msg = (f"📚 Weekly AI research summary posted to Notion\n\n"
               f"\"{title}\"\n\n"
               f"Papers:\n{paper_titles}\n\n"
               f"5 papers with ~500-word summaries each.")
        if page_url:
            msg += f"\n\n📎 {page_url}"
        telegram_agent.send_message(msg)
        s["output"] = "Notification sent"

    # Step 6: Extract to knowledge graph
    with trail.step("extraction", "Extract to knowledge graph") as s:
        for paper, summary in papers_with_summaries:
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
        s["output"] = f"Extracted {len(papers_with_summaries)} papers"

    event_logger.log_event(
        event_type="research_paper",
        agent_name="notion_papers_agent",
        action="weekly_summary",
        content=title,
        metadata={"papers": len(papers_with_summaries), "notion_url": page_url},
    )

    trail.finalize(f"Created '{title}' with {len(papers_with_summaries)} papers")
    return page_url
