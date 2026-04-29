# Platform Strategy Adapters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split monolithic PlaywrightAdapter into per-platform strategy files that plug into the shared NativeFormFiller pipeline, with platform-scoped DB tables for field/screening pattern memory.

**Architecture:** Strategy pattern — `BasePlatformStrategy` ABC defines hooks (pre_fill, post_page, field scan, timing, label mappings). Each platform gets its own strategy file. `NativeFormFiller.fill()` accepts an optional strategy and calls its hooks at each decision point. Two new DB tables (`field_patterns`, `screening_patterns`) store learned mappings scoped by platform.

**Tech Stack:** Python 3.12, Playwright, SQLite, pytest

---

### Task 1: BasePlatformStrategy ABC + Registry

**Files:**
- Create: `jobpulse/ats_adapters/strategy.py`
- Test: `tests/jobpulse/ats_adapters/test_strategy.py`

- [ ] **Step 1: Write test for strategy registry**

```python
# tests/jobpulse/ats_adapters/test_strategy.py
"""Tests for BasePlatformStrategy ABC and registry."""
import pytest
from jobpulse.ats_adapters.strategy import (
    BasePlatformStrategy,
    get_strategy,
    register_strategy,
    _STRATEGY_REGISTRY,
)


def test_get_strategy_returns_generic_for_unknown():
    strategy = get_strategy("nonexistent_platform")
    assert strategy.name == "generic"


def test_get_strategy_returns_generic_for_none():
    strategy = get_strategy(None)
    assert strategy.name == "generic"


def test_register_strategy_decorator():
    @register_strategy
    class _TestStrategy(BasePlatformStrategy):
        name = "_test_dummy"

        def detect(self, url: str) -> bool:
            return "_test_" in url

    try:
        result = get_strategy("_test_dummy")
        assert result.name == "_test_dummy"
        assert result.detect("https://_test_example.com")
    finally:
        _STRATEGY_REGISTRY.pop("_test_dummy", None)


def test_base_strategy_defaults():
    """Default hook returns should be safe no-ops."""
    @register_strategy
    class _DefaultsStrategy(BasePlatformStrategy):
        name = "_test_defaults"
        def detect(self, url): return False

    try:
        s = get_strategy("_test_defaults")
        assert s.min_page_time == 5.0
        assert s.max_form_pages == 20
        assert s.extra_label_mappings() == {}
        assert s.next_button_selectors() == []
        assert s.screening_defaults() == {}
        assert s.field_fill_overrides() == {}
    finally:
        _STRATEGY_REGISTRY.pop("_test_defaults", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobpulse.ats_adapters.strategy'`

- [ ] **Step 3: Create strategy.py with ABC + registry**

```python
# jobpulse/ats_adapters/strategy.py
"""Platform strategy ABC and registry.

Each ATS platform provides a strategy that customizes the shared
NativeFormFiller pipeline: timing, label mappings, navigation selectors,
pre/post hooks, and field scan overrides.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

_STRATEGY_REGISTRY: dict[str, type["BasePlatformStrategy"]] = {}


def register_strategy(cls: type["BasePlatformStrategy"]) -> type["BasePlatformStrategy"]:
    """Class decorator — registers a strategy by its name."""
    _STRATEGY_REGISTRY[cls.name] = cls
    return cls


def get_strategy(platform: str | None) -> "BasePlatformStrategy":
    """Return the strategy for a platform, or GenericStrategy as fallback."""
    key = (platform or "generic").lower()
    cls = _STRATEGY_REGISTRY.get(key)
    if cls is None:
        from jobpulse.ats_adapters.generic import GenericStrategy
        return GenericStrategy()
    return cls()


class BasePlatformStrategy(ABC):
    name: str = "base"
    min_page_time: float = 5.0
    max_form_pages: int = 20

    @abstractmethod
    def detect(self, url: str) -> bool:
        """Return True if this strategy handles this URL."""

    def extra_label_mappings(self) -> dict[str, str]:
        """Platform-specific label→profile_key mappings."""
        return {}

    async def pre_fill(
        self, page: "Page", cv_path: str | None,
        profile: dict, custom_answers: dict,
    ) -> dict[str, Any]:
        """Hook before form filling starts.

        Return dict with optional keys:
        - skip_fields: list[str] — field labels to skip
        - inject_values: dict[str, str] — extra values to inject
        """
        return {}

    async def post_page(
        self, page: "Page", page_num: int, result: dict,
    ) -> None:
        """Hook after each form page is filled."""

    def next_button_selectors(self) -> list[str]:
        """Ordered CSS selectors for next/submit buttons."""
        return []

    def screening_defaults(self) -> dict[str, str]:
        """Platform-specific default screening answers."""
        return {}

    async def custom_field_scan(self, page: "Page") -> list[dict] | None:
        """Override field scanning. Return None to use default NativeFormFiller scan."""
        return None

    def field_fill_overrides(self) -> dict[str, Any]:
        """Platform-specific fill behavior (typing delay, dropdown strategy, etc)."""
        return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_strategy.py -v`
