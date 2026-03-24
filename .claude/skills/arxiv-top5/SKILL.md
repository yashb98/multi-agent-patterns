---
name: arxiv-top5
argument-hint: "[optional: specific topic like 'multi-agent' or 'RAG']"
description: Fetch and summarize the top 5 AI research papers from arXiv this week
---

Fetch the top 5 AI research papers from arXiv. Topic filter: $ARGUMENTS

1. Use WebFetch to get https://arxiv.org/list/cs.AI/recent
2. Parse the listing to identify the 5 most relevant papers
3. If $ARGUMENTS specifies a topic (e.g., "multi-agent", "RAG", "reasoning"), filter for that topic
4. For each paper, extract:
   - **Title**
   - **Authors** (first 3 + et al.)
   - **arXiv ID and link**
   - **Abstract summary** (2-3 sentences)
   - **Why it matters** (1 sentence on practical impact)
5. Format as a clean markdown table or numbered list
6. If any paper is directly relevant to this multi-agent patterns project, flag it with a note
