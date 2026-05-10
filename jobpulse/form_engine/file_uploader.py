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


class FileUploadError(Exception):
    """Upload finished without the file actually attaching to the input.

    Raised when the readback (``input.files.length``) is 0 or unreadable
    after a retry. Callers must decide whether to surface this to the
    human (Telegram alert + skip) or fail the application — the previous
    behavior of logging a warning and continuing silently caused jobs to
    submit without a CV attached.
    """

    def __init__(self, file_path: str, files_length: int, *,
                 retry_attempted: bool = False) -> None:
        self.file_path = file_path
        self.files_length = files_length
        self.retry_attempted = retry_attempted
        super().__init__(
            f"upload verification failed for {file_path} "
            f"(files.length={files_length}, retried={retry_attempted})"
        )


async def _readback_files_length(locator: Any) -> int:
    """Re-fire input/change events on the input + dropzone ancestors and
    return ``el.files.length`` so callers can verify the page actually
    accepted the file.
    """

    try:
        return await locator.evaluate(
            r"""el => {
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                let node = el.parentElement;
                for (let i = 0; node && i < 6; i++, node = node.parentElement) {
                    const cls = (node.className || '') + '';
                    if (/drop|upload|cv|resume|file/i.test(cls)) {
                        node.dispatchEvent(new Event('change', {bubbles: true}));
                        node.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                }
                return el.files ? el.files.length : 0;
            }"""
        )
    except Exception as exc:
        logger.debug("upload_pdf: files-length readback failed: %s", exc)
        return -1


async def _retry_via_attach_button(
    locator: Any, file_path: Any, p: Any,
) -> int:
    """When the first set_input_files lands an empty input (React widget
    rejected/swapped the underlying input), look for a visible 'Attach'
    trigger near the input, click it to surface the freshly-rendered
    input, and retry ``set_input_files`` on it.

    Returns the post-retry ``files.length`` so the caller can decide
    whether to raise ``FileUploadError``.
    """

    try:
        page = locator.page
    except Exception:  # pragma: no cover
        return -1

    try:
        # Walk up from the input to find a sibling/ancestor button whose
        # accessible text contains attach/upload/browse. Prefer visible
        # buttons over hidden ones — the input may be hidden behind a
        # styled button.
        clicked = await locator.evaluate(
            r"""el => {
                const labels = ['attach', 'upload', 'browse',
                                 'choose file', 'select file'];
                let node = el.parentElement;
                for (let i = 0; node && i < 8; i++, node = node.parentElement) {
                    const buttons = node.querySelectorAll(
                        'button, [role="button"], label, a'
                    );
                    for (const b of buttons) {
                        const text = (b.textContent || '').trim().toLowerCase();
                        if (labels.some(l => text.includes(l))) {
                            const r = b.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                b.click();
                                return text.slice(0, 60);
                            }
                        }
                    }
                }
                return '';
            }"""
        )
        if clicked:
            logger.info(
                "upload_pdf: triggered attach button %r — retrying set_input_files",
                clicked,
            )
            await asyncio.sleep(0.4)
    except Exception as exc:
        logger.debug("upload_pdf: attach-button trigger failed: %s", exc)

    # Re-resolve the input — the click above may have re-rendered it.
    try:
        await locator.set_input_files({
            "name": p.name,
            "mimeType": "application/pdf",
            "buffer": p.read_bytes(),
        })
    except Exception as exc:
        logger.warning("upload_pdf: retry set_input_files failed: %s", exc)
        return -1
    return await _readback_files_length(locator)