Expected: FAIL — `GenericStrategy` doesn't exist yet. Create it next.

- [ ] **Step 5: Create GenericStrategy**

```python
# jobpulse/ats_adapters/generic.py
"""Generic fallback strategy — no platform-specific overrides."""
from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy


@register_strategy
class GenericStrategy(BasePlatformStrategy):
    name = "generic"
    min_page_time = 5.0

    def detect(self, url: str) -> bool:
        return False
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_strategy.py -v`
Expected: All 4 tests PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/ats_adapters/strategy.py jobpulse/ats_adapters/generic.py tests/jobpulse/ats_adapters/test_strategy.py
git commit -m "feat(adapters): add BasePlatformStrategy ABC, registry, and GenericStrategy"
```

---

### Task 2: LinkedIn Strategy

**Files:**
- Create: `jobpulse/ats_adapters/linkedin.py`
- Test: `tests/jobpulse/ats_adapters/test_linkedin_strategy.py`

- [ ] **Step 1: Write tests**

```python
# tests/jobpulse/ats_adapters/test_linkedin_strategy.py
"""Tests for LinkedInStrategy."""
from jobpulse.ats_adapters.linkedin import LinkedInStrategy


def test_detect_linkedin_url():
    s = LinkedInStrategy()
    assert s.detect("https://www.linkedin.com/jobs/view/12345")
    assert s.detect("https://uk.linkedin.com/jobs/view/99999")
    assert not s.detect("https://greenhouse.io/jobs/12345")


def test_min_page_time():
    s = LinkedInStrategy()
    assert s.min_page_time == 3.0


def test_extra_label_mappings():
    s = LinkedInStrategy()
    mappings = s.extra_label_mappings()
    assert "headline" in mappings
    assert "phone country code" in mappings


def test_next_button_selectors():
    s = LinkedInStrategy()
    selectors = s.next_button_selectors()
    assert len(selectors) >= 3
    assert any("Continue" in s or "next step" in s for s in selectors)
    assert any("Review" in s for s in selectors)
    assert any("Submit" in s for s in selectors)


def test_screening_defaults():
    s = LinkedInStrategy()
    defaults = s.screening_defaults()
    assert "How did you hear about this job?" in defaults


def test_field_fill_overrides():
    s = LinkedInStrategy()
    overrides = s.field_fill_overrides()
    assert "typing_delay_ms" in overrides


def test_registered_in_registry():
    from jobpulse.ats_adapters.strategy import get_strategy
    s = get_strategy("linkedin")
    assert s.name == "linkedin"
    assert isinstance(s, LinkedInStrategy)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_linkedin_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement LinkedInStrategy**

```python
# jobpulse/ats_adapters/linkedin.py
"""LinkedIn Easy Apply strategy.

LinkedIn auto-fills name/email from the logged-in profile. Forms are
multi-step (2-5 pages) with screening questions. Human-like typing
delays required (50-150ms/char) to avoid ML behavioral detection.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy


@register_strategy
class LinkedInStrategy(BasePlatformStrategy):
    name = "linkedin"
    min_page_time = 3.0

    def detect(self, url: str) -> bool:
        return "linkedin.com" in url

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "headline": "headline",
            "phone country code": "phone_code",
            "summary": "summary",
        }

    async def pre_fill(
        self, page: "Page", cv_path: str | None,
        profile: dict, custom_answers: dict,
    ) -> dict[str, Any]:
        return {"skip_fields": ["first name", "last name", "email address"]}

    def next_button_selectors(self) -> list[str]:
        return [
            'button[aria-label="Continue to next step"]',
            'button[aria-label="Review your application"]',
            'button[aria-label="Submit application"]',
        ]

    def screening_defaults(self) -> dict[str, str]:
        return {
            "How did you hear about this job?": "LinkedIn",
        }

    def field_fill_overrides(self) -> dict[str, Any]:
        return {
            "typing_delay_ms": (50, 150),
            "use_human_like_typing": True,
        }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_linkedin_strategy.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ats_adapters/linkedin.py tests/jobpulse/ats_adapters/test_linkedin_strategy.py
git commit -m "feat(adapters): add LinkedInStrategy with timing, label mappings, and nav selectors"
```

---

### Task 3: Greenhouse Strategy

**Files:**
- Create: `jobpulse/ats_adapters/greenhouse.py`
- Test: `tests/jobpulse/ats_adapters/test_greenhouse_strategy.py`

- [ ] **Step 1: Write tests**

