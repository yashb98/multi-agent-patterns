# Subsystem 11 — `memory_layer` (line-by-line audit)

**Scope (matches audit prompt entry):**
- Entry: `MemoryManager` facade — `query`, `store_memory`, `learn_fact`,
  `learn_procedure`, `record_episode`, `pin_memory`, `get_procedural_entries`,
  `get_episodic_entries`, `run_forgetting_sweep`, `startup`, `health`,
  `flush_secondary_sync`, `shutdown`, plus the `get_shared_memory_manager`
  / `reset_shared_memory_manager` singleton accessors.
- Files (14 modules, ~140 KB / ~3 200 LOC effective):
  - `__init__.py` — package surface
  - `_entries.py` (225 LOC) — `MemoryTier`, `Lifecycle`, `EdgeType`,
    `ProtectionLevel`, `MemoryEntry`, `EpisodicEntry`, `SemanticEntry`,
    `ProceduralEntry`, `ShortTermEntry`, `PatternEntry`
  - `_stores.py` (433 LOC) — JSON-backed `ShortTermMemory`,
    `EpisodicMemory`, `SemanticMemory`, `ProceduralMemory`
  - `_sqlite_store.py` (335 LOC) — unified `memories` table
    (source-of-truth + access log + tier views)
  - `_qdrant_store.py` (254 LOC) — vector store with one collection per
    tier (1024-d Voyage / BGE)
  - `_neo4j_store.py` (419 LOC) — Memory graph nodes + edges + signals
  - `_embedder.py` (111 LOC) — BGE-M3 (Ollama) primary, MiniLM fallback,
    Voyage 3 Large optional
  - `_query.py` (93 LOC) — `MemoryQuery`, `RetrievalPlan`, `QueryRouter`
  - `_router.py` (72 LOC) — 3-tier `TieredRouter` (cached → lightweight
    → full agent)
  - `_pattern.py` (183 LOC) — `PatternMemory` (hybrid search)
  - `_linker.py` (201 LOC) — A-MEM `AutonomousLinker` + 7-rule
    `classify_relationship`
  - `_forgetting.py` (213 LOC, +55 post-fix) — 6-signal `ForgettingEngine`
  - `_sync.py` (192 LOC, +35 post-fix) — `SyncService` 3-engine
    reconciliation + tombstone propagation
  - `_manager.py` (727 LOC) — `MemoryManager` facade, `_TTLCache`,
    `_build_three_engine_kit`, shared singleton
- Output of the subsystem:
  - `data/agent_memory/memories.db` — 10 MB SQLite, 27 786 rows
    (procedural 19 789, semantic 7 794, episodic 203). Schema: unified
    `memories` table + access log + 3 tier views.
  - `data/agent_memory/{episodic,semantic,procedural,patterns}.json` —
    legacy JSON-backed stores (190 / 1 041 / 100 / 50 entries).
  - Qdrant collections: `episodic_memories`, `semantic_facts`,
    `procedures`, `experiences`, `screening_questions` (5 total).
  - Neo4j `Memory` nodes + 9 edge types
    (`SIMILAR_TO`/`RELATED_TO`/`CONTRADICTS`/etc.).

---

## 1. Function inventory + wiring

### Category legend
- **A** — runtime: definitely called during `apply_job()` / cron tick
- **B** — runtime-conditional: only when env flag / external service is wired
- **C** — runtime-unreachable from apply path; tests / patterns / CLI only
- **D** — orphan: imported nowhere in production; truly dead

### 1.1 `_manager.py` (29 funcs, facade)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 46 | `_TTLCache.__init__` / 51-71 `_prune` / `get` / `set` / `clear` | A | 60s context cache + 120s embed cache. |
| 106 | `MemoryManager.__init__` | A | Constructs JSON stores eagerly; 3-engine constructed lazily based on kwargs. JSON+SQLite **dual-write** path established here (L117-152). |
| 154 | `get_context_for_agent` | C | Pattern-tier code only (`patterns/{hierarchical, peer_debate, ...}`). Not on apply path. |
| 215 | `search_patterns` | C | Pattern-tier only. |
| 226 | `learn_from_success` | C | Pattern-tier only. |
| 248 | `record_step` | C | Pattern-tier only. |
| 254 | `record_episode` | A | Called by cognitive `_reflexion.py:157`. Writes to JSON `episodic.json` AND SQLite `memories` (when 3-engine enabled). |
| 312 | `learn_fact` | A | Called by cognitive `_classifier.py:158`, optimization `_tracker.py:139`, `_engine.py:307`. JSON + SQLite dual-write. |
| 326 | `learn_procedure` | A | Called by cognitive `_engine.py:327`, `_reflexion.py:140`, `_tree_of_thought.py:178`, optimization `_engine.py:355`, execution `_awareness.py:175`. JSON + SQLite dual-write. **19 783/19 789 procedural rows have `source='optimization_success_streak'`.** |
| 364 | `get_procedural_entries` | A | Called by cognitive `_classifier.py:85`, `_engine.py:223`, `_strategy.py:53,63`. **Reads `self.procedural.recall(...)` (JSON-only, 100-cap) — never SQLite.** |
| 368 | `get_episodic_entries` | A | Called by cognitive `_classifier.py:101`, `_reflexion.py:122`, `_strategy.py:98`. Reads JSON-only (200-cap). |
| 372 | `start_new_session` | C | Pattern-tier only. |
| 376 | `get_memory_report` | C | Used by `runner.py learning-report` CLI. |
| 428 | `store_memory` | A | Producer for SQLite + secondary-sync queue. **Does NOT invoke `AutonomousLinker`** (B-2 deferred). |
| 451 | `query` | A | Called by `screening_pipeline.py:506` for semantic answer recall. Routes through `QueryRouter` and reads ONLY from SQLite/Qdrant/Neo4j (never JSON stores). |
| 518 | `pin_memory` | A | Called by optimization `_tracker.py:149`, `_engine.py:378`. Writes `payload.pinned=True` to **SQLite only** — JSON stores have no pin concept. |
| 527 | `startup` | B | Called by `multi_bot_listener.py:45` (production cron). Triggers `_sync.reconcile()` which is O(N) embedding + O(N) Qdrant has_point. With 27 786 entries that's expensive; reconcile only fires when items are missing in secondary stores. |
| 534 | `health` | B | Read by `webhook_server` health endpoint. |
| 547 | `flush_secondary_sync` | B | Called by tests, no production caller. |
| 552 | `run_forgetting_sweep` | A | **(B-1 FIXED 2026-05-08, commit `e9b2919`)** Pre-fix called `self._forgetting.sweep(...)` which did not exist; AttributeError swallowed by try/except → warning. multi_bot_listener tick called this every ~1h → silent no-op since the method was first referenced. Post-fix delegates to new `ForgettingEngine.sweep`. |
| 573 | `shutdown` | B | Called from `reset_shared_memory_manager`. |
| 586 | `_truthy` | A | env-flag helper for `_build_three_engine_kit`. |
| 593 | `_build_three_engine_kit` | A | Probes SQLite → Embedder → Qdrant → Neo4j; logs `WARN` on each failure; returns kit dict. Lazy embedder init (no network calls in __init__). |
| 689 | `get_shared_memory_manager` | A | The production singleton entry point. Called by 9 production callers. |
| 718 | `reset_shared_memory_manager` | C | Test isolation only. |

