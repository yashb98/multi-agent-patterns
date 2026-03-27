# Agents

Two agent systems: orchestration agents (blog generation) and JobPulse agents (daily automation).

## Orchestration Agents (shared/agents.py)

### Researcher (`researcher_node`)
- Gathers facts, technical details, trends, expert opinions
- Reads: `topic`, `review_feedback` · Writes: `research_notes` (append-only)

### Writer (`writer_node`)
- Transforms research into polished articles
- Reads: `topic`, `research_notes`, `review_feedback` · Writes: `draft`

### Reviewer (`reviewer_node`)
- Evaluates quality, returns structured JSON scores
- Reads: `draft`, `topic` · Writes: `review_feedback`, `review_score`, `review_passed`

### Fact Checker (`fact_check_node`)
- Extracts all verifiable claims from the draft (benchmark, date, attribution, comparison, technical)
- Verifies each claim against: research notes → paper abstract → web search (DuckDuckGo) → cached facts
- Deterministic scoring: VERIFIED +1.0, INACCURATE -2.0, EXAGGERATED -1.0, UNVERIFIED -0.5/-1.5
- Hard accuracy gate: 9.5/10 floor, 9.7 target
- Generates targeted revision notes with specific fix instructions per failed claim
- SQLite cache (`data/verified_facts.db`) stores previously verified facts for instant reuse
- Unified module (`shared/fact_checker.py`) used by both orchestration patterns and blog generator
- Reads: `draft`, `topic`, `research_notes` · Writes: `extracted_claims`, `claim_verifications`, `accuracy_score`, `accuracy_passed`, `fact_revision_notes`

## JobPulse Agents (jobpulse/)

### Gmail Agent (`gmail_agent.py`)
- Scans inbox via Gmail API, classifies emails with LLM (SELECTED/INTERVIEW/REJECTED/OTHER)
- **Pre-classifier** (`email_preclassifier.py`): rule-based triage eliminates 70-85% of unnecessary LLM calls
- Sends Telegram alerts for recruiter emails, auto-extracts knowledge (company, role)
- Uses evolved persona — learns to skip automated rejections over time

### Email Pre-Classifier (`email_preclassifier.py`)
- Rule-based pre-classification before LLM — eliminates 70-85% of unnecessary LLM calls
- 4-tier system: Learning → Static Rules → LLM Fallback → User Feedback
- Static rules: sender patterns, domain patterns, subject keywords, dual subject+body match
- Categories: auto-OTHER (newsletters, receipts), auto-REJECTED (template rejections), auto-SELECTED (congratulations patterns)
- Evidence-based attribution: every decision logged with rule name, matched patterns, reasoning
- Adaptive audit decay: 50% → 30% → 20% → 10% as classifier processes more emails
- Learned rules: dynamically generated from LLM audits + user feedback (`data/gmail_learned_rules.json`)
- Telegram review flow: ✅ (correct), ❌ (wrong), 🔄 CATEGORY (reclassify) — user corrections have 2x weight
- Auto-graduation: exits learning phase when accuracy > 95% on last 50 audits (min 100 emails, 20 audits)
- Rules priority: dual-match → ATS domain → recruiter hints → sender OTHER → domain OTHER → subject OTHER → learned

### Email Review (`email_review.py`)
- Telegram review flow for pre-classifier decisions (mirrors `approval.py` pattern)
- One pending review at a time, auto-expires after 1 hour
- ✅ confirms classification, boosts rule confidence
- ❌ marks rule as incorrect, reduces confidence (disabled after 3 corrections)
- 🔄 CATEGORY reclassifies email and updates SQLite record
- User feedback has 2x weight compared to LLM audit corrections

### Calendar Agent (`calendar_agent.py`)
- Fetches today + tomorrow events via Google Calendar API
- Formats for Telegram display, sends upcoming reminders (2-hour window)

### GitHub Agent (`github_agent.py`)
- Fetches yesterday's commits using Commits API per-repo (not Events API)
- Fetches trending repos via GitHub Search API

### arXiv Agent (`arxiv_agent.py`)
- Fetches daily AI papers from arXiv and ranks by **broad AI impact** (not project-specific)
- Ranking criteria: novelty, significance, practical value, breadth of applicability
- Category tags per paper: [LLM, Agents, Vision, RL, Efficiency, Safety, Reasoning]
- Each paper includes key technique + practical takeaway
- SQLite `papers.db` tracks all papers with read/unread status
- Interactive commands: "paper 3" (full abstract), "read 1" (mark read), "papers stats" (counts + category breakdown)
- Digest sent to Research bot