```python
# tests/jobpulse/ats_adapters/test_greenhouse_strategy.py
"""Tests for GreenhouseStrategy."""
from jobpulse.ats_adapters.greenhouse import GreenhouseStrategy


def test_detect_greenhouse_urls():
    s = GreenhouseStrategy()
    assert s.detect("https://boards.greenhouse.io/company/jobs/12345")
    assert s.detect("https://job-boards.greenhouse.io/company/jobs/12345")
    assert not s.detect("https://linkedin.com/jobs/view/12345")


def test_min_page_time():
    assert GreenhouseStrategy().min_page_time == 5.0


def test_extra_label_mappings():
    mappings = GreenhouseStrategy().extra_label_mappings()
    assert "cover letter" in mappings


def test_next_button_selectors():
    selectors = GreenhouseStrategy().next_button_selectors()
    assert any("submit" in s.lower() for s in selectors)


def test_field_fill_overrides_escape_between_fills():
    overrides = GreenhouseStrategy().field_fill_overrides()
    assert overrides.get("escape_between_comboboxes") is True


def test_registered():
    from jobpulse.ats_adapters.strategy import get_strategy
    assert get_strategy("greenhouse").name == "greenhouse"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_greenhouse_strategy.py -v`
Expected: FAIL

- [ ] **Step 3: Implement GreenhouseStrategy**

```python
# jobpulse/ats_adapters/greenhouse.py
"""Greenhouse ATS strategy.

Greenhouse uses React Select comboboxes with aria-owns scoping.
Phone country code conflicts require Escape between fills.
Cover letter detection triggers lazy CL generation.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy


@register_strategy
class GreenhouseStrategy(BasePlatformStrategy):
    name = "greenhouse"
    min_page_time = 5.0

    def detect(self, url: str) -> bool:
        return "greenhouse.io" in url or "boards.greenhouse" in url

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "cover letter": "_cover_letter",
            "resume/cv": "_resume",
            "resume": "_resume",
            "how did you hear about this job?": "source",
        }

    def next_button_selectors(self) -> list[str]:
        return [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit Application")',
        ]

    def screening_defaults(self) -> dict[str, str]:
        return {
            "How did you hear about this job?": "Company website",
        }

    def field_fill_overrides(self) -> dict[str, Any]:
        return {
            "escape_between_comboboxes": True,
            "aria_owns_scoping": True,
        }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_greenhouse_strategy.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ats_adapters/greenhouse.py tests/jobpulse/ats_adapters/test_greenhouse_strategy.py
git commit -m "feat(adapters): add GreenhouseStrategy with React Select quirks"
```

---

### Task 4: Lever, Workday, Indeed, Reed Strategies

**Files:**
- Create: `jobpulse/ats_adapters/lever.py`
- Create: `jobpulse/ats_adapters/workday.py`
- Create: `jobpulse/ats_adapters/indeed.py`
- Create: `jobpulse/ats_adapters/reed.py`
- Test: `tests/jobpulse/ats_adapters/test_remaining_strategies.py`

- [ ] **Step 1: Write tests for all four**

```python
# tests/jobpulse/ats_adapters/test_remaining_strategies.py
"""Tests for Lever, Workday, Indeed, Reed strategies."""
import pytest
from jobpulse.ats_adapters.strategy import get_strategy


class TestLeverStrategy:
    def test_detect(self):
        from jobpulse.ats_adapters.lever import LeverStrategy
        s = LeverStrategy()
        assert s.detect("https://jobs.lever.co/company/12345")
        assert s.detect("https://lever.co/company/12345")
        assert not s.detect("https://greenhouse.io/jobs/12345")

    def test_min_page_time(self):
        assert get_strategy("lever").min_page_time == 5.0

    def test_single_page_form(self):
        assert get_strategy("lever").max_form_pages == 20

    def test_registered(self):
        assert get_strategy("lever").name == "lever"


class TestWorkdayStrategy:
    def test_detect(self):
        from jobpulse.ats_adapters.workday import WorkdayStrategy
        s = WorkdayStrategy()
        assert s.detect("https://company.myworkdayjobs.com/en-US/jobs/12345")
        assert not s.detect("https://linkedin.com/jobs/view/12345")

    def test_min_page_time_highest(self):
        assert get_strategy("workday").min_page_time == 45.0

    def test_field_fill_overrides(self):
        overrides = get_strategy("workday").field_fill_overrides()
        assert overrides.get("react_controlled_inputs") is True

    def test_registered(self):
        assert get_strategy("workday").name == "workday"


class TestIndeedStrategy:
    def test_detect(self):
        from jobpulse.ats_adapters.indeed import IndeedStrategy
        s = IndeedStrategy()
        assert s.detect("https://uk.indeed.com/viewjob?jk=12345")
        assert not s.detect("https://linkedin.com/jobs/view/12345")

    def test_min_page_time(self):
        assert get_strategy("indeed").min_page_time == 10.0

    def test_field_fill_overrides(self):
        overrides = get_strategy("indeed").field_fill_overrides()
        assert overrides.get("mouse_movement_simulation") is True

    def test_registered(self):
        assert get_strategy("indeed").name == "indeed"


class TestReedStrategy:
    def test_detect(self):
        from jobpulse.ats_adapters.reed import ReedStrategy
        s = ReedStrategy()
        assert s.detect("https://www.reed.co.uk/jobs/12345")
        assert not s.detect("https://linkedin.com/jobs/view/12345")

    def test_min_page_time(self):
        assert get_strategy("reed").min_page_time == 5.0

    def test_registered(self):
        assert get_strategy("reed").name == "reed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_remaining_strategies.py -v`
