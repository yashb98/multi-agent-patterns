"""Blog Generator — 5-agent pipeline to turn arXiv papers into 2000-word blog posts.

Pipeline: Deep Reader → GRPO Writer (3 candidates) → Fact Checker → Diagram Generator → Editor
Output: Notion page with structured blog post + workflow diagrams.
"""

import json
import re
from datetime import datetime
from shared.logging_config import get_logger
from jobpulse.config import OPENAI_API_KEY, DATA_DIR
from jobpulse import event_logger

logger = get_logger(__name__)


def _llm_call(system: str, user: str, max_tokens: int = 2500, temperature: float = 0.3) -> str:
    """Helper for OpenAI chat call."""
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()


# ── AGENT 1: Deep Reader ──

def deep_read(paper: dict) -> str:
    """Extract structured research notes from a paper's abstract.

    Returns ~1000 words of structured notes covering:
    problem, method, key insight, results, limitations, significance.
    """
    system = """You are a senior AI researcher. Read this paper carefully and extract
structured research notes. Be precise — use specific numbers, method names, and findings.
Do NOT add information not in the abstract. If something is unclear, say so."""

    user = f"""Paper: {paper['title']}
Authors: {', '.join(paper['authors'][:5]) if isinstance(paper['authors'], list) else paper['authors']}
Categories: {', '.join(paper['categories'][:5]) if isinstance(paper['categories'], list) else paper['categories']}

Abstract:
{paper['abstract']}

Extract structured notes (~1000 words) with these sections:

## PROBLEM
What specific problem does this paper address? What gap exists in current methods?

## METHOD
How does the proposed approach work? Step-by-step technical description.
Name specific architectures, algorithms, loss functions, or training procedures.

## KEY INSIGHT
What is the ONE core insight that makes this work? What's the "aha" moment?

## RESULTS
Specific benchmark results, numbers, comparisons with baselines.
Include exact metrics where available.

## LIMITATIONS
What are the acknowledged or apparent limitations? What doesn't this solve?

## SIGNIFICANCE
Why does this matter for the broader AI field? What does it enable?"""

    return _llm_call(system, user, max_tokens=1500, temperature=0.2)


# ── AGENT 2: Blog Writer (GRPO — 3 candidates) ──

BLOG_TEMPLATE = """Write a 2000-word blog post about this AI research paper.
Your audience: AI/ML engineers and researchers who want practical understanding.

RESEARCH NOTES:
{notes}

PAPER TITLE: {title}
AUTHORS: {authors}
ARXIV: {arxiv_url}

BLOG STRUCTURE (follow exactly):

# [Write a catchy, descriptive title — NOT the paper title]
## What this means for AI practitioners

### TL;DR
One paragraph (50 words) explaining what this paper does and why someone should care.

### The Problem
(~200 words) What gap does this address? Why hasn't it been solved? Use a real-world analogy.

### The Approach
(~500 words) Step-by-step how the method works. Use clear language.
Include a section marked [DIAGRAM_PLACEHOLDER] where a workflow diagram should go.
Use bullet points for steps. Include pseudocode if helpful.

### Key Results
(~300 words) Benchmarks, numbers, comparisons with prior work.
Include a markdown table comparing this method vs baselines if data is available.

### Why This Matters
(~300 words) Impact on AI field. Which industries benefit. Connection to current trends.

### Practical Takeaways
(~400 words) How a practitioner could apply this. Implementation hints.
Links to code if mentioned. Limitations to be aware of.

### Further Reading
(~100 words) Related papers, author's other work.

---
*Based on: {title} ({arxiv_url})*
*Authors: {authors}*

RULES:
- Write ~2000 words total (1800-2200 acceptable)
- Use analogies to explain complex concepts
- Be technically accurate — don't oversimplify to the point of being wrong
- Include [DIAGRAM_PLACEHOLDER] where a workflow diagram should go
- Write in active voice, short paragraphs
- No fluff, no filler, every sentence must inform"""


