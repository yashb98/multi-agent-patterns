# Ultraplan Phase 1: Fix Broken Automation + Community-First Paper Discovery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get the system back to 100% operational: fix crontab paths, Gmail OAuth, arXiv retry, wiring gaps, add community-first paper discovery with Nitter resilience, and OAuth health monitoring.

**Architecture:** Fix infrastructure first (crontab, OAuth, daemon restart), then build the paper discovery module that replaces the rate-limited arXiv API with community-first discovery from 5 platforms. OAuth monitor and Nitter health tracker provide proactive alerting.

**Tech Stack:** Python 3.12, httpx, praw (Reddit), huggingface_hub, SQLite, launchd, cron

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| crontab | Modify | Fix all paths from Downloads/ to projects/ |
| `jobpulse/arxiv_agent.py` | Modify | Increase retry backoff, integrate paper_discovery |
| `patterns/hierarchical.py` | Modify | Wire ExperienceMemory (like peer_debate.py) |
| `jobpulse/paper_discovery.py` | Create | Community-first paper discovery + NitterHealthTracker |
| `jobpulse/oauth_monitor.py` | Create | Google OAuth health check + Telegram alerts |
| `tests/jobpulse/test_paper_discovery.py` | Create | Tests for all 5 community sources + dedup + fallback |
| `tests/jobpulse/test_oauth_monitor.py` | Create | Tests for scope mismatch detection + alert logic |
| `scripts/setup_integrations.py` | No code change | Re-run for OAuth re-authorization (manual step) |

---

### Task 1: Fix Crontab Paths

**Files:**
- Modify: system crontab (via `crontab -e`)

- [ ] **Step 1: Backup current crontab**

```bash
crontab -l > /Users/yashbishnoi/projects/multi_agent_patterns/data/crontab-backup-2026-04-14.txt
```

- [ ] **Step 2: Replace all paths**

```bash
crontab -l | sed 's|/Users/yashbishnoi/Downloads/multi_agent_patterns|/Users/yashbishnoi/projects/multi_agent_patterns|g' | crontab -
```

- [ ] **Step 3: Add missing profile-sync cron**

```bash
(crontab -l; echo "0 3 * * * cd /Users/yashbishnoi/projects/multi_agent_patterns && /opt/homebrew/anaconda3/bin/python -m jobpulse.runner profile-sync >> logs/profile-sync.log 2>&1") | crontab -
```

- [ ] **Step 4: Verify**

```bash
crontab -l | grep -c "projects/multi_agent_patterns"
# Expected: all entries show "projects/", zero show "Downloads/"
crontab -l | grep -c "Downloads"
# Expected: 0
crontab -l | grep "profile-sync"
# Expected: 0 3 * * * ... profile-sync ...
```

- [ ] **Step 5: Fix daemon restart script quarantine**

```bash
xattr -d com.apple.quarantine /Users/yashbishnoi/projects/multi_agent_patterns/scripts/restart_daemon.sh 2>/dev/null || echo "No quarantine flag"
chmod +x /Users/yashbishnoi/projects/multi_agent_patterns/scripts/restart_daemon.sh
```

---

### Task 2: Gmail OAuth Re-Authorization

**Files:**
- Run: `scripts/setup_integrations.py` (no code change)

- [ ] **Step 1: Check current token scopes**

```bash
python -c "
import json
token = json.load(open('data/google_token.json'))
print('Current scopes:', token.get('scopes', []))
print('Expiry:', token.get('expiry', 'unknown'))
"
```

- [ ] **Step 2: Re-authorize with all 4 scopes**

This is an interactive step — requires browser:

```bash
python scripts/setup_integrations.py
```

Select Google OAuth when prompted. Browser opens, authorize all 4 scopes:
- `gmail.readonly`
- `gmail.modify`
- `calendar.readonly`
- `drive.file`

- [ ] **Step 3: Verify new token**

```bash
python -c "
import json
token = json.load(open('data/google_token.json'))
print('New scopes:', token.get('scopes', []))
from jobpulse.config import GOOGLE_SCOPES
missing = set(GOOGLE_SCOPES) - set(token.get('scopes', []))
print('Missing scopes:', missing or 'NONE - all good')
"
```

Expected: Missing scopes: NONE - all good

- [ ] **Step 4: Test Gmail agent**

```bash
python -c "
from jobpulse.gmail_agent import check_emails
result = check_emails()
print('Gmail agent:', 'OK' if 'error' not in str(result).lower() else result)
"
```

---

### Task 3: Increase arXiv Retry Backoff

**Files:**
- Modify: `jobpulse/arxiv_agent.py:98-114`
- Test: manual verification (existing tests cover arxiv_agent)

- [ ] **Step 1: Read current retry logic**

