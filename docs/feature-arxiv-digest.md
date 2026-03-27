# Feature: Intelligent arXiv Research Digest

Personalized daily AI research digest — scans arXiv, ranks papers by relevance to YOUR projects and interests, summarizes the top 5, extracts techniques you could implement, and feeds everything into the knowledge graph.

## Problem

- The current `arxiv-daily.sh` shells out to `claude -p` — no intelligence, no personalization, no persistence
- arXiv publishes 100-200+ papers/day in cs.AI + cs.LG + cs.CL alone — impossible to scan manually
- You miss relevant papers because keyword search doesn't understand YOUR context
- Papers you read don't connect back to your projects in the knowledge graph
- No tracking of what you've read, what techniques you've tried, what's queued

## Solution

An intelligent agent that:
1. Fetches latest papers from relevant arXiv categories
2. Ranks them against YOUR knowledge graph + project context (not just keywords)
3. Summarizes the top 5 with actionable takeaways
4. Extracts techniques and connects them to your existing projects
5. Sends a formatted digest to Telegram
6. Stores everything in the knowledge graph for future retrieval

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   CRON (7:57 AM daily)               │
└───────────────────────┬─────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│              ARXIV RESEARCH AGENT                    │
│                                                      │
│  Phase 1: FETCH                                     │
│  ├─ Query arXiv API for cs.AI, cs.LG, cs.CL, cs.MA │
│  ├─ Last 24h papers (or 7 days for weekly digest)   │
│  └─ Parse: title, authors, abstract, categories     │
│                                                      │
│  Phase 2: RANK (the intelligence layer)             │
│  ├─ Score each paper against:                       │
│  │   ├─ Knowledge graph entities (your projects)    │
│  │   ├─ Research interest profile                   │
│  │   └─ Recency of related work                    │
│  ├─ LLM relevance scoring for top 20 candidates    │
│  └─ Select top 5                                   │
│                                                      │
│  Phase 3: SUMMARIZE                                 │
│  ├─ LLM generates for each paper:                  │
│  │   ├─ 2-3 sentence summary                       │
│  │   ├─ Key technique / contribution                │
│  │   ├─ How YOU could use this                     │
│  │   └─ Connection to your existing work            │
│  └─ GRPO: generate 3 summaries, pick best          │
│                                                      │
│  Phase 4: EXTRACT + STORE                           │
│  ├─ Extract entities: Paper, Authors, Techniques    │
│  ├─ Create relations: USES, IMPROVES, REFERENCES    │
│  ├─ Link to existing knowledge graph entities       │
│  ├─ Store in papers DB (SQLite)                    │
│  └─ Sync to Notion research database               │
│                                                      │
│  Phase 5: DELIVER                                   │
│  ├─ Format Telegram digest                         │
│  ├─ Send to all configured platforms               │
│  └─ Log to simulation events                       │
└─────────────────────────────────────────────────────┘
```

## Relevance Scoring (The Key Differentiator)

This is what makes it intelligent vs a dumb keyword filter.

### Stage 1: Fast Filter (No LLM, Instant)

Score every paper (100-200) with keyword/entity matching:

```python
def fast_score(paper, profile, knowledge_graph):
    score = 0.0

    # Keyword match against title + abstract
    text = (paper.title + " " + paper.abstract).lower()
    for keyword, weight in profile.interests.items():
        if keyword in text:
            score += weight  # e.g., "multi-agent": 3.0, "llm": 2.0

    # Knowledge graph entity match
    for entity in knowledge_graph.entities:
        if entity.name.lower() in text:
            score += entity.importance * 2.0
            # Bonus if entity is YOUR project
            if entity.entity_type == "PROJECT":
                score += 3.0

    # Category bonus
    if "cs.MA" in paper.categories:  # Multi-agent systems
        score += 2.0
    if "cs.AI" in paper.categories:
        score += 1.0

    # Author familiarity (have you seen their work before?)
    for author in paper.authors:
        if knowledge_graph.has_entity(author, "PERSON"):
            score += 1.5

    return score
