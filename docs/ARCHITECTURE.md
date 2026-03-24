# Architecture — Every Function, Every Flow

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TELEGRAM (You)                                │
│   Send: "spent 5 coffee" / "show tasks" / "calendar" / "budget"     │
└─────────────────────┬───────────────────────────────────────────────┘
                      │ long-poll (1-3s response)
┌─────────────────────▼───────────────────────────────────────────────┐
│                    TELEGRAM DAEMON (launchd)                          │
│   telegram_listener.py → command_router.py → dispatcher.py           │
│                      ↓                              ↓                │
│              classify intent              dispatch to agent           │
│              (rule-based + LLM)           (returns reply text)       │
└──────────┬──────┬──────┬──────┬──────┬──────┬──────┬───────────────┘
           │      │      │      │      │      │      │
     ┌─────▼┐ ┌──▼──┐ ┌─▼──┐ ┌▼───┐ ┌▼───┐ ┌▼───┐ ┌▼────────┐
     │Gmail │ │Cal. │ │Git │ │Not.│ │Bud.│ │arXiv│ │MindGraph│
     │Agent │ │Agent│ │Agent│ │Agent│ │Agent│ │Agent│ │  App    │
     └──┬───┘ └──┬──┘ └──┬──┘ └─┬──┘ └─┬──┘ └──┬──┘ └──┬─────┘
        │        │       │      │      │       │       │
        ▼        ▼       ▼      ▼      ▼       ▼       ▼
   ┌─────────────────────────────────────────────────────────┐
   │              EVENT LOGGER (simulation_events)            │
   │   Every agent action logged → feeds MindGraph + timeline │
   └─────────────────────────┬───────────────────────────────┘
                             │
   ┌─────────────────────────▼───────────────────────────────┐
   │              SQLITE DATABASES (data/)                     │
   │   mindgraph.db: knowledge_entities + knowledge_relations  │
   │                 + simulation_events                       │
   │   jobpulse.db:  processed_emails + gmail_check_state     │
   │   budget.db:    transactions + planned_budgets            │
   └─────────────────────────────────────────────────────────┘
