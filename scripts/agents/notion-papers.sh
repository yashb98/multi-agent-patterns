#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Notion Weekly AI Papers
# Runs: Monday 8:33am
#
# 1. Fetches top 5 AI papers from arXiv + HuggingFace
# 2. Generates 500-word summary for each using LLM
# 3. Creates a Notion page in "Weekly AI Research" database
# 4. Sends Telegram notification when done
# ─────────────────────────────────────────────────────────────────

PROJECT_DIR="/Users/yashbishnoi/Downloads/multi_agent_patterns"
LOG_DIR="$PROJECT_DIR/logs"
WEEK_DATE=$(date +%Y-%m-%d)

source "$PROJECT_DIR/.env"
mkdir -p "$LOG_DIR"

echo "[$(date)] Notion weekly papers started..." >> "$LOG_DIR/notion.log"

/Users/yashbishnoi/.local/bin/claude -p "
You have access to Notion via MCP and WebFetch. Follow these steps:

STEP 1 — FETCH PAPERS:
Use WebFetch to get:
  a) https://arxiv.org/list/cs.AI/recent
  b) https://huggingface.co/papers
Pick the top 5 most impactful papers. Prioritize topics: multi-agent systems, LLM agents, RAG, fine-tuning, reasoning, knowledge graphs, reinforcement learning for LLMs.

STEP 2 — FETCH ABSTRACTS:
For each paper, use WebFetch on https://arxiv.org/abs/PAPER_ID to get the full abstract.

STEP 3 — GENERATE SUMMARIES:
For each paper, write a 500-word summary with these sections:
- **Problem**: What gap or challenge does this address?
- **Approach**: What is the method, architecture, or technique?
- **Key Results**: Benchmarks, numbers, comparisons that matter
- **Why It Matters**: Practical relevance for AI engineers working with agents, RAG, or LLMs
- **Practical Takeaways**: What can a practitioner apply today?

STEP 4 — CREATE NOTION PAGE:
Use Notion MCP to create a new page with:
- Title: 'AI Research — Week of $WEEK_DATE'
- If you can find a database called 'Weekly AI Research' or 'AI Research Weekly', create the page there.
- If no such database exists, create the page at the workspace top level.
- Page content should have:
  - A 'Key Themes This Week' section at the top (3-5 sentences summarizing common threads)
  - Then each paper as a section: Title (as heading), Authors, arXiv link, 500-word summary

STEP 5 — CONFIRM:
After creating the page, print: NOTION_DONE|{page_title}

IMPORTANT: Actually create the Notion page. Don't just describe what you would do.
" --dangerously-skip-permissions 2>> "$LOG_DIR/notion.log" | tee -a "$LOG_DIR/notion.log" | while IFS= read -r line; do
    if echo "$line" | grep -q "NOTION_DONE"; then
        PAGE_TITLE=$(echo "$line" | sed 's/NOTION_DONE|//')
        # Send Telegram notification
        MSG="📚 Weekly AI research summary posted to Notion

\"$PAGE_TITLE\"

5 papers with 500-word summaries each. Check Notion for details."
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -H "Content-Type: application/json" \
            -d "$(python3 -c "import json; print(json.dumps({'chat_id': '$TELEGRAM_CHAT_ID', 'text': '''$MSG'''}))")" > /dev/null
    fi
done

echo "[$(date)] Notion weekly papers done." >> "$LOG_DIR/notion.log"
