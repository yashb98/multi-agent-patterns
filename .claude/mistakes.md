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
