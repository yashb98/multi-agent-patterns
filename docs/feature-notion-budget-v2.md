# Feature: Notion Weekly Budget v2 — Full Transaction Tracking + Dataset Building

## What You Asked For
1. Salary timesheet linked from the Salary category row
2. Weekly budget archival + new week auto-creation
3. Every category gets a detailed sub-page with individual transactions
4. NLP auto-categorization from natural language

## What I'm Also Adding (Knowledge to MAX)

5. **Item-level parsing**: "spent 3.47 on yogurt and protein shake" stores items separately: yogurt, protein shake — not just as one description string
6. **Store/merchant tracking**: "spent 15 at Tesco" → captures Tesco as the store. Builds a dataset of WHERE you spend
7. **Running total per category**: Each transaction row shows how much of your budget you've used so far
8. **Weekly comparison**: "You spent 30% more on Eating Out vs last week"
9. **Monthly rollup**: Auto-generates monthly summary from 4 weekly sheets
10. **Dataset export**: CSV of all transactions with columns ready for ML analysis
11. **Smart alerts**: Not just 80% threshold — also "You usually spend X on Groceries by Wednesday, you're at Y"

## Architecture

### Current State
```
Notion Budget Page
  ├── Income Table (Salary, Freelance, Other)
  ├── Fixed Table (Rent, Utilities, Phone, Subs, Insurance)
  ├── Variable Table (Groceries, Eating Out, Transport, ...)
  ├── Savings Table (Savings, Investments, Credit Card)
  └── Summary Table (Totals, Net)

Each row: [Category | Planned | Actual | Notes]
Problem: "Actual" is just a number. No breakdown. No history.
```

### New State
```
Notion Budget Page
  ├── Income Table
  │     └── Salary → LINK → Salary Timesheet (hours table)
  │     └── Freelance → LINK → Freelance Details (project, amount, date)
  ├── Fixed Table
  │     └── Subscriptions → LINK → Subscription Log (service, amount, date)
  ├── Variable Table
  │     └── Groceries → LINK → Grocery Transactions
  │     │     [Amount | Date | Items | Store | Running Total]
  │     │     [£3.47 | Mar 26 | yogurt, protein shake | Tesco | £23.47/£50]
  │     └── Eating Out → LINK → Eating Out Transactions
  │     └── Transport → LINK → Transport Transactions
  │     └── ... (every category gets its own page)
  ├── Savings Table
  ├── Summary Table
  └── Weekly Comparison (this week vs last week)

Each week: new budget page auto-created on Sunday
Previous weeks: archived, accessible via links
```

### Transaction Sub-Page Columns

| Column | Type | Source | Why |
|--------|------|--------|-----|
| Amount | Number | Parsed from message | Core data |
| Date | Date | Auto or parsed | When |
| Description | Text | Raw input cleaned | What was said |
| Items | Text | NLP extracted | Individual items (comma-separated) |
| Store | Text | NLP extracted or "at X" | Where purchased |
| Category | Text | Auto-classified | For verification |
| Time | Text | Auto (morning/afternoon/evening) | Spending pattern analysis |
| Running Total | Text | Calculated | "£23.47 / £50 (47%)" |
| Week | Text | Auto | For filtering |

### Dataset Value

After 3 months you'll have ~300-500 transactions with:
- What you buy (items)
- Where you buy (stores)
- When you buy (day of week, time of day)
- How much you spend per category over time
- Seasonal patterns (winter vs summer spending)

This is a proper ML dataset. You could build:
- Spending prediction models
- Budget recommendation engine
- Anomaly detection (unusual spending)
- Personal inflation tracker (same items, price over time)

## Implementation

### Phase 1: Category Sub-Pages + Transaction Logging

Every category gets a Notion sub-page with a table. When you log "spent 3.47 on yogurt", it:
1. Classifies → Groceries
2. Updates the Actual column on the budget page (existing)
3. Opens/creates the Groceries sub-page for this week
4. Appends a row: [£3.47 | Mar 26 | yogurt | - | £23.47/£50]
5. Links the sub-page from the category row's Notes column

### Phase 2: Item + Store Extraction (NLP)

Enhanced parsing from natural language:

| Input | Amount | Items | Store | Category |
|-------|--------|-------|-------|----------|
| "spent 3.47 on yogurt and protein shake" | 3.47 | yogurt, protein shake | - | Groceries |
| "spent 15 at Tesco on groceries" | 15.00 | groceries | Tesco | Groceries |
| "8.50 coffee and sandwich at Pret" | 8.50 | coffee, sandwich | Pret | Eating Out |
| "uber was 12 quid" | 12.00 | uber ride | Uber | Transport |
| "netflix subscription" | - | netflix | Netflix | Subscriptions |
| "paid 45 for new trainers at JD Sports" | 45.00 | trainers | JD Sports | Shopping |