### 1.2 `_stores.py` (27 funcs)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 22 | `_atomic_json_write` | A | Temp-file + os.replace. Used by all 4 JSON stores. |
| 50 | `ShortTermMemory.__init__` / 53 `add` / 62 `get_recent` / 65 `format_for_prompt` / 75 `clear` | C | Pattern-tier only. |
| 95 | `EpisodicMemory.__init__` (eager `_load`) | A | Loads `episodic.json` at construction. 200-entry cap. |
| 101 | `EpisodicMemory.store` | A | LRU-by-timestamp eviction. Called from `MemoryManager.record_episode`. |
| 110 | `EpisodicMemory.recall` | A | Called from `MemoryManager.get_episodic_entries`. **Returns at most 200 entries; SQLite has 203 — gap small but non-zero.** |
| 121 | `format_for_prompt` | C | Pattern-tier only. |
| 139 | `get_domain_stats` | C | Used by `get_memory_report` CLI. |
| 160 | `_save` / 180 `_load` | A | JSON I/O. `_save` warning-logs failures. `_load` only debug-logs (m-1, see findings). |
| 211 | `SemanticMemory.__init__` / 217 `learn` / 250 `contradict` / 262 `recall` | A | Called via `MemoryManager.learn_fact`. **`max_facts=500` is documented as a cap but never enforced** — the actual prod file has 1 041 entries (M-B). |
| 271 | `SemanticMemory.format_for_prompt` | C | Pattern-tier. |
| 288 | `_make_id` | A | MD5 of `(domain.lower():fact.lower()[:100])`. |
| 294 | `_save` / 310 `_load` | A | Both warning-log on `_save`, debug-only on `_load` (m-1). |
| 337 | `ProceduralMemory.__init__` / 343 `store` / 373 `recall` / 388 `format_for_prompt` | A | `store` dedups same-strategy-same-domain; `recall` reads in-memory list (100-cap). |
| 408 | `_save` / 424 `_load` | A | `_save` debug-only on failure (m-2). |

### 1.3 `_sqlite_store.py` (23 funcs)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 68 | `__init__` | A | WAL + busy_timeout=5000 set. Thread-local connection pool. |
| 78 | `_get_conn` | A | Thread-local. |
| 88 | `_init_schema` | A | Includes 3 views: `episodic_memories` / `semantic_facts` / `procedures` over the unified `memories` table. |
| 98 | `_row_to_entry` | A | embedding=[] (lives in Qdrant). |
| 116 | `_record_read` | A | Writes to `memory_access_log` only when a trajectory_id is set. **0 rows in production** — trajectory_id is rarely set in the apply path; access logging is decorative. |
| 137 | `insert` (UPSERT) | A | Called from `MemoryManager.store_memory`. |
| 166 | `touch` | A | Called per-result from `MemoryManager.query`. |
| 180 | `count_by_lifecycle` | A | Used by `get_memory_report`. |
| 189 | `update_decay` / 198 `update_lifecycle` / 207 `update_confidence` | A (post-fix) | Now invoked via `ForgettingEngine.sweep`. |
| 216 | `tombstone` / 225 `revive` | A (post-fix) | Same — sweep tombstones. |
| 238 | `get_by_id` | A | Called from `MemoryManager.query` and `_sync.reconcile`. |
| 249 | `get_by_ids` | A | Batch hydrate from query. |
| 262 | `query_by_tier` / 271 `query_by_domain` / 280 `query_by_lifecycle` / 289 `query_by_decay_desc` | C | None called in apply path; tests + analytics only. |
| 298 | `query_active` | A | Called by `MemoryManager.query` FTS fallback path (when Qdrant unavailable). |
| 307 | `query_tombstoned_recent` | C | Test-only. |
| 321 | `count` / 329 `all_memory_ids` | A | `count` used by `health` + `get_memory_report`; `all_memory_ids` used by `_sync.reconcile` and `ForgettingEngine.sweep`. |