```

**Result**: From 150 papers → top 20 candidates. Cost: $0 (no LLM).

### Stage 2: Deep Scoring (LLM, Top 20 Only)

For the top 20 candidates, use an LLM to assess relevance:

```
System: You are a research relevance scorer. Rate how relevant
        this paper is to the researcher's active projects and
        interests. Score 0-10.

User: RESEARCHER CONTEXT:
      Active projects: {from knowledge graph}
      - Multi-Agent Orchestration (LangGraph, GRPO, persona evolution)
      - Velox_AI (RAG architecture)
      - Knowledge MindGraph (entity extraction, GraphRAG)

      Research interests: {from profile}
      - Multi-agent systems, LLM agents, reinforcement learning
      - Prompt optimization, knowledge graphs, reasoning

      PAPER:
      Title: {title}
      Abstract: {abstract}
      Categories: {categories}

      Score 0-10 and explain in one sentence WHY it's relevant
      (or not). Return JSON: {"score": X, "reason": "..."}
```

**Result**: Top 20 → ranked by LLM score → top 5 selected. Cost: ~$0.01 (20 calls to gpt-4o-mini).

## Research Interest Profile

Stored in `data/research_profile.yaml`:

```yaml
# Auto-populated from knowledge graph + manually editable
categories:
  - cs.AI    # Artificial Intelligence
  - cs.LG    # Machine Learning
  - cs.CL    # Computation and Language (NLP/LLMs)
  - cs.MA    # Multi-Agent Systems
  - stat.ML  # Machine Learning (stats)

interests:
  # keyword: weight (higher = more important)
  multi-agent: 3.0
  orchestration: 3.0
  llm agent: 3.0
  reinforcement learning: 2.5
  grpo: 3.0
  prompt optimization: 2.5
  knowledge graph: 2.5
  rag: 2.5
  reasoning: 2.0
  fine-tuning: 2.0
  transformer: 1.5
  attention mechanism: 1.5
  chain of thought: 2.0
  tool use: 2.5
  code generation: 2.0
  evaluation: 1.5
  benchmark: 1.0

# Papers you've already read (auto-tracked)
read_papers: []

# Authors you follow
followed_authors:
  - "Yao, Shunyu"      # ReAct, Tree of Thoughts
  - "Wei, Jason"        # Chain of Thought
  - "Schick, Timo"     # Toolformer
  - "Wang, Guanzhi"    # Voyager
```

Editable via Telegram: `"interest: add swarm intelligence 2.5"` or `"follow: Anthropic Research"`.

## Paper Summary Format

### Telegram Digest

```
📚 AI RESEARCH DIGEST — March 25, 2026

━━━━━━━━━━━━━━━━━━━━

1️⃣ STRONG MATCH (9.2/10)
"Self-Evolving Multi-Agent Prompts via Competitive Sampling"
Authors: Chen et al. (Stanford)
🏷️ cs.AI, cs.MA

📝 Proposes agents that evolve their own prompts through group
competition — similar to your GRPO but with inter-agent dynamics.

💡 YOU COULD USE THIS: Apply competitive sampling between your
researcher/writer/reviewer agents in Enhanced Swarm. Could
improve convergence speed.

🔗 https://arxiv.org/abs/2603.xxxxx

━━━━━━━━━━━━━━━━━━━━

2️⃣ HIGH MATCH (8.1/10)
"GraphRAG 2.0: Hierarchical Retrieval over Knowledge Graphs"
Authors: Microsoft Research
🏷️ cs.CL, cs.IR

📝 Extends GraphRAG with hierarchical community detection for
better retrieval on large graphs. 40% improvement over flat search.

💡 YOU COULD USE THIS: Your MindGraph retriever uses flat
local_search. This hierarchical approach could improve deep_query
results as the graph grows past 500 entities.

🔗 https://arxiv.org/abs/2603.xxxxx

━━━━━━━━━━━━━━━━━━━━

[... 3 more papers ...]

━━━━━━━━━━━━━━━━━━━━