### Notion Agent (`notion_agent.py`)
- Manages daily tasks (to_do blocks), creates/completes/removes tasks
- Fuzzy matching for "mark X done" and "remove X" (word overlap + number normalization)
- Duplicate detection on task creation (fuzzy score >= 0.7)
- Big task detection + LLM subtask suggestion (tasks >12 words or with conjunctions)
- Priority levels: `!!` = urgent (red), `!` = high (yellow)
- Due dates via NLP: "by Friday", "by March 30", "tomorrow", "today"
- Weekly planning: fetch undone tasks from past 7 days, carry forward to today
- All API calls via curl (avoids Python SSL issues)

### Budget Agent (`budget_agent.py`)
- Parses natural language ("spent 15 on lunch"), classifies category (keyword + LLM)
- Stores in SQLite, syncs Actual column to Notion Weekly Budget Sheet
- 17 categories across Income/Fixed/Variable/Savings sections
- Set planned budgets per category (`set budget groceries 50`)
- Recurring expenses: daily/weekly/monthly auto-log rules
- Budget alerts: warns when spending hits 80% of planned amount
- Undo last transaction: deletes from SQLite + recalculates Notion totals
- **Item + store NLP extraction**: "yogurt and protein shake at Tesco" extracts items and store (50+ known UK stores)
- **Category sub-pages**: each of the 17 categories gets a Notion sub-page with individual transaction rows (Amount, Date, Items, Store, Running Total)
- **Category links**: every budget row's Notes column links to its detail sub-page
- **Salary timesheet link**: Salary row links to the timesheet page
- **Weekly comparison**: "budget compare" shows this week vs last week per category with delta
- **Historical pace alerts**: e.g. "Groceries £35 so far (was £20 by this day last week)"
- **Dataset export**: "budget-export" generates CSV with 12 columns for ML analysis
- **Weekly comparison in morning briefing**: briefing includes week-over-week spending delta

### Budget Tracker (`budget_tracker.py`)
- **Weekly archival**: Sunday 7am cron archives current week's budget sheet and creates a new one carrying over planned amounts
- Manages category sub-page lifecycle (create, update running totals, link from parent row)
- Weekly comparison engine: computes per-category deltas between current and previous week
- Used by `budget_agent.py` for sub-page sync and by `morning_briefing.py` for weekly comparison

### Telegram Listener (`telegram_listener.py`)
- Long-polling daemon, instant replies (1-3s)
- Routes through Enhanced Swarm dispatcher (or flat, via env var)
- Multi-bot routing: 4 bots (Main, Budget, Research, Alert) each with dedicated chat
- Falls back to main bot token when dedicated bot env vars are not set

### Morning Briefing (`morning_briefing.py`)
- Collects from all 7 agents (including arXiv), assembles Telegram message
- Evolves briefing persona after each run
- RLM synthesis when data exceeds 5K chars
- Includes weekly budget comparison (this week vs last week per category)

### Weekly Report Agent (`weekly_report.py`)
- Aggregates 7-day data from all agents (tasks, emails, commits, budget, calendar)
- Generates formatted summary with trends and highlights
- Triggered via Telegram ("weekly report") or CLI

### Voice Handler (`voice_handler.py`)
- Receives Telegram voice messages, downloads the audio file
- Transcribes via OpenAI Whisper API
- Passes transcribed text through normal intent classification and dispatch

### A/B Testing (`ab_testing.py`)
- Runs prompt variants side-by-side for agents (budget classification, briefing synthesis)
- Tracks which variant produces higher scores over N trials
- Results stored in SQLite, exportable via backup system

## Platform Adapters (`jobpulse/platforms/`)

### Base Adapter (`base.py`)
- Abstract base class for all platform adapters
- Defines `poll_continuous()`, `send_message()`, `receive_message()` interface

### Telegram Adapter (`telegram_adapter.py`)
- Long-polling implementation for Telegram Bot API
- Voice message support via Whisper transcription

### Slack Adapter (`slack_adapter.py`)
- Polls Slack channels via Slack Web API
- Maps Slack messages through the same command router and dispatcher

### Discord Adapter (`discord_adapter.py`)
- Polls Discord channels via Discord API
- Filters by configured user ID to avoid responding to others

### Multi-Listener (`multi_listener.py`)
- Starts all configured platform adapters in parallel threads
- Only starts adapters whose tokens are present in env vars