```bash
# Read lines 95-115 of arxiv_agent.py to see current backoff
```

Current: `time.sleep(5 * (attempt + 1))` → 5s, 10s, 15s

- [ ] **Step 2: Increase backoff to exponential**

In `jobpulse/arxiv_agent.py`, change the retry sleep inside `fetch_papers()`:

```python
# Old:
time.sleep(5 * (attempt + 1))

# New:
time.sleep(30 * (2 ** attempt))  # 30s, 60s, 120s
```

- [ ] **Step 3: Verify change**

```bash
python -c "
for attempt in range(3):
    print(f'Attempt {attempt}: sleep {30 * (2 ** attempt)}s')
"
# Expected: Attempt 0: sleep 30s, Attempt 1: sleep 60s, Attempt 2: sleep 120s
```

- [ ] **Step 4: Commit**

```bash
git add jobpulse/arxiv_agent.py
git commit -m "fix(arxiv): increase retry backoff to 30s/60s/120s

arXiv API returns 429 since Apr 7. Old backoff (5s/10s/15s) was
insufficient. This is a stopgap — Phase 1.5 replaces arXiv API
entirely with community-first discovery."
```

---

### Task 4: Wire ExperienceMemory into Hierarchical Pattern

**Files:**
- Modify: `patterns/hierarchical.py`
- Reference: `patterns/peer_debate.py` (has the pattern to copy)

- [ ] **Step 1: Read peer_debate.py experiential learning wiring**

Check lines 58-71 and the finish node for how `get_shared_experience_memory()` is used:
- Import at top
- Inject past experiences into research/writing prompts
- Extract learnings at finish node when score >= 7.0

- [ ] **Step 2: Read hierarchical.py current state**

Check line 59 and 69 — it has `get_shared_memory_manager()` but NOT `get_shared_experience_memory()`.

- [ ] **Step 3: Add ExperienceMemory import to hierarchical.py**

Add to the imports section (near `get_shared_memory_manager`):

```python
from shared.experiential_learning import get_shared_experience_memory
```

- [ ] **Step 4: Initialize experience memory in the build function**

Where `get_shared_memory_manager()` is called, add:

```python
experience_memory = get_shared_experience_memory()
```

- [ ] **Step 5: Inject experiences into researcher node prompt**

In the researcher node function, before the LLM call, add:

```python
past_experiences = experience_memory.get_relevant(topic, top_k=3)
if past_experiences:
    experience_context = "\n".join(
        f"- {exp['learning']}" for exp in past_experiences
    )
    # Prepend to the research prompt
```

Follow the exact pattern used in `peer_debate.py`'s researcher node.

- [ ] **Step 6: Extract learnings at finish node**

In the finish/convergence node, after scoring:

```python
if quality_score >= 7.0:
    experience_memory.store(
        topic=state["topic"],
        learning=f"Hierarchical pattern produced quality={quality_score:.1f}",
        quality=quality_score,
        pattern="hierarchical",
    )
```

Follow the exact pattern used in `peer_debate.py`'s finish node.

- [ ] **Step 7: Run existing pattern tests**

```bash
python -m pytest tests/test_pattern_memory_integration.py -v
```

Expected: all tests pass (no regressions)

- [ ] **Step 8: Commit**

```bash
git add patterns/hierarchical.py
git commit -m "feat(patterns): wire ExperienceMemory into hierarchical pattern

Only pattern without experiential learning. Now injects past experiences
into researcher prompt and stores learnings at finish node (score >= 7.0).
Matches peer_debate.py pattern."
```

---

### Task 5: OAuth Monitor — Tests First

**Files:**
- Create: `tests/jobpulse/test_oauth_monitor.py`
- Create: `jobpulse/oauth_monitor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/jobpulse/test_oauth_monitor.py`:

```python
"""Tests for Google OAuth health monitoring."""
import json
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestOAuthMonitor:
    """Test OAuth health check and alerting."""

    def test_healthy_token(self, tmp_path):
        """Token with all scopes and valid expiry returns healthy."""
        from jobpulse.oauth_monitor import check_oauth_health

        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": "test",
            "refresh_token": "test_refresh",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/calendar.readonly",
                "https://www.googleapis.com/auth/drive.file",
            ],
            "expiry": "2026-12-31T00:00:00Z",
        }))

        result = check_oauth_health(token_path=token_file)
        assert result["status"] == "healthy"
        assert result["missing_scopes"] == []

    def test_missing_scopes(self, tmp_path):
        """Token missing scopes returns scope_mismatch."""
        from jobpulse.oauth_monitor import check_oauth_health

        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": "test",
            "refresh_token": "test_refresh",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
            "expiry": "2026-12-31T00:00:00Z",
        }))

        result = check_oauth_health(token_path=token_file)
        assert result["status"] == "scope_mismatch"
        assert len(result["missing_scopes"]) == 3

    def test_missing_token_file(self, tmp_path):
        """Missing token file returns missing status."""
        from jobpulse.oauth_monitor import check_oauth_health

        result = check_oauth_health(token_path=tmp_path / "nonexistent.json")
        assert result["status"] == "missing"

    def test_alert_message_for_scope_mismatch(self):
        """Alert message includes re-auth command."""
        from jobpulse.oauth_monitor import format_alert

        health = {
            "status": "scope_mismatch",
            "missing_scopes": ["gmail.modify", "drive.file"],
        }
        msg = format_alert(health)
        assert "setup_integrations.py" in msg
        assert "gmail.modify" in msg

    def test_no_alert_when_healthy(self):
        """Healthy token produces no alert."""
        from jobpulse.oauth_monitor import format_alert

        health = {"status": "healthy", "missing_scopes": []}
        msg = format_alert(health)
        assert msg is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_oauth_monitor.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jobpulse.oauth_monitor'`

- [ ] **Step 3: Implement oauth_monitor.py**

Create `jobpulse/oauth_monitor.py`:

```python
"""Google OAuth health monitor — detects scope mismatches and expiry."""
import json
from pathlib import Path
from datetime import datetime, timezone
from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR, GOOGLE_SCOPES
from jobpulse.telegram_bots import send_alert

logger = get_logger(__name__)

DEFAULT_TOKEN_PATH = DATA_DIR / "google_token.json"


def check_oauth_health(token_path: Path = None) -> dict:
    """Check Google OAuth token validity and scope coverage.

    Returns dict with status, missing_scopes, hours_until_expiry.
    """
    path = token_path or DEFAULT_TOKEN_PATH

    if not path.exists():
        return {"status": "missing", "missing_scopes": [], "message": "Token file not found"}

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return {"status": "broken", "missing_scopes": [], "message": str(e)}

    token_scopes = set(data.get("scopes", []))
    required_scopes = set(GOOGLE_SCOPES)
    missing = sorted(required_scopes - token_scopes)

    if missing:
        return {
            "status": "scope_mismatch",
            "missing_scopes": missing,
            "message": f"Token missing {len(missing)} scope(s)",
        }

    # Check expiry
    expiry_str = data.get("expiry", "")
    hours_left = None
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            hours_left = (expiry - datetime.now(timezone.utc)).total_seconds() / 3600
        except ValueError:
            pass

    return {
        "status": "healthy",
        "missing_scopes": [],
        "hours_until_expiry": hours_left,
    }


def format_alert(health: dict) -> str | None:
    """Format a Telegram alert for unhealthy OAuth state. Returns None if healthy."""
    status = health["status"]

    if status == "healthy":
        return None

    if status == "missing":
        return (
            "\U0001f511 Google OAuth: token file missing.\n"
            "Run: python scripts/setup_integrations.py"
        )

    if status == "scope_mismatch":
        scopes = ", ".join(health["missing_scopes"])
        return (
            f"\U0001f511 Google OAuth: missing scopes: {scopes}\n"
            "Gmail/Calendar/Drive will fail on next token refresh.\n"
            "Run: python scripts/setup_integrations.py"
        )

    if status == "broken":
        return (
            f"\U0001f511 Google OAuth: token broken — {health.get('message', 'unknown')}\n"
            "Run: python scripts/setup_integrations.py"
        )

    return None


def run_health_check(token_path: Path = None, send_alerts: bool = True) -> dict:
    """Run health check and optionally send Telegram alert."""
    health = check_oauth_health(token_path)
    logger.info("OAuth health: %s", health["status"])

    if send_alerts:
        alert = format_alert(health)
        if alert:
            logger.warning("OAuth alert: %s", alert)
            send_alert(alert)

    return health
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/jobpulse/test_oauth_monitor.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/oauth_monitor.py tests/jobpulse/test_oauth_monitor.py
git commit -m "feat: add OAuth health monitor with scope mismatch detection

Checks token file for missing scopes, expiry, and corruption.
Sends Telegram alert via Alert bot with exact re-auth command.
Called on daemon startup + health watchdog cron."
```

---

### Task 6: Paper Discovery — Tests First

**Files:**
- Create: `tests/jobpulse/test_paper_discovery.py`
- Create: `jobpulse/paper_discovery.py`

- [ ] **Step 1: Write failing tests for core discovery logic**

Create `tests/jobpulse/test_paper_discovery.py`:

