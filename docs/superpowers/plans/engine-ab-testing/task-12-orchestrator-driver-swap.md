# Task 12: Orchestrator Driver Swap — `self.bridge` → `self.driver`

**Files:**
- Modify: `jobpulse/application_orchestrator.py`

**Why:** The orchestrator currently hardcodes `self.bridge` (ExtensionBridge). We rename it to `self.driver` so it can accept either ExtensionBridge or PlaywrightDriver. This is the central change that enables A/B testing.

**Dependencies:** Tasks 1, 5 (DriverProtocol and PlaywrightDriver must exist)

---

- [ ] **Step 1: Update `__init__` to accept a driver parameter**

Change the `__init__` signature (line 80-93):

```python
    def __init__(
        self,
        bridge: Any = None,
        driver: Any = None,
        engine: str = "extension",
        account_manager: AccountManager | None = None,
        gmail_verifier: GmailVerifier | None = None,
        navigation_learner: NavigationLearner | None = None,
    ):
        # Support both old bridge= and new driver= parameter
        self.driver = driver or bridge
        # Keep self.bridge as alias for backward compat with ext_adapter.py
        self.bridge = self.driver
        self.engine = engine
        self.accounts = account_manager or AccountManager()
        self.gmail = gmail_verifier or GmailVerifier()
        self.learner = navigation_learner or NavigationLearner()
        self.analyzer = PageAnalyzer(self.driver)
        self.cookie_dismisser = CookieBannerDismisser(self.driver)
        self.sso = SSOHandler(self.driver)
        self.gotchas = GotchasDB()
```

Note: We keep `self.bridge` as an alias so `ext_adapter.py` and other callers that pass `bridge=` still work. No breaking changes.

- [ ] **Step 2: Find-and-replace `self.bridge.` → `self.driver.` throughout the file**

There are ~70 occurrences of `self.bridge.` in the file. Replace all with `self.driver.`:

```
self.bridge.navigate(...)     → self.driver.navigate(...)
self.bridge.fill(...)         → self.driver.fill(...)
self.bridge.click(...)        → self.driver.click(...)
self.bridge.get_snapshot(...) → self.driver.get_snapshot(...)
self.bridge._snapshot = ...   → self.driver._snapshot = ...
```

**Important:** The one case `self.bridge._snapshot = self._to_page_snapshot(...)` (line 132) accesses a private attribute. This only works with ExtensionBridge. Guard it:

```python
        if pre_navigated_snapshot is not None:
            if hasattr(self.driver, '_snapshot'):
                self.driver._snapshot = self._to_page_snapshot(pre_navigated_snapshot)
```

- [ ] **Step 3: Pass engine to GotchasDB lookups**

Find `self.gotchas.lookup_domain(...)` calls in the file. Add `engine=self.engine`:

```python
        _gotchas = self.gotchas.lookup_domain(_parsed_domain, engine=self.engine)
```

- [ ] **Step 4: Verify existing tests still pass**

Run: `python -m pytest tests/jobpulse/test_application_orchestrator.py -v`
Expected: PASS (backward compat via `bridge=` alias)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator.py
git commit -m "refactor(orchestrator): self.bridge → self.driver for engine swap

Orchestrator now accepts driver= parameter (PlaywrightDriver or
ExtensionBridge). self.bridge kept as alias for backward compatibility.
GotchasDB lookups filtered by self.engine."
```
