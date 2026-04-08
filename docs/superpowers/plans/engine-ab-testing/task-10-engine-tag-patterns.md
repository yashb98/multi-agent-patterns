# Task 10: Engine-Tag PatternStore

**Files:**
- Modify: `jobpulse/ralph_loop/pattern_store.py`
- Test: `tests/jobpulse/test_ralph_loop.py` (extend)

**Why:** Ralph Loop fixes must be per-engine. A selector override that works for Playwright may not work for the extension. Adding an `engine` column lets each engine learn independently.

---

- [ ] **Step 1: Add `engine` column to schema**

Find the `CREATE TABLE IF NOT EXISTS fix_patterns` statement in `PatternStore._init_db()`. Add `engine TEXT NOT NULL DEFAULT 'extension'` after the `confirmed` column.

Also add to the `CREATE INDEX` statements:
```sql
CREATE INDEX IF NOT EXISTS idx_fix_engine ON fix_patterns(engine);
```

- [ ] **Step 2: Update `store_fix` to accept and store engine**

Find the `store_fix` method. Add `engine: str = "extension"` parameter. Include `engine` in the INSERT statement's columns and values.

- [ ] **Step 3: Update `get_fixes` to filter by engine**

Find the `get_fixes` method. Add `engine: str = "extension"` parameter. Add `AND engine = ?` to the WHERE clause and include the engine value in the query parameters.

- [ ] **Step 4: Update FixPattern dataclass**

Add `engine: str = "extension"` field to the `FixPattern` dataclass (after `occurrence_count`).

Update the row-to-FixPattern mapping to include the new column.

- [ ] **Step 5: Handle migration for existing DB**

Add to `_init_db()` after the CREATE TABLE:
```python
        # Migration: add engine column if missing (existing DBs)
        try:
            conn.execute("ALTER TABLE fix_patterns ADD COLUMN engine TEXT NOT NULL DEFAULT 'extension'")
        except sqlite3.OperationalError:
            pass  # Column already exists
```

- [ ] **Step 6: Commit**

```bash
git add jobpulse/ralph_loop/pattern_store.py
git commit -m "feat(ralph_loop): engine-tag on fix patterns for per-engine learning

Adds engine column to fix_patterns table. Existing fixes default to
'extension'. get_fixes() and store_fix() filter/store by engine."
```