```

## Scheduling Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  PLAN A: macOS (Primary)                                         │
│                                                                   │
│  launchd daemon (com.jobpulse.daemon)                            │
│    └── python -m jobpulse.runner daemon                          │
│        └── telegram_listener.poll_continuous()                   │
│            └── long-poll Telegram → classify → dispatch → reply  │
│                                                                   │
│  crontab (10 jobs):                                              │
│    8:03am  → briefing      (morning digest)                      │
│    1/3/5pm → gmail         (recruiter scan)                      │
│    9/12/3  → calendar-remind (2-hour lookahead)                  │
│    */10min → health        (daemon watchdog)                     │
│    7:57am  → arxiv-daily   (claude -p + WebFetch)                │
│    Mon 8:33→ notion-papers (claude -p + Notion MCP)              │
├─────────────────────────────────────────────────────────────────┤
│  PLAN C: GitHub Actions (Backup)                                 │
│                                                                   │
│    morning-briefing.yml  → 8:30am (failover if 8:03 missed)     │
│    gmail-check.yml       → 1/3/5pm                               │
│    telegram-poll.yml     → */5min (backup command processing)    │
│    health-check.yml      → */10min (watchdog)                    │
│    failover-briefing.yml → 8:30am (sends if primary missed)     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module-by-Module Breakdown

### jobpulse/command_router.py

**Purpose**: Classifies Telegram messages into intents.

| Function | What It Does |
|----------|-------------|
| `classify(text)` | Main entry — tries rules, then task-list detection, then LLM fallback |
| `classify_rule_based(text)` | Regex patterns against 17 intents. First match wins. Free + instant. |
| `classify_llm(text)` | Sends to gpt-4o-mini for ambiguous messages. ~$0.00002/call. |
| `is_task_list(text)` | Detects multi-line messages as task lists (2+ short lines = tasks). |

**Intents**: CREATE_TASKS, SHOW_TASKS, COMPLETE_TASK, CALENDAR, CREATE_EVENT, GMAIL, GITHUB, TRENDING, BRIEFING, ARXIV, LOG_SPEND, LOG_INCOME, LOG_SAVINGS, SET_BUDGET, SHOW_BUDGET, HELP, UNKNOWN

### jobpulse/dispatcher.py

**Purpose**: Maps each intent to an agent function, executes, returns reply text.

| Handler | Intent | Agent Called |
|---------|--------|------------|
| `_handle_show_tasks` | SHOW_TASKS | `notion_agent.get_today_tasks()` |
| `_handle_create_tasks` | CREATE_TASKS | `notion_agent.create_tasks_batch()` |
| `_handle_complete_task` | COMPLETE_TASK | `notion_agent.complete_task()` |
| `_handle_calendar` | CALENDAR | `calendar_agent.get_today_and_tomorrow()` |
| `_handle_gmail` | GMAIL | `gmail_agent.check_emails()` |
| `_handle_github` | GITHUB | `github_agent.get_yesterday_commits()` |
| `_handle_trending` | TRENDING | `github_agent.get_trending_repos()` |
| `_handle_briefing` | BRIEFING | `morning_briefing.build_and_send()` |
| `_handle_log_spend` | LOG_SPEND | `budget_agent.log_transaction()` |
| `_handle_log_income` | LOG_INCOME | `budget_agent.log_transaction()` |
| `_handle_log_savings` | LOG_SAVINGS | `budget_agent.log_transaction()` |
| `_handle_set_budget` | SET_BUDGET | `budget_agent.set_budget()` |
| `_handle_show_budget` | SHOW_BUDGET | `budget_agent.get_week_summary()` |
| `_handle_help` | HELP | Returns static help text |

Every dispatch logs an `agent_action` event to the simulation log.

### jobpulse/telegram_listener.py

| Function | What It Does |
|----------|-------------|
| `poll_and_process()` | Single poll: get updates → classify → dispatch → reply. For cron. |
| `poll_continuous()` | Long-polling daemon loop. Blocks on Telegram API (30s timeout). Writes heartbeat every cycle. |
| `_parse_tasks(text)` | Splits multi-line text into individual task strings. Strips prefixes (-, •, 1., etc). |
| `_get_last_update_id()` | Reads checkpoint from `data/telegram_last_update_id.txt`. |
| `_save_last_update_id(uid)` | Saves checkpoint after processing. Prevents reprocessing. |

### jobpulse/gmail_agent.py

| Function | What It Does |
|----------|-------------|
| `check_emails()` | Fetches inbox since last check, classifies each email, stores in SQLite, sends Telegram alerts for categories 1-3, auto-extracts knowledge. Returns list of recruiter emails. |
| `_get_gmail_service()` | Builds Gmail API client using stored OAuth2 token. Auto-refreshes expired tokens. |
| `_classify_email(subject, body)` | Sends subject + 500 chars to gpt-4o-mini. Returns: SELECTED_NEXT_ROUND, INTERVIEW_SCHEDULING, REJECTED, or OTHER. |
| `_extract_body(payload)` | Recursively extracts plain text from Gmail message payload (handles multipart MIME). |
| `get_yesterday_recruiter_emails()` | Queries SQLite for yesterday's classified emails. Used by morning briefing. |

### jobpulse/calendar_agent.py

| Function | What It Does |
|----------|-------------|
| `get_today_and_tomorrow()` | Fetches events for today + tomorrow via Google Calendar API. Logs each event to simulation. |
| `get_upcoming_reminders(within_minutes)` | Finds events starting within N minutes. Used for 2-hour reminders. |
| `_get_calendar_service()` | Builds Calendar API client using OAuth2 token. Auto-refreshes. |
| `_fetch_events(service, start, end)` | Raw API call to list events in a time range. Parses to readable format. |
| `format_events(events)` | Formats as "• 10:00 AM — Event title (location)". |

### jobpulse/github_agent.py

| Function | What It Does |
|----------|-------------|
| `get_yesterday_commits()` | Lists repos pushed yesterday, then fetches commits per-repo via Commits API. Logs to simulation. |
| `get_trending_repos()` | GitHub Search API: repos created in last 7 days, sorted by stars. Returns top 5. |
| `format_commits(data)` | "3 commit(s) across repo1, repo2" + bullet list. |
| `format_trending(repos)` | Numbered list with language, stars, description, URL. |
| `_gh_api(endpoint)` | Calls GitHub API via `gh` CLI. |

### jobpulse/notion_agent.py

| Function | What It Does |
|----------|-------------|
| `get_today_tasks()` | Finds today's daily page, reads `to_do` blocks, returns checklist. |
| `create_task(title, date)` | Appends a `to_do` checkbox to today's daily page. |
| `create_tasks_batch(tasks, date)` | Batch-creates multiple `to_do` items in one API call. |
| `complete_task(task_name)` | Fuzzy-matches task name → toggles `to_do` checkbox to checked. |
| `_get_or_create_daily_page(date)` | Finds or creates "Tasks — Monday, March 24" page in Daily Tasks DB. |
| `_fuzzy_score(query, title)` | Word overlap ratio with normalization (strips punctuation, converts "one"→"1"). |
| `_normalize(text)` | Lowercase, strip punctuation, normalize number words. |
| `_notion_api(method, endpoint, data)` | Calls Notion REST API via curl (avoids Python SSL issues). |

### jobpulse/budget_agent.py

| Function | What It Does |
|----------|-------------|
| `log_transaction(text)` | Full pipeline: parse → classify → SQLite → Notion → Telegram reply with link. |
| `parse_transaction(text)` | Extracts amount + description + type from natural language. Detects income/expense/savings from keywords. |
| `classify_transaction(desc, amount, type)` | Keyword match first (free), then LLM fallback. Maps to Notion sheet categories. |
| `add_transaction(amount, desc, category, section, type)` | Stores in SQLite `transactions` table. |
| `sync_expense_to_notion(txn)` | Updates the Actual (col 2) column in the matching row of YOUR existing Notion budget sheet. Preserves all other columns. Recalculates totals. |
| `set_budget(text)` | Parses "set budget groceries 50" → updates Planned (col 1) in Notion. |
| `get_week_summary()` | Aggregates by category: income total, spending total, savings total, net. |
| `get_today_spending()` | Today's transactions with totals. |
| `_update_table_row(row_id, actual, notes)` | READS existing row first (preserves category name + planned), then updates actual + notes. |
| `_update_planned_column(row_id, amount)` | Updates col 1 (Planned) while preserving all other columns. |
| `_update_section_totals(week_start)` | Recalculates Total income, Total fixed, Total variable, Total savings, Net across all sections. |

**Notion Sheet Structure** (hardcoded row IDs for your existing sheet):
- Income: Salary, Freelance, Other, Total income
- Fixed: Rent, Utilities, Phone, Subscriptions, Insurance, Total fixed
- Variable: Groceries, Eating out, Transport, Shopping, Entertainment, Health, Misc, Total variable
- Savings: Savings, Investments, Credit card, Total savings
- Summary: Total income, Total spending, Total savings, Net

### jobpulse/morning_briefing.py

| Function | What It Does |
|----------|-------------|
| `build_and_send()` | Collects 6 sections → sends ONE Telegram message + separate todo prompt if no tasks. Logs briefing_sent event. |

**Sections**: Recruiter emails, Calendar (today + tomorrow), Notion tasks, GitHub commits, GitHub trending, Budget summary.

### jobpulse/event_logger.py

| Function | What It Does |
|----------|-------------|
| `log_event(event_type, action, content, agent_name, metadata)` | Inserts into `simulation_events` table in mindgraph.db. |
| `get_events_for_day(day)` | All events for a specific date. |
| `get_events_for_agent(agent_name)` | Recent events by agent. |
| `get_events_mentioning(entity_name)` | Events where content or metadata contains entity name. |
| `get_timeline_summary()` | Day-by-day: event count, positive/negative counts, event types. Used by D3 timeline bar. |
| `get_event_stats()` | Total events, today's count, breakdown by type. |

**Event Types**: agent_action, email_classified, calendar_event, github_activity, notion_task, research_paper, knowledge_extracted, briefing_sent, budget_transaction, error

### jobpulse/auto_extract.py

| Function | What It Does |
|----------|-------------|
| `extract_from_email(sender, subject, category, body)` | Extracts company/role entities from recruiter emails → knowledge graph. |
| `extract_from_paper_summary(title, authors, summary)` | Extracts tech/concept entities from research papers. |
| `extract_from_conversation(transcript, topic, agents)` | Extracts from multi-agent conversation transcripts. |
| `extract_from_text_input(text, source)` | Manual extraction from uploaded text. |

### jobpulse/healthcheck.py

| Function | What It Does |
|----------|-------------|
| `write_heartbeat()` | Updates `data/daemon_heartbeat.txt` with current timestamp. Called every poll cycle. |
| `check_daemon_health(max_age_minutes)` | Returns alive/dead based on heartbeat age. |
| `alert_if_down()` | Sends Telegram alert if heartbeat is stale (>10 min). |

---

## MindGraph App

### mindgraph_app/extractor.py

| Function | What It Does |
|----------|-------------|
| `extract_from_text(text, filename)` | Full pipeline: chunk → extract per chunk → dedup → store → recompute importance. |
| `extract_from_chunk(text, model)` | Sends text to LLM with extraction prompt. Returns `{entities, relationships}` JSON. |
| `chunk_text(text, max_tokens, overlap)` | Splits text into ~3000 token chunks with 200 token overlap. |
| `deduplicate_entities(all_entities)` | Case-insensitive merge by name. Keeps longer description. |

**Entity Types**: PROJECT, TECHNOLOGY, CONCEPT, DECISION, PERSON, COMPANY, METRIC, SKILL, PHASE, RESEARCH_PAPER

**Relationship Types**: USES, CONTAINS, DECIDED, DEPENDS_ON, PART_OF, BUILDS, TARGETS, REQUIRES, IMPROVES, MEASURED_BY, REFERENCES, HAS_SKILL, WORKS_ON, ALTERNATIVE_TO

### mindgraph_app/storage.py

| Function | What It Does |
|----------|-------------|
| `upsert_entity(name, type, description)` | Insert or increment mention_count. Deterministic ID from name+type. |
| `upsert_relation(from_id, to_id, type, context)` | Insert or update (keeps longer context). |
| `recompute_importance()` | importance = mention_count / max_mention_count across all entities. |
| `get_full_graph()` | Returns `{nodes: [...], edges: [...]}` for D3. |
| `search_entities(query)` | LIKE search on name. |
| `get_stats()` | Total entities, relations, top 5 by mention count. |
| `is_file_processed(hash)` | SHA256 dedup — re-ingesting same text is a no-op. |

### mindgraph_app/retriever.py (GraphRAG)

| Function | What It Does |
|----------|-------------|
| `local_search(query, limit)` | Text search on entity names → returns entities + their direct connections. |
| `multi_hop_search(entity_name, max_hops)` | Graph traversal: start entity → follow relationships N hops. |
| `temporal_search(date)` | All simulation events for a date. Uses event_logger. |
| `retrieve(query, method)` | Auto-detects best method: temporal for dates, local for entity names. |

### mindgraph_app/api.py (FastAPI)

| Endpoint | Method | What It Does |
|----------|--------|-------------|
| `/api/mindgraph/ingest` | POST | Upload text/file/URL → extract → store |
| `/api/mindgraph/ingest/json` | POST | JSON body `{text, url}` → extract → store |
| `/api/mindgraph/graph` | GET | Full graph `{nodes, edges}`. Optional `?filter=` |
| `/api/mindgraph/entity/{id}` | GET | Single entity + connections + recent events |
| `/api/mindgraph/stats` | GET | Knowledge graph stats + simulation event stats |
| `/api/mindgraph/search?q=` | GET | Search entities by name |
| `/api/mindgraph/simulation/events` | GET | Events by `?date=`, `?agent=`, or `?entity=` |
| `/api/mindgraph/simulation/timeline` | GET | Day-by-day summary for timeline bar |
| `/api/mindgraph/retrieve` | POST | GraphRAG: `{query, method: auto/local/multi_hop/temporal}` |
| `/api/mindgraph/clear` | DELETE | Clear entire graph |

### static/index.html (D3.js Frontend)

| Feature | Implementation |
|---------|---------------|
| Force-directed graph | D3.js v7 simulation with link, charge, center, collision forces |
| 14 entity type colors | Purple/Blue/Green/Orange/Pink/Red/Gray/Teal/Indigo/Yellow/Violet/Orange/Amber/Cyan |
| Node sizing | radius = 6 + mention_count * 3 (min 6, max 35) |
| Hover tooltip | Entity name, type, mention count, description. Follows mouse. |
| Click detail panel | Full entity: connections (incoming/outgoing), recent simulation events |
| Double-click focus | Shows only clicked node + direct neighbors. Click background to reset. |
| Search | Debounced 300ms. Matches highlight, rest dims to 12% opacity. |
| Filter checkboxes | Toggle each entity type on/off |
| Timeline bar | Day dots with event counts. Green/red/blue dots. Click → show day's events. |
| Upload panel | Paste text / Upload file / Fetch URL → extract → graph updates |
| Export PNG | Canvas capture of SVG |
| Dark theme | #0f172a background, #1e293b panels |

---

## Data Flow Examples

### "spent 5 on coffee" (Telegram → Budget → Notion → Simulation)

```
1. You send "spent 5 on coffee" on Telegram
2. Daemon receives via long-poll (1-3s)
3. command_router.classify("spent 5 on coffee")
   → rule match: "spent + digit" → Intent.LOG_SPEND