def write_blog_grpo(paper: dict, notes: str) -> tuple[str, float]:
    """Generate 3 blog drafts at different temperatures, pick the best.

    Returns (best_draft, best_score).
    """
    authors = ', '.join(paper['authors'][:5]) if isinstance(paper['authors'], list) else paper['authors']

    prompt = BLOG_TEMPLATE.format(
        notes=notes,
        title=paper['title'],
        authors=authors,
        arxiv_url=paper.get('arxiv_url', ''),
    )

    candidates = []
    temps = [0.5, 0.7, 0.9]

    for temp in temps:
        try:
            draft = _llm_call(
                "You are an elite AI technical writer. You write engaging, accurate blog posts about research papers.",
                prompt,
                max_tokens=3000,
                temperature=temp,
            )
            candidates.append(draft)
        except Exception as e:
            logger.warning("Blog draft failed at temp %.1f: %s", temp, e)

    if not candidates:
        return ("Failed to generate blog draft.", 0.0)

    # Score each candidate
    scored = []
    for i, draft in enumerate(candidates):
        score = _score_blog(draft, paper)
        scored.append((score, draft, temps[i]))
        logger.debug("GRPO candidate %d (temp=%.1f): score=%.1f, words=%d",
                      i + 1, temps[i], score, len(draft.split()))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_draft, best_temp = scored[0]

    logger.info("GRPO: best draft at temp=%.1f, score=%.1f, words=%d",
                best_temp, best_score, len(best_draft.split()))

    return (best_draft, best_score)


def _score_blog(draft: str, paper: dict) -> float:
    """Score a blog draft on accuracy, completeness, readability, engagement."""
    score = 0.0
    words = len(draft.split())

    # Length: target 1800-2200
    if 1800 <= words <= 2200:
        score += 3.0
    elif 1500 <= words <= 2500:
        score += 2.0
    elif 1000 <= words <= 3000:
        score += 1.0

    # Has all required sections
    sections = ["TL;DR", "Problem", "Approach", "Results", "Matters", "Takeaway", "Reading"]
    for s in sections:
        if s.lower() in draft.lower():
            score += 0.5

    # Has diagram placeholder
    if "DIAGRAM_PLACEHOLDER" in draft or "diagram" in draft.lower():
        score += 1.0

    # Mentions paper title or key terms
    title_words = set(paper['title'].lower().split())
    draft_lower = draft.lower()
    matches = sum(1 for w in title_words if len(w) > 4 and w in draft_lower)
    score += min(matches * 0.3, 2.0)

    # Has markdown formatting
    if draft.count("#") >= 5:
        score += 1.0
    if "```" in draft or "|" in draft:
        score += 0.5

    # Penalize if too short or too long
    if words < 1000:
        score -= 2.0
    if words > 3000:
        score -= 1.0

    return max(0.0, score)


# ── AGENT 3: Fact Checker ──

def fact_check(draft: str, paper: dict) -> dict:
    """Cross-reference blog claims against the paper's abstract.

    Uses the unified fact-checker from shared/fact_checker.py.
    Returns: {"flags": [...], "flag_count": N, "passed": bool, "accuracy_score": float}
    """
    from shared.fact_checker import extract_claims, verify_claims, compute_accuracy_score

    topic = paper.get("title", "AI research")
    abstract = paper.get("abstract", "")

    # Extract claims from the draft
    claims = extract_claims(draft, topic)

    # Verify against paper abstract (primary source) + web search
    verifications = verify_claims(claims, [], paper_abstract=abstract, web_search=True)

    # Compute accuracy score
    accuracy = compute_accuracy_score(verifications)

    # Convert to legacy format for backward compatibility
    flags = []
    for v in verifications:
        if v.get("verdict", "").upper() != "VERIFIED":
            flags.append({
                "claim": v.get("claim", ""),
                "issue": v.get("evidence", ""),
                "severity": v.get("severity", "medium"),
            })

    return {
        "flags": flags,
        "flag_count": len(flags),
        "accuracy_score": accuracy,
        "recommendation": "approve" if accuracy >= 9.5 else "revise" if accuracy >= 5.0 else "reject",
        "passed": len([f for f in flags if f.get("severity") == "high"]) == 0,
    }