### Webhook Server (`webhook_server.py`)
- FastAPI server (port 8080) for receiving inbound webhooks
- Registers callback URLs, routes payloads through dispatcher
- Hosts health API and export endpoint

## Remote Control Agents (jobpulse/)

### Conversation Handler (`conversation.py`)
- Free-form LLM chat with project context injection
- Maintains per-session conversation history
- Uses `CONVERSATION_MODEL` (default gpt-4o-mini)

### Remote Shell (`remote_shell.py`)
- Execute shell commands via Telegram (`run: <cmd>` or `$ <cmd>`)
- Whitelisted commands only for safety
- Returns stdout/stderr with truncation for long output

### Git Operations (`git_ops.py`)
- `git status`, `git log`, `git diff`, `git branch` — formatted for Telegram
- `commit: <message>` — stages all + commits with approval flow
- `push` — push to remote with approval flow
- Uses `jobpulse/approval.py` for yes/no confirmation on destructive ops

### File Operations (`file_ops.py`)
- `show: <filepath>` — read file content, paginated
- `logs` / `errors` — tail recent log files or agent errors
- `more` / `next` — pagination for long outputs
- `status` — system dashboard (daemon health, agent stats, API rates)

### Approval Flow (`approval.py`)
- One pending approval at a time, auto-expires after timeout
- Telegram listener checks for approval replies before classifying messages
- Used by git commit, push, and Claude Code bash command approval

## NLP Intent Classifier (`nlp_classifier.py`)

3-tier classification pipeline that routes all incoming messages before they reach agents:

| Tier | Method | Speed | Cost | When Used |
|------|--------|-------|------|-----------|
| 1 | Regex patterns | Instant | Free | Exact command matches ("show tasks", "calendar", "budget") |
| 2 | Semantic embeddings (all-MiniLM-L6-v2) | ~5ms | Free (local) | Fuzzy/natural phrasing ("what's on my schedule?") |
| 3 | LLM fallback (gpt-4o-mini) | ~500ms | $0.001 | Truly ambiguous messages |

- 250+ training examples across 31 intents in `data/intent_examples.json`
- Continuous learning: when Tier 3 fires, the result is stored as a new Tier 2 example
- Embedding model loaded once at startup, cached in memory

## Salary/Hours Agent

- Tracks work hours at £13.99/hr with tax calculation (20%) and savings suggestion (30% of after-tax)
- Notion timesheet sync with table format (Hours, Rate, Date, Total)
- Sunday-based work week tracking
- Supports word numbers ("six hours and thirty minutes") and past dates ("worked 8h on monday")
- "saved"/"transferred" confirms savings transfer to designated account
- "undo hours" shows last 5 entries for selective removal, rebuilds Notion timesheet

## Enhanced Swarm Dispatcher (`swarm_dispatcher.py`)

Replaces flat dispatch with adaptive intelligence:
1. **Task Analyzer** — decomposes intent into priority queue
2. **Experience Memory** — retrieves learned patterns per intent
3. **Execute** — runs agents with GRPO sampling where flagged
4. **RLM Synthesis** — recursive LLM for large-context assembly
5. **Store Experience** — saves what worked for future runs

## LLM Configuration

```python
get_llm(temperature=0.7, model="gpt-4o-mini")  # shared/agents.py
```

JobPulse agents use OpenAI directly for classification (gpt-4o-mini).
RLM uses configurable backend via `RLM_BACKEND` env var.

## State Model (AgentState)

```python
AgentState(TypedDict):
    topic: str                              # Immutable input
    research_notes: Annotated[list, add]    # Append-only
    draft: str                              # Replace
    review_feedback: Optional[str]          # Replace
    review_score: float                     # 0-10
    review_passed: bool
    iteration: int
    current_agent: str
    agent_history: Annotated[list, add]     # Append-only
    pending_tasks: list[dict]               # Swarm only
    final_output: str
    extracted_claims: list[dict]             # Fact checker claims
    claim_verifications: list[dict]          # Fact checker results
    accuracy_score: float                    # 0-10, target 9.7
    accuracy_passed: bool                    # True if >= 9.5
    fact_revision_notes: Optional[str]       # Fix instructions
```

## Pattern Topologies

```
Hierarchical:   Supervisor ←→ {Researcher, Writer, Reviewer}
Peer Debate:    Round 1 pipeline → Round 2+ cross-critique
Dynamic Swarm:  Analyzer → Queue → Executor → Re-analyze loop
Enhanced Swarm: Dynamic + Factory + GRPO + Persona + RLM
```
