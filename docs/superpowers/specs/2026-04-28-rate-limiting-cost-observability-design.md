# Rate Limiting & Cost Observability Upgrade

**Goal:** Upgrade the rate limiting and cost tracking subsystem from 8.0/10 to 9.5+/10 by fixing five gaps: LLM attribution, application audit trail, cost reporting, proactive alerts, and DB retention.

**Current state:** 816/816 LLM calls recorded as `agent_name="unknown"`, zero trajectory attribution, no audit trail linking rate limit records to applications, cost data invisible in all reports, no proactive alerts, no DB retention.

---

## Gap 1: LLM Attribution

### Problem

All LLM calls record `agent_name="unknown"` because:
- `_InstrumentedLLM.invoke()` in `shared/agents.py:291` hardcodes `agent_name="unknown"`
- `smart_llm_call()` in `shared/streaming.py:269` hardcodes `agent_name="unknown"`
- Neither `get_llm()` nor `smart_llm_call()` accept an `agent_name` parameter
- 9 direct `client.chat.completions.create()` calls bypass cost tracking entirely
- `safe_openai_call()` in `jobpulse/utils/safe_io.py` (7 callers) has zero cost tracking

### Solution

**Core infrastructure changes:**

1. `shared/agents.py` — `_InstrumentedLLM.__init__` accepts `agent_name`, stores as `self._agent_name`, uses in `.invoke()` recording. `get_llm()` accepts `agent_name` parameter, passes to `_InstrumentedLLM` constructor on all 3 return paths (local, single-provider, multi-provider). (Partially done — `_InstrumentedLLM` and `get_llm()` signature updated, `get_llm()` body not yet passing `agent_name` to constructors.)

2. `shared/streaming.py` — `smart_llm_call()` resolves agent_name from `getattr(llm, '_agent_name', 'unknown')` for the streaming branch recording.

3. `shared/cost_tracker.py` — New `record_openai_usage(response, *, agent_name, model_hint)` function for raw OpenAI SDK responses. Extracts `response.usage.prompt_tokens` / `completion_tokens`, computes cost, records to `llm_calls` table.

**`safe_openai_call()` tracking:**

`jobpulse/utils/safe_io.py` — After `client.chat.completions.create()`, call `record_openai_usage(response, agent_name=caller or "safe_openai_call", model_hint=model)`. This covers 7 indirect callers: `gate4_quality.py:scrutinize_cv_llm`, `generate_cover_letter.py:polish_points_llm`, `scan_learning.py:run_llm_analysis`, and their callers.

**6 direct call sites (add 3-line wrapper after each):**

| File | Function | agent_name |
|------|----------|------------|
| `jobpulse/page_analyzer.py:43` | vision page detection | `"page_analyzer"` |
| `jobpulse/screening_answers.py:808` | `_generate_answer` | `"screening_answers"` |
| `jobpulse/budget_nlp.py:84` | `classify_transaction` | `"budget_nlp"` |
| `jobpulse/conversation.py:99` | `chat` | `"conversation"` |
| `jobpulse/form_engine/field_mapper.py:735` | `review_form` | `"field_mapper"` |
| `jobpulse/persona_evolution.py:103,192,204` | `_quick_evolve`, `evaluator` (2x) | `"persona_evolution"` |

**get_llm() caller updates (add `agent_name=` parameter):**

| File | Function | agent_name |
|------|----------|------------|
| `jobpulse/gmail_agent.py:139` | `_classify_email` | `"email_classifier"` |
| `jobpulse/portfolio_variants.py:291` | `_generate_jd_aware_bullets` | `"portfolio_variants"` |
| `jobpulse/portfolio_variants.py:356` | `generate_portfolio_entry` | `"portfolio_variants"` |
| `jobpulse/screening_answers.py:397` | `_generate_hiring_message` | `"screening_answers"` |
| `jobpulse/strategy_reflector.py:186` | `reflect_with_llm` | `"strategy_reflector"` |
| `jobpulse/persona_evolution.py:177` | `_deep_optimize` | `"persona_evolution"` |
| `shared/agents.py:409` | `researcher_node` | `"researcher"` |
| `shared/agents.py:475` | `writer_node` | `"writer"` |
| `shared/agents.py:541` | `reviewer_node` | `"reviewer"` |
| `shared/experiential_learning.py:340` | `make_variant` | `"grpo_variant"` |

