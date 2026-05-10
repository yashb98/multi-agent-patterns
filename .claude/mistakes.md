# Mistakes Log

Append on error. Re-check before committing. Use `semantic_search "mistake <topic>"` for full incident context.

---

- [2026-04-30] Ollama probe must verify model availability, not just server reachability — empty models list = cloud fallback
- [2026-04-30] Vision fallback must use a vision-capable cloud model (gpt-4o-mini), not local text-only model
- [2026-04-30] PageReasoner smart_llm_call() requires (llm, messages) positional args, not keyword-only
- [2026-04-30] Login pages with embedded CAPTCHA/hCaptcha classified as verification_wall — suppress wall score when login/signup signals coexist
- [2026-04-30] PageReasoner caches `abort` from failed LLM calls — stale cache blocks retry on same page
- [2026-04-30] Auth handlers must read actual page content, not follow hardcoded flows — Oracle Cloud email-only page crashed because handler assumed password field exists
- [2026-04-30] PageReasoner must be PRIMARY decision-maker, not fallback — hardcoded classifier→handler chains break on any page layout the code hasn't seen
- [2026-04-04] Use MCP tools before Explore agents — saves ~190k tokens per exploration
- [2026-04-03] Route jobs by `classify_action()` not `determine_match_tier()` — tier is display only
- [2026-04-01] Regex: specific patterns BEFORE general. Test against all question types
- [2026-04-01] LinkedIn: navigate to `/jobs/` first, then specific URL. Easy Apply badge can be `<a>` not `<button>`
- [2026-04-01] LinkedIn stuck detection: compare chars 300-700, not first 200 (generic wrapper)
- [2026-04-01] Numeric ATS fields: plain integers only (no currency, commas, ranges)
- [2026-04-01] Never use substring-matchable words in regex (`city` matches `ethnicity`)
- [2026-03-30] New intents must go in BOTH dispatcher.py AND swarm_dispatcher.py
- [2026-03-30] Always HTTPS + 429 backoff + User-Agent for arxiv
- [2026-03-28] One handler per message. 30s dedup guard on concurrent writes
- [2026-03-25] Tests NEVER touch data/*.db — patch DB_PATH to tmp_path
- [2026-03-25] Whisper adds punctuation — classify() strips trailing [.!?]+
- [2026-03-25] pushed_at: use >= or <, never ==
- [2026-03-24] Never wait for Telegram replies in Claude Code — poll API directly
- [2026-03-24] Never use Events API for commits — use Commits API per-repo
- [2026-03-24] Never rewrite a file without grepping for all function names used elsewhere

## DB drop-rate alert — user_profile.screening_defaults (2026-05-10)

DB ``user_profile.screening_defaults`` returned data that was dropped from 7/10 consumed lookups
(70.0% drop rate over the last 1 day(s)).

Top drop reason: ``option_misalignment`` (7 occurrences).

**OPRAL investigation prompt:**
- **Observe**: pull a sample of dropped values from
  ``data/db_observability.db`` where ``db_name='user_profile' AND table_name='screening_defaults' AND status='dropped'``
- **Plan**: trace which call site produced the lookup and which downstream
  consumer dropped it. Use ``mcp__code-intelligence__callers_of`` on the
  accessor.
- **Reason**: is the data wrong-shape (option_misalignment, validation_failed),
  or is the consumer buggy?
- **Act**: fix the source (rewrite stored row, retrain pattern, replace stale
  default) or the consumer (improve alignment, surface a clearer match).
- **Learn**: emit ``adaptation`` or ``correction`` signal once fixed; verify
  drop rate drops on next daily summary.

Sample dropped rows (max 5):
- field='Are you willing to commute to the office?' intended='Yes, willing to commute to any UK office' actual='' reason='option_misalignment'
- field='Are you willing to commute to the office?' intended='Yes, willing to commute to any UK office' actual='' reason='option_misalignment'
- field='Are you willing to relocate to the office in London?' intended='Yes, within the UK' actual='' reason='option_misalignment'
- field='Are you willing to relocate to the office in London?' intended='Yes, within the UK' actual='' reason='option_misalignment'
- field='Are you willing to commute to the office?' intended='Yes, willing to commute to any UK office' actual='' reason='option_misalignment'