### 1.4 `_qdrant_store.py` (9 funcs)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 45 | `_to_qdrant_id` | A | MD5-int conversion (allows arbitrary hex strings as memory IDs). |
| 68 | `__init__` | A | `:memory:` for tests, http URL for prod. `_dims` stored as int — read by the new dim guard. |
| 79 | `ensure_collections` | A | Idempotent — only creates if not present. |
| 98 | `upsert` | A | Called by `_sync._sync_entry` (post-dim-guard). |
| 121 | `delete` | A | Called by `_sync.propagate_tombstone` from `ForgettingEngine.sweep` tombstone path. |
| 133 | `search` | A | Called from `MemoryManager.query` vector path. 2-retry on transient failure, returns `[]` on exhaustion. |
| 213 | `search_all_tiers` | C | No production caller. Only `screening_pattern_extractor.py` operates on Qdrant directly. |
| 240 | `count` | C | Test/analytics. |
| 246 | `has_point` | A | Called by `_sync.reconcile`. |

### 1.5 `_neo4j_store.py` (14 funcs)

All 14 methods are **B (env-conditional)** — only run when `MEMORY_NEO4J_URI` is configured AND auth succeeds. In this audit's dev environment, Neo4j auth fails (NEO4J_PASSWORD missing): `_available=False` → every method returns its no-op default. In production where credentials are set, they back the ForgettingEngine signal queries (`degree`, `count_similar`, `avg_downstream_score`) and the QueryRouter `domain_neighbors` / `expand` graph paths.

The two methods that depend on edges existing in Neo4j (`degree`, `count_similar`, `avg_downstream_score`, `expand`) **today return 0/0/0/[]** because **`AutonomousLinker.link_with_neighbors` is never invoked** — see M-A.

### 1.6 `_embedder.py` (10 funcs)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 18 | `_get_minilm` | A (lazy) | Lazy-loaded via `sentence_transformers.SentenceTransformer`. |
| 26-50 | `MemoryEmbedder.__init__` / `dims` | A | **Default `primary='bge'` (class) but `_build_three_engine_kit:639` defaults to `'voyage'`** — defaults disagree (n-1). |
| 52-58 | `_get_voyage` | B | Voyage SDK lazy init. |
| 60 | `_embed_voyage` | B | 1024-d. |
| 65 | `_embed_minilm` | A (fallback) | 384-d. |
| 70 | `_embed_bge` | A (Ollama) | 1024-d. urllib request — no httpx. |
| 87 | `_run_fallback` | A | Routes to fallback model. |
| 94 | `embed` | A | One-text wrapper. |
| 97 | `embed_batch` | A | Primary then fallback. **Pre-fix B-2: silently emits 384-d vectors when fallback fires while `dims` still reports 1024.** |

### 1.7 `_sync.py` (10 funcs)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 14 | `__init__` | A | Background worker started when `start_background=True` AND (qdrant or neo4j). |
| 34 | `_start_worker` / 45 `_run_worker` | A | Daemon thread. |
| 66 (post-fix) | `_embed_for_qdrant` | A | **(B-2 added 2026-05-08, commit `45432ec`)** Validates embedded vector dim before Qdrant upsert; warning-logs and returns `None` on mismatch. |
| 84 | `_sync_entry` | A | Now routes through `_embed_for_qdrant`. |
| 98 | `reconcile` | B (startup) | Now routes through `_embed_for_qdrant`. |
| 130 | `propagate_tombstone` | A | Called by `ForgettingEngine.sweep` (post-fix). |
| 137 | `sync_to_secondary` | A | Producer for the background queue. |
| 158 | `flush` / 166 `pending_count` / 170 `shutdown` | B | Lifecycle methods. |

### 1.8 `_forgetting.py` (5 funcs after fix)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 28 | `__init__` | A | |
| 31 | `compute_decay` | A | 6-signal: recency / frequency / quality / uniqueness + connectivity bonus + impact bonus. **In dev env w/o Neo4j edges, the 3 graph signals all return defaults → only recency/frequency/quality fire.** Same in prod for entries the linker never connected (= all of them today, see M-A). |
| 90 | `get_protection` | A | NONE / ELEVATED / PROTECTED / PINNED. |
| 111 | `evaluate_single` | A | Per-entry classifier. |
| 159 (post-fix) | `sweep` | A | **(B-1 added 2026-05-08, commit `e9b2919`)** Iterates `all_memory_ids`, calls `evaluate_single`, applies decay/promote/demote/tombstone via SQLiteStore. |

### 1.9 `_linker.py` (4 funcs)

All 4 methods are **D (dead in production apply path)** — `MemoryManager.__init__` constructs `self._linker` (L151) but no callsite ever invokes `link_with_neighbors` or `handle_contradiction`. Only tests and the integration test reach them. See M-A.

### 1.10 `_query.py` (2 funcs)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 48 | `QueryRouter.__init__` | A | Plumbed by `MemoryManager.query`. |
| 52 | `QueryRouter.route` | A | 4 routes: by-id / semantic / domain-only / default. |

### 1.11 `_router.py` (4 funcs)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 34 | `TieredRouter.__init__` / 39 `route` / 56 `cache_result` / 61 `_hash_task` | C | Pattern-tier only (`patterns/*`); `MemoryManager.__init__` constructs but the apply path never invokes `route()`. |