```python
"""Tests for community-first paper discovery."""
import sqlite3
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestDedup:
    """Test arXiv ID deduplication across sources."""

    def test_dedup_by_arxiv_id(self):
        from jobpulse.paper_discovery import dedup_by_arxiv_id

        papers = [
            {"arxiv_id": "2406.01234", "title": "Paper A", "source": "reddit", "community_buzz": 50},
            {"arxiv_id": "2406.01234", "title": "Paper A", "source": "hackernews", "community_buzz": 100},
            {"arxiv_id": "2406.05678", "title": "Paper B", "source": "huggingface", "community_buzz": 30},
        ]
        result = dedup_by_arxiv_id(papers)
        assert len(result) == 2
        # Should keep the one with higher community_buzz for the duplicate
        paper_a = next(p for p in result if p["arxiv_id"] == "2406.01234")
        assert paper_a["community_buzz"] == 150  # aggregated across sources

    def test_dedup_empty(self):
        from jobpulse.paper_discovery import dedup_by_arxiv_id

        assert dedup_by_arxiv_id([]) == []


class TestNitterHealthTracker:
    """Test Nitter instance health tracking and rotation."""

    def test_record_success(self, tmp_path):
        from jobpulse.paper_discovery import NitterHealthTracker

        tracker = NitterHealthTracker(db_path=tmp_path / "nitter_health.db")
        tracker.record_attempt("https://nitter.net", success=True, response_code=200, latency_ms=300)
        assert tracker.get_success_rate("https://nitter.net") == 1.0

    def test_record_failure_rotates(self, tmp_path):
        from jobpulse.paper_discovery import NitterHealthTracker

        tracker = NitterHealthTracker(db_path=tmp_path / "nitter_health.db")
        # Record 3 failures for first instance
        for _ in range(3):
            tracker.record_attempt("https://nitter.net", success=False, response_code=403, latency_ms=0)

        best = tracker.get_best_instance()
        assert best != "https://nitter.net"  # Should rotate away

    def test_should_skip_when_all_blocked(self, tmp_path):
        from jobpulse.paper_discovery import NitterHealthTracker, NITTER_INSTANCES

        tracker = NitterHealthTracker(db_path=tmp_path / "nitter_health.db")
        for instance in NITTER_INSTANCES:
            for _ in range(5):
                tracker.record_attempt(instance, success=False, response_code=403, latency_ms=0)

        assert tracker.should_skip_x() is True

    def test_reset_after_success(self, tmp_path):
        from jobpulse.paper_discovery import NitterHealthTracker

        tracker = NitterHealthTracker(db_path=tmp_path / "nitter_health.db")
        # Fail 3 times
        for _ in range(3):
            tracker.record_attempt("https://nitter.net", success=False, response_code=403, latency_ms=0)
        # Then succeed
        tracker.record_attempt("https://nitter.net", success=True, response_code=200, latency_ms=500)

        assert tracker.get_success_rate("https://nitter.net") > 0


class TestDiscoverTrending:
    """Test the main discovery pipeline with mocked sources."""

    @patch("jobpulse.paper_discovery.fetch_huggingface_daily")
    @patch("jobpulse.paper_discovery.fetch_reddit_papers")
    @patch("jobpulse.paper_discovery.fetch_hackernews_papers")
    @patch("jobpulse.paper_discovery.fetch_papers_with_code")
    @patch("jobpulse.paper_discovery.fetch_x_via_searxng")
    @patch("jobpulse.paper_discovery.enrich_from_semantic_scholar")
    def test_full_pipeline(self, mock_enrich, mock_x, mock_pwc, mock_hn, mock_reddit, mock_hf):
        from jobpulse.paper_discovery import discover_trending_papers

        mock_hf.return_value = [
            {"arxiv_id": "2406.01234", "title": "Cool Paper", "source": "huggingface", "community_buzz": 80},
        ]
        mock_reddit.return_value = [
            {"arxiv_id": "2406.01234", "title": "Cool Paper", "source": "reddit", "community_buzz": 40},
            {"arxiv_id": "2406.09999", "title": "Other Paper", "source": "reddit", "community_buzz": 20},
        ]
        mock_hn.return_value = []
        mock_pwc.return_value = []
        mock_x.return_value = []
        mock_enrich.side_effect = lambda papers: papers  # passthrough

        result = discover_trending_papers()
        assert len(result) == 2
        # Deduped paper should have aggregated buzz
        cool = next(p for p in result if p["arxiv_id"] == "2406.01234")
        assert cool["community_buzz"] == 120  # 80 + 40

    @patch("jobpulse.paper_discovery.fetch_huggingface_daily")
    @patch("jobpulse.paper_discovery.fetch_reddit_papers")
    @patch("jobpulse.paper_discovery.fetch_hackernews_papers")
    @patch("jobpulse.paper_discovery.fetch_papers_with_code")
    @patch("jobpulse.paper_discovery.fetch_x_via_searxng")
    @patch("jobpulse.paper_discovery.fetch_arxiv_rss_fallback")
    def test_fallback_to_rss(self, mock_rss, mock_x, mock_pwc, mock_hn, mock_reddit, mock_hf):
        from jobpulse.paper_discovery import discover_trending_papers

        # All sources return empty
        mock_hf.return_value = []
        mock_reddit.return_value = []
        mock_hn.return_value = []
        mock_pwc.return_value = []
        mock_x.return_value = []
        mock_rss.return_value = [
            {"arxiv_id": "2406.11111", "title": "RSS Paper", "source": "arxiv_rss", "community_buzz": 0},
        ]

        result = discover_trending_papers()
        assert len(result) == 1
        assert result[0]["source"] == "arxiv_rss"
        mock_rss.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_paper_discovery.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jobpulse.paper_discovery'`

