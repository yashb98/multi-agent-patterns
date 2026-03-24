#!/bin/bash
# Daily arXiv top 5 AI papers → Telegram
# Runs via crontab at 8am daily

set -euo pipefail

PROJECT_DIR="/Users/yashbishnoi/Downloads/multi_agent_patterns"
LOG_FILE="$PROJECT_DIR/scripts/arxiv-daily.log"

# Load env vars
source "$PROJECT_DIR/.env"

echo "[$(date)] Starting arXiv daily fetch..." >> "$LOG_FILE"

# Run Claude in non-interactive mode
/Users/yashbishnoi/.local/bin/claude -p "
You are a research assistant. Do the following:

1. Use WebFetch to get https://arxiv.org/list/cs.AI/recent
2. Also check https://huggingface.co/papers for trending papers
3. Pick the 5 most impactful papers this week — prioritize: multi-agent systems, LLM agents, reinforcement learning, orchestration patterns, reasoning
4. For each paper: title, authors (first 3), arXiv link, 2-3 sentence summary, why it matters
5. Flag papers relevant to multi-agent orchestration

Then send the summary to Telegram by running this bash command:

source $PROJECT_DIR/.env
curl -s -X POST \"https://api.telegram.org/bot\${TELEGRAM_BOT_TOKEN}/sendMessage\" \\
  -H \"Content-Type: application/json\" \\
  -d \"\$(python3 -c \"import json,sys; print(json.dumps({'chat_id': 1309133583, 'text': sys.stdin.read()}))\" <<< \"\$MSG\")\"

Format with emoji headers and dividers. Start with: 📚 TOP 5 AI PAPERS — [today's date]
" --dangerously-skip-permissions 2>> "$LOG_FILE"

echo "[$(date)] Done." >> "$LOG_FILE"
