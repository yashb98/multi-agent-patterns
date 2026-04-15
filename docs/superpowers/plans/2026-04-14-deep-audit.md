# JobPulse Deep Audit Plan — 7-Dimension Production Readiness Review

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Systematically evaluate the JobPulse multi-agent system (82.5k LOC, 365 Python files, 18 databases, 5 Telegram bots, 4 LangGraph patterns) against the standard a senior AI engineer at Anthropic would apply on April 14, 2026.

**Architecture:** 7 independent audit dimensions, each producing a findings report with severity ratings (CRITICAL / HIGH / MEDIUM / LOW / INFO) and concrete remediation steps. Each dimension has specific automated checks (tests, scripts, MCP queries) plus manual review criteria.

**Tech Stack:** Python 3.12, pytest, MCP code-intelligence tools, SQLite introspection, AST analysis, grep-based static analysis.

**Methodology:** Every check falls into one of three categories:
- **Automated** — a command you run, with expected output and pass/fail criteria
- **Inspection** — read specific files/lines and evaluate against criteria
- **Structural** — use MCP tools (find_symbol, callers_of, boundary_check, etc.) to verify architectural invariants

**Scoring:** Each dimension scores 0-10. The overall audit score is the weighted average:
| Dimension | Weight |
|-----------|--------|
| System Design | 20% |
| Tool & Contract Design | 15% |
| Retrieval Engineering | 10% |
| Reliability Engineering | 20% |
| Security & Safety | 20% |
| Evaluation & Observability | 10% |
| Product Thinking / UX | 5% |

---

## Dimension 1: System Design (Weight: 20%)

**What an Anthropic engineer looks for:** Anthropic's own "Building Effective Agents" guidance (2025) says: *start simple, add complexity only when simpler solutions fall short.* For a system with 4 orchestration patterns, the auditor asks: does each pattern justify its existence? Is the agent topology hierarchical (good) or a "bag of agents" mesh (bad, amplifies errors 17x per TDS research)? Are convergence gates enforced? Is state management race-free?

### Check 1.1: Agent Topology — Hierarchical vs Mesh

**Type:** Structural (MCP)

- [ ] **Step 1: Map all agent-to-agent call paths**

Run `callers_of` on each pattern's entry node to verify the topology is a DAG (directed acyclic graph), not a fully-connected mesh.

```
Targets:
- patterns/hierarchical.py → researcher_node, writer_node, reviewer_node
- patterns/map_reduce.py → map_node, reduce_node
- patterns/plan_and_execute.py → planner_node, executor_node
- patterns/peer_debate.py → debater_node, judge_node (if exists)
```

**Pass criteria:** Each pattern has a clear hierarchy: dispatcher → orchestrator → workers. No worker-to-worker direct calls without going through the orchestrator.

**Fail indicators:** Any call path where agent A calls agent B and agent B calls agent A (bidirectional coupling).

- [ ] **Step 2: Verify no "bag of agents" topology**

```bash
# Check that patterns/ agents don't import from each other
grep -r "from patterns\." patterns/ | grep -v "__pycache__" | grep -v "test" | grep -v "__init__"
```

**Pass:** Zero cross-pattern imports. Each pattern is self-contained.

- [ ] **Step 3: Count agents per workflow**

For each pattern, count the number of distinct agent nodes. Research shows >5 sequential agents in a workflow causes compound accuracy decay (0.85^n).

```
Check: callers_of / callees_of on each pattern's build_graph() function
Target: ≤5 sequential steps per workflow
```

### Check 1.2: Convergence Gates

**Type:** Inspection

- [ ] **Step 1: Verify dual-gate convergence in all 4 patterns**

```
Files to inspect:
- patterns/hierarchical.py — look for quality >= 8.0 AND accuracy >= 9.5
- patterns/map_reduce.py — same dual gate
- patterns/plan_and_execute.py — same dual gate
- patterns/peer_debate.py (or dynamic_swarm.py) — patience counter + threshold
```

**Pass:** Every pattern has BOTH quality AND accuracy gates. No pattern accepts output on quality alone.

- [ ] **Step 2: Verify max iteration bounds**

```bash
grep -rn "max_iterations\|MAX_ITER\|max_retries\|iteration.*<\|iteration.*>" patterns/
```

**Pass:** Every loop in every pattern has a hard cap (expected: 3 for hierarchical/dynamic_swarm, patience-based for peer_debate).

**CRITICAL fail:** Any unbounded loop (while True without break condition tied to iteration count).

### Check 1.3: State Management — Race Conditions

**Type:** Structural + Inspection

- [ ] **Step 1: Verify state update atomicity**

LangGraph uses `TypedDict` with reducer annotations. Check that all state updates use `Annotated` types with proper reducers (not raw assignment that overwrites).

```
File: shared/state.py — inspect AgentState definition
Check: All list-type fields use Annotated[list, operator.add] reducer
Check: No agent returns the full state — only changed fields
```

- [ ] **Step 2: Verify prune_state() called at convergence points**

```bash
grep -rn "prune_state" patterns/
```

**Pass:** prune_state() called at every convergence/routing node in all 4 patterns. Expected limits: research_notes=3, agent_history=20, token_usage=30.

- [ ] **Step 3: Verify topic immutability**

```bash
grep -rn 'state\["topic"\]\s*=' patterns/ shared/agents.py
grep -rn "topic.*=.*state" patterns/ shared/agents.py | grep -v "topic = state\[" | grep -v "topic=state\["
```

**Pass:** `topic` is only READ from state, never reassigned after `create_initial_state()`.

### Check 1.4: Dual Dispatcher Invariant

**Type:** Automated

- [ ] **Step 1: Compare AGENT_MAP keys in both dispatchers**