📊 Scanned: 147 papers | Matched: 23 | Shown: 5
Reply "paper 1" for full abstract | "read 1" to mark as read
```

### Interactive Commands

| Command | What It Does |
|---------|-------------|
| `papers` | Today's digest (or re-send if already generated) |
| `papers weekly` | Last 7 days, top 10 |
| `paper 3` | Full abstract + PDF link for paper #3 |
| `read 1` | Mark paper #1 as read, extract to knowledge graph |
| `implement 2` | Add paper #2's technique to your implementation queue |
| `interest: add moe 2.0` | Add "moe" (mixture of experts) with weight 2.0 |
| `follow: Hinton` | Add Geoffrey Hinton to followed authors |

## Database Schema

New table in `data/papers.db`:

```sql
CREATE TABLE papers (
    id TEXT PRIMARY KEY,           -- arXiv ID (e.g., "2603.12345")
    title TEXT NOT NULL,
    authors TEXT NOT NULL,          -- JSON array of author names
    abstract TEXT NOT NULL,
    categories TEXT NOT NULL,       -- JSON array of categories
    pdf_url TEXT NOT NULL,
    arxiv_url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    fast_score REAL DEFAULT 0,     -- Stage 1 keyword score
    llm_score REAL DEFAULT 0,      -- Stage 2 LLM relevance score
    relevance_reason TEXT DEFAULT '',
    summary TEXT DEFAULT '',        -- LLM-generated summary
    technique TEXT DEFAULT '',      -- Key technique extracted
    use_case TEXT DEFAULT '',       -- How YOU could use this
    status TEXT DEFAULT 'new',     -- new, sent, read, implementing, implemented, skipped
    discovered_at TEXT NOT NULL,
    read_at TEXT,
    digest_date TEXT               -- which daily digest included this
);

CREATE TABLE research_interests (
    keyword TEXT PRIMARY KEY,
    weight REAL DEFAULT 1.0,
    added_at TEXT NOT NULL
);