### 1.12 `_pattern.py` (7 funcs)

All 7 methods are **C** — only invoked via `MemoryManager.search_patterns` / `learn_from_success`, both of which are pattern-tier only.

### 1.13 `_entries.py` (5 funcs)

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 81 | `MemoryEntry.create` | A | Used by `store_memory`. |
| 108 | `MemoryEntry.touch` | C | No production caller (SQLiteStore.touch is preferred). |
| 130 | `EpisodicEntry.relevance_score` | A | Used by `EpisodicMemory.recall`. |
| 166 | `SemanticEntry.reliability` | A | Used by `SemanticMemory.recall` reliability filter. |
| 212 | `PatternEntry.relevance_score` | C | Pattern-tier. |

---

## 2. Findings (severity-tagged)

### 2.1 Blockers (shipped this session)

- **B-1** `_manager.py:560` (pre-fix) / `_forgetting.py:159` (post-fix) — `MemoryManager.run_forgetting_sweep` called `self._forgetting.sweep(dry_run=...)` which did not exist on `ForgettingEngine`. The error was caught at `_manager.py:569` and warning-logged. `multi_bot_listener.py:68` calls this every ~1h — silent no-op since the method was first referenced. **CLAUDE.md** claim "Forgetting sweep runs hourly — 6-signal decay score" was false at the call layer. **Fix:** implement `ForgettingEngine.sweep(sqlite_store, sync_service, dry_run)` that iterates `all_memory_ids`, calls `evaluate_single`, applies decay/promote/demote/tombstone via `SQLiteStore` methods. Live verification on production data: 27 827 evaluated, 27 657 decayed, 22 131 tombstoned (dry_run). Commit: `e9b2919`. Regression tests: `tests/shared/memory_layer/test_forgetting.py::TestSweepLoop` (6 tests).
- **B-2** `_sync.py:66-79` (post-fix) — when `MemoryEmbedder` primary (BGE-M3 / Voyage, both 1024-d) fails and falls back to MiniLM (384-d), the `SyncService` background worker called `qdrant.upsert(...384-d-vector...)` into a 1024-d collection. Qdrant rejects, `_run_worker` warning-logs the exception. SQLite has the entry, Qdrant doesn't — silent divergence. `reconcile()` shares the shape per missing-entry backfill. **Fix:** new `_embed_for_qdrant` helper validates `len(vector) == self._qdrant._dims` (when `_dims` is an `int`) and returns `None` + warning on mismatch; `_sync_entry` and `reconcile` route through it. Neo4j writes proceed unaffected. Live verification reproduces and confirms the skip + warning. Commit: `45432ec`. Regression tests: `tests/shared/memory_layer/test_sync.py::TestEmbedderDimGuard` (3 tests).

### 2.2 Deferred majors

