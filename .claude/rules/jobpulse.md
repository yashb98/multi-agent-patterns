# Rules: JobPulse Agents (jobpulse/**/*)

## Dual Dispatcher Invariant
When adding a new intent or agent:
1. Add handler to jobpulse/dispatcher.py AGENT_MAP
2. Add SAME handler to jobpulse/swarm_dispatcher.py AGENT_MAP
3. Add intent string to the correct *_INTENTS set in both files
4. Add NLP examples to shared/nlp_classifier.py
5. Add tests for the new intent in both dispatch paths
NEVER update only one dispatcher. This has caused production bugs (see mistakes.md 2026-03-30).

## Telegram Message Handling
- One handler per message. Main bot MUST exclude dedicated bot intents.
- 30s dedup guard on all concurrent write paths (budget, tasks, hours).
- Never wait for Telegram replies in Claude Code — poll API directly.
- Voice messages: Whisper adds punctuation. classify() strips trailing [.!?]+ before matching.

## Budget Rules
- 17 categories — check BUDGET_CATEGORIES before adding new ones
- Recurring transactions: "recurring: 10 on spotify monthly"
- Undo reverses last transaction only. One level of undo.
- Weekly archival runs Sunday 7am cron — never archive manually in agent code.

## API Rules
- Always HTTPS for external APIs (arXiv burned rate limit on HTTP→HTTPS redirect)
- Always handle 429 with exponential backoff (3 attempts: 5s/10s/15s)
- Always include User-Agent header for arxiv.org requests
- Gmail pre-classifier runs BEFORE LLM classification to save 70-85% of API costs

## Surgical Changes
- Match existing agent style. Don't refactor working agents while fixing a bug.
- Don't add docstrings, comments, or type annotations to code you didn't change.
- Every changed line should trace directly to the request.
- If you notice unrelated dead code, mention it — don't delete it.

## Database Safety
- Production DBs live in data/*.db — tests MUST NEVER touch these
- All test fixtures use tmp_path or monkeypatch DB_PATH
- budget_agent.py add_transaction() has dedup guard — don't add another
