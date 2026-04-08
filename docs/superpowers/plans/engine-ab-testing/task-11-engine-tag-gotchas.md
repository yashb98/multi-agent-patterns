# Task 11: Engine-Tag GotchasDB

**Files:**
- Modify: `jobpulse/form_engine/gotchas.py`
- Test: `tests/jobpulse/form_engine/test_gotchas.py` (extend)

**Why:** A gotcha like "this field needs execCommand instead of fill()" is extension-specific. Playwright might have a different solution. Engine-tagging ensures independent learning.

---

- [ ] **Step 1: Update schema — add engine column**

In `GotchasDB._init_db()`, change the CREATE TABLE to add `engine TEXT NOT NULL DEFAULT 'extension'` and update the PRIMARY KEY to `(domain, selector_pattern, engine)`:

```python
            conn.execute(
                """CREATE TABLE IF NOT EXISTS gotchas (
                    domain TEXT NOT NULL,
                    selector_pattern TEXT NOT NULL,
                    problem TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    engine TEXT NOT NULL DEFAULT 'extension',
                    times_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    PRIMARY KEY (domain, selector_pattern, engine)
                )"""
            )
```

- [ ] **Step 2: Add migration for existing DB**

After CREATE TABLE in `_init_db()`:

```python
            # Migration: add engine column if missing
            try:
                conn.execute("ALTER TABLE gotchas ADD COLUMN engine TEXT NOT NULL DEFAULT 'extension'")
            except sqlite3.OperationalError:
                pass  # Already exists
```

- [ ] **Step 3: Update `store()` — accept engine param**

```python
    def store(self, domain: str, selector_pattern: str, problem: str, solution: str, engine: str = "extension") -> None:
```

Update the INSERT to include `engine` in columns and values. Update the ON CONFLICT to use `(domain, selector_pattern, engine)`.

- [ ] **Step 4: Update `lookup()` and `lookup_domain()` — filter by engine**

```python
    def lookup(self, domain: str, selector_pattern: str, engine: str = "extension") -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? AND selector_pattern = ? AND engine = ?",
                (domain, selector_pattern, engine),
            ).fetchone()
            return dict(row) if row else None

    def lookup_domain(self, domain: str, engine: str = "extension") -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? AND engine = ? ORDER BY times_used DESC",
                (domain, engine),
            ).fetchall()
            return [dict(r) for r in rows]
```

- [ ] **Step 5: Update `record_usage()` — include engine in WHERE**

```python
    def record_usage(self, domain: str, selector_pattern: str, engine: str = "extension") -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE gotchas SET times_used = times_used + 1, last_used_at = ?
                   WHERE domain = ? AND selector_pattern = ? AND engine = ?""",
                (now, domain, selector_pattern, engine),
            )
            conn.commit()
```

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_engine/gotchas.py
git commit -m "feat(gotchas): engine-tag for per-engine gotcha learning

Primary key now (domain, selector_pattern, engine). All queries filter
by engine. Existing data defaults to 'extension'."
```