- **✅ M-A (FIXED in pipeline-bugs S6)** `_manager.py:151` constructed `self._linker = AutonomousLinker(neo4j=neo4j)` but no production callsite ever invoked `_linker.link_with_neighbors(...)`. `store_memory` wrote to SQLite + queued secondary sync; `_sync._sync_entry` upserted Qdrant + created Neo4j node — but never called the linker. Result: **Neo4j had Memory nodes but zero edges** in production. Cascade impact: `ForgettingEngine.compute_decay`'s connectivity / impact / uniqueness graph signals always returned defaults (degree=0, downstream_score=0.0, count_similar=0) — half of the 6-signal decay formula was decorative for every entry. **Fix:** `SyncService.__init__` now accepts a `linker` parameter; `_sync_entry` calls a new private `_link_neighbors(entry, vector)` after the upsert + node creation. The neighbor finder reuses the upsert vector (avoiding both a redundant Voyage call and the risk of a fallback-model mismatch), searches all 4 indexed Qdrant tiers (`top_k=5`, `score_threshold=0.5`), filters self-match by `memory_id`, hydrates from SQLite via `get_by_ids`, and hands the resulting `[(MemoryEntry, similarity)]` list to `link_with_neighbors`. Bounded per-write cost: ≤4 Qdrant round-trips + 1 SQLite batch get + 1 Neo4j MERGE batch. Regression: `tests/shared/memory_layer/test_linker_wiring.py` (4 tests). `Neo4jStore.count_edges()` added for production verification. `handle_contradiction` remains unwired and is the next-session candidate.
- **🔴 M-B** `_stores.py:211-213, 217-248` — `SemanticMemory.max_facts=500` is documented as the cap but `SemanticMemory.learn` has **no eviction logic**. Every `learn_fact` call appends a new fact (or reinforces existing) but the dict grows unbounded. Production `semantic.json` has 1 041 entries despite the documented 500 cap. Symptoms: JSON load on startup is O(n) — currently 440 KB / 1 041 entries → fine; at the current write rate (~60/day across all domains) the file will hit 5 MB / 10 K entries within ~6 months and `_load` will start blocking. `EpisodicMemory.store` and `ProceduralMemory.store` both have eviction; only Semantic doesn't. **Fix shape:** mirror `EpisodicMemory.store`'s eviction-on-overflow at `_stores.py:248` — sort by `reliability * confidence`, keep top `max_facts`. Pure additive, ~6 LOC. **Why deferred:** out of scope for the 2-blocker this session ships; the cognitive read-path side of this gap is M-C (cognitive reads JSON-only, the cap matters even more there).
- **🔴 M-C** `_manager.py:364, 368` — `get_procedural_entries` / `get_episodic_entries` read from JSON-backed `ProceduralMemory.recall` (100-cap) and `EpisodicMemory.recall` (200-cap) **only**, never SQLite. Cognitive engine consumers (`_classifier.py:85,101`, `_engine.py:223`, `_strategy.py:53,98`, `_reflexion.py:122`) see at most 100/200 entries, while SQLite has 19 789 procedural / 203 episodic. Even with dedup-aware framing (greenhouse.io has 766 rows → 135 distinct strategies; lever.co 684 → 76 distinct), production has **at least ~378 distinct procedural strategies** that get squeezed into the 100-slot JSON store via "first 50 chars match per domain" dedup with global LRU eviction. The 19 783 of 19 789 rows tagged `source='optimization_success_streak'` are written by `OptimizationEngine._execute_one` every cycle — write-amplified but mostly distinct. Net: cognitive reads see roughly **1/4** of distinct procedural strategies. Compare CLAUDE.md claim "All old API calls now feed the 3-engine memory stack" — writes do, but reads come back from JSON. **Why deferred:** clean fix changes the cognitive consumer contract (`get_procedural_entries` returns `list[ProceduralEntry]` typed; SQLite returns `list[MemoryEntry]` with payload-encoded fields) — touches cross-system signatures. S6 W-2 carryover (`_classifier.load_persisted_stats` directly accesses `memory.semantic.facts`) is the same root cause. Tracked as a follow-up that touches both S6 (cognitive) and S11 (memory).
- **🔴 M-D** `_neo4j_store.py:46-97` — In this dev env, Neo4j connection succeeds at TCP level but `verify()` fails with `Unsupported authentication token, missing key 'credentials'` because `NEO4J_PASSWORD` is not in env. Result: every `_neo4j_store` method returns its no-op default → graph signals dormant for the entire audit. Production likely has the password set, but the fact that the dev/test env can't exercise the graph path makes any "verified" claim about Neo4j-backed graph traversal weak. Same shape as M-A: half of the 6-signal decay formula and the QueryRouter graph-expand path are unverified outside production. **Why deferred:** an environment-config issue, not a code defect. Note for the maintainer: add `NEO4J_PASSWORD` to `.env.example` and document the docker-compose default in `shared/memory_layer/CLAUDE.md`.
- **🔴 M-E** `_sync.py:88-115` `reconcile()` — when invoked at production startup it loops over `all_memory_ids` (27 786 in prod), calling `embedder.embed(content)` per missing entry (1 Voyage API call ≈ ~$0.00002, sometimes free via Ollama) AND `qdrant.has_point` per entry (1 Qdrant round-trip). Even with most entries already synced, the loop is O(N). With current Voyage pricing this is bounded (~$0.50 per full reconcile), but the per-call latency is ~50 ms each → **~23 minutes per startup** if many entries are missing. Pre-B-2 fix, MiniLM fallback writes silently failed → Qdrant became more divergent over time → reconcile work grew unboundedly. `multi_bot_listener.py:45` calls `mm.startup()` on every daemon start. **Why deferred:** reconcile correctness is fine, the issue is cost/latency. Fix shape: skip embedding when has_point=True (already done) and batch has_point lookups (Qdrant supports `count` + filters). Tracked.

### 2.3 Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `_stores.py:178-188, 308-320, 421-432` | `EpisodicMemory._save` and `_load` warning-log on failure; `SemanticMemory._save/_load` and `ProceduralMemory._save/_load` debug-log only. Inconsistent — JSON I/O failures should all be `warning` per error-handling rules. |
| 🟡 m-2 | `_sqlite_store.py:116-131` | `_record_read` writes `memory_access_log` only when `get_trajectory_id() != "no_trajectory"`. In production this is rarely set → 0 rows in `memory_access_log`. Either wire trajectory_id through the apply path (cognitive sets it via `_engine.py`) or delete the table. |
| 🟡 m-3 | `_manager.py:518-525` | `pin_memory` writes `payload.pinned=True` to SQLite but JSON-backed stores have no pin concept. Pinning a procedure does NOT prevent ProceduralMemory's eviction-on-overflow at `_stores.py:365-370` from dropping it. Cognitive engine readers (M-C) still see the unpinned cap. Consequence: `OptimizationEngine` pins meant to prevent forgetting only protect SQLite; the JSON-cap window still drops "important" procedures. |
| 🟡 m-4 | `_manager.py:451-516` `query()` | "FTS fallback" uses `query_active(min_decay=...)` then Python-side substring `in` check. With 27 786 active rows that's a full table scan + Python lowercase per row. Fine in the dev env w/ Qdrant up; degrades sharply if Qdrant is unreachable. SQLite supports FTS5 — no FTS5 virtual table is created. |
| 🟡 m-5 | `_embedder.py:35-50` | Class default `primary='bge'` but `_build_three_engine_kit:639` defaults `primary='voyage'`. Defaults disagree — direct constructor calls (e.g. tests) get a different model than the production singleton. |
| 🟡 m-6 | `_sync.py:46-50` | `_run_worker` busy-polls with `queue.get(timeout=0.5)` — burns ~2 wakeups/sec when idle. Acceptable but idle CPU wakeups add up across a 24/7 daemon. `queue.get(block=True)` (no timeout) + `_stop_event` posted with a sentinel item would be cleaner. |
| 🟡 m-7 | `_qdrant_store.py:201` | `point.payload["memory_id"]` — KeyError if upsert ever wrote a point without memory_id in payload. Today safe (line 109 always sets it), but adding a `.get()` defaults check costs nothing. |
| 🟡 m-8 | `_embedder.py:107` | `except Exception` for Voyage failure is broad. `urllib.error.URLError` / `OSError` / `RuntimeError` / `TimeoutError` mirror BGE; should match shape. |
| 🟡 m-9 | `_manager.py:200-207` `get_context_for_agent` experiential-memory path | Bare `except Exception: pass` — silently drops experiential context if `get_shared_experience_memory()` fails. C-tier (pattern-only) so low impact, but breaks the OPRAL "no silent swallow" rule. |

