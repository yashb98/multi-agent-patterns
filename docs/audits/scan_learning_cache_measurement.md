# scan_learning.run_llm_analysis — cache hit-rate measurement

**Status:** measurement scaffold landed 2026-05-10. Awaiting 7 days of cron data.

**Plan reference:** Item 8 of `docs/superpowers/plans/2026-05-09-form-fill-followups.md`.
**Code:** `jobpulse/scan_learning.py:_record_llm_analysis_call` (instrumentation),
`ScanLearningEngine.llm_analysis_hit_rate(days=7)` (aggregator).

## Why

`run_llm_analysis` runs every 5 scan blocks (~5 calls/day on the current
3-platform schedule) and asks the LLM to identify the pattern that
triggers verification walls from the last 20 events. Each call costs
~$0.001. A `(signal_set_hash)` cache would help only if the same
20-event window keeps recurring — which is non-obvious, because every
new scan adds a fresh event that bumps the hash.

The audit (cache-llm-completion-report.md §7 S2-DEF) explicitly said
"requires measurement first" — don't add cache infra on speculation.

## Decision rule

After 7 days of cron data:

| Hit rate | Action |
|---|---|
| ≥ 30 % | Add a cache table keyed `(platform, signal_set_hash)` with 7-day TTL, same shape as the other content caches landed in this plan. |
| < 30 % | Document the negative result here and **close** the item. |

Hit rate = `1 - distinct(signal_set_hash) / total_calls` per platform.

## How to run the measurement

```python
from jobpulse.scan_learning import ScanLearningEngine
engine = ScanLearningEngine()
print(engine.llm_analysis_hit_rate(days=7))
# {"linkedin": {"total": N, "distinct": D, "hit_rate": 1 - D/N}, ...}
```

Or via SQLite directly:

```bash
sqlite3 data/scan_learning.db <<'SQL'
SELECT platform,
       COUNT(*)                     AS total,
       COUNT(DISTINCT signal_set_hash) AS distinct_hashes,
       ROUND(1.0 - (CAST(COUNT(DISTINCT signal_set_hash) AS REAL) / COUNT(*)), 3) AS hit_rate
  FROM llm_analysis_calls
 WHERE ts >= datetime('now', '-7 days')
 GROUP BY platform
 ORDER BY platform;
SQL
```

## Results — 7-day window

_Fill in after 2026-05-17._

| Platform | Total calls | Distinct hashes | Hit rate | Decision |
|---|---|---|---|---|
| linkedin | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| reed | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| indeed | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

## Notes

- Even one `run_llm_analysis` call mid-day after a single new event
  bumps the hash — the cache only earns its keep on retried analyses
  during a stable scan window. If hit rates are 0 across the board,
  abandon the cache idea and close.
- This measurement deliberately adds **no** cache logic; the wrap is
  pure observation. If hit-rate justifies it, the cache table will
  follow the established `cv_scrutiny_cache` / `portfolio_variant_cache`
  pattern from this plan's other items.