async def upload_pdf(locator: Any, file_path: str) -> None:
    """Upload a PDF to a file input. Raises ``FileUploadError`` if the
    file fails to attach after one retry.

    Robust against React drop-zone widgets (react-dropzone, custom
    dropzones) where set_input_files attaches the file but the page's
    component doesn't notice because it listens for `drop` on the zone
    wrapper rather than `change` on the input. We do four things, in
    order:

      1. set_input_files (Playwright dispatches input + change on the
         input automatically — sufficient for plain HTML forms).
      2. After the set, also fire `input` + `change` events bubbling up
         from the input. React-dropzones with onInputChange handlers
         pick this up.
      3. Re-fire `change` on each ancestor with a class matching
         drop|upload (the dropzone wrapper). Some widgets bind their
         listener there, not on the input.
      4. Verify via ``el.files.length``. On zero/failure, click the
         visible Attach button near the input to refresh the input and
         retry once. If still failing, raise ``FileUploadError`` so the
         caller can route to human review or fail the application
         instead of submitting without a CV attached.
    """
    from pathlib import Path
    p = Path(file_path)
    if not p.is_file():
        logger.error("upload_pdf: file not found: %s", file_path)
        raise FileNotFoundError(file_path)
    try:
        await locator.set_input_files({
            "name": p.name,
            "mimeType": "application/pdf",
            "buffer": p.read_bytes(),
        })
    except Exception as exc:
        logger.error("upload_pdf: set_input_files failed for %s: %s", p.name, exc)
        raise

    files_attached = await _readback_files_length(locator)

    if files_attached and files_attached > 0:
        logger.info(
            "upload_pdf: ✓ uploaded %s (%d bytes, files.length=%d)",
            p.name, p.stat().st_size, files_attached,
        )
        return

    # Not attached on first pass. Try the Attach-button retry once.
    logger.warning(
        "upload_pdf: first pass did not attach %s (files.length=%s) — "
        "retrying via Attach trigger",
        p.name, files_attached,
    )
    files_attached = await _retry_via_attach_button(locator, file_path, p)
    if files_attached and files_attached > 0:
        logger.info(
            "upload_pdf: ✓ retry succeeded for %s (files.length=%d)",
            p.name, files_attached,
        )
        return

    # Both attempts failed — raise so the caller can route to human review.
    logger.error(
        "upload_pdf: ✗ %s did not attach after retry (files.length=%s)",
        p.name, files_attached,
    )
    raise FileUploadError(
        str(p), files_length=files_attached if files_attached is not None else -1,
        retry_attempted=True,
    )


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
    logger.info(
        "upload_files: entry — cv=%s cl=%s",
        (cv_path or "(none)")[-80:], (cl_path or "(none)")[-80:],
    )
    await enable_optional_cover_letter_checkbox(page, get_accessible_name)
    cl_path = await resolve_lazy_cover_letter_path(page, cl_path, custom_answers)

    # Scan all file inputs with their accessible label, ID/name, the
    # nearest section heading (h2/h3/h4/legend), and surrounding text.
    # Greenhouse renders two file inputs both labelled "Attach" — they
    # are disambiguated only by the closest <h3>Resume/CV</h3> /
    # <h3>Cover Letter</h3> heading, so we read it explicitly here.
    file_meta = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll("input[type='file']")).map((el, idx) => {
            let ctx = '';
            let heading = '';
            let node = el.parentElement;
            for (let i = 0; node && i < 8; i++, node = node.parentElement) {
                if (!ctx) {
                    const t = (node.textContent || '').trim().slice(0, 500);
                    if (t.length > 20) ctx = t;
                }
                if (!heading) {
                    const h = node.querySelector('h2, h3, h4, h5, legend, [role="heading"]');
                    if (h) {
                        const ht = (h.textContent || '').trim();
                        if (ht && ht.length < 120) heading = ht;
                    }
                }
                if (ctx && heading) break;
            }
            return {
                idx,
                id: el.id || '',
                name: el.name || '',
                label: (el.labels?.[0]?.textContent?.trim() || el.getAttribute('aria-label') || ''),
                surrounding_text: ctx,
                heading: heading,
            };
        });
    }""")
    logger.info("upload_files: scanned %d file input(s) on page", len(file_meta))

    # First pass: classify each input as cv | cl | unknown using
    # identifiers, heading, and surrounding text.
    classified: list[tuple[dict, str]] = []
    for meta in file_meta:
        identifiers = f"{meta['label']} {meta['id']} {meta['name']}".lower()
        if "autofill" in identifiers:
            continue
        heading = (meta.get("heading") or "").lower()
        surrounding = meta.get("surrounding_text", "").lower()

        is_cl = (
            any(kw in identifiers for kw in ("cover", "cl", "letter"))
            or ("other" in identifiers and "attach" in identifiers)
            or ("cover letter" in heading)
            or ("cover letter" in surrounding)
            or (
                "additional" in surrounding
                and ("attachment" in surrounding or "document" in surrounding)
                and ("cover" in surrounding or "letter" in surrounding or "portfolio" in surrounding)
            )
        )
        is_cv = (
            any(kw in identifiers for kw in ("resume", "cv"))
            or any(kw in heading for kw in ("resume", "cv"))
        )
        if is_cl:
            kind = "cl"
        elif is_cv:
            kind = "cv"
        else:
            kind = "unknown"
        classified.append((meta, kind))

    # Greenhouse-style "two Attach" disambiguation: when we have exactly
    # two unknown-kind inputs (both labelled Attach) and no other
    # cv/cl signal, the first IS the CV and the second IS the CL by the
    # convention Greenhouse renders. Per the plan's Item 3b, this
    # ordering is consistent across Greenhouse application forms.
    unknown_metas = [m for m, k in classified if k == "unknown"]
    if (
        cl_path
        and len(unknown_metas) == 2
        and not any(k == "cl" for _, k in classified)
        and not any(k == "cv" for _, k in classified)
    ):
        new_classified: list[tuple[dict, str]] = []
        first_unknown = True
        for m, k in classified:
            if k == "unknown":
                new_classified.append((m, "cv" if first_unknown else "cl"))
                first_unknown = False
            else:
                new_classified.append((m, k))
        classified = new_classified
        logger.info(
            "upload_files: 2-Attach Greenhouse disambiguation — "
            "first input → CV, second → CL",
        )

    cv_uploaded = False
    cl_uploaded = False

    for meta, kind in classified:
        if meta["id"]:
            fi = page.locator(f'input[type="file"][id="{meta["id"]}"]').first
        elif meta["name"]:
            fi = page.locator(f'input[type="file"][name="{meta["name"]}"]').first
        else:
            fi = page.locator("input[type='file']").nth(meta["idx"])

        if kind == "cl" and cl_path and not cl_uploaded:
            try:
                await upload_pdf(fi, str(cl_path))
                cl_uploaded = True
            except FileUploadError as exc:
                logger.warning(
                    "upload_files: cover-letter upload failed (%s) — "
                    "continuing without CL",
                    exc,
                )
        elif kind in ("cv", "unknown") and cv_path and not cv_uploaded:
            try:
                await upload_pdf(fi, str(cv_path))
                cv_uploaded = True
            except FileUploadError:
                # CV is mandatory — re-raise so the caller can route to
                # human review or fail the application instead of
                # silently submitting without it.
                raise


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