def revise_with_flags(draft: str, flags: list, paper: dict) -> str:
    """Revise the blog post to address fact-check flags."""
    if not flags:
        return draft

    flags_text = "\n".join(
        f"- CLAIM: {f.get('claim', '?')}\n  ISSUE: {f.get('issue', '?')}\n  SEVERITY: {f.get('severity', '?')}"
        for f in flags
    )

    system = "You are revising a blog post to fix factual errors. Keep the same structure and tone."
    user = f"""ORIGINAL BLOG POST:
{draft[:3000]}

FACT CHECK FLAGS:
{flags_text}

PAPER ABSTRACT (source of truth):
{paper['abstract'][:1000]}

Revise the blog to fix ALL flagged issues. Keep the same structure.
Return the COMPLETE revised blog post."""

    return _llm_call(system, user, max_tokens=3000, temperature=0.3)


# ── AGENT 4: Diagram Generator ──

def generate_diagram(notes: str, paper: dict) -> str:
    """Generate a Mermaid.js diagram of the paper's method/architecture.

    Returns mermaid code string.
    """
    system = """You generate Mermaid.js diagrams for AI research papers.
Create a clear, readable flowchart showing the method's architecture or workflow.
Use meaningful labels. Keep it under 15 nodes. Use colors for different components."""

    user = f"""Paper: {paper['title']}

Research notes:
{notes[:1500]}

Generate a Mermaid.js flowchart diagram showing the paper's main method or architecture.
Return ONLY the mermaid code (no fences, no explanation):

graph TD
    A[...] --> B[...]
    ..."""

    try:
        mermaid_code = _llm_call(system, user, max_tokens=500, temperature=0.3)
        # Clean up — remove any markdown fences
        mermaid_code = mermaid_code.strip()
        if mermaid_code.startswith("```"):
            mermaid_code = mermaid_code.split("\n", 1)[1] if "\n" in mermaid_code else mermaid_code[3:]
            mermaid_code = mermaid_code.rsplit("```", 1)[0]
        return mermaid_code.strip()
    except Exception as e:
        logger.warning("Diagram generation failed: %s", e)
        return ""


def get_diagram_url(mermaid_code: str) -> str:
    """Convert mermaid code to a PNG URL via mermaid.ink API."""
    if not mermaid_code:
        return ""
    import base64
    encoded = base64.urlsafe_b64encode(mermaid_code.encode()).decode()
    return f"https://mermaid.ink/img/{encoded}"


# ── AGENT 5: Editor ──

def edit_blog(draft: str, paper: dict, diagram_url: str = "") -> str:
    """Final polish: title, tone, flow, key takeaways box, SEO."""
    system = """You are a senior blog editor. Polish this AI research blog post.
Fix: awkward phrasing, unclear explanations, missing transitions, weak title.
Add: a KEY TAKEAWAYS box (3-4 bullet points) after the TL;DR.
Keep the same structure and length. Do NOT remove content."""

    diagram_instruction = ""
    if diagram_url:
        diagram_instruction = f"\n\nReplace [DIAGRAM_PLACEHOLDER] with:\n![Architecture Diagram]({diagram_url})"

    user = f"""BLOG POST TO EDIT:
{draft}
{diagram_instruction}

Polish the post. Fix any rough edges. Ensure the title is catchy but accurate.
Add a KEY TAKEAWAYS section (3-4 bullet points) right after the TL;DR.
Return the COMPLETE edited blog post."""

    return _llm_call(system, user, max_tokens=3000, temperature=0.3)


# ── FULL PIPELINE ──