Extraction logic:
- "at X" or "from X" or "in X" → Store = X
- "on X and Y and Z" → Items = [X, Y, Z]
- "X and Y" after amount → Items = [X, Y]
- Known store names auto-detected: Tesco, Aldi, Lidl, Sainsbury's, Pret, Costa, etc.

### Phase 3: Weekly Archival + New Week Auto-Creation

**Sunday morning (before briefing):**
1. Archive current week's budget page (add "ARCHIVED" tag, lock editing)
2. Create new week's budget page (copy structure, reset Actual to £0)
3. Carry over Planned amounts from last week
4. Send Telegram: "New budget week started (Mar 30 - Apr 5). Last week: spent £X, saved £Y"

**Storage:**
- SQLite: transactions table already has week_start column
- Notion: each week gets its own page, linked from a master "Budget Archive" page
- Old weeks remain accessible via archive links

### Phase 4: Weekly Comparison + Smart Alerts

After 2+ weeks of data:

```
📊 WEEKLY COMPARISON:

Category        This Week    Last Week    Change
Groceries       £42.30       £38.50       +£3.80 (↑10%)
Eating Out      £28.00       £15.00       +£13.00 (↑87%) ⚠️
Transport       £12.00       £14.00       -£2.00 (↓14%) ✅
Shopping        £0.00        £45.00       -£45.00 ✅

Total Spending  £82.30       £112.50      -£30.20 (↓27%) ✅

💡 Eating Out jumped 87% — 3 more restaurant visits than usual.
```

Smart alerts:
- "By Wednesday you usually spend £20 on Groceries. You're at £35. Heads up."
- "You haven't logged any Transport this week — unusual for a weekday."
- "Subscriptions due tomorrow: Netflix £12, Spotify £10"

### Phase 5: Monthly Rollup + Dataset Export

**First of each month:**
1. Auto-generate "Monthly Summary - March 2026" page in Notion
2. Aggregate all 4 weekly sheets
3. Category totals, trends, biggest single expenses
4. Compare vs previous month

**Dataset export:**
- `run: export budget csv` → generates transactions.csv with all columns
- Ready for pandas/sklearn analysis
- Columns: date, amount, category, section, description, items, store, time_of_day, day_of_week, week_start

## SQLite Schema Changes

### Enhanced transactions table

```sql
ALTER TABLE transactions ADD COLUMN items TEXT DEFAULT '';
ALTER TABLE transactions ADD COLUMN store TEXT DEFAULT '';
ALTER TABLE transactions ADD COLUMN time_of_day TEXT DEFAULT '';
ALTER TABLE transactions ADD COLUMN notion_page_id TEXT DEFAULT '';
```

### Category sub-pages tracking

```sql
CREATE TABLE category_pages (
    week_start TEXT NOT NULL,
    category TEXT NOT NULL,
    notion_page_id TEXT NOT NULL,
    PRIMARY KEY (week_start, category)
);
```

### Weekly budget archive

```sql
CREATE TABLE weekly_archives (
    week_start TEXT PRIMARY KEY,
    notion_page_id TEXT NOT NULL,
    total_income REAL DEFAULT 0,
    total_spending REAL DEFAULT 0,
    total_savings REAL DEFAULT 0,
    net REAL DEFAULT 0,
    archived_at TEXT NOT NULL
);
```

## NLP Item + Store Extraction

```python
def extract_items_and_store(text: str) -> dict:
    """
    'spent 3.47 on yogurt and protein shake at Tesco'
    → {"items": ["yogurt", "protein shake"], "store": "Tesco"}

    'uber was 12 quid'
    → {"items": ["uber ride"], "store": "Uber"}

    '8.50 coffee and sandwich at Pret'
    → {"items": ["coffee", "sandwich"], "store": "Pret"}
    """

    # Known stores (auto-expands over time from user data)
    KNOWN_STORES = [
        "tesco", "aldi", "lidl", "sainsbury", "asda", "morrisons",
        "waitrose", "marks and spencer", "m&s",
        "pret", "costa", "starbucks", "greggs", "mcdonald",
        "uber", "bolt", "addison lee",
        "amazon", "ebay", "jd sports", "primark", "tk maxx",
        "boots", "superdrug",
    ]

    store = ""
    items = []

    # Extract store: "at X" / "from X" / "in X"
    store_match = re.search(r'\b(?:at|from|in)\s+([A-Z][\w\s&]+)', text)
    if store_match:
        store = store_match.group(1).strip()

    # Auto-detect known stores from anywhere in text
    if not store:
        for s in KNOWN_STORES:
            if s in text.lower():
                store = s.title()
                break

    # Extract items: text between amount and store/date
    # "on X and Y" pattern
    items_match = re.search(r'\bon\s+(.+?)(?:\s+at\s+|\s+from\s+|$)', desc)
    if items_match:
        raw = items_match.group(1)
        items = [i.strip() for i in re.split(r'\s+and\s+|,\s*', raw) if i.strip()]

    return {"items": items, "store": store}
```

