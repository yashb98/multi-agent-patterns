"""Record-replay test harness for ATS form fixtures.

Usage (record):
    pytest tests/jobpulse/test_harness.py::record_greenhouse --record-url "..."

Usage (replay in CI):
    pytest tests/jobpulse/ats_fixtures/ -v --replay
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from jobpulse.form_engine.engine import FormFillEngine
from jobpulse.form_engine.models import FieldInfo
from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner


class ATSTestHarness:
    """Record Playwright traces of real ATS forms and replay in CI."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def record_fixture(
        self,
        url: str,
        platform: str,
        page: Any,
        driver: Any,
    ) -> Path:
        """Record a Playwright trace + DOM snapshots at each step.

        Returns the directory containing the recorded fixture.
        """
        fixture_dir = self.output_dir / f"{platform}_{self._slugify(url)}"
        fixture_dir.mkdir(parents=True, exist_ok=True)

        steps: list[dict] = []

        # Step 0: initial page state
        snapshot = await self._get_snapshot(page, driver)
        step0 = fixture_dir / "step_00_initial.json"
        step0.write_text(json.dumps(snapshot, indent=2, default=str))
        steps.append({"step": 0, "action": "navigate", "file": str(step0.name)})

        # Step 1: after clicking apply
        try:
            apply_btn = page.locator(
                "button:has-text('Apply'), button:has-text('Easy Apply'), a:has-text('Apply')"
            ).first
            if await apply_btn.count():
                await apply_btn.click()
                await asyncio.sleep(3)
                snapshot = await self._get_snapshot(page, driver)
                step1 = fixture_dir / "step_01_after_apply.json"
                step1.write_text(json.dumps(snapshot, indent=2, default=str))
                steps.append({"step": 1, "action": "click_apply", "file": str(step1.name)})
        except Exception as exc:
            steps.append({"step": 1, "action": "click_apply", "error": str(exc)})

        # Step 2: scan fields
        try:
            scanner = UnifiedFieldScanner(page)
            fields = await scanner.scan()
            fields_json = [asdict(f) if hasattr(f, "__dataclass_fields__") else dict(f) for f in fields]
            step2 = fixture_dir / "step_02_fields.json"
            step2.write_text(json.dumps(fields_json, indent=2, default=str))
            steps.append({"step": 2, "action": "scan", "file": str(step2.name), "count": len(fields)})
        except Exception as exc:
            steps.append({"step": 2, "action": "scan", "error": str(exc)})

        # Write manifest
        manifest = {
            "url": url,
            "platform": platform,
            "steps": steps,
        }
        (fixture_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        return fixture_dir

    async def replay_fixture(
        self,
        fixture_dir: Path,
        engine: FormFillEngine,
    ) -> dict:
        """Replay a recorded fixture against the form engine.

        Loads the recorded field snapshot and runs the engine's mapping
        and fill logic with mocked page interactions.
        """
        manifest_path = fixture_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest found in {fixture_dir}")

        manifest = json.loads(manifest_path.read_text())

        # Load scanned fields
        fields_path = fixture_dir / "step_02_fields.json"
        if not fields_path.exists():
            return {"success": False, "error": "No field scan recorded"}

        fields_raw = json.loads(fields_path.read_text())
        fields = [FieldInfo(**f) for f in fields_raw]

        # Mock the scanner to return recorded fields
        engine._scanner.scan = lambda: asyncio.sleep(0) or asyncio.Future().set_result(fields) or fields  # type: ignore[assignment]

        # Run a lightweight fill pass (mapping only, no real page interaction)
        mapping, llm_calls = await engine._build_mapping(
            fields,
            profile={},
            custom_answers={},
            platform=manifest["platform"],
            strategy=object(),  # dummy strategy
        )

        return {
            "success": True,
            "platform": manifest["platform"],
            "fields_count": len(fields),
            "mapping": mapping,
            "llm_calls": llm_calls,
        }

    # ── helpers ──

    async def _get_snapshot(self, page: Any, driver: Any) -> dict:
        """Get a JSON-serialisable page snapshot."""
        try:
            if driver and hasattr(driver, "get_snapshot"):
                snap = await driver.get_snapshot(force_refresh=True)
                if hasattr(snap, "model_dump"):
                    return snap.model_dump()
                return dict(snap)
        except Exception as exc:
            return {"error": str(exc)}

        # Fallback: basic page info
        try:
            return {
                "url": page.url,
                "title": await page.title(),
            }
        except Exception as exc:
            return {"error": str(exc)}

    @staticmethod
    def _slugify(url: str) -> str:
        """Create a filesystem-safe slug from a URL."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.strip("/").replace("/", "_")
        return f"{parsed.netloc}_{path}"[:100]


# ── pytest fixtures ──

@pytest.fixture
def harness(tmp_path: Path) -> ATSTestHarness:
    return ATSTestHarness(tmp_path / "fixtures")


# ── record commands (run manually with --record-url) ──

@pytest.mark.skip(reason="Manual record command — run with --record-url")
@pytest.mark.asyncio
async def record_greenhouse(harness: ATSTestHarness, request: Any) -> None:
    url = request.config.getoption("--record-url")
    assert url, "Pass --record-url"
    # Requires a live Playwright page — skipped in CI


@pytest.mark.skip(reason="Manual record command — run with --record-url")
@pytest.mark.asyncio
async def record_workday(harness: ATSTestHarness, request: Any) -> None:
    url = request.config.getoption("--record-url")
    assert url, "Pass --record-url"


def pytest_addoption(parser: Any) -> None:
    parser.addoption("--record-url", action="store", default=None, help="URL to record")
    parser.addoption("--replay", action="store_true", default=False, help="Replay recorded fixtures")