```python
# Script: verify both dispatchers handle the same intents
import ast, sys

def get_agent_map_keys(filepath):
    with open(filepath) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "AGENT_MAP":
                    if isinstance(node.value, ast.Dict):
                        return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return set()

d1 = get_agent_map_keys("jobpulse/dispatcher.py")
d2 = get_agent_map_keys("jobpulse/swarm_dispatcher.py")
diff = d1.symmetric_difference(d2)
if diff:
    print(f"FAIL: Mismatched intents: {diff}")
    sys.exit(1)
print(f"PASS: Both dispatchers handle {len(d1)} intents")
```

**Pass:** Zero symmetric difference between the two AGENT_MAPs.

### Check 1.5: Dependency Direction

**Type:** Automated (MCP)

- [ ] **Step 1: Run boundary_check**

```
MCP: boundary_check() with default rules
Expected: shared/ never imports from jobpulse/, patterns/, mindgraph_app/
```

**Known violations from current codebase (must be fixed):**
- `shared/fact_checker.py:145` imports `jobpulse.config.OPENAI_API_KEY`
- `shared/fact_checker.py:199` imports `jobpulse.config.OPENAI_API_KEY`
- `shared/streaming.py:200` references test file in jobpulse/

**Severity:** HIGH — these break the dependency invariant.

### Check 1.6: Dependency Cycles

**Type:** Automated (MCP)

- [ ] **Step 1: Run dependency_cycles**

```
MCP: dependency_cycles(max_depth=4)
```

**Known cycles (must be evaluated):**
- `mindgraph_app/api.py ↔ mindgraph_app/retriever.py` — circular import within same module
- `shared/tool_integration.py ↔ shared/tools/browser.py` — likely lazy-import, but verify
- `shared/external_verifiers.py ↔ tests/shared/test_searxng_fact_checker.py` — test importing source is fine, reverse is a bug

**Pass:** No cycles between separate top-level modules. Intra-module cycles rated MEDIUM.

### Check 1.7: God Functions

**Type:** Automated (MCP)

- [ ] **Step 1: Run suggest_extract**

```
MCP: suggest_extract(top_n=10, min_lines=100)
```

**Known hotspots:**
- `_run_scan_window_inner` — 630 lines, risk 0.75, 130 callees. **CRITICAL** complexity.
- `LinkedInAdapter.fill_and_submit` — 710 lines (worktree), 212 callees. **CRITICAL**.
- `runner.py::main` — 342 lines. HIGH.

**Pass criteria:** No function >200 lines in main branch (stretch goal: >100 lines).

### Scoring Rubric (Dimension 1)

| Score | Criteria |
|-------|----------|
| 9-10 | All checks pass. No god functions >200 lines. Zero boundary violations. |
| 7-8 | Convergence gates present. 1-2 boundary violations. God functions identified but bounded. |
| 5-6 | Missing convergence gates in 1 pattern. 3+ boundary violations. Unbounded god functions. |
| 3-4 | Bag-of-agents topology in any pattern. Missing iteration bounds. Race conditions possible. |
| 0-2 | No convergence gates. Circular dependencies between modules. Unbounded loops. |

---

## Dimension 2: Tool & Contract Design (Weight: 15%)

**What an Anthropic engineer looks for:** MCP spec (2025-11-25) requires JSON Schema with strict types, required fields, and descriptions for all tool parameters. Equixly's 2026 audit found 43% of MCP implementations had command injection vulnerabilities. Every tool input is an injection surface. The auditor checks: are schemas complete? Are inputs validated? Are permissions enforced? Is there an audit trail?

### Check 2.1: Tool Schema Completeness

**Type:** Inspection