## Notion Sub-Page Structure

### Example: Groceries Sub-Page (Week of Mar 23)

**Page title:** "Groceries — Week of 2026-03-23"
**Parent:** Weekly Budget Page

| Amount | Date | Items | Store | Running Total |
|--------|------|-------|-------|---------------|
| £3.47 | Mar 23 | yogurt, protein shake | Tesco | £3.47 / £50 (7%) |
| £12.30 | Mar 24 | chicken, rice, vegetables | Aldi | £15.77 / £50 (32%) |
| £5.99 | Mar 25 | milk, bread, eggs | Sainsbury's | £21.76 / £50 (44%) |
| £8.50 | Mar 26 | snacks, drinks | Tesco | £30.26 / £50 (61%) |
| **TOTAL** | **4 items** | | | **£30.26 / £50 (61%)** |

### Example: Eating Out Sub-Page

| Amount | Date | Items | Store | Running Total |
|--------|------|-------|-------|---------------|
| £8.50 | Mar 23 | coffee, sandwich | Pret | £8.50 / £30 (28%) |
| £15.00 | Mar 25 | dinner | Nando's | £23.50 / £30 (78%) |
| £4.50 | Mar 26 | coffee | Costa | £28.00 / £30 (93%) ⚠️ |
| **TOTAL** | **3 visits** | | | **£28.00 / £30 (93%)** |

## Telegram Flow

### Logging a Transaction

```
You: spent 3.47 on yogurt and protein shake at Tesco

Bot: 💸 Logged: £3.47 — yogurt, protein shake
     🏪 Store: Tesco
     📂 Category: Groceries (variable)
     📊 Budget: £30.26 / £50 (61%)
     Today: spent £12.47 | earned £0.00

     📎 Groceries detail: https://notion.so/...
     📎 Weekly budget: https://notion.so/...
```

### Weekly Summary (Sunday evening)

```
Bot: 📊 WEEK ENDING Mar 29

     Groceries:   £42.30 / £50 (85%) ⚠️
     Eating Out:  £28.00 / £30 (93%) ⚠️
     Transport:   £12.00 / £20 (60%)
     Shopping:    £15.00 / £40 (38%)

     vs Last Week:
     Eating Out: ↑87% (+£13) — 3 more visits
     Groceries:  ↑10% (+£3.80)

     New week starts tomorrow (Sunday).
     Carrying over planned budgets.
```

## Cost

| Component | Cost |
|-----------|------|
| NLP item extraction | Free (regex + known stores) |
| Notion API calls (sub-page creation) | Free |
| Notion API calls (row append) | Free |
| LLM for ambiguous categories | ~$0.001 per transaction |
| Weekly comparison generation | Free (SQLite aggregation) |
| Monthly rollup | Free |
| **Total additional cost** | **~$0.00** (already paying for classification) |

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `jobpulse/budget_tracker.py` | Category sub-pages, item extraction, weekly comparison |
| `data/known_stores.json` | Auto-expanding store database |

### Modified Files
| File | Change |
|------|--------|
| `jobpulse/budget_agent.py` | Enhanced log_transaction with items/store, sub-page linking |
| `jobpulse/morning_briefing.py` | Add weekly comparison section |
| `scripts/install_cron.py` | Add Sunday weekly archival cron |
| `jobpulse/runner.py` | Add `new-week` and `archive-week` commands |

## Timeline

| Phase | What | Complexity |
|-------|------|------------|
| 1 | Category sub-pages + transaction rows | High |
| 2 | Item + store extraction (NLP) | Medium |
| 3 | Weekly archival + new week creation | Medium |
| 4 | Weekly comparison + smart alerts | Medium |
| 5 | Monthly rollup + dataset export | Low |