### Verification

Query `llm_usage.db`: `SELECT agent_name, COUNT(*) FROM llm_calls WHERE agent_name = 'unknown' AND timestamp > '2026-04-28'` should return 0 rows.

---

## Gap 2: Application Audit Trail

### Problem

`rate_limits.db` has `daily_counts(date, platform, count)` — aggregates only. No job_id, company, URL, or timestamp per application. Can't answer "which 4 apps did we submit on April 26?"

### Solution

Add `application_log` table to `rate_limits.db`:

```sql
CREATE TABLE IF NOT EXISTS application_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    platform TEXT NOT NULL,
    job_id TEXT,
    company TEXT,
    url TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_applog_date ON application_log(date);
```

Extend `RateLimiter.record_application()` signature:

```python
def record_application(
    self, platform: str,
    job_id: str = "", company: str = "", url: str = "",
) -> None:
```

After inserting into `daily_counts`, also insert into `application_log`.

Add `get_application_log(self, days: int = 7) -> list[dict]` query method.

Update callers in `jobpulse/applicator.py`:
- `apply_job()` line ~265: pass `job_id`, `company`, `url` from `job_context`
- `confirm_application()` line ~467: pass `job_id`, `company`, `url` from `job_context`

### Verification

`SELECT COUNT(*) FROM application_log` should match `SELECT SUM(count) FROM daily_counts` for any given date range.

---

## Gap 3: Cost Reporting

### Problem

LLM cost data exists in `llm_usage.db` but is invisible — not in morning briefing, weekly report, or any Telegram command.

### Solution

**New query function in `shared/cost_tracker.py`:**

```python
def get_daily_llm_summary(days: int = 1) -> dict:
    """Per-agent cost breakdown for the last N days."""
```

Returns: `{"total_cost": float, "total_calls": int, "by_agent": {name: {"calls": int, "cost": float}}, "by_day": {date: float}}`.

**Morning briefing** (`jobpulse/morning_briefing.py`):
- Add a cost section after existing sections
- Shows yesterday's total cost + top agents by spend
- Format: `"LLM Cost: $0.03 (gate4: $0.01, screening: $0.01, page_analyzer: $0.005, ...)"`

**Weekly report** (`jobpulse/weekly_report.py`):
- Add 7-day cost trend section
- Shows daily costs and weekly total

---

## Gap 4: Proactive Quota Alerts

### Problem

User discovers exhausted quota only when next application fails. No warning at 80% capacity.

### Solution

In `RateLimiter.record_application()`, after recording:

```python
total = self.get_total_today()
threshold = int(TOTAL_DAILY_CAP * 0.8)
if total == threshold:
    send_pipeline_alert(
        f"Daily quota at {total}/{TOTAL_DAILY_CAP} (80%). "
        f"{TOTAL_DAILY_CAP - total} slots remaining.",
        severity="warning", category="quota",
    )
```

Same per-platform:
```python
platform_count = self._get_platform_count(platform)
cap = DAILY_CAPS.get(platform, DAILY_CAPS["generic"])
platform_threshold = int(cap * 0.8)
if platform_count == platform_threshold:
    send_pipeline_alert(...)
```

Alert fires exactly once per threshold crossing (uses `==` not `>=`).

---

## Gap 5: DB Retention

### Problem

`rate_limits.db` and `llm_usage.db` grow unbounded. No cleanup mechanism.

### Solution

**`RateLimiter.cleanup_old(retention_days: int = 30)`:**
- Deletes `daily_counts` and `application_log` rows older than retention_days
- Returns count of deleted rows

**`cleanup_old_usage(retention_days: int = 90)` in `cost_tracker.py`:**
- Deletes `llm_calls` rows older than retention_days
- Returns count of deleted rows

Both wired to the daemon's optimization tick in `jobpulse/multi_bot_listener.py:_optimization_tick()` (runs every 15 minutes, cleanup runs every 4th tick = ~1 hour).

---

## Live Pipeline Wiring

Every integration point must fire during actual production pipeline runs. Here's how each piece connects:

### Cron scan pipeline (job_autopilot.py → scan_pipeline.py)