- [ ] **Step 1: Audit each tool definition in shared/tools/**

```bash
ls shared/tools/*.py
```

For each tool file, verify:
1. Every parameter has `type` (str, int, bool, list, dict)
2. Every parameter has `description` (not empty)
3. Required fields are marked as `required`
4. Enum values are constrained (not free-text where a finite set exists)
5. String parameters have reasonable length limits or format constraints
6. Examples are provided in descriptions

```python
# Check pattern — for each tool's get_definition() method:
# Verify actions dict has this structure:
{
    "action_name": {
        "description": str,       # Non-empty
        "risk": RiskLevel,        # Properly classified
        "params": {
            "param_name": {
                "type": str,      # "string", "integer", "boolean", etc.
                "description": str,  # Non-empty, with example
                "required": bool,
                "enum": list | None,  # For finite sets
                "maxLength": int | None,  # For strings
            }
        }
    }
}
```

**Pass:** Every tool has complete schemas. Every parameter has type + description.

- [ ] **Step 2: Verify tool descriptions are prompt-engineered**

Tool descriptions are what the LLM uses to decide when/how to call tools. They must be specific, unambiguous, and include:
- What the tool does (one sentence)
- When to use it (conditions)
- What it returns (output format)
- Constraints (rate limits, permissions)

### Check 2.2: Input Validation & Injection Defense

**Type:** Inspection + Automated

- [ ] **Step 1: Check for command injection surfaces**

```bash
# Find all subprocess/os.system/eval/exec calls in tool implementations
grep -rn "subprocess\|os\.system\|os\.popen\|eval(\|exec(" shared/tools/ jobpulse/
```

For each hit, verify:
- User-supplied input is NEVER interpolated into shell commands
- `subprocess.run` uses list args (not `shell=True`)
- No `eval()` or `exec()` on user input

- [ ] **Step 2: Check for path traversal in file-handling tools**

```bash
grep -rn "open(\|Path(\|os\.path" shared/tools/
```

Verify all file paths are:
- Validated against an allowlist of directories
- Canonicalized (resolve symlinks) before access
- Not constructed from user input without sanitization

- [ ] **Step 3: Check for SQL injection in SQLite operations**

```bash
# Find f-string or %-format SQL queries (injection risk)
grep -rn 'f".*SELECT\|f".*INSERT\|f".*UPDATE\|f".*DELETE' shared/ jobpulse/ mindgraph_app/
grep -rn '% .*SELECT\|% .*INSERT' shared/ jobpulse/ mindgraph_app/
```

**Pass:** All SQL uses parameterized queries (`?` placeholders). Zero f-string SQL.

### Check 2.3: Permission Model Enforcement

**Type:** Inspection

- [ ] **Step 1: Verify ToolExecutor permission gates are not bypassable**

```
File: shared/tool_integration.py
Check: execute() ALWAYS checks permissions before calling execute_fn
Check: No tool has a public execute method that bypasses ToolExecutor
Check: approval_fn is called for REQUIRES_APPROVAL and CRITICAL risk
```

- [ ] **Step 2: Verify DEFAULT_PERMISSIONS follow least privilege**

```
Current permissions (from tool_integration.py:121-167):
- researcher: read-only web_search + browser + gmail + discord. GOOD.
- writer: all DENY. GOOD — writers shouldn't touch tools.
- reviewer: read-only web_search only. GOOD.
- code_expert: read-only web + requires_approval terminal. ACCEPTABLE.
- notifier: read_write on gmail/telegram/discord, requires_approval on linkedin. REVIEW: is linkedin approval actually enforced?
```

- [ ] **Step 3: Verify rate limiting works**

```
Check: _call_counts is per (agent, tool) key
BUG: _call_counts is never reset — after rate_limit_per_minute hits, the agent is permanently blocked.
Need: time-window-based rate limiting (sliding window or reset per minute).
```

**Severity:** HIGH — rate limiting is broken (no time window reset).

### Check 2.4: Audit Trail Completeness

**Type:** Inspection

- [ ] **Step 1: Verify all tool calls are audited**

```
Check: ToolExecutor.execute() records AuditEntry for ALL outcomes:
- Success ✓ (line 246)
- Permission denied ✓ (line 217-218, 221-222, 229-230)
- Rate limited ✓ (line 237-238)
- Exception ✓ (line 254-261)
- Tool not found ✓ (line 201-202)
- Action not found ✓ (line 207-208)
```

- [ ] **Step 2: Verify audit log is persisted (not just in-memory)**

```
FINDING: AuditLog stores entries in a Python list (self.entries = []).
This is VOLATILE — lost on process restart.
Severity: HIGH — audit trail must survive restarts for compliance.
Recommendation: Persist to SQLite (data/audit.db) with WAL mode.
```

- [ ] **Step 3: Verify record_dispatch is called by both dispatchers**

```bash
grep -rn "record_dispatch" jobpulse/dispatcher.py jobpulse/swarm_dispatcher.py
```

**Pass:** Both dispatchers call `record_dispatch` after every agent invocation.

### Check 2.5: Test Coverage for Tool Integration

**Type:** Automated (MCP)

- [ ] **Step 1: Run test_coverage_map for tool_integration.py**

```
MCP: test_coverage_map(file="shared/tool_integration.py")
```

**Known finding:** 0% test coverage on tool_integration.py. Every function is uncovered:
- `AuditLog.__init__`, `record`, `get_recent` — untested
- `ToolExecutor.__init__`, `execute`, `_audit_denied`, `grant_permission` — untested

**Severity:** CRITICAL — the permission/audit system has zero tests.

### Scoring Rubric (Dimension 2)

| Score | Criteria |
|-------|----------|
| 9-10 | All schemas complete with types + descriptions + examples. Zero injection surfaces. Rate limiting works. Audit persisted. 80%+ test coverage. |
| 7-8 | Schemas mostly complete. No injection. Rate limiting has minor bugs. Audit in-memory only. |
| 5-6 | Missing descriptions/types on some params. 1-2 injection risks. Rate limiting broken. |
| 3-4 | Incomplete schemas. SQL injection possible. No audit trail. |
| 0-2 | No schemas. Command injection. No permission model. |

---

## Dimension 3: Retrieval Engineering (Weight: 10%)

**What an Anthropic engineer looks for:** The gold standard in 2026 is a 3-stage pipeline: BM25 + dense semantic + cross-encoder reranking, achieving 87% relevant docs in top-10 (vs 62% BM25-only, 71% semantic-only). The auditor checks: is there hybrid search? Are embeddings fresh? Is there reranking? Are retrieval quality metrics tracked?

### Check 3.1: Hybrid Search Architecture

**Type:** Inspection

- [ ] **Step 1: Verify HybridSearch implements FTS5 + vector + RRF**

```
File: shared/hybrid_search.py
Check:
- FTS5 table with porter + unicode61 tokenizer ✓ (line 68-73)
- Vector cosine similarity via embeddings ✓ (line 55-56, numpy)
- Reciprocal Rank Fusion with K=60 ✓ (line 38)
- Configurable weights: fts_weight=1.3, vec_weight=1.0 ✓ (line 52-53)
```

**Pass:** All three stages present. FTS5 weight > vector weight (appropriate for code search where exact identifiers matter).

- [ ] **Step 2: Check for cross-encoder reranking stage**

```bash
grep -rn "rerank\|cross.encoder\|cohere.*rerank\|jina.*rerank" shared/ mindgraph_app/
```

**Finding:** Likely no cross-encoder reranking. This is the biggest retrieval quality gap — reranking alone adds 59% MRR@5 improvement.

**Severity:** MEDIUM — hybrid BM25+vector is good, but reranking is the 2026 standard.

### Check 3.2: Embedding Quality

**Type:** Inspection

- [ ] **Step 1: Check embedding model and dimensionality**

```bash
grep -rn "embedding\|voyage\|text-embedding\|ada-002" shared/hybrid_search.py shared/code_intelligence/
```

From startup hook: "Loaded 16611 embeddings into memory (numpy)". Verify:
- Which embedding model? (Voyage Code 3 from semantic_search description)
- Dimensionality?
- Are embeddings refreshed when source code changes?

- [ ] **Step 2: Check embedding staleness**

```bash
# When was the embedding index last rebuilt?
ls -la data/code_intelligence.db
```

Verify there's a mechanism to rebuild embeddings when code changes (pre-commit hook, cron, or manual).

### Check 3.3: Retrieval in Fact Checker

**Type:** Inspection

- [ ] **Step 1: Verify 3-level verification pipeline**

```
File: shared/fact_checker.py
Check verify_claims() (159 lines, risk 0.65):
1. Research notes verification (from agent state) — fast, free
2. External verification (Semantic Scholar, web search via SearXNG) — slower, API costs
3. Cache lookup (data/verified_facts.db) — instant reuse

Verify: cache is checked BEFORE external APIs (not after).
```

- [ ] **Step 2: Check scoring honesty**

```
Per rules: abstract-only verification = 0.5 (5.0/10), not 1.0.
Verify compute_accuracy_score() applies correct weights:
- VERIFIED: +1.0
- INACCURATE: -2.0
- EXAGGERATED: -1.0
- UNVERIFIED: -0.5 to -1.5
```

### Check 3.4: MindGraph Retrieval (Knowledge Graph)

**Type:** Inspection

- [ ] **Step 1: Verify multi-hop search**

```
File: mindgraph_app/retriever.py
Functions: multi_hop_search (risk 0.75), deep_query (risk 0.75)
Check: multi-hop traversal has depth limit (prevent infinite graph walks)
Check: results are ranked by relevance, not arbitrary order
```

### Check 3.5: Retrieval Metrics

**Type:** Inspection

- [ ] **Step 1: Check if retrieval quality is measured**

```bash
grep -rn "MRR\|NDCG\|recall_at_k\|precision_at_k\|retrieval.*metric" shared/ mindgraph_app/ tests/
```

**Expected finding:** No retrieval quality metrics. This is a gap — without MRR/NDCG tracking, there's no way to know if retrieval is degrading over time.

**Severity:** MEDIUM — retrieval works but quality is unmeasured.

### Scoring Rubric (Dimension 3)

| Score | Criteria |
|-------|----------|
| 9-10 | Hybrid search + reranking. Embedding freshness verified. Retrieval metrics tracked. Fact-checker cache-first. |
| 7-8 | Hybrid search without reranking. Embeddings reasonably fresh. Fact-checker pipeline correct. |
| 5-6 | Single-mode search (BM25-only or vector-only). No freshness checks. |
| 3-4 | Naive RAG. No caching. Context flooding. |
| 0-2 | No structured retrieval. Raw LLM without grounding. |

---

## Dimension 4: Reliability Engineering (Weight: 20%)

**What an Anthropic engineer looks for:** Anthropic tracked 114 incidents in 90 days (early 2026), OpenAI had a 34-hour outage in June 2025. Multi-provider fallback is mandatory. The auditor checks: retry with jitter? Circuit breakers? Fallback chains? Cost caps? Timeout on every LLM call?

### Check 4.1: Retry Strategy

**Type:** Inspection

- [ ] **Step 1: Audit shared/llm_retry.py**

```
Current implementation:
- MAX_RETRIES = 3 ✓
- BASE_DELAY = 2.0s ✓
- BACKOFF_FACTOR = 2.0 ✓ (delays: 2s, 4s, 8s)
- MAX_DELAY = 30s ✓
- RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504} ✓
- Pattern matching for transient errors ✓

MISSING: Jitter. Current implementation uses deterministic delays.
```

```python
# Current (line 89):
delay = min(base_delay * (backoff_factor ** attempt), max_delay)

# Should be (with jitter):
import random
delay = min(base_delay * (backoff_factor ** attempt), max_delay)
delay = delay * (0.5 + random.random())  # ±50% jitter
```

**Severity:** MEDIUM — works for single-agent, but without jitter, multiple agents hitting the same rate limit will retry in sync (thundering herd).

- [ ] **Step 2: Verify smart_llm_call uses retry**

```
File: shared/streaming.py — smart_llm_call()
Check: Does it wrap calls with retry_with_backoff?
```

- [ ] **Step 3: Verify non-retryable errors are not retried**

```
Check is_retryable_error() correctly returns False for:
- 400 Bad Request
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found
- AuthenticationError
- InvalidRequestError
```

### Check 4.2: Circuit Breakers

**Type:** Structural

- [ ] **Step 1: Check for circuit breaker pattern**

```bash
grep -rn "circuit.breaker\|CircuitBreaker\|OPEN\|HALF_OPEN\|failure_count\|failure_threshold" shared/ jobpulse/
```

**Expected finding:** No circuit breaker implementation. When an LLM provider is down, every request retries 3x before failing — wasting time and potentially money.

**Severity:** HIGH — without circuit breakers, a provider outage causes cascading delays across all 5 bots.

### Check 4.3: Multi-Provider Fallback

**Type:** Inspection

- [ ] **Step 1: Check get_llm() for fallback chain**

```
File: shared/agents.py — get_llm() (32 lines, risk 0.6)
Check: Does it support fallback from primary model to secondary?
Current: _resolve_provider() probes Ollama, falls back to cloud.
```

```bash
grep -rn "fallback\|secondary.*model\|backup.*model\|FALLBACK" shared/agents.py
```

**Expected finding:** Fallback is Ollama → cloud only. No cloud-to-cloud fallback (e.g., OpenAI → Anthropic → local).

**Severity:** MEDIUM — cloud provider outage = total system failure.

### Check 4.4: Timeout on LLM Calls

**Type:** Inspection

- [ ] **Step 1: Check for timeout parameters**

```bash
grep -rn "timeout\|request_timeout\|max_tokens" shared/agents.py shared/streaming.py
```

Verify every LLM instantiation sets:
- `request_timeout` (network timeout, recommended: 60s)
- `max_tokens` (output cap, prevents runaway generation)

**CRITICAL if missing:** An LLM call without timeout can hang indefinitely, blocking the event loop.

### Check 4.5: Cost Controls

**Type:** Inspection

- [ ] **Step 1: Audit cost_tracker.py**

```
Current implementation:
- estimate_cost() with MODEL_COSTS dict ✓
- track_llm_usage() extracts tokens from response ✓
- compute_cost_summary() aggregates per-agent ✓

BUG: Duplicate key in MODEL_COSTS:
  "gpt-4.1-mini": (0.40, 1.60),  # line 19
  "gpt-4.1-mini": (0.15, 0.60),  # line 21 — OVERWRITES line 19
```

**Severity:** LOW — dict silently uses last value. But confusing and potentially wrong pricing.

- [ ] **Step 2: Check for cost budget enforcement**

```bash
grep -rn "budget\|cost_limit\|max_cost\|spending_cap\|COST_MAX" shared/ jobpulse/
```

**Check:** Is there a per-run or per-day cost cap that STOPS execution when exceeded?
Tracking cost is not the same as enforcing a budget.

- [ ] **Step 3: Verify cost tracking is actually called**

```bash
grep -rn "track_llm_usage\|estimate_cost" patterns/ jobpulse/ shared/agents.py
```

**Pass:** Every LLM call site tracks usage. Not just declared but wired in.

### Check 4.6: Database Reliability

**Type:** Automated

- [ ] **Step 1: Verify WAL mode on all 18 databases**

```python
import sqlite3, glob
for db in sorted(glob.glob("data/*.db")):
    conn = sqlite3.connect(db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    status = "✓" if mode == "wal" else f"✗ ({mode})"
    print(f"  {db}: {status}")
```

**Pass:** All 18 databases use WAL mode (concurrent reads, no blocking).

- [ ] **Step 2: Check for connection leaks**

```bash
# Find sqlite3.connect() calls that don't use context manager
grep -rn "sqlite3.connect" shared/ jobpulse/ mindgraph_app/ | grep -v "with " | grep -v "test"
```

**Pass:** All connections use `with` context managers or explicit close().

### Check 4.7: Graceful Degradation

**Type:** Inspection

- [ ] **Step 1: Check error propagation in dispatchers**

```
File: jobpulse/dispatcher.py, jobpulse/swarm_dispatcher.py
Check: When an agent fails, does the dispatcher:
  a) Return a structured error (DispatchError) ✓ per error-handling rules
  b) Include partial results if available
  c) NOT crash the entire bot
```

- [ ] **Step 2: Verify Telegram bot error handling**

```bash
grep -rn "except.*Exception\|except:" jobpulse/telegram/ jobpulse/platforms/
```

**Pass:** Top-level handlers catch exceptions and send user-friendly error messages.
**Fail:** Any `except: pass` or unhandled exception that crashes the bot process.

### Scoring Rubric (Dimension 4)

| Score | Criteria |
|-------|----------|
| 9-10 | Retry + jitter. Circuit breakers. Multi-provider fallback. Timeouts everywhere. Cost enforcement. WAL on all DBs. |
| 7-8 | Retry without jitter. No circuit breakers. Timeouts present. Cost tracked but not enforced. |
| 5-6 | Basic retry. No fallback chain. Some missing timeouts. Cost partially tracked. |
| 3-4 | Retry without backoff. No cost tracking. Connection leaks. |
| 0-2 | No retry. No timeouts. No cost visibility. |

---

## Dimension 5: Security & Safety (Weight: 20%)

**What an Anthropic engineer looks for:** Anthropic's "Trustworthy Agents" framework (April 9, 2026) defines 5 principles: human control, alignment with expectations, security (defense-in-depth), transparency, and privacy. OWASP LLM Top 10 (2025) puts prompt injection at #1 and sensitive information disclosure at #2. The auditor checks: is there layered injection defense? Are credentials managed properly? Is the system prompt protected? Does the Google token file in data/ contain live credentials?

### Check 5.1: Credential Management

**Type:** Automated + Inspection

- [ ] **Step 1: Scan for hardcoded secrets**

```bash
# Check for API keys, tokens, passwords in source code
grep -rn "api_key\s*=\s*['\"]" --include="*.py" . | grep -v test | grep -v ".claude/worktrees"
grep -rn "password\s*=\s*['\"]" --include="*.py" . | grep -v test | grep -v ".claude/worktrees"
grep -rn "secret\s*=\s*['\"]" --include="*.py" . | grep -v test | grep -v ".claude/worktrees"
grep -rn "token\s*=\s*['\"]" --include="*.py" . | grep -v test | grep -v ".claude/worktrees"
```

- [ ] **Step 2: Check .gitignore for sensitive files**

```bash
cat .gitignore | grep -i "env\|secret\|key\|token\|credential\|\.db"
```

**CRITICAL finding from semantic_search:** `data/google_token.json` contains a live OAuth token (`ya29.a0Aa7MYip81PVs...`). This file MUST be:
1. In .gitignore
2. NOT committed to git history
3. Using short-lived tokens (check expiry)

- [ ] **Step 3: Check credential loading pattern**

```bash
grep -rn "os\.environ\|os\.getenv\|dotenv\|load_dotenv" shared/ jobpulse/ | head -20
```

**Pass:** All credentials loaded from environment variables or .env files, never from source code.
**Check:** Is there a `.env.example` with dummy values?

- [ ] **Step 4: Verify ATS_ACCOUNT_PASSWORD handling**

```
Per rules: single password via ATS_ACCOUNT_PASSWORD env var, credentials in data/ats_accounts.db.
Check: password is NOT logged, NOT included in error messages, NOT sent over unencrypted channels.
```

### Check 5.2: Prompt Injection Defense

**Type:** Inspection

- [ ] **Step 1: Check system prompt boundaries**

```bash
grep -rn "SystemMessage\|system.*prompt\|SYSTEM_PROMPT" shared/prompts.py shared/agents.py
```

Verify:
- System prompts are NOT constructed from user input
- User input is clearly delimited (e.g., wrapped in XML tags like `<user_input>`)
- No user input is interpolated into system messages without sanitization

- [ ] **Step 2: Check for output injection (LLM05)**

```bash
# Find places where LLM output is used as input to another LLM or tool
grep -rn "result.*invoke\|output.*execute\|response.*call" patterns/ jobpulse/
```

In multi-agent systems, one agent's output becomes another's input. If agent A is compromised via prompt injection, it can inject instructions into agent B's prompt.

**Check:** Is there any sanitization between agent handoffs? (Typically there isn't — this is a known industry gap.)

- [ ] **Step 3: Check Telegram input sanitization**

```bash
grep -rn "message\.text\|update\.message" jobpulse/telegram/ jobpulse/platforms/
```

Verify user messages from Telegram are:
- Stripped of control characters
- Length-limited before being passed to LLM
- Not directly interpolated into prompts as system-level text

### Check 5.3: OWASP LLM Top 10 Specific Checks

**Type:** Inspection

- [ ] **Step 1: LLM02 — Sensitive Information Disclosure**

```bash
# Check what data is sent to external LLM APIs
grep -rn "messages.*=\|prompt.*=\|content.*=" shared/agents.py shared/streaming.py | head -20
```

Verify:
- Personal data (email, phone, address) is not included in LLM prompts unless necessary
- Error messages don't leak internal paths, database schemas, or API keys

- [ ] **Step 2: LLM06 — Excessive Agency**

```
File: shared/tool_integration.py
Current: DEFAULT_PERMISSIONS properly restricts tools per agent role.
But: _default_approval() auto-approves everything (line 272-274).

FINDING: The "human approval" gate auto-approves by default.
This means REQUIRES_APPROVAL and CRITICAL risk actions are NEVER actually blocked.
```

**Severity:** CRITICAL — the entire permission model is a no-op unless a custom approval_fn is provided.

- [ ] **Step 3: LLM07 — System Prompt Leakage**

```bash
# Check if system prompts are included in user-visible output
grep -rn "system.*prompt\|SYSTEM_PROMPT" shared/prompts.py | head -10
```

Verify prompts are not returned in error messages or API responses.

- [ ] **Step 4: LLM08 — Vector/Embedding Weaknesses**

```
Check: Can untrusted data be added to the embedding store?
File: shared/hybrid_search.py — add() method
Verify: Only trusted, internal data is embedded. No user-submitted content in the vector store.
```

- [ ] **Step 5: LLM10 — Unbounded Consumption**

```
Check: Is there a max_tokens on every LLM call?
Check: Is there a per-run cost cap?
Check: Is there a per-day token budget?
```

### Check 5.4: Database Security

**Type:** Automated

- [ ] **Step 1: Check database file permissions**

```bash
ls -la data/*.db | awk '{print $1, $NF}'
```

**Pass:** Database files are readable/writable only by the owner (600 or 644).

- [ ] **Step 2: Check for data at rest concerns**

```
data/ats_accounts.db — contains ATS credentials
data/google_token.json — contains OAuth token
data/applications.db — contains personal application data

Verify: These files are NOT world-readable.
Recommendation: Encrypt sensitive databases or use OS-level encryption.
```

### Check 5.5: HTTPS Enforcement

**Type:** Automated

- [ ] **Step 1: Scan for HTTP (non-HTTPS) URLs**

```bash
grep -rn "http://" --include="*.py" . | grep -v "https://" | grep -v "localhost" | grep -v "127.0.0.1" | grep -v "0.0.0.0" | grep -v test | grep -v ".claude/worktrees" | grep -v "# http"
```

**Pass:** Zero non-localhost HTTP URLs. Per rules: "Always HTTPS for external APIs (arXiv burned rate limit on HTTP→HTTPS redirect)."

### Scoring Rubric (Dimension 5)

| Score | Criteria |
|-------|----------|
| 9-10 | No hardcoded secrets. Short-lived tokens. Layered injection defense. Working HITL approval. All OWASP items addressed. |
| 7-8 | Credentials in env vars. Basic input sanitization. Permission model present (even if auto-approve). HTTPS enforced. |
| 5-6 | Some secrets in config files. No prompt injection defense. Auto-approve on critical actions. |
| 3-4 | Hardcoded secrets. No input validation. No permission model. |
| 0-2 | Live tokens in git. Command injection possible. No security model at all. |

---

## Dimension 6: Evaluation & Observability (Weight: 10%)

**What an Anthropic engineer looks for:** OpenTelemetry GenAI Semantic Conventions (v1.37+) define standard tracing for LLM apps. LLM-as-judge aligns with human judgment at 85% (higher than human-to-human). The auditor checks: is every LLM call traced? Are costs tracked per-agent? Is there quality monitoring? Can you debug a failed workflow from logs alone?

### Check 6.1: Tracing & Log Correlation

**Type:** Inspection

- [ ] **Step 1: Verify run ID correlation**

```
File: shared/logging_config.py
Current: Thread-local run_id injected into every log record ✓
Format: "%(asctime)s [%(levelname)s] [%(run_id)s] %(name)s: %(message)s" ✓
Run ID format: "run_a1b2c3" (6 hex chars) ✓
```

- [ ] **Step 2: Check run ID propagation across agent handoffs**

```bash
grep -rn "set_run_id\|generate_run_id" patterns/ jobpulse/ shared/
```

**Check:** Is a run_id generated at workflow start and propagated through all agent calls? Or does each agent get its own run_id (making correlation impossible)?

- [ ] **Step 3: Verify log level appropriateness**

```bash
# Check for excessive DEBUG logging in hot paths
grep -rn "logger\.debug" shared/hybrid_search.py shared/streaming.py shared/agents.py | wc -l
# Check for missing ERROR logging on exceptions
grep -rn "except.*Exception" shared/ jobpulse/ | grep -v "logger\.\(error\|warning\|exception\)" | head -20
```

### Check 6.2: Per-Agent Cost Tracking

**Type:** Structural

- [ ] **Step 1: Verify track_llm_usage is called at every LLM callsite**

```bash
# Count LLM call sites vs tracking calls
grep -rn "smart_llm_call\|\.invoke(" shared/agents.py patterns/ | wc -l
grep -rn "track_llm_usage" shared/agents.py patterns/ | wc -l
```

**Pass:** Every `smart_llm_call` / `.invoke()` site has a corresponding `track_llm_usage` call.

- [ ] **Step 2: Check cost summary in pattern finish nodes**

```bash
grep -rn "compute_cost_summary\|cost_summary\|total_cost" patterns/
```

**Pass:** Every pattern's finish/convergence node computes and logs the cost summary.

### Check 6.3: Quality Evaluation Infrastructure

**Type:** Structural

- [ ] **Step 1: Check for LLM-as-judge evaluation**

```bash
grep -rn "judge\|evaluate.*quality\|score.*output\|llm.*eval" shared/ patterns/ jobpulse/
```

**Check:** Is there automated quality scoring on production outputs? Or only convergence gates during execution?

- [ ] **Step 2: Check for regression test generation from failures**

```bash
grep -rn "regression\|failure.*test\|test.*from.*log" shared/ tests/
```

**Expected finding:** No automated regression test generation from production failures.
**Severity:** LOW — nice-to-have, not critical.

### Check 6.4: Dashboard & Alerting

**Type:** Inspection

- [ ] **Step 1: Verify 4 dashboards exist and are current**

```
Per CLAUDE.md: "4 dashboards"
Check: Where are they? What do they show?
- FastAPI /docs at localhost:8080?
- MindGraph visualization?
- Job analytics dashboard?
- Cost dashboard?
```

- [ ] **Step 2: Check for alerting on anomalies**

```bash
grep -rn "alert\|notify\|telegram.*send.*error\|send.*warning" jobpulse/ shared/
```

**Check:** Are there automated alerts for:
- LLM provider outage (3+ consecutive failures)
- Cost anomaly (>2x daily average)
- Quality degradation (convergence scores dropping)
- Rate limit exhaustion (platform daily caps hit)

### Check 6.5: Experiential Learning Feedback Loop

**Type:** Inspection

- [ ] **Step 1: Verify learning extraction**

```
File: shared/experiential_learning.py
Check: High-scoring runs (>= 7.0) extract learnings at convergence nodes.
Check: Extracted patterns are injected into future agent prompts.
Check: LRU eviction maintains quality (quality * 0.6 + recency * 0.4).
```

- [ ] **Step 2: Verify experience is actually used**

```bash
grep -rn "experience\|ExperienceMemory\|learned_pattern\|inject.*experience" patterns/ shared/agents.py
```

**Pass:** Experience retrieval is wired into all 4 patterns' agent prompts.

### Scoring Rubric (Dimension 6)

| Score | Criteria |
|-------|----------|
| 9-10 | Full tracing with run_id correlation. Per-agent cost tracking. Quality evals on production traffic. Alerting on anomalies. |
| 7-8 | Run_id logging. Cost tracking present. Convergence gates as quality eval. Basic alerting. |
| 5-6 | Logging but no correlation. Cost tracked but not per-agent. No quality evals. |
| 3-4 | Basic print logging. No cost tracking. No alerting. |
| 0-2 | No logging. No observability. Flying blind. |

---

## Dimension 7: Product Thinking / UX Design (Weight: 5%)

**What an Anthropic engineer looks for:** Smashing Magazine's 2026 framework defines 6 agentic UX patterns: intent preview, autonomy dial, explainable rationale, graceful error recovery, progressive disclosure, and accountability trail. Anthropic's Claude Code uses read-only-by-default with explicit approval for modifications. The auditor asks: does the user feel in control? Can they interrupt? Do they understand what the agent is doing?

### Check 7.1: Confirmation Before Irreversible Actions

**Type:** Inspection

- [ ] **Step 1: Verify JOB_AUTOPILOT_AUTO_SUBMIT=false by default**

```bash
grep -rn "AUTO_SUBMIT\|auto_submit" jobpulse/
```

**Pass:** Auto-submit is off by default. User must explicitly approve each application.

- [ ] **Step 2: Check all irreversible actions have confirmation**

```
Irreversible actions in JobPulse:
- Submitting a job application (Easy Apply, Greenhouse, Lever, Workday)
- Sending an email (Gmail agent)
- Posting to Notion
- Deleting budget entries (undo is one-level only)
```

For each: verify there's a Telegram confirmation prompt before execution.

### Check 7.2: Progressive Disclosure

**Type:** Inspection

- [ ] **Step 1: Check Telegram message structure**

```bash
grep -rn "send_message\|reply_text\|reply_html\|reply_markdown" jobpulse/telegram/ jobpulse/platforms/ | head -20
```

**Check:** Are messages structured with:
- Short summary first (1-2 sentences)
- Details expandable or in follow-up messages
- Inline keyboards for actions (not "type YES to confirm")

- [ ] **Step 2: Check for wall-of-text responses**

```bash
# Find message strings longer than 500 characters
grep -rn '""".*"""' jobpulse/telegram/ | awk 'length > 600'
```

### Check 7.3: Error Communication

**Type:** Inspection

- [ ] **Step 1: Check Telegram error messages**

```bash
grep -rn "Error\|error\|fail\|exception" jobpulse/telegram/ | grep -i "send_message\|reply" | head -20
```

**Check:** Error messages should:
- Be human-readable (not stack traces)
- Suggest a next action ("try again", "check X", "contact Y")
- Not expose internal implementation details

### Check 7.4: Escape Routes

**Type:** Inspection

- [ ] **Step 1: Check /cancel and /help availability**

```bash
grep -rn "/cancel\|/help\|/stop\|/abort" jobpulse/telegram/
```

**Pass:** /help and /cancel are registered handlers that work at any point in a conversation.

### Check 7.5: Scan Summary UX

**Type:** Inspection

- [ ] **Step 1: Verify scan completion notification**

```
Per rules: Telegram scan summary includes:
- Notion Skill Tracker link
- Pending skill count
- Funnel stats (Found → Applied → Interview with conversion rates)
```

Check the scan completion message actually includes all of these.

### Check 7.6: Feedback Loops

**Type:** Inspection

- [ ] **Step 1: Check Notion Skill Tracker integration**

```
Flow: JD scan → extract skills → push unverified to Notion → user marks "I Know"/"Don't Know" → sync back to MindGraph

Check: Is the UX smooth? Can the user verify skills from Telegram? Or must they open Notion?
```

### Check 7.7: Typing Indicators & Progress

**Type:** Inspection

- [ ] **Step 1: Check for typing indicators on long operations**

```bash
grep -rn "send_chat_action\|typing\|ChatAction" jobpulse/telegram/
```

**Pass:** Long operations (scan, application, CV generation) show typing indicator.

### Scoring Rubric (Dimension 7)

| Score | Criteria |
|-------|----------|
| 9-10 | Confirmation on all irreversible actions. Progressive disclosure. Human-readable errors with next actions. /help + /cancel always work. Typing indicators. |
| 7-8 | Confirmation present. Messages mostly structured. Basic error handling. |
| 5-6 | Some confirmations missing. Wall-of-text messages. Generic error messages. |
| 3-4 | No confirmation. Raw error dumps. No escape routes. |
| 0-2 | Fully autonomous with no user control. Black box operation. |

---

## Execution Plan

### Phase 1: Automated Checks (parallel, ~15 minutes)

Run all automated checks (MCP queries, grep scans, SQLite introspection) in parallel. These produce binary pass/fail results.

| Check | Tool | Output |
|-------|------|--------|
| 1.4 Dual Dispatcher | Python script | PASS/FAIL + diff |
| 1.5 Boundary Check | MCP boundary_check | Violations list |
| 1.6 Dependency Cycles | MCP dependency_cycles | Cycle list |
| 1.7 God Functions | MCP suggest_extract | Functions >100 lines |
| 2.2 Injection Scan | grep | Hit list |
| 2.5 Tool Coverage | MCP test_coverage_map | Coverage % |
| 4.6 WAL Mode | Python script | Per-DB status |
| 5.1 Secret Scan | grep | Hit list |
| 5.5 HTTPS Scan | grep | Non-HTTPS URLs |

### Phase 2: Inspection Checks (sequential, ~30 minutes)

Read specific files and evaluate against criteria. These require judgment.

### Phase 3: Scoring & Report (5 minutes)

Compute per-dimension scores, weighted average, and generate the final report with:
1. Overall score (0-10)
2. Per-dimension breakdown
3. CRITICAL findings (must fix before production)
4. HIGH findings (fix within 1 week)
5. MEDIUM findings (fix within 1 month)
6. LOW/INFO findings (backlog)

---

## Known Findings (Pre-Audit, from exploration)

These are already confirmed from the codebase analysis:

| # | Severity | Dimension | Finding |
|---|----------|-----------|---------|
| F1 | **CRITICAL** | Security | `data/google_token.json` contains live OAuth token in semantic search index |
| F2 | **CRITICAL** | Security | `_default_approval()` auto-approves all REQUIRES_APPROVAL and CRITICAL risk actions |
| F3 | **CRITICAL** | Tool Design | `shared/tool_integration.py` has 0% test coverage |
| F4 | **HIGH** | System Design | 3 boundary violations: `shared/fact_checker.py` imports from `jobpulse.config` |
| F5 | **HIGH** | Tool Design | AuditLog is in-memory only — lost on restart |
| F6 | **HIGH** | Tool Design | Rate limiting has no time window — permanent block after N calls |
| F7 | **HIGH** | Reliability | No circuit breaker pattern anywhere in codebase |
| F8 | **HIGH** | System Design | `_run_scan_window_inner` is 630 lines with 130 callees |
| F9 | **MEDIUM** | Reliability | Retry has no jitter (thundering herd risk) |
| F10 | **MEDIUM** | Reliability | Duplicate key `gpt-4.1-mini` in MODEL_COSTS dict |
| F11 | **MEDIUM** | Retrieval | No cross-encoder reranking stage |
| F12 | **MEDIUM** | Retrieval | No retrieval quality metrics (MRR, NDCG, recall@k) |
| F13 | **MEDIUM** | Observability | Unknown if run_id propagates across agent handoffs |
| F14 | **LOW** | System Design | `mindgraph_app/api.py ↔ retriever.py` circular dependency |
| F15 | **LOW** | Observability | No automated quality evals on production traffic |