def generate_blog_post(paper: dict) -> dict:
    """Full 5-agent pipeline: read → write → check → diagram → edit.

    Returns: {"title": str, "content": str, "diagram_url": str,
              "word_count": int, "grpo_score": float, "fact_check": dict}
    """
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("blog_generator", "blog_pipeline")

    logger.info("Generating blog for: %s", paper['title'][:60])

    # Agent 1: Deep Reader
    with trail.step("llm_call", "Agent 1: Deep Reader",
                     step_input=paper['title'][:100]) as s:
        notes = deep_read(paper)
        s["output"] = f"{len(notes.split())} words of notes"

    # Agent 2: Blog Writer (GRPO)
    with trail.step("llm_call", "Agent 2: Blog Writer (GRPO × 3)",
                     step_input=f"Notes: {len(notes.split())} words") as s:
        draft, grpo_score = write_blog_grpo(paper, notes)
        s["output"] = f"Best draft: {len(draft.split())} words, score={grpo_score:.1f}"

    # Agent 3: Fact Checker
    with trail.step("llm_call", "Agent 3: Fact Checker") as s:
        fc_result = fact_check(draft, paper)
        s["output"] = f"{fc_result['flag_count']} flags, accuracy={fc_result['accuracy_score']}"
        s["metadata"] = fc_result

    # Revise if flags found
    if not fc_result["passed"]:
        with trail.step("llm_call", "Agent 3b: Revision",
                         step_input=f"{fc_result['flag_count']} flags") as s:
            draft = revise_with_flags(draft, fc_result["flags"], paper)
            s["output"] = f"Revised: {len(draft.split())} words"

    # Agent 4: Diagram Generator
    with trail.step("llm_call", "Agent 4: Diagram Generator") as s:
        mermaid_code = generate_diagram(notes, paper)
        diagram_url = get_diagram_url(mermaid_code)
        s["output"] = f"Diagram: {len(mermaid_code)} chars" if mermaid_code else "No diagram"

    # Agent 5: Editor
    with trail.step("llm_call", "Agent 5: Editor + Polish") as s:
        final_draft = edit_blog(draft, paper, diagram_url)
        s["output"] = f"Final: {len(final_draft.split())} words"

    # Extract title from the blog
    title_match = re.search(r"^#\s+(.+)", final_draft, re.MULTILINE)
    blog_title = title_match.group(1) if title_match else paper['title']

    result = {
        "title": blog_title,
        "content": final_draft,
        "mermaid_code": mermaid_code,
        "diagram_url": diagram_url,
        "word_count": len(final_draft.split()),
        "grpo_score": grpo_score,
        "fact_check": fc_result,
        "paper": paper,
        "generated_at": datetime.now().isoformat(),
    }

    event_logger.log_event(
        event_type="research_paper",
        agent_name="blog_generator",
        action="blog_generated",
        content=f"Blog: {blog_title[:60]} ({result['word_count']} words)",
        metadata={"paper": paper['title'][:100], "score": grpo_score, "flags": fc_result['flag_count']},
    )

    trail.finalize(f"Blog: {blog_title[:60]} | {result['word_count']} words | score={grpo_score:.1f} | flags={fc_result['flag_count']}")
    return result


# ── NOTION PUBLISHING ──