Expected: FAIL

- [ ] **Step 3: Implement LeverStrategy**

```python
# jobpulse/ats_adapters/lever.py
"""Lever ATS strategy.

Lever forms are typically single-page. Cover letter detected via
additional file upload field.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy


@register_strategy
class LeverStrategy(BasePlatformStrategy):
    name = "lever"
    min_page_time = 5.0

    def detect(self, url: str) -> bool:
        return "lever.co" in url or "jobs.lever" in url

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "resume/cv": "_resume",
            "additional information": "cover_letter_text",
        }

    def next_button_selectors(self) -> list[str]:
        return [
            'button[type="submit"]',
            'button:has-text("Submit application")',
        ]

    def screening_defaults(self) -> dict[str, str]:
        return {
            "How did you hear about us?": "Company website",
        }
```

- [ ] **Step 4: Implement WorkdayStrategy**

```python
# jobpulse/ats_adapters/workday.py
"""Workday ATS strategy.

5-step form wizard with React controlled inputs. Aggressive anti-detection
requires 45s minimum page time. Skills multiselect has quirks. Session
timeout after ~15 minutes of inactivity.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy


@register_strategy
class WorkdayStrategy(BasePlatformStrategy):
    name = "workday"
    min_page_time = 45.0

    def detect(self, url: str) -> bool:
        return "myworkdayjobs.com" in url

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "how did you hear about us?": "source",
            "country": "country",
            "country phone code": "phone_code",
        }

    def next_button_selectors(self) -> list[str]:
        return [
            'button[data-automation-id="bottom-navigation-next-button"]',
            'button[data-automation-id="submit-button"]',
            'button:has-text("Submit")',
            'button:has-text("Next")',
        ]

    def field_fill_overrides(self) -> dict[str, Any]:
        return {
            "react_controlled_inputs": True,
            "typing_delay_ms": (80, 200),
            "session_timeout_minutes": 15,
        }
```

- [ ] **Step 5: Implement IndeedStrategy**

```python
# jobpulse/ats_adapters/indeed.py
"""Indeed ATS strategy.

Conservative approach — Indeed has aggressive anti-automation detection.
Mouse movement simulation and extra delays required.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy


@register_strategy
class IndeedStrategy(BasePlatformStrategy):
    name = "indeed"
    min_page_time = 10.0

    def detect(self, url: str) -> bool:
        return "indeed.com" in url

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "city, state": "location",
        }

    def next_button_selectors(self) -> list[str]:
        return [
            'button[id="ia-continueButton"]',
            'button:has-text("Continue")',
            'button:has-text("Submit your application")',
        ]

    def field_fill_overrides(self) -> dict[str, Any]:
        return {
            "mouse_movement_simulation": True,
            "typing_delay_ms": (60, 180),
            "use_human_like_typing": True,
        }
```

- [ ] **Step 6: Implement ReedStrategy**

```python
# jobpulse/ats_adapters/reed.py
"""Reed ATS strategy.

Reed Easy Apply uses modal overlays with pre-filled CV from profile.
Must detect CV mismatch and upload tailored CV via Update button.
Google SSO login required on first visit.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy


@register_strategy
class ReedStrategy(BasePlatformStrategy):
    name = "reed"
    min_page_time = 5.0

    def detect(self, url: str) -> bool:
        return "reed.co.uk" in url

    async def pre_fill(
        self, page: "Page", cv_path: str | None,
        profile: dict, custom_answers: dict,
    ) -> dict[str, Any]:
        return {"handle_modal_cv_upload": True}

    def next_button_selectors(self) -> list[str]:
        return [
            'button:has-text("Submit application")',
            'button:has-text("Apply")',
        ]

    def screening_defaults(self) -> dict[str, str]:
        return {
            "How did you hear about this job?": "Reed",
        }
```