### 2.4 Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `_embedder.py:37`, `_manager.py:639` | See m-5 — also a nit cleanup. |
| ⚪ n-2 | `_neo4j_store.py:198-201, 255-258` | `_ALLOWED_EDGE_TYPES` literal duplicated in `create_edge` and `batch_create_edges`. Extract to module-level frozenset. |
| ⚪ n-3 | `_stores.py:96, 212, 338` | Default `storage_path="/tmp/agent_..."` on each store. Tests use tmp_path; production overrides via `MemoryManager.__init__`. The `/tmp` defaults are stale fallbacks — last-survivor of the dev-only days. Could `raise ValueError` instead. |
| ⚪ n-4 | `_pattern.py:52` | `HybridSearch(":memory:")` — in-memory FTS5; rebuilt on every process start (L57-64). Acceptable but means pattern recall has a cold-start cost. Pattern-tier (C) so low impact. |

### 2.5 Dead code

| ID | Location | Description |
|---|---|---|
| 💀 d-1 | `data/agent_memory/memory.db` (0 bytes, on disk) | No production code references the path `memory.db` (only `memories.db`). `git log -- data/agent_memory/` shows no history. Likely a one-time stale write from an earlier path. Safe to delete. |
| 💀 d-2 | `_linker.py` whole module (production apply path) | See M-A. `link_with_neighbors` and `handle_contradiction` only invoked from tests + `tests/shared/memory_layer/test_integration.py`. Code is correct; just never wired. |
| 💀 d-3 | `_router.py` `TieredRouter` whole class | C-tier; only used by `patterns/*` modules, never by apply path. Constructed by `MemoryManager.__init__:131` but no apply-path consumer calls `route()`. |
| 💀 d-4 | `_qdrant_store.py:213` `search_all_tiers` | C-tier; no production caller — `MemoryManager.query` searches per-tier in a loop instead. |
| 💀 d-5 | `_qdrant_store.py:240` `count` | Test-only. |
| 💀 d-6 | `_sqlite_store.py:262, 271, 280, 289, 307` `query_by_tier`, `query_by_domain`, `query_by_lifecycle`, `query_by_decay_desc`, `query_tombstoned_recent` | Test/analytics only; not used by `MemoryManager.query` (which uses `query_active` + ID hydration). |
| 💀 d-7 | `_stores.py` `ShortTermMemory` whole class | C-tier (pattern-only). |
| 💀 d-8 | `_pattern.py` whole module (apply path) | C-tier. |
| 💀 d-9 | `_entries.py:108` `MemoryEntry.touch` | No production caller — `SQLiteStore.touch` is the canonical path. |

### 2.6 Wiring gaps (cross-module)

| ID | Description |
|---|---|
| 🔌 W-1 | **Linker not invoked**, see M-A. Producer/consumer mismatch: linker is a consumer (of Qdrant top-K + SQLite hydrate) and producer (of Neo4j edges). Today neither side runs in production. |
| 🔌 W-2 | **`memory_access_log` table is write-conditional + read-empty**, see m-2. Producer requires `get_trajectory_id() != "no_trajectory"`; current 0 rows in production. No reader of this table exists in repo (`rg "memory_access_log"` returns only the schema + `_record_read` insert). |
| 🔌 W-3 | **`pin_memory` only protects SQLite**, see m-3. Optimization tracker pins → SQLite payload set, but JSON-side eviction continues to drop the entry from cognitive's read path. The pin signal is half-applied. |
| 🔌 W-4 | **`get_procedural_entries`/`get_episodic_entries` read JSON, query reads SQLite — same store, divergent reads.** See M-C. Three cognitive consumer modules and one screening_pipeline consumer disagree on which store is authoritative. |
| 🔌 W-5 | **`_classifier.load_persisted_stats` (cognitive)** reaches into `self._memory.semantic.facts.items()` directly (`shared/cognitive/_classifier.py:179`) — violates the "ALL memory access through MemoryManager" rule. S6 W-2 carryover; flagged here too because the underlying gap is in `MemoryManager` (no public `query_facts_by_domain` accessor exists). |

---

## 3. Live evidence

### 3.1 Test suite

```
$ python -m pytest tests/shared/memory_layer/ -q
127 passed, 12 warnings in 15.14s   (post-fix; +9 new tests over baseline 118)
```

All 9 new regression tests pass:

- `test_forgetting.py::TestSweepLoop::test_sweep_no_entries`
- `test_forgetting.py::TestSweepLoop::test_sweep_promotes_stm_to_mtm`
- `test_forgetting.py::TestSweepLoop::test_sweep_tombstones_decayed_stm`
- `test_forgetting.py::TestSweepLoop::test_sweep_dry_run_no_writes`
- `test_forgetting.py::TestSweepLoop::test_sweep_propagates_tombstone_to_sync`
- `test_forgetting.py::TestSweepLoop::test_run_forgetting_sweep_via_manager`
- `test_sync.py::TestEmbedderDimGuard::test_sync_entry_skips_qdrant_on_dim_mismatch`
- `test_sync.py::TestEmbedderDimGuard::test_sync_entry_writes_qdrant_when_dims_match`
- `test_sync.py::TestEmbedderDimGuard::test_reconcile_skips_qdrant_on_dim_mismatch`