```
scan_pipeline.route_and_apply()
  → apply_job(url, cv_path, job_context={"job_id", "company", "title", ...})
    → RateLimiter.record_application("greenhouse", job_id="abc", company="Snowflake", url="https://...")
      → INSERT daily_counts + application_log ← GAP 2 FIX
      → if total >= 80% cap → send_pipeline_alert() via Alert bot ← GAP 4 FIX
    → adapter.fill_and_submit()
      → get_llm(agent_name="screening_answers") ← GAP 1 FIX
      → safe_openai_call(caller="gate4") → record_openai_usage() ← GAP 1 FIX
    → post_apply_hook()
```

### Manual apply (Claude Code session)

```
apply_job(url, dry_run=True, job_context={...})
  → fill form, stop before Submit
  → user reviews, approves
confirm_application(dry_run_result, job_context={...})
  → RateLimiter.record_application("workday", job_id="xyz", company="ASOS", url="https://...")
    → INSERT daily_counts + application_log ← GAP 2 FIX
    → if total >= 80% cap → send_pipeline_alert() ← GAP 4 FIX
  → post_apply_hook()
```

### Morning briefing (cron 8am → morning_briefing.py)

```
build_and_send()
  → Section 10 (existing): job autopilot stats from job_db + job_analytics
  → Section 11 (NEW): LLM cost section ← GAP 3 FIX
    → get_daily_llm_summary(days=1)
    → query llm_usage.db for yesterday's per-agent breakdown
    → format: "💰 LLM COST: $0.03 (gate4: $0.01, screening: $0.01, ...)"
  → telegram_agent.send_message(message)
```

Insert as a new section after section 10 (job stats), before the message assembly at line ~179.

### Weekly report (cron Sunday → weekly_report.py)

```
build_weekly_report()
  → Section 5 (existing): job application stats
  → Section 6 (NEW): LLM cost trend ← GAP 3 FIX
    → get_daily_llm_summary(days=7)
    → format: "💰 LLM COST (7-day): $0.15 total, $0.02/day avg"
    → per-agent top-3 breakdown
  → telegram_agent.send_message(report)
```

Insert as a new section (section 6) after job applications, before the message assembly at line ~128.

### Daemon optimization tick (multi_bot_listener.py)

```
_optimization_tick() — runs every 15 min
  → every 4th tick (~1 hour):
    → forgetting sweep (existing)
    → RateLimiter().cleanup_old(retention_days=30) ← GAP 5 FIX
    → cleanup_old_usage(retention_days=90) ← GAP 5 FIX
```

Add after the existing forgetting sweep block at line ~76.

### LLM call tracking flow (all agents)

Every LLM call in the system goes through one of two paths:

**Path A: get_llm() pipeline** (LangChain-style)
```
get_llm(agent_name="email_classifier")
  → _InstrumentedLLM(agent_name="email_classifier")
    → .invoke() → record_llm_usage(agent_name="email_classifier")
```

**Path B: Direct OpenAI SDK** (safe_openai_call or raw)
```
safe_openai_call(client, messages=..., caller="gate4")
  → client.chat.completions.create()
  → record_openai_usage(response, agent_name="gate4")
```

---

## Testing

All tests use `tmp_path` for DB isolation. Never touch `data/*.db`. Tests use production-realistic data patterns — real platform names, real ATS URLs, real company names, real token counts matching actual gpt-4o-mini usage.

### Rate limiter tests (extend `tests/test_rate_limiter.py`)

**test_application_log_recorded:**
- Record application with `platform="greenhouse"`, `job_id="abc123"`, `company="Snowflake"`, `url="https://boards.greenhouse.io/snowflake/jobs/12345"`
- Query `application_log` table
- Assert: row exists with matching fields and `recorded_at` timestamp

**test_application_log_links_to_daily_counts:**
- Record 3 applications: greenhouse/Snowflake, workday/ASOS, linkedin/Arm
- Assert: `daily_counts` shows 3 platforms with count=1 each
- Assert: `application_log` has 3 rows with correct company/url per platform
- Assert: `SUM(daily_counts.count)` == `COUNT(application_log)` for today

**test_cleanup_old_preserves_recent:**
- Insert records for 3 dates: 45 days ago, 15 days ago, today
- Call `cleanup_old(retention_days=30)`
- Assert: 45-day-old records deleted from both `daily_counts` and `application_log`
- Assert: 15-day-old and today records preserved