- [ ] **Step 7: Run all tests**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_remaining_strategies.py -v`
Expected: All 15 tests PASS

- [ ] **Step 8: Commit**

```bash
git add jobpulse/ats_adapters/lever.py jobpulse/ats_adapters/workday.py jobpulse/ats_adapters/indeed.py jobpulse/ats_adapters/reed.py tests/jobpulse/ats_adapters/test_remaining_strategies.py
git commit -m "feat(adapters): add Lever, Workday, Indeed, Reed strategies"
```

---

### Task 5: DB Schema — field_patterns and screening_patterns tables

**Files:**
- Modify: `jobpulse/form_experience_db.py`
- Test: `tests/jobpulse/test_form_experience_db.py` (add new tests)

- [ ] **Step 1: Write tests for new tables**

```python
# tests/jobpulse/test_form_experience_db_patterns.py
"""Tests for field_patterns and screening_patterns tables."""
import pytest
from jobpulse.form_experience_db import FormExperienceDB


@pytest.fixture
def db(tmp_path):
    return FormExperienceDB(db_path=str(tmp_path / "test_fe.db"))


class TestFieldPatterns:
    def test_record_and_lookup(self, db):
        db.record_field_pattern(
            platform="linkedin", domain="company.com",
            field_label="phone number", field_type="text",
            profile_key="phone", value="+447909445288", success=True,
        )
        patterns = db.lookup_field_patterns("linkedin", "company.com")
        assert patterns["phone number"] == "phone"

    def test_lookup_empty(self, db):
        patterns = db.lookup_field_patterns("linkedin", "unknown.com")
        assert patterns == {}

    def test_success_count_increments(self, db):
        db.record_field_pattern(
            platform="greenhouse", domain="co.com",
            field_label="email", field_type="text",
            profile_key="email", value="test@test.com", success=True,
        )
        db.record_field_pattern(
            platform="greenhouse", domain="co.com",
            field_label="email", field_type="text",
            profile_key="email", value="test@test.com", success=True,
        )
        patterns = db.lookup_field_patterns("greenhouse", "co.com")
        assert patterns["email"] == "email"

    def test_failure_excludes_from_lookup(self, db):
        db.record_field_pattern(
            platform="workday", domain="fail.com",
            field_label="city", field_type="text",
            profile_key="location", value="London", success=False,
        )
        patterns = db.lookup_field_patterns("workday", "fail.com")
        assert "city" not in patterns

    def test_cross_platform_isolation(self, db):
        db.record_field_pattern(
            platform="linkedin", domain="shared.com",
            field_label="phone", field_type="text",
            profile_key="phone", value="123", success=True,
        )
        assert db.lookup_field_patterns("greenhouse", "shared.com") == {}
        assert "phone" in db.lookup_field_patterns("linkedin", "shared.com")


