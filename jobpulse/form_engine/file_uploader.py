"""File uploader — CV/CL uploads, consent checkboxes, and modal CV handling."""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


async def upload_pdf(locator: Any, file_path: str) -> None:
    from pathlib import Path
    p = Path(file_path)
    if not p.is_file():
        logger.error("PDF upload failed — file not found: %s", file_path)
        return
    await locator.set_input_files({
        "name": p.name,
        "mimeType": "application/pdf",
        "buffer": p.read_bytes(),
    })


async def page_has_cover_letter_file_input(page: "Page") -> bool:
    return await page.evaluate("""() => {
        const ins = document.querySelectorAll("input[type='file']");
        for (const el of ins) {
            const parts = [
                (el.labels && el.labels[0] && el.labels[0].textContent) || "",
                el.getAttribute("aria-label") || "",
                el.id || "",
                el.name || "",
            ].join(" ").toLowerCase();
            if (parts.includes("cover") || (parts.includes("letter") && !parts.includes("newsletter")))
                return true;
            // Scan surrounding context: parent containers, headings, sibling text
            let node = el.parentElement;
            for (let i = 0; node && i < 5; i++, node = node.parentElement) {
                const ctx = (node.textContent || "").toLowerCase().slice(0, 500);
                if ((ctx.includes("cover letter") || ctx.includes("cover_letter"))
                    && !ctx.includes("newsletter")) return true;
                if (ctx.includes("additional") && (ctx.includes("attachment") || ctx.includes("document"))
                    && (ctx.includes("cover") || ctx.includes("letter") || ctx.includes("portfolio")))
                    return true;
            }
        }
        return false;
    }""")


async def enable_optional_cover_letter_checkbox(
    page: "Page", get_accessible_name: Any,
) -> None:
    positive = (
        "include a cover letter",
        "attach a cover letter",
        "add a cover letter",
        "upload a cover letter",
        "include cover letter",
    )
    try:
        for cb in await page.get_by_role("checkbox").all():
            label = (await get_accessible_name(cb)).strip().lower()
            if not label:
                continue
            if not any(p in label for p in positive):
                continue
            if await cb.is_checked():
                continue
            logger.info("enabling optional cover-letter checkbox: %s", label[:100])
            await cb.check()
            return
    except Exception as exc:
        logger.debug("optional CL checkbox pass failed: %s", exc)


async def resolve_lazy_cover_letter_path(
    page: "Page",
    cl_path: str | None,
    custom_answers: dict | None,
) -> str | None:
    if cl_path:
        return cl_path
    gen = (custom_answers or {}).get("_cl_generator")
    if not callable(gen):
        return None
    if not await page_has_cover_letter_file_input(page):
        return None
    try:
        p = gen()
        return str(p) if p else None
    except Exception as exc:
        logger.warning("lazy cover letter generation failed: %s", exc)
        return None