- [ ] **Step 3: Implement paper_discovery.py — data structures and dedup**

Create `jobpulse/paper_discovery.py`:

```python
"""Community-first paper discovery — find papers people are talking about.

Replaces the old 200-paper arXiv API fetch with community-driven discovery
from 5 sources: HuggingFace, Reddit, Hacker News, Papers with Code, X/Twitter.
"""
import re
import time
import sqlite3
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path
from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.woodland.cafe",
]

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")


def dedup_by_arxiv_id(papers: list[dict]) -> list[dict]:
    """Deduplicate papers by arXiv ID, aggregating community_buzz."""
    seen: dict[str, dict] = {}
    for p in papers:
        aid = p.get("arxiv_id", "")
        if not aid:
            continue
        if aid in seen:
            seen[aid]["community_buzz"] = seen[aid].get("community_buzz", 0) + p.get("community_buzz", 0)
            # Track which sources contributed
            sources = seen[aid].get("sources", [seen[aid].get("source", "")])
            sources.append(p.get("source", ""))
            seen[aid]["sources"] = sources
        else:
            seen[aid] = dict(p)
            seen[aid]["sources"] = [p.get("source", "")]
    return list(seen.values())
```

- [ ] **Step 4: Run dedup tests**

```bash
python -m pytest tests/jobpulse/test_paper_discovery.py::TestDedup -v
```

Expected: PASS

- [ ] **Step 5: Implement NitterHealthTracker**

Append to `jobpulse/paper_discovery.py`:

```python
class NitterHealthTracker:
    """Track Nitter instance health, learn block patterns, auto-adapt.

    Reuses the scan_learning.py architecture: SQLite-backed event tracking
    with statistical correlation for block pattern detection.
    """

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DATA_DIR / "nitter_health.db"
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nitter_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance TEXT NOT NULL,
                success INTEGER NOT NULL,
                response_code INTEGER,
                latency_ms INTEGER,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nitter_cooldowns (
                instance TEXT PRIMARY KEY,
                cooldown_until TEXT NOT NULL,
                consecutive_failures INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def record_attempt(self, instance: str, success: bool,
                       response_code: int = 0, latency_ms: int = 0):
        """Record a fetch attempt."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO nitter_attempts (instance, success, response_code, latency_ms, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (instance, int(success), response_code, latency_ms,
             datetime.now(timezone.utc).isoformat()),
        )
        if success:
            conn.execute(
                "DELETE FROM nitter_cooldowns WHERE instance = ?", (instance,)
            )
        else:
            # Increment consecutive failures
            row = conn.execute(
                "SELECT consecutive_failures FROM nitter_cooldowns WHERE instance = ?",
                (instance,),
            ).fetchone()
            failures = (row[0] + 1) if row else 1
            # Exponential cooldown: 2hr, 4hr, 24hr
            hours = min(24, 2 * (2 ** (failures - 1)))
            cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO nitter_cooldowns (instance, cooldown_until, consecutive_failures) "
                "VALUES (?, ?, ?)",
                (instance, cooldown_until, failures),
            )
        conn.commit()
        conn.close()

    def get_success_rate(self, instance: str, window_hours: int = 24) -> float:
        """Success rate for an instance in the last N hours."""
        conn = sqlite3.connect(str(self.db_path))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*), SUM(success) FROM nitter_attempts "
            "WHERE instance = ? AND timestamp > ?",
            (instance, cutoff),
        ).fetchone()
        conn.close()
        total, successes = row[0] or 0, row[1] or 0
        return successes / total if total > 0 else 0.5  # neutral if no data

    def get_best_instance(self) -> str:
        """Pick the healthiest instance not in cooldown."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        cooled = {
            row[0]
            for row in conn.execute(
                "SELECT instance FROM nitter_cooldowns WHERE cooldown_until > ?",
                (now,),
            ).fetchall()
        }
        conn.close()
        available = [i for i in NITTER_INSTANCES if i not in cooled]
        if not available:
            return NITTER_INSTANCES[0]  # all blocked, try first anyway
        return max(available, key=lambda i: self.get_success_rate(i))

    def should_skip_x(self) -> bool:
        """True if all instances have been blocked recently."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        cooled_count = conn.execute(
            "SELECT COUNT(*) FROM nitter_cooldowns WHERE cooldown_until > ?",
            (now,),
        ).fetchone()[0]
        conn.close()
        return cooled_count >= len(NITTER_INSTANCES)
```