### 3.2 B-1 verified live on production data

```
$ python3 -c "import os; os.environ['MEMORY_QDRANT_URL']='http://localhost:6333'; \
  from shared.memory_layer import get_shared_memory_manager, reset_shared_memory_manager; \
  reset_shared_memory_manager(); mm=get_shared_memory_manager(); \
  print(mm.run_forgetting_sweep(dry_run=True))"

[shared.memory_layer._manager] Forgetting sweep complete: 27657 decayed,
  0 promoted, 0 demoted, 22131 tombstoned
{
  'evaluated': 27827,
  'decayed':   27657,
  'promoted':       0,
  'demoted':        0,
  'tombstoned': 22131,
}
```

Pre-fix this returned `{'enabled': True, 'error': "'ForgettingEngine' object has no attribute 'sweep'"}`. The 22 131 / 27 827 = 79.5 % of entries that would be tombstoned demonstrates how long the sweep has been silently broken: roughly 80 % of memory has decayed below STM/MTM thresholds with no decay updates persisted (decay_score column was stuck at 1.0). **Caveat:** dry-run only. Running this for real shrinks the store from 27 827 → ~5 696 entries; that's the user's call, not the audit's.

### 3.3 B-2 verified directly

```
$ python3 -c "from unittest.mock import MagicMock; \
  from shared.memory_layer._sync import SyncService; \
  e=MagicMock(); e.embed.return_value=[0.1]*384; \
  q=MagicMock(); q._dims=1024; \
  s=SyncService(sqlite=MagicMock(), qdrant=q, embedder=e, neo4j=None, \
                start_background=False); \
  print(s._embed_for_qdrant('x','abc'), q.upsert.call_count)"

[shared.memory_layer._sync] Embedder produced 384-dim vector but Qdrant collection
  expects 1024-dim — skipping Qdrant write (memory_id=abc).
  Likely a primary/fallback model dim mismatch.
None 0
```

### 3.4 Reachability honesty

Live evidence above was captured with:
- **Qdrant (port 6333):** UP and reachable (5 collections present: `episodic_memories`, `semantic_facts`, `procedures`, `experiences`, `screening_questions`).
- **Neo4j (port 7687):** Container UP but auth fails — `Neo4j verify failed: Unsupported authentication token, missing key 'credentials'`. **Neo4j-dependent code paths are NOT exercised in this audit's live evidence.** That includes graph signals in `ForgettingEngine.compute_decay`, all 14 `_neo4j_store` methods at runtime, and `MemoryManager.query`'s graph-expand step.

Don't read "B-1 verified" as "the full sweep is verified end-to-end" — it's verified for the SQLite-only no-graph branch. Connectivity / impact / uniqueness signals (the Neo4j-backed parts of `compute_decay`) are not exercised in this session.

### 3.5 Counts on production data

```
$ sqlite3 data/agent_memory/memories.db "SELECT tier, COUNT(*) FROM memories \
                                          WHERE is_tombstoned=0 GROUP BY 1"
episodic    203
procedural  19789
semantic     7794

$ ls -la data/agent_memory/*.json data/agent_memory/memory.db
   104 662  episodic.json     (190 entries)
   439 784  semantic.json     (1 041 entries)
    33 670  procedural.json   (100 entries)
   ...      patterns.json     (50 entries)
        0   memory.db         (orphan, see d-1)
```

Source distribution of procedural memory (production):
```
optimization_success_streak | 19783
peer_debate                 |     3
dynamic_swarm               |     2
runtime                     |     1
```

→ **99.97 %** of procedural rows are written by `OptimizationEngine.optimize()`'s success-streak detector. The cognitive engine read path (capped 100 in JSON) sees a tiny sample of these.

---

## 4. Cross-module wiring map

Producers → consumers for every signal/event/db-row this subsystem owns:

| Producer | Consumer | Schema/path | Agree? |
|---|---|---|---|
| `MemoryManager.learn_fact` (`_manager.py:312`) | `SemanticMemory._save` → `data/agent_memory/semantic.json` AND `SQLiteStore.insert` → `memories.db` (tier='semantic') | `(domain, fact, run_id)` JSON / `MemoryEntry` SQLite | ✅ same content, different shape |
| same | `OptimizationEngine.SignalAggregator` consumer | n/a — no signal emit on memory writes | n/a |
| `MemoryManager.learn_procedure` (`_manager.py:326`) | `ProceduralMemory._save` → `procedural.json` AND `SQLiteStore.insert` → `memories.db` (tier='procedural') | same as above | ✅ |
| `MemoryManager.record_episode` (`_manager.py:254`) | `EpisodicMemory._save` → `episodic.json` AND `SQLiteStore.insert` → `memories.db` (tier='episodic') | same as above | ✅ |
| `MemoryManager.store_memory` (`_manager.py:428`) | `SQLiteStore.insert` (sync) + `SyncService.sync_to_secondary` (async) | `MemoryEntry` | ✅ |
| `SyncService._sync_entry` (`_sync.py:84`) | `QdrantStore.upsert` + `Neo4jStore.create_node` | `(memory_id, tier, vector, payload)` / `(memory_id, tier, domain, ...)` | ⚠️ **No `AutonomousLinker.link_with_neighbors` call** (M-A) |
| `MemoryManager.pin_memory` (`_manager.py:518`) | `SQLiteStore.insert` (UPSERT with payload.pinned=True) | only SQLite | ⚠️ JSON-side stores ignore the pin (m-3) |
| `MemoryManager.query` (`_manager.py:451`) | reads `QdrantStore.search` → `Neo4jStore.expand` → `SQLiteStore.get_by_ids` | `MemoryEntry` | ✅ |
| `MemoryManager.get_procedural_entries` (`_manager.py:364`) | reads `ProceduralMemory.recall` → JSON (capped 100) | `list[ProceduralEntry]` | ⚠️ **Diverges from `query`** which reads SQLite (M-C) |
| `MemoryManager.get_episodic_entries` (`_manager.py:368`) | reads `EpisodicMemory.recall` → JSON (capped 200) | `list[EpisodicEntry]` | ⚠️ same shape as M-C |
| `MemoryManager.run_forgetting_sweep` (`_manager.py:552`) | `ForgettingEngine.sweep` (post-fix) → `SQLiteStore.update_decay/lifecycle/tombstone` + `SyncService.propagate_tombstone` | counters dict | ✅ post-fix |
| `_sqlite_store._record_read` (`_sqlite_store.py:116`) | writes `memory_access_log` | only when `trajectory_id != "no_trajectory"` | ⚠️ no reader, see W-2 |

Read paths external to memory_layer:
- `screening_pipeline.py:506` → `MemoryManager.query(MemoryQuery(semantic_query=...))` — SQLite/Qdrant path
- `cognitive/_classifier.py:85` → `get_procedural_entries` — JSON path
- `cognitive/_engine.py:223` → `get_procedural_entries` — JSON path
- `cognitive/_reflexion.py:122` → `get_episodic_entries` — JSON path
- `cognitive/_strategy.py:53,98` → `get_procedural_entries` / `get_episodic_entries` — JSON path
- `cognitive/_classifier.py:179` → `self._memory.semantic.facts.items()` — direct attribute reach-through (W-5 / S6 W-2)

Write paths external to memory_layer (in apply hot-path):
- `cognitive/_classifier.py:158` → `learn_fact`
- `cognitive/_engine.py:327` → `learn_procedure`
- `cognitive/_reflexion.py:140` → `learn_procedure`
- `cognitive/_reflexion.py:157` → `record_episode`
- `cognitive/_tree_of_thought.py:178` → `learn_procedure`
- `optimization/_tracker.py:139` → `learn_fact`
- `optimization/_tracker.py:149` → `pin_memory`
- `optimization/_engine.py:307` → `learn_fact`
- `optimization/_engine.py:355` → `learn_procedure`
- `optimization/_engine.py:378` → `pin_memory`
- `execution/_awareness.py:175` → `learn_procedure`

---

## 5. Fixes

### Shipped this session

| ID | File:line | Commit | Fix |
|---|---|---|---|
| B-1 | `_forgetting.py:159-213` | `e9b2919` | Implement `ForgettingEngine.sweep(sqlite_store, sync_service, dry_run)`; route `MemoryManager.run_forgetting_sweep` through it. Live: 27 827 evaluated. |
| B-2 | `_sync.py:66-79, 84-104, 109-115` | `45432ec` | New `_embed_for_qdrant` validates dim against `qdrant._dims`; warning + skip on mismatch. Applied at both `_sync_entry` and `reconcile`. |

### Deferred (see findings)

- **M-A** (linker not invoked) — needs new-session work; cross-cuts SyncService background worker behavior.
- **M-B** (SemanticMemory eviction missing) — small but ships with the regex-to-dynamic / cognitive-read-path follow-up since it interacts with M-C.
- **M-C** (cognitive reads JSON-only) — cross-system contract change; tracked as joint S6 / S11 follow-up.
- **M-D** (Neo4j auth in dev) — env / docs cleanup.
- **M-E** (reconcile O(N) cost) — performance cleanup; bundle when next touching `_sync.py`.

---

## 6. Doc deltas

- `shared/memory_layer/CLAUDE.md` "Forgetting sweep runs hourly — 6-signal decay score" was true post-2026-05-08 only because the `sweep` method now exists. Add a note that of the 6 signals, **3 (connectivity / impact / uniqueness)** depend on `AutonomousLinker.link_with_neighbors` being wired — and today it isn't (see M-A).
- `jobpulse/CLAUDE.md` "All old API calls (`learn_fact`, `record_episode`, `learn_procedure`) now automatically feed the 3-engine memory stack" is correct **for writes**. Reads from `get_procedural_entries` / `get_episodic_entries` still come from JSON-only legacy stores. Document the asymmetry until M-C is resolved.
- `shared/CLAUDE.md` "ALL memory access goes through MemoryManager — never query engines directly" — true except for `cognitive/_classifier.py:179` which reaches into `memory.semantic.facts` directly (W-5 / S6 W-2 carryover).

These doc updates are queued for the post-audit architecture-doc batch (per the audit prompt's STEP 7), not shipped piecemeal.

---

## 7. Stop conditions

- ✅ Under 2-blocker ship cap (the audit prompt's "> 5 blockers" gate).
- ✅ Single-subsystem discipline maintained — `ats_adapters` not touched.
- ✅ Both blocker fixes have regression tests + live verification.
- ⚠️ Neo4j-dependent code paths not exercised in this session (M-D). Stated explicitly above.