async def upload_files(
    page: "Page",
    cv_path: str | None,
    cl_path: str | None,
    custom_answers: dict | None,
    get_accessible_name: Any,
) -> None:
    await enable_optional_cover_letter_checkbox(page, get_accessible_name)
    cl_path = await resolve_lazy_cover_letter_path(page, cl_path, custom_answers)

    file_meta = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll("input[type='file']")).map((el, idx) => {
            let ctx = '';
            let node = el.parentElement;
            for (let i = 0; node && i < 5; i++, node = node.parentElement) {
                ctx = (node.textContent || '').trim().slice(0, 500);
                if (ctx.length > 20) break;
            }
            return {
                idx,
                id: el.id || '',
                name: el.name || '',
                label: (el.labels?.[0]?.textContent?.trim() || el.getAttribute('aria-label') || ''),
                surrounding_text: ctx,
            };
        });
    }""")
    cv_uploaded = False
    cl_uploaded = False

    for meta in file_meta:
        identifiers = f"{meta['label']} {meta['id']} {meta['name']}".lower()

        if "autofill" in identifiers or "drag and drop" in identifiers:
            continue

        if meta["id"]:
            fi = page.locator(f'input[type="file"][id="{meta["id"]}"]').first
        elif meta["name"]:
            fi = page.locator(f'input[type="file"][name="{meta["name"]}"]').first
        else:
            fi = page.locator("input[type='file']").nth(meta["idx"])

        surrounding = meta.get("surrounding_text", "").lower()
        is_cl_field = any(kw in identifiers for kw in ("cover", "cl", "letter")) or (
            "other" in identifiers and "attach" in identifiers
        ) or (
            "cover letter" in surrounding
            or ("additional" in surrounding and ("attachment" in surrounding or "document" in surrounding)
                and ("cover" in surrounding or "letter" in surrounding or "portfolio" in surrounding))
        )
        if is_cl_field and cl_path and not cl_uploaded:
            await upload_pdf(fi, str(cl_path))
            cl_uploaded = True
        elif cv_path and not cv_uploaded:
            await upload_pdf(fi, str(cv_path))
            cv_uploaded = True


async def check_consent(page: "Page", get_accessible_name: Any) -> None:
    from jobpulse.form_engine.consent_policy import is_required_consent

    checkboxes = await page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await get_accessible_name(cb)
        if not label or len(label.strip()) <= 1:
            try:
                label = await cb.evaluate(
                    "el => {"
                    "  let node = el;"
                    "  for (let i = 0; node && i < 10; i++) {"
                    "    const next = node.parentElement || "
                    "      (node.getRootNode && node.getRootNode() !== node"
                    "       ? node.getRootNode().host : null);"
                    "    if (!next) break;"
                    "    node = next;"
                    "    const txt = (node.textContent || '').trim();"
                    "    if (txt.length > 30 && txt.length < 1000) return txt;"
                    "  }"
                    "  return '';"
                    "}"
                )
            except Exception:
                pass
        if not is_required_consent(label):
            if label.strip():
                logger.debug("consent: skipping non-required checkbox: %r", label)
            continue
        if not await cb.is_checked():
            logger.info("consent: auto-ticking required consent: %r", label)
            await cb.check()

    await check_consent_selects(page)
    await _check_consent_custom_dropdowns(page)


async def check_consent_selects(page: "Page") -> None:
    _ACCEPT_RE = re.compile(r"^i\s+accept$", re.IGNORECASE)
    _PLACEHOLDER_RE = re.compile(
        r"(make a selection|select|choose|please select|—)", re.IGNORECASE,
    )

    for loc in await page.locator("select").all():
        try:
            options = await loc.locator("option").all_text_contents()
            options_stripped = [o.strip() for o in options]
            accept_option = next(
                (o for o in options_stripped if _ACCEPT_RE.search(o)), None,
            )
            if not accept_option:
                continue
            current = await loc.evaluate(
                "el => el.options[el.selectedIndex]?.text?.trim() || ''"
            )
            if _ACCEPT_RE.search(current):
                continue
            if not _PLACEHOLDER_RE.search(current):
                continue
            await loc.select_option(label=accept_option, timeout=5000)
            logger.info("consent: selected '%s' in consent dropdown", accept_option)
        except Exception as exc:
            logger.debug("consent: select dropdown handling failed: %s", exc)


async def _check_consent_custom_dropdowns(page: "Page") -> None:
    """Handle custom React consent dropdowns (data-testid pattern)."""
    _ACCEPT_RE = re.compile(r"i\s*accept|i\s*agree", re.IGNORECASE)
    _CONSENT_SELECTORS = [
        '[data-testid="agree-data-privacy-dropdown"]',
        '[data-testid*="consent"][data-testid*="dropdown"]',
        '[data-testid*="privacy"][data-testid*="dropdown"]',
    ]

    for sel in _CONSENT_SELECTORS:
        try:
            container = page.locator(sel).first
            if not await container.count():
                continue

            btn = container.locator("button").first
            if not await btn.count():
                continue

            current = (await btn.text_content() or "").strip()
            if _ACCEPT_RE.search(current):
                continue

            await btn.scroll_into_view_if_needed()
            await btn.click()
            await asyncio.sleep(0.5)

            clicked = await page.evaluate("""() => {
                const re = /i\\s*accept|i\\s*agree/i;
                const candidates = document.querySelectorAll(
                    '[role="option"], [role="listbox"] li, ul li'
                );
                for (const c of candidates) {
                    if (c.offsetParent === null) continue;
                    const text = (c.textContent || '').trim();
                    if (re.test(text)) {
                        c.click();
                        return text;
                    }
                }
                return null;
            }""")

            if clicked:
                logger.info("consent: custom dropdown %s → '%s'", sel, clicked)
            else:
                await page.keyboard.press("Escape")
        except Exception as exc:
            logger.debug("consent: custom dropdown %s failed: %s", sel, exc)


async def handle_modal_cv_upload(page: "Page", cv_path: str | None) -> bool:
    if not cv_path:
        return False

    modal = page.locator('[data-qa="apply-job-modal"]')
    if not await modal.count():
        return False

    modal_text = await modal.text_content() or ""
    expected_filename = os.path.basename(cv_path)

    if expected_filename in modal_text:
        logger.info("Modal CV already matches: %s", expected_filename)
        return True

    logger.info("Modal CV mismatch — uploading tailored CV: %s", expected_filename)

    update_btn = page.locator('[data-qa="UpdateCvBtn"]')
    if not await update_btn.count():
        return False

    await update_btn.click()
    await asyncio.sleep(2)

    choose_btn = page.locator('text=Choose your CV file')
    if await choose_btn.is_visible(timeout=5000):
        async with page.expect_file_chooser(timeout=10000) as fc_info:
            await choose_btn.click()
        file_chooser = await fc_info.value
        from pathlib import Path as _Path
        _p = _Path(cv_path)
        if not _p.is_file():
            logger.error("Modal CV upload failed — file not found: %s", cv_path)
            return False
        await file_chooser.set_files({
            "name": _p.name,
            "mimeType": "application/pdf",
            "buffer": _p.read_bytes(),
        })
        logger.info("Uploaded tailored CV via modal file chooser")
        await asyncio.sleep(3)
        return True

    file_inputs = await page.locator("input[type='file']").all()
    if file_inputs:
        await upload_pdf(file_inputs[0], str(cv_path))
        logger.info("Uploaded tailored CV via hidden file input")
        await asyncio.sleep(3)
        return True

    logger.warning("Could not find file upload mechanism in CV modal")
    return False