- [ ] **Step 6: Run NitterHealthTracker tests**

```bash
python -m pytest tests/jobpulse/test_paper_discovery.py::TestNitterHealthTracker -v
```

Expected: all 4 tests PASS

- [ ] **Step 7: Commit data structures and tracker**

```bash
git add jobpulse/paper_discovery.py tests/jobpulse/test_paper_discovery.py
git commit -m "feat(papers): add paper_discovery module with dedup + NitterHealthTracker

Core data structures for community-first paper discovery.
NitterHealthTracker: SQLite-backed instance health tracking with
exponential cooldown (2hr→4hr→24hr), instance rotation, skip logic."
```

- [ ] **Step 8: Implement community source fetchers**

Append to `jobpulse/paper_discovery.py`:

```python
def _extract_arxiv_ids(text: str) -> list[str]:
    """Extract arXiv IDs from text (URLs or bare IDs)."""
    return ARXIV_ID_RE.findall(text)


def fetch_huggingface_daily() -> list[dict]:
    """Fetch trending papers from HuggingFace Daily Papers."""
    try:
        resp = httpx.get("https://huggingface.co/api/daily_papers", timeout=15)
        resp.raise_for_status()
        papers = []
        for item in resp.json():
            paper = item.get("paper", {})
            arxiv_id = paper.get("id", "")
            if not arxiv_id:
                continue
            papers.append({
                "arxiv_id": arxiv_id,
                "title": paper.get("title", ""),
                "source": "huggingface",
                "community_buzz": item.get("numUpvotes", 0),
            })
        logger.info("HuggingFace: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("HuggingFace fetch failed: %s", e)
        return []


def fetch_reddit_papers() -> list[dict]:
    """Fetch papers discussed on r/MachineLearning and r/LocalLLaMA."""
    try:
        import praw
        reddit = praw.Reddit(
            client_id=_get_env("REDDIT_CLIENT_ID", ""),
            client_secret=_get_env("REDDIT_CLIENT_SECRET", ""),
            user_agent="JobPulse/1.0 paper-discovery",
        )
        papers = []
        for sub_name in ["MachineLearning", "LocalLLaMA"]:
            sub = reddit.subreddit(sub_name)
            for post in sub.new(limit=50):
                # Only posts from last 24 hours
                if time.time() - post.created_utc > 86400:
                    continue
                ids = _extract_arxiv_ids(post.url + " " + post.selftext)
                for aid in ids:
                    papers.append({
                        "arxiv_id": aid,
                        "title": post.title,
                        "source": "reddit",
                        "community_buzz": post.score,
                    })
        logger.info("Reddit: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("Reddit fetch failed: %s", e)
        return []


def fetch_hackernews_papers() -> list[dict]:
    """Fetch papers from Hacker News via Algolia API."""
    try:
        resp = httpx.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"query": "arxiv.org", "tags": "story", "numericFilters": "created_at_i>%d" % (time.time() - 86400)},
            timeout=15,
        )
        resp.raise_for_status()
        papers = []
        for hit in resp.json().get("hits", []):
            url = hit.get("url", "")
            ids = _extract_arxiv_ids(url + " " + hit.get("title", ""))
            for aid in ids:
                papers.append({
                    "arxiv_id": aid,
                    "title": hit.get("title", ""),
                    "source": "hackernews",
                    "community_buzz": hit.get("points", 0),
                })
        logger.info("HackerNews: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("HackerNews fetch failed: %s", e)
        return []


def fetch_papers_with_code() -> list[dict]:
    """Fetch trending papers from Papers with Code."""
    try:
        resp = httpx.get("https://paperswithcode.com/api/v1/papers/", params={"ordering": "-proceeding"}, timeout=15)
        resp.raise_for_status()
        papers = []
        for item in resp.json().get("results", [])[:20]:
            arxiv_id = item.get("arxiv_id", "")
            if not arxiv_id:
                url = item.get("url_abs", "")
                ids = _extract_arxiv_ids(url)
                arxiv_id = ids[0] if ids else ""
            if arxiv_id:
                papers.append({
                    "arxiv_id": arxiv_id,
                    "title": item.get("title", ""),
                    "source": "paperswithcode",
                    "community_buzz": item.get("stars", 0),
                })
        logger.info("PapersWithCode: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("PapersWithCode fetch failed: %s", e)
        return []


def fetch_x_via_searxng(nitter_tracker: NitterHealthTracker = None) -> list[dict]:
    """Fetch paper discussions from X/Twitter via SearXNG Nitter engine."""
    import os
    searxng_url = os.getenv("SEARXNG_URL", "http://localhost:8888")

    tracker = nitter_tracker or NitterHealthTracker()
    if tracker.should_skip_x():
        logger.info("X/Nitter: all instances blocked, skipping")
        return []

    try:
        resp = httpx.get(
            f"{searxng_url}/search",
            params={"q": "arxiv paper", "engines": "nitter", "format": "json", "time_range": "day"},
            timeout=15,
        )
        if resp.status_code != 200:
            tracker.record_attempt(searxng_url, success=False, response_code=resp.status_code)
            return []

        tracker.record_attempt(searxng_url, success=True, response_code=200, latency_ms=int(resp.elapsed.total_seconds() * 1000))
        papers = []
        for result in resp.json().get("results", []):
            content = result.get("content", "") + " " + result.get("url", "")
            ids = _extract_arxiv_ids(content)
            for aid in ids:
                papers.append({
                    "arxiv_id": aid,
                    "title": result.get("title", ""),
                    "source": "x_nitter",
                    "community_buzz": 10,  # no score available from SearXNG
                })
        logger.info("X/Nitter: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("X/Nitter fetch failed: %s", e)
        if tracker:
            tracker.record_attempt(searxng_url, success=False, response_code=0)
        return []


def fetch_arxiv_rss_fallback() -> list[dict]:
    """Fallback: fetch from arXiv RSS (zero rate limiting)."""
    import xml.etree.ElementTree as ET
    papers = []
    for category in ["cs.AI", "cs.LG", "cs.CL"]:
        try:
            resp = httpx.get(
                f"https://rss.arxiv.org/rss/{category}",
                headers={"User-Agent": "JobPulse/1.0 (mailto:bishnoiyash274@gmail.com)"},
                timeout=15,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            for item in root.findall(".//item"):
                link = item.findtext("link", "")
                ids = _extract_arxiv_ids(link)
                if ids:
                    papers.append({
                        "arxiv_id": ids[0],
                        "title": item.findtext("title", ""),
                        "source": "arxiv_rss",
                        "community_buzz": 0,
                    })
        except Exception as e:
            logger.warning("arXiv RSS %s failed: %s", category, e)
    logger.info("arXiv RSS fallback: %d papers", len(papers))
    return papers


def enrich_from_semantic_scholar(papers: list[dict]) -> list[dict]:
    """Enrich papers with metadata from Semantic Scholar."""
    for paper in papers:
        try:
            resp = httpx.get(
                f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{paper['arxiv_id']}",
                params={"fields": "title,abstract,citationCount,authors,year"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                paper["abstract"] = data.get("abstract", "")
                paper["citation_count"] = data.get("citationCount", 0)
                paper["authors"] = [a.get("name", "") for a in data.get("authors", [])]
                paper["year"] = data.get("year")
            time.sleep(0.1)  # 10 req/sec courtesy
        except Exception:
            pass  # enrichment is best-effort
    return papers


def _get_env(key: str, default: str) -> str:
    import os
    return os.getenv(key, default)


def discover_trending_papers() -> list[dict]:
    """Main entry point: discover papers from community, enrich, return."""
    # Parallel-ish fetch (sequential for simplicity, each has timeout)
    all_papers = []
    all_papers.extend(fetch_huggingface_daily())
    all_papers.extend(fetch_reddit_papers())
    all_papers.extend(fetch_hackernews_papers())
    all_papers.extend(fetch_papers_with_code())
    all_papers.extend(fetch_x_via_searxng())

    # Dedup
    unique = dedup_by_arxiv_id(all_papers)
    logger.info("Discovery: %d total → %d unique papers", len(all_papers), len(unique))

    # Fallback if nothing found
    if not unique:
        logger.warning("All community sources empty, falling back to arXiv RSS")
        unique = fetch_arxiv_rss_fallback()

    # Enrich with Semantic Scholar
    enriched = enrich_from_semantic_scholar(unique)

    return enriched
```