def publish_to_notion(blog: dict) -> str:
    """Create a Notion page with the blog post. Returns page URL."""
    from jobpulse.notion_agent import _notion_api
    from jobpulse.config import NOTION_RESEARCH_DB_ID

    paper = blog["paper"]
    content = blog["content"]

    # Build Notion blocks from markdown-ish content
    blocks = []

    # Header with metadata
    blocks.append({
        "object": "block", "type": "callout", "callout": {
            "rich_text": [{"text": {"content":
                f"GRPO Score: {blog['grpo_score']:.1f}/10 | "
                f"Fact Check: {blog['fact_check']['flag_count']} flags | "
                f"Words: {blog['word_count']} | "
                f"Status: Draft — awaiting approval"
            }}],
            "icon": {"emoji": "📝"},
        }
    })

    # Split content into paragraphs and headings
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            blocks.append({
                "object": "block", "type": "heading_1", "heading_1": {
                    "rich_text": [{"text": {"content": line[2:][:100]}}]
                }
            })
        elif line.startswith("## "):
            blocks.append({
                "object": "block", "type": "heading_2", "heading_2": {
                    "rich_text": [{"text": {"content": line[3:][:100]}}]
                }
            })
        elif line.startswith("### "):
            blocks.append({
                "object": "block", "type": "heading_3", "heading_3": {
                    "rich_text": [{"text": {"content": line[4:][:100]}}]
                }
            })
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({
                "object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                    "rich_text": [{"text": {"content": line[2:][:2000]}}]
                }
            })
        elif line.startswith("|"):
            # Table rows — add as code block for simplicity
            blocks.append({
                "object": "block", "type": "paragraph", "paragraph": {
                    "rich_text": [{"text": {"content": line[:2000]}}]
                }
            })
        elif line.startswith("!["):
            # Image — extract URL
            url_match = re.search(r"\((.+?)\)", line)
            if url_match:
                blocks.append({
                    "object": "block", "type": "image", "image": {
                        "type": "external", "external": {"url": url_match.group(1)}
                    }
                })
        elif line.startswith("```"):
            continue  # skip code fences
        elif line.startswith("---"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        else:
            # Regular paragraph — Notion has 2000 char limit per block
            for chunk_start in range(0, len(line), 2000):
                chunk = line[chunk_start:chunk_start + 2000]
                blocks.append({
                    "object": "block", "type": "paragraph", "paragraph": {
                        "rich_text": [{"text": {"content": chunk}}]
                    }
                })

    # Add diagram as image if available
    if blog.get("diagram_url"):
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        blocks.append({
            "object": "block", "type": "heading_3", "heading_3": {
                "rich_text": [{"text": {"content": "Architecture Diagram"}}]
            }
        })
        blocks.append({
            "object": "block", "type": "image", "image": {
                "type": "external", "external": {"url": blog["diagram_url"]}
            }
        })

    # Add mermaid code block
    if blog.get("mermaid_code"):
        blocks.append({
            "object": "block", "type": "code", "code": {
                "rich_text": [{"text": {"content": blog["mermaid_code"][:2000]}}],
                "language": "mermaid",
            }
        })

    # Create the page
    parent = {"database_id": NOTION_RESEARCH_DB_ID} if NOTION_RESEARCH_DB_ID else None
    if not parent:
        from jobpulse.budget_agent import BUDGET_PAGE_ID
        parent = {"page_id": BUDGET_PAGE_ID}  # fallback

    # Notion limits children to 100 blocks per request
    first_batch = blocks[:100]
    remaining = blocks[100:]

    data = {
        "parent": parent,
        "properties": {
            "Title" if NOTION_RESEARCH_DB_ID else "title": {
                "title": [{"text": {"content": blog["title"][:100]}}]
            },
        },
        "children": first_batch,
        "icon": {"emoji": "📝"},
    }

    result = _notion_api("POST", "/pages", data)
    page_id = result.get("id", "")

    # Append remaining blocks if any
    if page_id and remaining:
        for i in range(0, len(remaining), 100):
            batch = remaining[i:i + 100]
            _notion_api("PATCH", f"/blocks/{page_id}/children", {"children": batch})

    page_url = f"https://www.notion.so/{page_id.replace('-', '')}" if page_id else ""
    logger.info("Blog published to Notion: %s", page_url)
    return page_url


# ── TELEGRAM COMMAND HANDLER ──

def handle_blog_command(paper_index: int) -> str:
    """Generate a blog post for paper #N from today's digest."""
    from jobpulse.arxiv_agent import get_paper_by_index
    today = datetime.now().strftime("%Y-%m-%d")

    paper = get_paper_by_index(today, paper_index)
    if not paper:
        return f"No paper #{paper_index} in today's digest. Say \"papers\" first."

    # Parse stored JSON fields
    if isinstance(paper.get("authors"), str):
        try:
            paper["authors"] = json.loads(paper["authors"])
        except Exception:
            paper["authors"] = [paper["authors"]]
    if isinstance(paper.get("categories"), str):
        try:
            paper["categories"] = json.loads(paper["categories"])
        except Exception:
            paper["categories"] = [paper["categories"]]

    # Generate the blog
    blog = generate_blog_post(paper)

    # Publish to Notion
    notion_url = publish_to_notion(blog)

    # Build reply
    fc = blog["fact_check"]
    return (
        f"📝 BLOG DRAFT READY\n\n"
        f"\"{blog['title']}\"\n\n"
        f"📊 Quality: {blog['grpo_score']:.1f}/10 (GRPO best of 3)\n"
        f"{'✅' if fc['passed'] else '⚠️'} Fact check: {fc['flag_count']} flags "
        f"(accuracy: {fc['accuracy_score']}/10)\n"
        f"📐 Diagram: {'generated' if blog.get('diagram_url') else 'none'}\n"
        f"📏 Words: {blog['word_count']}\n\n"
        f"📎 Review on Notion: {notion_url}\n\n"
        f"Reply:\n"
        f"  \"approve\" — mark as ready to publish\n"
        f"  \"regenerate {paper_index}\" — try again\n"
        f"  \"skip\" — don't blog this paper"
    )