class TestScreeningPatterns:
    def test_record_and_lookup(self, db):
        db.record_screening_pattern(
            platform="linkedin",
            question="are you authorized to work in the uk?",
            answer="Yes",
            source="pattern",
        )
        result = db.lookup_screening_pattern(
            "linkedin", "are you authorized to work in the uk?"
        )
        assert result == "Yes"

    def test_lookup_missing(self, db):
        result = db.lookup_screening_pattern("linkedin", "unknown question")
        assert result is None

    def test_user_correction_overwrites(self, db):
        db.record_screening_pattern(
            platform="greenhouse", question="salary expectations",
            answer="40000", source="llm",
        )
        db.record_screening_pattern(
            platform="greenhouse", question="salary expectations",
            answer="35000", source="user_correction",
        )
        result = db.lookup_screening_pattern("greenhouse", "salary expectations")
        assert result == "35000"

    def test_cross_platform_isolation(self, db):
        db.record_screening_pattern(
            platform="indeed", question="notice period",
            answer="1 month", source="pattern",
        )
        assert db.lookup_screening_pattern("linkedin", "notice period") is None
        assert db.lookup_screening_pattern("indeed", "notice period") == "1 month"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_experience_db_patterns.py -v`
Expected: FAIL — `record_field_pattern` method doesn't exist

- [ ] **Step 3: Add new tables and methods to FormExperienceDB**

Add to `jobpulse/form_experience_db.py` — in `_init_db()`, add the two new CREATE TABLE statements after the existing one:

```python
# Add inside _init_db(), after existing CREATE TABLE form_experience:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS field_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    profile_key TEXT,
                    last_value TEXT,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform, domain, field_label)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_fp_platform
                ON field_patterns(platform)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_fp_domain
                ON field_patterns(domain)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS screening_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    question_normalized TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    success_count INTEGER DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'llm',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform, question_normalized)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sp_platform
                ON screening_patterns(platform)
            """)
```

Add these methods to the `FormExperienceDB` class:

```python
    def record_field_pattern(
        self, platform: str, domain: str, field_label: str,
        field_type: str, profile_key: str | None, value: str | None,
        success: bool,
    ) -> None:
        domain = self.normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            if success:
                conn.execute(
                    """INSERT INTO field_patterns
                       (platform, domain, field_label, field_type, profile_key,
                        last_value, success_count, failure_count, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                       ON CONFLICT(platform, domain, field_label) DO UPDATE SET
                           profile_key = excluded.profile_key,
                           last_value = excluded.last_value,
                           success_count = success_count + 1,
                           updated_at = excluded.updated_at""",
                    (platform, domain, field_label, field_type, profile_key,
                     value, now, now),
                )
            else:
                conn.execute(
                    """INSERT INTO field_patterns
                       (platform, domain, field_label, field_type, profile_key,
                        last_value, success_count, failure_count, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                       ON CONFLICT(platform, domain, field_label) DO UPDATE SET
                           failure_count = failure_count + 1,
                           updated_at = excluded.updated_at""",
                    (platform, domain, field_label, field_type, profile_key,
                     value, now, now),
                )

    def lookup_field_patterns(
        self, platform: str, domain: str,
    ) -> dict[str, str]:
        """Return {field_label: profile_key} for successful mappings."""
        domain = self.normalize_domain(domain)
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """SELECT field_label, profile_key FROM field_patterns
                   WHERE platform = ? AND domain = ?
                   AND success_count > 0 AND failure_count = 0
                   AND profile_key IS NOT NULL""",
                (platform, domain),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def record_screening_pattern(
        self, platform: str, question: str, answer: str, source: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO screening_patterns
                   (platform, question_normalized, answer, success_count,
                    source, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(platform, question_normalized) DO UPDATE SET
                       answer = excluded.answer,
                       success_count = success_count + 1,
                       source = excluded.source,
                       updated_at = excluded.updated_at""",
                (platform, question, answer, source, now, now),
            )

    def lookup_screening_pattern(
        self, platform: str, question: str,
    ) -> str | None:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT answer FROM screening_patterns
                   WHERE platform = ? AND question_normalized = ?
                   AND success_count > 0""",
                (platform, question),
            ).fetchone()
        return row[0] if row else None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_form_experience_db_patterns.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_form_experience_db_patterns.py
git commit -m "feat(db): add field_patterns and screening_patterns tables to FormExperienceDB"
```

---

### Task 6: Wire strategy into PlaywrightAdapter → Orchestrator → FormFiller → NativeFormFiller

**Files:**
- Modify: `jobpulse/playwright_adapter.py`
- Modify: `jobpulse/application_orchestrator_pkg/__init__.py`
- Modify: `jobpulse/application_orchestrator_pkg/_form_filler.py`
- Modify: `jobpulse/native_form_filler.py`
- Modify: `jobpulse/ats_adapters/__init__.py`
- Test: `tests/jobpulse/test_strategy_wiring.py`

- [ ] **Step 1: Write integration test for strategy wiring**

```python
# tests/jobpulse/test_strategy_wiring.py
"""Tests that strategy flows through the adapter chain."""
from jobpulse.ats_adapters import get_strategy
from jobpulse.ats_adapters.strategy import get_strategy as _get_strategy


def test_get_strategy_exported():
    """get_strategy available from ats_adapters package."""
    s = get_strategy("linkedin")
    assert s.name == "linkedin"


def test_all_platforms_resolve():
    platforms = ["linkedin", "greenhouse", "lever", "workday", "indeed", "reed", "generic"]
    for p in platforms:
        s = _get_strategy(p)
        assert s.name == p, f"Expected {p}, got {s.name}"


def test_unknown_platform_falls_back():
    s = _get_strategy("some_unknown_ats")
    assert s.name == "generic"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_strategy_wiring.py -v`
Expected: FAIL — `get_strategy` not exported from `ats_adapters`

- [ ] **Step 3: Update `ats_adapters/__init__.py`**

Replace the contents of `jobpulse/ats_adapters/__init__.py`:

```python
"""ATS adapter registry — Playwright-only mode.

All job applications route through PlaywrightAdapter which uses
Playwright CDP for form filling. SmartRecruiters uses its own
dedicated Playwright CDP adapter (shadow DOM web components).

Platform strategies customize the shared NativeFormFiller pipeline.
"""
from __future__ import annotations

from jobpulse.ats_adapters.base import BaseATSAdapter
from jobpulse.ats_adapters.strategy import get_strategy

# Import all strategy modules to trigger @register_strategy decorators
import jobpulse.ats_adapters.generic  # noqa: F401
import jobpulse.ats_adapters.linkedin  # noqa: F401
import jobpulse.ats_adapters.greenhouse  # noqa: F401
import jobpulse.ats_adapters.lever  # noqa: F401
import jobpulse.ats_adapters.workday  # noqa: F401
import jobpulse.ats_adapters.indeed  # noqa: F401
import jobpulse.ats_adapters.reed  # noqa: F401


def get_adapter(ats_platform: str | None = None) -> BaseATSAdapter:
    """Return the appropriate adapter for the ATS platform."""
    if ats_platform == "smartrecruiters":
        from jobpulse.ats_adapters.smartrecruiters import SmartRecruitersAdapter
        return SmartRecruitersAdapter()
    from jobpulse.playwright_adapter import PlaywrightAdapter
    return PlaywrightAdapter()


def reset_adapter() -> None:
    """No-op — kept for test compatibility."""


__all__ = ["BaseATSAdapter", "get_adapter", "get_strategy", "reset_adapter"]
```

- [ ] **Step 4: Update `playwright_adapter.py` to resolve and pass strategy**

Replace the contents of `jobpulse/playwright_adapter.py`:

```python
"""PlaywrightAdapter — ATS adapter using Playwright CDP for form filling."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)


def _detect_ats_platform(url: str) -> str:
    from jobpulse.jd_analyzer import detect_ats_platform
    return detect_ats_platform(url) or "generic"


class PlaywrightAdapter(BaseATSAdapter):
    name: str = "playwright"

    def detect(self, url: str) -> bool:
        return False

    async def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None = None,
        profile: dict | None = None,
        custom_answers: dict | None = None,
        overrides: dict[str, Any] | None = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> dict:
        from jobpulse.application_orchestrator import ApplicationOrchestrator
        from jobpulse.playwright_driver import PlaywrightDriver
        from jobpulse.ats_adapters.strategy import get_strategy

        profile = profile or {}
        custom_answers = custom_answers or {}
        platform = _detect_ats_platform(url)
        strategy = get_strategy(platform)
        logger.info("PlaywrightAdapter: applying to %s via %s (strategy=%s)", url, platform, strategy.name)

        driver = PlaywrightDriver()
        await driver.connect()

        orchestrator = ApplicationOrchestrator(driver=driver, engine="playwright")
        try:
            result = await orchestrator.apply(
                url=url,
                platform=platform,
                cv_path=cv_path,
                cover_letter_path=cover_letter_path,
                profile=profile,
                custom_answers=custom_answers,
                overrides=overrides,
                dry_run=dry_run,
                strategy=strategy,
            )
        finally:
            if driver.page:
                try:
                    from jobpulse.browser_cleanup import flush_browser_caches
                    await flush_browser_caches(driver.page)
                except Exception as exc:
                    logger.debug("PlaywrightAdapter: flush_browser_caches: %s", exc)
            await driver.close()
        return result
```

- [ ] **Step 5: Update `application_orchestrator_pkg/__init__.py` — accept strategy param**

In `ApplicationOrchestrator.apply()`, add `strategy=None` parameter and pass it to `self._filler.fill_application()`.

In the method signature at line ~81, add the parameter:

```python
    async def apply(
        self,
        url: str,
        platform: str,
        cv_path: "Path",
        cover_letter_path: "Path | None" = None,
        profile: dict | None = None,
        custom_answers: dict | None = None,
        overrides: dict | None = None,
        dry_run: bool = False,
        form_intelligence: Any | None = None,
        jd_keywords: list[str] | None = None,
        company_research: "CompanyResearch | None" = None,
        pre_navigated_snapshot: dict | None = None,
        strategy: Any | None = None,  # BasePlatformStrategy
    ) -> dict:
```

At line ~162 where `self._filler.fill_application()` is called, add `strategy=strategy`:

```python
        result = await self._filler.fill_application(
            platform=platform,
            snapshot=nav_result["snapshot"],
            cv_path=cv_path,
            cover_letter_path=cover_letter_path,
            profile=profile,
            custom_answers=custom_answers,
            overrides=overrides,
            dry_run=dry_run,
            form_intelligence=form_intelligence,
            strategy=strategy,
        )
```

- [ ] **Step 6: Update `application_orchestrator_pkg/_form_filler.py` — pass strategy to NativeFormFiller**

Replace the contents of `jobpulse/application_orchestrator_pkg/_form_filler.py`:

```python
"""Form filling — delegates to NativeFormFiller (Playwright locators + LLM)."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from jobpulse.application_orchestrator_pkg._executor import ActionExecutor
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator

logger = get_logger(__name__)

MAX_FORM_PAGES = 20


class FormFiller:
    """Form filling via NativeFormFiller."""

    def __init__(self, orch, executor: "ActionExecutor", navigator: "FormNavigator"):
        self._orch = orch
        self.executor = executor
        self.navigator = navigator

    @property
    def driver(self):
        return self._orch.driver

    async def fill_application(
        self, platform, snapshot, cv_path, cover_letter_path, profile,
        custom_answers, overrides, dry_run, form_intelligence,
        strategy=None,
    ) -> dict:
        from jobpulse.native_form_filler import NativeFormFiller
        filler = NativeFormFiller(page=self.driver.page, driver=self.driver)
        return await filler.fill(
            platform=platform,
            cv_path=str(cv_path) if cv_path else None,
            cl_path=str(cover_letter_path) if cover_letter_path else None,
            profile=profile or {},
            custom_answers=custom_answers or {},
            dry_run=dry_run,
            strategy=strategy,
        )
```

- [ ] **Step 7: Update `native_form_filler.py` — accept strategy, use its hooks**

In `NativeFormFiller.fill()` at line ~1583, add `strategy=None` param and wire it in:

```python
    async def fill(
        self,
        platform: str,
        cv_path: str | None,
        cl_path: str | None,
        profile: dict,
        custom_answers: dict,
        dry_run: bool,
        strategy: Any | None = None,
    ) -> dict:
```

At the top of the method body, resolve strategy and merge label mappings:

```python
        # Resolve strategy
        if strategy is None:
            from jobpulse.ats_adapters.strategy import get_strategy
            strategy = get_strategy(platform)

        # Pre-fill hook
        pre_fill_result = await strategy.pre_fill(
            self.page, cv_path, profile, custom_answers,
        )
        skip_fields = set(pre_fill_result.get("skip_fields", []))

        # Merge strategy label mappings into the global lookup
        strategy_labels = strategy.extra_label_mappings()
```

Replace the `_PLATFORM_MIN_PAGE_TIME` lookup at line ~1803:

```python
            # 8. Anti-detection timing — use strategy's min_page_time
            min_time = strategy.min_page_time
            await asyncio.sleep(min_time * random.uniform(0.8, 1.2))
```

After each page is filled (before anti-detection timing), add the post_page hook:

```python
            # Post-page hook
            await strategy.post_page(self.page, page_num, {
                "fields_filled": page_fields_filled,
                "fields_failed": fill_failures,
            })
```

Delete the `_PLATFORM_MIN_PAGE_TIME` dict at the top of the file (lines 30-38).

- [ ] **Step 8: Run wiring tests + existing tests**

Run: `python -m pytest tests/jobpulse/test_strategy_wiring.py tests/jobpulse/ats_adapters/ -v`
Expected: All tests PASS

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v`
Expected: Existing tests still PASS (strategy defaults to GenericStrategy)

- [ ] **Step 9: Commit**

```bash
git add jobpulse/ats_adapters/__init__.py jobpulse/playwright_adapter.py jobpulse/application_orchestrator_pkg/__init__.py jobpulse/application_orchestrator_pkg/_form_filler.py jobpulse/native_form_filler.py tests/jobpulse/test_strategy_wiring.py
git commit -m "feat(adapters): wire platform strategy through adapter → orchestrator → filler pipeline"
```

---

### Task 7: Full regression test

**Files:**
- No new files — run existing test suite

- [ ] **Step 1: Run full jobpulse test suite**

Run: `python -m pytest tests/jobpulse/ -v --tb=short`
Expected: All existing tests PASS. The strategy changes are backward-compatible because:
- `strategy=None` defaults to `GenericStrategy` everywhere
- `GenericStrategy.min_page_time == 5.0` matches the old `_PLATFORM_MIN_PAGE_TIME["generic"]`
- No existing test injects a strategy, so they all use the default path

- [ ] **Step 2: Run adapter-specific tests**

Run: `python -m pytest tests/jobpulse/ats_adapters/ tests/jobpulse/test_form_experience_db_patterns.py tests/jobpulse/test_strategy_wiring.py -v`
Expected: All new tests PASS

- [ ] **Step 3: Commit if any fixes were needed**

```bash
git add -u
git commit -m "fix: resolve test regressions from strategy wiring"
```

---

### Task 8: Create tests/jobpulse/ats_adapters/ directory init

**Files:**
- Create: `tests/jobpulse/ats_adapters/__init__.py`

- [ ] **Step 1: Create empty init file**

This must exist before Task 1's tests can be discovered by pytest.

```python
# tests/jobpulse/ats_adapters/__init__.py
```

- [ ] **Step 2: Verify test discovery**

Run: `python -m pytest tests/jobpulse/ats_adapters/ --collect-only`
Expected: Shows test files (once they exist from later tasks)

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/ats_adapters/__init__.py
git commit -m "chore: add tests/jobpulse/ats_adapters/ package init"
```

**NOTE:** This task must be executed BEFORE Task 1.

---

## Execution Order

Task 8 → Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7

Task 8 creates the test directory. Tasks 1-4 are strategy files (independent of each other but Task 1 provides the base). Task 5 is the DB schema (independent of strategies). Task 6 wires everything together. Task 7 validates.