- [ ] **Step 9: Run full test suite**

```bash
python -m pytest tests/jobpulse/test_paper_discovery.py -v
```

Expected: all tests PASS (TestDedup: 2, TestNitterHealthTracker: 4, TestDiscoverTrending: 2)

- [ ] **Step 10: Commit**

```bash
git add jobpulse/paper_discovery.py tests/jobpulse/test_paper_discovery.py
git commit -m "feat(papers): community-first discovery from 5 sources

HuggingFace Daily Papers + Reddit + Hacker News + Papers with Code +
X (via SearXNG/Nitter). Dedup by arXiv ID with aggregated community
buzz. Semantic Scholar enrichment. arXiv RSS as zero-rate-limit fallback.
Replaces 200-paper arXiv API fetch with ~20-40 pre-validated papers."
```

---

### Task 7: Integrate Paper Discovery into arXiv Agent

**Files:**
- Modify: `jobpulse/arxiv_agent.py`

- [ ] **Step 1: Read current arxiv_agent.py entry point**

Check how `fetch_papers()` is called and where ranking happens.

- [ ] **Step 2: Add paper_discovery import and integration**

At the top of `arxiv_agent.py`, add:

```python
from jobpulse.paper_discovery import discover_trending_papers
```

In the main function that produces the daily digest, add a community-first path:

```python
# Try community-first discovery
trending = discover_trending_papers()
if trending:
    papers = trending  # Already enriched with metadata
else:
    # Fallback to existing arXiv API fetch (with improved backoff)
    papers = fetch_papers(query, max_results)
```

- [ ] **Step 3: Test manually**

```bash
python -c "
from jobpulse.paper_discovery import discover_trending_papers
papers = discover_trending_papers()
print(f'Found {len(papers)} trending papers')
for p in papers[:3]:
    print(f'  [{p[\"source\"]}] {p[\"title\"][:60]}... (buzz={p.get(\"community_buzz\", 0)})')
"
```

- [ ] **Step 4: Commit**

```bash
git add jobpulse/arxiv_agent.py
git commit -m "feat(arxiv): integrate community-first paper discovery

arxiv_agent now tries discover_trending_papers() first (community sources),
falls back to direct arXiv API fetch only when community returns empty."
```

---

### Task 8: Wire OAuth Monitor into Daemon Startup + Health Watchdog

**Files:**
- Modify: `jobpulse/multi_bot_listener.py`
- Modify: `jobpulse/healthcheck.py`

- [ ] **Step 1: Add OAuth check to daemon startup**

In `jobpulse/multi_bot_listener.py`, in `start_all_bots()` before starting threads:

```python
# Check OAuth health on startup
from jobpulse.oauth_monitor import run_health_check
run_health_check()
```

- [ ] **Step 2: Add OAuth check to health watchdog**

In `jobpulse/healthcheck.py`, in the watchdog function (if it exists) or create a new function:

```python
def check_all_health():
    """Run all health checks."""
    write_heartbeat()
    from jobpulse.oauth_monitor import run_health_check
    run_health_check()
```

- [ ] **Step 3: Test daemon startup**

```bash
python -c "
from jobpulse.oauth_monitor import run_health_check
result = run_health_check(send_alerts=False)
print('OAuth status:', result['status'])
"
```

- [ ] **Step 4: Commit**

```bash
git add jobpulse/multi_bot_listener.py jobpulse/healthcheck.py
git commit -m "feat: wire OAuth monitor into daemon startup + health watchdog

Checks Google OAuth health on every daemon start and every 10-minute
health watchdog cycle. Sends Telegram alert on scope mismatch or broken token."
```

---

### Task 9: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short -q 2>&1 | tail -30
```

Expected: no new failures

- [ ] **Step 2: Verify crontab**

```bash
crontab -l | grep "Downloads" | wc -l
# Expected: 0
crontab -l | grep "profile-sync"
# Expected: one entry
```

- [ ] **Step 3: Verify all Telegram bots respond**

```bash
python -c "
from jobpulse.telegram_bots import send_main, send_budget, send_research, send_jobs
for name, fn in [('Main', send_main), ('Budget', send_budget), ('Research', send_research), ('Jobs', send_jobs)]:
    ok = fn(f'Phase 1 verification: {name} bot OK')
    print(f'{name}: {\"OK\" if ok else \"FAIL\"}'  )
"
```

- [ ] **Step 4: Verify paper discovery**

```bash
python -c "
from jobpulse.paper_discovery import discover_trending_papers
papers = discover_trending_papers()
print(f'Discovered {len(papers)} trending papers')
assert len(papers) > 0, 'No papers found from any source'
print('PASS')
"
```

- [ ] **Step 5: Verify OAuth monitor**

```bash
python -c "
from jobpulse.oauth_monitor import check_oauth_health
health = check_oauth_health()
print('OAuth:', health['status'])
"
```

- [ ] **Step 6: Final commit if any loose changes**

```bash
git status
# If clean: Phase 1 complete
```