4. dispatcher.dispatch(LOG_SPEND)
   → budget_agent.log_transaction("spent 5 on coffee")
5. budget_agent.parse_transaction("spent 5 on coffee")
   → {amount: 5.0, description: "coffee", type: "expense"}
6. budget_agent.classify_transaction("coffee", 5.0, "expense")
   → keyword match: "coffee" → ("variable", "Eating out")
7. budget_agent.add_transaction(5.0, "coffee", "Eating out", "variable", "expense")
   → INSERT INTO transactions in budget.db
8. budget_agent.sync_expense_to_notion(txn)
   → READ existing Notion row (preserve category + planned)
   → PATCH col 2 = "£5.00", col 3 = "Last: coffee (2026-03-24)"
   → PATCH all total rows (Total variable, Total spending, Net)
9. event_logger.log_event("budget_transaction", ...)
   → INSERT INTO simulation_events in mindgraph.db
10. Reply: "💸 Logged: £5.00 — coffee\n   Category: Eating out\n   📎 notion.so/..."
```

### Morning Briefing (Cron → All Agents → Telegram)

```
1. Cron fires at 8:03am: python -m jobpulse.runner briefing
2. morning_briefing.build_and_send():
   a. gmail_agent.get_yesterday_recruiter_emails() → query SQLite
   b. calendar_agent.get_today_and_tomorrow() → Google Calendar API → log events
   c. notion_agent.get_today_tasks() → Notion API → to_do blocks
   d. github_agent.get_yesterday_commits() → gh CLI → Commits API → log event
   e. github_agent.get_trending_repos() → GitHub Search API
   f. budget_agent.get_week_summary() → query SQLite
3. Assemble all 6 sections into one message
4. telegram_agent.send_message(message)
5. event_logger.log_event("briefing_sent", ...)
6. If no tasks: send separate "todo prompt" message
```

---

## Database Schemas

### mindgraph.db

```sql
knowledge_entities (id, name, entity_type, description, mention_count, importance)
knowledge_relations (id, from_id, to_id, type, context)
processed_files (file_hash, filename, processed_at, entity_count)
simulation_events (id, event_type, agent_name, target_agent_name, action, content, metadata, day_date, created_at)
```

### jobpulse.db

```sql
processed_emails (email_id, sender, subject, category, snippet, received_at, processed_at)
gmail_check_state (id=1, last_check_ts)
```

### budget.db

```sql
transactions (id, amount, description, category, section, type, date, week_start, created_at)
weekly_budgets (week_start, notion_page_id, created_at)
planned_budgets (week_start, category, section, planned_amount)
```