CREATE TABLE followed_authors (
    name TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE INDEX idx_papers_status ON papers(status);
CREATE INDEX idx_papers_score ON papers(llm_score DESC);
CREATE INDEX idx_papers_date ON papers(published_at DESC);
```

## Knowledge Graph Integration

Every paper in the digest feeds into MindGraph:

```
Entities extracted:
  [RESEARCH_PAPER] "Self-Evolving Multi-Agent Prompts" (importance: 9.2)
  [PERSON] "Chen" (author)
  [CONCEPT] "Competitive Sampling" (technique)
  [TECHNOLOGY] "Multi-Agent Prompts" (method)

Relations created:
  Chen --WORKS_ON--> "Self-Evolving Multi-Agent Prompts"
  "Competitive Sampling" --IMPROVES--> "Multi-Agent Systems"
  "Self-Evolving Multi-Agent Prompts" --REFERENCES--> GRPO
  Yash --INTERESTED_IN--> "Competitive Sampling"
```

Over weeks, the knowledge graph builds a **research landscape** — showing which techniques connect to which papers, which authors work on what, and how everything relates to YOUR projects.

## Notion Sync

Papers sync to the existing Notion Weekly AI Research database:

| Property | Type | Maps From |
|----------|------|-----------|
| Title | Title | paper title |
| Authors | Text | authors (joined) |
| Score | Number | LLM relevance score |
| Categories | Multi-select | arXiv categories |
| Summary | Text | LLM summary |
| Technique | Text | key technique |
| Use Case | Text | how you could use it |
| Status | Select | new/read/implementing/implemented |
| arXiv URL | URL | arxiv_url |
| PDF | URL | pdf_url |
| Date | Date | published_at |

## Files to Create

| File | Purpose |
|------|---------|
| `jobpulse/arxiv_agent.py` | Main agent: fetch, rank, summarize, deliver |
| `jobpulse/arxiv_scorer.py` | Fast filter + LLM relevance scoring |
| `data/research_profile.yaml` | Editable interests + followed authors |

## Files to Modify

| File | Change |
|------|--------|
| `jobpulse/command_router.py` | Expand ARXIV patterns for `paper N`, `read N`, `interest:`, `follow:` |
| `jobpulse/dispatcher.py` | Replace placeholder `_handle_arxiv` with real agent call |
| `jobpulse/swarm_dispatcher.py` | Wire arxiv agent into swarm |
| `jobpulse/auto_extract.py` | Already has `extract_from_paper_summary()` — wire to new agent |
| `jobpulse/morning_briefing.py` | Add "Top paper" section to morning digest |
| `scripts/install_cron.py` | Update 7:57am cron to call new agent instead of shell script |
| `requirements.txt` | Add `arxiv>=2.0.0`, `pyyaml>=6.0` |

## Env Vars

```env
# No new API keys needed — arXiv is completely free and open
ARXIV_CATEGORIES=cs.AI,cs.LG,cs.CL,cs.MA,stat.ML
ARXIV_MAX_RESULTS=200          # Papers to fetch per scan
ARXIV_TOP_N=5                   # Papers to include in digest
ARXIV_MIN_SCORE=5.0             # Minimum LLM score for digest
NOTION_RESEARCH_DB_ID=...       # Already exists in .env
```

## Schedule

| Time | Action |
|------|--------|
| 7:57 AM | Full daily digest (fetch → rank → summarize → deliver) |
| On demand | `papers` command re-sends today's digest |
| On demand | `papers weekly` generates 7-day compilation |
| On `read N` | Extracts paper into knowledge graph |

## GRPO Integration

The summary generation uses GRPO (already built in `shared/experiential_learning.py`):

1. Generate 3 summary candidates at different temperatures
2. Score each by: conciseness, actionability, connection to user's work
3. Pick the best one
4. Store what made it best as experience for future summaries

Over time, summaries get more tailored — the agent learns that you prefer bullet points over paragraphs, that you care more about implementation details than theoretical contributions, etc.

## Persona Evolution

The arXiv agent evolves its scoring and summarization style:

| Week | Base Behavior | Learned Behavior |
|------|--------------|-------------------|
| 1 | Score by keyword match | Same |
| 2 | Same | "Yash reads papers about agents more than pure theory" |
| 4 | Same | "Prioritize papers with code/implementation. Skip survey papers." |
| 8 | Same | "Authors from Anthropic, DeepMind, MIT,  Stanford consistently score high. Boost them." |

## Cost Estimate

| Component | Per Run | Daily | Monthly |
|-----------|---------|-------|---------|
| arXiv API | Free | Free | Free |
| Fast filter (local) | $0 | $0 | $0 |
| LLM scoring (20 papers) | ~$0.01 | $0.01 | $0.30 |
| LLM summaries (5 papers) | ~$0.01 | $0.01 | $0.30 |
| GRPO (3x candidates for 5 papers) | ~$0.02 | $0.02 | $0.60 |
| Knowledge extraction | ~$0.005 | $0.005 | $0.15 |
| **Total** | | **~$0.045/day** | **~$1.35/month** |

## Comparison: Current vs New

| Aspect | Current (shell script) | New (intelligent agent) |
|--------|----------------------|------------------------|
| Fetching | `claude -p` ad-hoc | arXiv API, structured |
| Ranking | None (first 5 results) | 2-stage: keyword + LLM scoring |
| Personalization | None | Knowledge graph + interest profile |
| Summaries | Generic | Tailored to your projects |
| Persistence | None (ephemeral) | SQLite + Notion + knowledge graph |
| Learning | None | GRPO + persona evolution |
| Cost | Varies (claude -p) | ~$0.045/day predictable |
| Integration | Standalone | Connected to briefing, MindGraph, Notion |

## Success Metrics

- Papers discovered per week (target: 35)
- Read rate (sent → read, target: 60%+)
- Implementation rate (read → implementing, tracking)
- Relevance accuracy (user skips < 20% of sent papers)
- Knowledge graph growth from papers (entities/week)
- Summary quality (persona evolution score trend)