**test_quota_alert_at_80_percent:**
- Set `TOTAL_DAILY_CAP = 10` (via monkeypatch)
- Mock `send_pipeline_alert`
- Record 7 applications (below threshold) — assert alert NOT called
- Record 8th application (80% of 10) — assert alert called once with severity="warning", category="quota"
- Record 9th application — assert alert NOT called again (fires only at threshold crossing)

**test_platform_quota_alert:**
- Set `DAILY_CAPS["linkedin"] = 5` (via monkeypatch)
- Mock `send_pipeline_alert`
- Record 3 linkedin applications — no alert
- Record 4th linkedin application (80% of 5) — alert fires with platform name

### Cost tracker tests (extend `tests/shared/test_cost_tracker_ledger.py`)

**test_record_openai_usage:**
- Create a mock OpenAI ChatCompletion response:
  ```python
  response.usage.prompt_tokens = 150
  response.usage.completion_tokens = 45
  response.model = "gpt-4o-mini-2024-07-18"
  ```
- Call `record_openai_usage(response, agent_name="gate4", model_hint="gpt-4o-mini")`
- Query `llm_calls` table
- Assert: row with `agent_name="gate4"`, `model="gpt-4o-mini-2024-07-18"`, `prompt_tokens=150`, `completion_tokens=45`, `cost_usd > 0`

**test_record_openai_usage_missing_usage:**
- Create response with `usage=None` (API timeout edge case)
- Call `record_openai_usage()` — should not raise
- Assert: row recorded with `prompt_tokens=0`, `completion_tokens=0`, `cost_usd=0`

**test_get_daily_llm_summary:**
- Insert 5 rows into `llm_calls` with realistic data:
  - 2x `agent_name="gate4"`, model="gpt-4o-mini-2024-07-18", cost=$0.001 each
  - 2x `agent_name="screening_answers"`, cost=$0.002 each
  - 1x `agent_name="page_analyzer"`, cost=$0.003
- Call `get_daily_llm_summary(days=1)`
- Assert: `total_cost == 0.007`, `total_calls == 5`
- Assert: `by_agent["gate4"]["calls"] == 2`, `by_agent["gate4"]["cost"] == 0.002`
- Assert: `by_agent["screening_answers"]["calls"] == 2`

**test_get_daily_llm_summary_excludes_old:**
- Insert rows with timestamps from 3 days ago
- Call `get_daily_llm_summary(days=1)`
- Assert: returns empty/zero (old data excluded)

**test_cleanup_old_usage:**
- Insert rows with timestamps: 120 days ago, 60 days ago, today
- Call `cleanup_old_usage(retention_days=90)`
- Assert: 120-day-old rows deleted, 60-day and today preserved

### Integration wiring tests

**test_safe_openai_call_records_cost** (new test in `tests/jobpulse/test_safe_io.py`):
- Mock `client.chat.completions.create` to return a response with `usage.prompt_tokens=100, completion_tokens=30, model="gpt-4o-mini-2024-07-18"`
- Monkeypatch `LLM_USAGE_DB` to tmp_path
- Call `safe_openai_call(client, messages=[...], caller="gate4_scrutiny")`
- Query the tmp llm_usage.db
- Assert: row with `agent_name="gate4_scrutiny"`, correct token counts

**test_get_llm_passes_agent_name:**
- Call `get_llm(agent_name="email_classifier")`
- Assert: returned `_InstrumentedLLM` has `_agent_name == "email_classifier"`

**test_smart_llm_call_resolves_agent_name:**
- Create `_InstrumentedLLM` with `agent_name="screening_answers"`
- Mock `.invoke()` to return a response
- Monkeypatch `LLM_USAGE_DB` to tmp_path
- Call `smart_llm_call(llm, messages)` (non-streaming path)
- Query tmp llm_usage.db
- Assert: row with `agent_name="screening_answers"` (resolved from `llm._agent_name`)

---

## Non-goals

- Migrating direct `client.chat.completions.create()` calls to `get_llm()` pipeline — keeps existing code structure
- Per-application cost attribution (linking LLM costs to specific job applications) — would require trajectory_id threading through the entire application pipeline, out of scope
- New Telegram command for cost querying — morning briefing and weekly report provide sufficient visibility
