"""FormFillEngine — unified form-filling orchestrator.

Replaces NativeFormFiller as the sole production pipeline.
Uses the clean form_engine/ fillers with widget-aware strategies on top.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.form_engine.field_mapper import (
    clean_mapping,
    map_fields,
    screen_questions,
    try_cached_mapping,
)
from jobpulse.form_engine.field_resolver import LabelMappingStore
from jobpulse.form_engine.models import FieldInfo, FillResult, InputType
from jobpulse.form_engine.page_filler import fill_field_by_type
from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
from jobpulse.form_engine.widget_detector import WidgetLibraryDetector
from jobpulse.form_engine.widget_strategies import get_strategy as get_widget_strategy
from jobpulse.ats_adapters.strategy import get_strategy as get_platform_strategy
from jobpulse.tracked_driver import ABTracker

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

MAX_FORM_PAGES = 20


@dataclass
class PageFillResult:
    """Result of filling one form page."""

    success: bool
    fields_filled: int = 0
    fields_failed: list[str] = field(default_factory=list)
    llm_calls: int = 0
    screenshot: str | None = None


@dataclass
class FormFillResult:
    """Result of filling a complete multi-page form."""

    success: bool
    pages_filled: int = 0
    total_fields_filled: int = 0
    total_fields_failed: int = 0
    failed_labels: list[str] = field(default_factory=list)
    llm_calls: int = 0
    time_seconds: float = 0.0
    error: str | None = None
    dry_run: bool = False
    agent_mapping: dict[str, str] = field(default_factory=dict)


class FormFillEngine:
    """Unified form-filling engine."""

    def __init__(self, page: "Page", driver: Any, application_id: str | None = None) -> None:
        self._page = page
        self._driver = driver
        self._scanner = UnifiedFieldScanner(page)
        self._widget_detector = WidgetLibraryDetector(page)
        self._label_store = LabelMappingStore()
        self._llm_calls: int = 0
        self._tracker = ABTracker() if application_id else None
        self._app_id = application_id or ""
        self._engine_name = "unified_form_engine"

    # ── Public API ──

    async def fill(
        self,
        profile: dict[str, str],
        custom_answers: dict[str, Any],
        platform: str = "generic",
        dry_run: bool = False,
    ) -> FormFillResult:
        """Fill a complete multi-page form."""
        import time

        t0 = time.monotonic()
        total_filled = 0
        total_failed: list[str] = []
        all_mappings: dict[str, str] = {}
        pages_navigated = 0

        for page_num in range(1, MAX_FORM_PAGES + 1):
            page_result = await self._fill_page(
                profile, custom_answers, platform, page_num, dry_run
            )

            total_filled += page_result.fields_filled
            total_failed.extend(page_result.fields_failed)
            self._llm_calls += page_result.llm_calls

            if not page_result.success:
                result = FormFillResult(
                    success=False,
                    pages_filled=page_num - 1,
                    total_fields_filled=total_filled,
                    total_fields_failed=len(total_failed),
                    failed_labels=total_failed,
                    llm_calls=self._llm_calls,
                    time_seconds=round(time.monotonic() - t0, 1),
                    error=f"Page {page_num} fill failed",
                    agent_mapping=all_mappings,
                )
                self._log_outcome(platform, result, pages_navigated)
                return result

            # Check if we're on a submit/confirmation page
            if await self._is_confirmation_page():
                result = FormFillResult(
                    success=True,
                    pages_filled=page_num,
                    total_fields_filled=total_filled,
                    total_fields_failed=len(total_failed),
                    failed_labels=total_failed,
                    llm_calls=self._llm_calls,
                    time_seconds=round(time.monotonic() - t0, 1),
                    agent_mapping=all_mappings,
                )
                self._log_outcome(platform, result, pages_navigated)
                return result

            # Click next/submit
            nav_result = await self._click_navigation(dry_run, platform)
            if nav_result == "submitted":
                result = FormFillResult(
                    success=True,
                    pages_filled=page_num,
                    total_fields_filled=total_filled,
                    total_fields_failed=len(total_failed),
                    failed_labels=total_failed,
                    llm_calls=self._llm_calls,
                    time_seconds=round(time.monotonic() - t0, 1),
                    agent_mapping=all_mappings,
                )
                self._log_outcome(platform, result, pages_navigated)
                return result
            if nav_result == "dry_run_stop":
                result = FormFillResult(
                    success=True,
                    pages_filled=page_num,
                    total_fields_filled=total_filled,
                    dry_run=True,  # type: ignore[call-arg]
                    llm_calls=self._llm_calls,
                    time_seconds=round(time.monotonic() - t0, 1),
                    agent_mapping=all_mappings,
                )
                self._log_outcome(platform, result, pages_navigated)
                return result
            if nav_result in ("next",):
                pages_navigated += 1
            if not nav_result:
                # No navigation button — might be single-page form already submitted
                if page_num == 1 and await self._is_confirmation_page():
                    result = FormFillResult(
                        success=True,
                        pages_filled=1,
                        total_fields_filled=total_filled,
                        total_fields_failed=len(total_failed),
                        failed_labels=total_failed,
                        llm_calls=self._llm_calls,
                        time_seconds=round(time.monotonic() - t0, 1),
                        agent_mapping=all_mappings,
                    )
                    self._log_outcome(platform, result, pages_navigated)
                    return result
                result = FormFillResult(
                    success=False,
                    pages_filled=page_num,
                    total_fields_filled=total_filled,
                    total_fields_failed=len(total_failed),
                    failed_labels=total_failed,
                    llm_calls=self._llm_calls,
                    time_seconds=round(time.monotonic() - t0, 1),
                    error=f"No navigation button on page {page_num}",
                    agent_mapping=all_mappings,
                )
                self._log_outcome(platform, result, pages_navigated)
                return result

        result = FormFillResult(
            success=False,
            pages_filled=MAX_FORM_PAGES,
            total_fields_filled=total_filled,
            total_fields_failed=len(total_failed),
            failed_labels=total_failed,
            llm_calls=self._llm_calls,
            time_seconds=round(time.monotonic() - t0, 1),
            error=f"Exhausted {MAX_FORM_PAGES} form pages",
            agent_mapping=all_mappings,
        )
        self._log_outcome(platform, result, pages_navigated)
        return result

    # ── Page-level fill ──

    async def _fill_page(
        self,
        profile: dict[str, str],
        custom_answers: dict[str, Any],
        platform: str,
        page_num: int,
        dry_run: bool,
    ) -> PageFillResult:
        """Scan, map, and fill all fields on the current page."""
        strategy = get_platform_strategy(platform)

        # 0. Pre-fill hook
        try:
            await strategy.pre_fill(self._page, None, profile, custom_answers)
        except Exception as exc:
            logger.debug("Platform pre_fill failed: %s", exc)

        # 1. Scan fields (with platform iframe awareness)
        fields = await self._scanner.scan()
        if not fields:
            logger.warning("Page %d: no fields found", page_num)
            return PageFillResult(success=True, fields_filled=0)

        logger.info("Page %d: %d fields scanned", page_num, len(fields))

        # 2. Detect widget libraries per field (prioritise platform-known libs)
        widget_map = await self._widget_detector.detect_for_page()
        known_libs = set(strategy.known_widget_libraries())
        for f in fields:
            if f.selector in widget_map:
                lib = widget_map[f.selector]
                if lib in known_libs or not known_libs:
                    f.attributes["widget_library"] = lib

        # 3. Normalise labels with platform-specific rules
        for f in fields:
            if f.label:
                f.label = strategy.normalize_label(f.label)

        # 4. Build mapping (deterministic → LLM)
        mapping, llm_calls = await self._build_mapping(
            fields, profile, custom_answers, platform, strategy
        )
        self._llm_calls += llm_calls

        # 5. Fill each field
        filled = 0
        failed: list[str] = []
        for label, value in mapping.items():
            field = self._find_field_by_label(fields, label)
            if not field:
                logger.warning("Field '%s' not found in scan", label)
                failed.append(label)
                self._log_field(platform, "fill", "", False, error="field_not_found")
                continue

            # Skip buttons (navigation, not form data)
            if str(field.input_type) == "button":
                logger.debug("Skipping button field: %s", label)
                continue

            result = await self._fill_single_field(field, value, strategy)
            if result.success and result.value_verified:
                filled += 1
                self._log_field(
                    platform, "fill", field.selector, True,
                    value_verified=True,
                )
            else:
                failed.append(label)
                self._log_field(
                    platform, "fill", field.selector, False,
                    value_verified=False, error=result.error or "unverified",
                )
                logger.warning(
                    "Fill failed for '%s': %s", label, result.error or "unverified"
                )

        # 6. Handle file uploads (separate from field mapping)
        # TODO: file upload handling

        # 7. Auto-check consent boxes
        # TODO: consent handling

        # 8. Post-page hook
        try:
            await strategy.post_page(
                self._page, page_num,
                {"filled": filled, "failed": failed, "llm_calls": llm_calls},
            )
        except Exception as exc:
            logger.debug("Platform post_page failed: %s", exc)

        return PageFillResult(
            success=True,
            fields_filled=filled,
            fields_failed=failed,
            llm_calls=llm_calls,
        )

    # ── Mapping ──

    async def _build_mapping(
        self,
        fields: list[FieldInfo],
        profile: dict[str, str],
        custom_answers: dict[str, Any],
        platform: str,
        strategy: Any,
    ) -> tuple[dict[str, str], int]:
        """Build label→value mapping. Returns (mapping, llm_calls)."""
        llm_calls = 0

        # Try cached mapping first
        cached = try_cached_mapping(
            page_url=getattr(self._page, "url", "") or "",
            fields=[{"label": f.label, "type": f.input_type, "value": f.current_value} for f in fields],
            profile=profile,
            custom_answers=custom_answers,
            known_domain=False,
        )
        if cached:
            return clean_mapping(cached), 0

        # Deterministic seed mapping (with platform extra mappings)
        from jobpulse.form_engine.field_mapper import seed_mapping
        extra = strategy.extra_label_mappings()
        if extra:
            # Seed the label store with platform-specific mappings
            for label, key in extra.items():
                self._label_store.learn(label, key)

        mapping, unresolved = seed_mapping(
            fields=[{"label": f.label, "type": f.input_type, "value": f.current_value} for f in fields],
            profile=profile,
            custom_answers=custom_answers,
        )

        if not unresolved:
            return clean_mapping(mapping), 0

        # LLM mapping for unresolved text fields
        llm_mapping, calls = await map_fields(
            page_url=getattr(self._page, "url", "") or "",
            fields=[{"label": f["label"], "type": f["type"], "value": f.get("value")} for f in unresolved],
            profile=profile,
            custom_answers=custom_answers,
            platform=platform,
            known_domain=False,
            correction_warning="",
        )
        llm_calls += calls
        mapping.update(llm_mapping)

        # Screening questions for remaining unresolved
        still_unresolved = [
            f for f in unresolved
            if f["label"] not in mapping and f["type"] != "file"
        ]
        if still_unresolved:
            screening, s_calls = await screen_questions(
                unresolved_fields=still_unresolved,
                job_context=custom_answers.get("_job_context"),
                profile_store=None,
                correction_warning="",
            )
            llm_calls += s_calls
            mapping.update(clean_mapping(screening))

        return clean_mapping(mapping), llm_calls

    # ── Single field fill ──

    async def _fill_single_field(
        self, field: FieldInfo, value: str, strategy: Any,
    ) -> FillResult:
        """Fill a single field using widget strategy or generic filler."""
        # Platform combobox / select override first
        if str(field.input_type) in ("combobox", "select", "select_custom", "select-one", "select-multiple"):
            try:
                locator = self._page.locator(field.selector).first
                override = await strategy.fill_combobox(self._page, locator, value, field.label)
                if override is not None:
                    return FillResult(
                        success=True, selector=field.selector,
                        value_verified=True, value_filled=override,
                    )
            except Exception as exc:
                logger.debug("Platform combobox override failed: %s", exc)

        # Check widget strategy
        widget_lib = field.attributes.get("widget_library")
        widget_strategy = get_widget_strategy(widget_lib)
        if widget_strategy:
            return await widget_strategy(self._page, field, value)

        # Generic filler via form_engine/
        try:
            return await fill_field_by_type(self._page, field, value)
        except Exception as exc:
            logger.warning("Generic fill failed for '%s': %s", field.label, exc)
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error=str(exc),
            )

    # ── Navigation ──

    async def _click_navigation(
        self, dry_run: bool, platform: str = "generic",
    ) -> str:
        """Click next/submit button. Returns 'submitted', 'next', 'dry_run_stop', or ''."""
        page = self._page
        strategy = get_platform_strategy(platform)

        # Try platform-specific selectors first
        for action, selectors in [
            ("submit", strategy.submit_selectors()),
            ("next", strategy.next_page_selectors()),
        ]:
            for selector in selectors:
                try:
                    btn = page.locator(selector).first
                    if await btn.count() and await btn.is_visible():
                        if action == "submit" and dry_run:
                            return "dry_run_stop"
                        await btn.click()
                        return "submitted" if action == "submit" else "next"
                except Exception:
                    continue

        # Generic role-based search
        button_groups = [
            ("submit", ["Submit Application", "Submit", "Apply"]),
            ("next", ["Review", "Save and Continue", "Save & Continue", "Continue", "Next", "Proceed"]),
        ]

        for action, names in button_groups:
            for name in names:
                try:
                    btn = page.get_by_role("button", name=name, exact=False).first
                    if await btn.count() and await btn.is_visible():
                        if action == "submit" and dry_run:
                            return "dry_run_stop"
                        await btn.click()
                        return "submitted" if action == "submit" else "next"
                except Exception:
                    continue

        # Fallback to link roles
        for name in ["Submit", "Apply Now", "Continue"]:
            try:
                link = page.get_by_role("link", name=name, exact=False).first
                if await link.count() and await link.is_visible():
                    await link.click()
                    return "next"
            except Exception:
                continue

        return ""

    # ── Page detection ──

    async def _is_confirmation_page(self) -> bool:
        """Check if current page is a confirmation/thank-you page."""
        try:
            body = await self._page.locator("body").text_content()
            text = (body or "").lower()[:2000]
            return any(phrase in text for phrase in (
                "thank you for applying",
                "application has been received",
                "application submitted",
                "successfully submitted",
                "your application has been sent",
            ))
        except Exception:
            return False

    # ── A/B Tracking ──

    def _log_field(
        self,
        platform: str,
        action: str,
        selector: str,
        success: bool,
        value_verified: bool | None = None,
        error: str | None = None,
    ) -> None:
        if self._tracker is None:
            return
        try:
            self._tracker.log_field(
                application_id=self._app_id,
                engine=self._engine_name,
                platform=platform,
                action=action,
                selector=selector,
                success=success,
                value_verified=value_verified,
                error=error,
            )
        except Exception as exc:
            logger.debug("ABTracker log_field failed: %s", exc)

    def _log_outcome(
        self,
        platform: str,
        result: FormFillResult,
        pages_navigated: int,
    ) -> None:
        if self._tracker is None:
            return
        try:
            self._tracker.log_outcome(
                app_id=self._app_id,
                engine=self._engine_name,
                platform=platform,
                domain="",
                total_fields=result.total_fields_filled + result.total_fields_failed,
                fields_filled=result.total_fields_filled,
                fields_verified=result.total_fields_filled,
                validation_errors=result.total_fields_failed,
                outcome="submitted" if result.success else "failed",
                total_duration_s=result.time_seconds,
                pages_navigated=pages_navigated,
                fixes_applied=0,
                fixes_learned=0,
            )
        except Exception as exc:
            logger.debug("ABTracker log_outcome failed: %s", exc)

    # ── Helpers ──

    @staticmethod
    def _find_field_by_label(fields: list[FieldInfo], label: str) -> FieldInfo | None:
        """Find a field by its label (exact or case-insensitive)."""
        for f in fields:
            if f.label == label or f.label.lower() == label.lower():
                return f
        return None
