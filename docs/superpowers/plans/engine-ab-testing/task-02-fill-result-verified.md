# Task 2: Add `value_verified` to FillResult

**Files:**
- Modify: `jobpulse/form_engine/models.py:31-39`
- Test: `tests/jobpulse/form_engine/test_models.py` (new)

**Why:** The Playwright form engine's `FillResult` has no `value_verified` field. The extension already returns this on every fill. We need parity for A/B comparison.

---

- [ ] **Step 1: Write failing test**

```python
"""tests/jobpulse/form_engine/test_models.py"""
from jobpulse.form_engine.models import FillResult

def test_fill_result_has_value_verified():
    r = FillResult(success=True, selector="#email", value_attempted="a@b.com")
    assert r.value_verified is False  # default

def test_fill_result_value_verified_set():
    r = FillResult(success=True, selector="#email", value_attempted="a@b.com", value_verified=True)
    assert r.value_verified is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/form_engine/test_models.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'value_verified'`

- [ ] **Step 3: Add field to FillResult**

In `jobpulse/form_engine/models.py`, add after line 39 (`skipped: bool = False`):

```python
    value_verified: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/form_engine/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/models.py tests/jobpulse/form_engine/test_models.py
git commit -m "feat(form_engine): add value_verified field to FillResult"
```
