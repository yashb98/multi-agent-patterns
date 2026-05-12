# Rules: JobPulse Agents (jobpulse/**/*)

## Claude = Orchestrator, Not Doer (MANDATORY)
When running the pipeline, invoke the actual AI agents — don't bypass them with ad-hoc scripts. Agents only learn from runs they performed. Observe, diagnose, direct. Manual scripts only as fallback for issues agents can't handle yet — then feed corrections into agent DBs so it's autonomous next time.

## OPRAL Error Loop (MANDATORY)
On every error/issue: **Observe** (capture error + context) → **Plan** (trace root cause via MCP) → **Reason** (why did this fail? which learning system prevents recurrence?) → **Act** (fix surgically, re-run with real data, route to correct DB) → **Learn** (emit signal, verify DB persisted, confirm agent handles it autonomously next run). Every error must make the system smarter — if it can recur, the fix is incomplete.

## Dual Dispatcher Invariant
When adding a new intent or agent:
1. Add handler to jobpulse/handler_registry.py handler map
2. Add intent to jobpulse/intent_registry.py in the correct intent group
3. Add routing in jobpulse/command_router.py (Intent enum + classification)
4. Verify both jobpulse/dispatcher.py AND jobpulse/swarm_dispatcher.py pick it up via get_handler_map()
5. Add NLP examples to jobpulse/nlp_classifier.py (embedding examples, NOT regex)
6. Add tests for the new intent in both dispatch paths
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

## No PII in Source Code (MANDATORY)
Personal information MUST NEVER be hardcoded in source files. Full policy: `.claude/rules/pii-policy.md`
- Identity, screening answers, skills, links, address, DEI — ALL from databases
- Retrieve via `get_profile()`, `ScreeningPipeline`, `get_profile_links()`, etc.
- No "defaults" dicts with personal values. No PII in comments/docstrings.
- Tests use anonymized fixtures or `@pytest.mark.live` with profile DB access.

## Dynamic Over Hardcoded (MANDATORY)
Every pipeline change MUST be dynamic and adaptive — never hardcoded.
- Field values: read from DOM/a11y tree, databases, LLM, or config — never literal strings in code
- Personal data: ALWAYS from profile/screening databases — never literal values in source (see PII policy above)
- Selectors: discover via a11y tree or learned selectors — never hardcoded CSS/XPath for specific forms
- Timing: use adaptive timing from FormExperienceDB — never hardcoded sleep values
- Screening answers: generate from LLM with JD+CV context — never stale dictionaries
- Flow logic: detect page type dynamically (DOM + vision) — never assume fixed page sequences
- Options: scan dropdown/radio options at runtime — never guess option text
- Platform behavior: use strategy pattern + experience DB — never if/else chains with string matching
- Thresholds/limits: pull from config or database — never magic numbers in logic
If a value could change across domains, platforms, or form variants, it MUST be resolved at runtime.
Hardcoded fallbacks are acceptable ONLY as last-resort defaults when all dynamic sources fail.

### No Regex for Classification (MANDATORY)
Regex MUST NOT be used for semantic work: intent routing, question categorization, consent detection, field matching, command parsing, or screening question classification.
- **Use instead**: LLM classification (with caching), embedding similarity, `semantic_matcher.py`, a11y tree inspection, database-stored learned patterns
- **Regex remains OK for**: text normalization (`re.sub` for whitespace/punctuation), security sanitization (stripping injection tags), structural format validation (email/phone/date/URL patterns), number extraction from known-format strings
- **Migration rule**: When touching a file that uses regex for classification or routing, migrate those patterns to dynamic approaches in the same change
- **Why**: Regex breaks on new phrasing, typos, multilingual content, platform changes. Dynamic approaches adapt and learn from experience.

## Surgical Changes
- Match existing agent style. Don't refactor working agents while fixing a bug.
- Don't add docstrings, comments, or type annotations to code you didn't change.
- Every changed line should trace directly to the request.
- If you notice unrelated dead code, mention it — don't delete it.

## Real Data + Wiring Verification (MANDATORY)
Every new agent/feature: test with real URLs, real APIs, real DB queries (never mocks or stale fixtures). Then verify end-to-end that all downstream systems fire — hooks, signals, DB writes, learning chains, Notion syncs, Telegram notifications. Not wired = not done.

## API Pitfalls (from incidents)
- GitHub: use Commits API per-repo, never Events API (Events API misses commits and has aggressive rate limits)
- GitHub: `pushed_at` timestamp filtering uses `>=` or `<`, never `==` (timestamps have sub-second precision)
- ATS numeric fields: plain integers only — no currency symbols, commas, ranges, or units

## Database Safety
- Production DBs live in data/*.db — tests MUST NEVER touch these
- All test fixtures use tmp_path or monkeypatch DB_PATH
- budget_agent.py add_transaction() has dedup guard — don't add another
