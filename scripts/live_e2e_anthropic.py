#!/usr/bin/env python3
"""Live end-to-end dry-run driver for the Anthropic Greenhouse URL.

One-shot purpose-built driver that exercises apply_job() end-to-end with
dry_run=True so all learning chains fire (CorrectionCapture, AgentRulesDB,
post_apply_hook, strategy_reflector, OptimizationEngine).

Goals (per docs/superpowers/plans/2026-05-10-live-e2e-dry-run.md):
- Run apply_job(URL, dry_run=True) to completion without raising.
- Generate CV at data/applications/Anthropic/Yash_Bishnoi_Anthropic.pdf.
- Fire confirm_application() so all learning chains write rows.
- Every LLM call routes to Kimi (Moonshot), no Ollama, no OpenAI.
"""
from __future__ import annotations

import os
import sys
import json
import time
import traceback
from datetime import datetime, UTC
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# --- Force Kimi-only routing (no Ollama, no OpenAI fallback) -------
os.environ["LLM_PROVIDER"] = "openai"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("JOBPULSE_TEST_MODE", None)
os.environ.pop("UNIFIED_FORM_ENGINE", None)
os.environ["JOB_AUTOPILOT_AUTO_SUBMIT"] = "false"

URL = "https://job-boards.greenhouse.io/anthropic/jobs/4017331008"


def _fetch_jd(url: str) -> tuple[str, str, str]:
    import httpx
    from bs4 import BeautifulSoup

    with httpx.Client(timeout=20, follow_redirects=True) as c:
        r = c.get(url, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title = ""
    if soup.h1:
        title = soup.h1.get_text(strip=True)
    if not title and soup.title:
        # "Job Application for {title} at {company}"
        t = soup.title.string or ""
        if " at " in t and "Job Application for " in t:
            title = t.split(" at ")[0].replace("Job Application for ", "").strip()

    path_parts = urlparse(url).path.strip("/").split("/")
    company = ""
    if soup.title:
        t = soup.title.string or ""
        if " at " in t:
            company = t.rsplit(" at ", 1)[-1].strip()
    if not company and path_parts:
        company = path_parts[0].replace("-", " ").title()

    jd_parts: list[str] = []
    for tag in soup.find_all(["h2", "h3", "p", "li"]):
        txt = tag.get_text(separator=" ", strip=True)
        if txt and len(txt) > 10:
            jd_parts.append(txt)
    jd_text = "\n".join(jd_parts)[:8000]
    return title or "Research Engineer", company or "Anthropic", jd_text


def _step(name: str) -> None:
    print(f"\n{'='*70}\n[STEP] {name}\n{'='*70}", flush=True)


def main() -> int:
    start = time.time()
    _step("0. Environment")
    print(f"  KimiAI_API_KEY set: {bool(os.environ.get('KimiAI_API_KEY'))}", flush=True)
    print(f"  OPENAI_API_KEY removed: {not bool(os.environ.get('OPENAI_API_KEY'))}", flush=True)
    print(f"  LLM_PROVIDER={os.environ.get('LLM_PROVIDER')}", flush=True)
    print(f"  JOBPULSE_TEST_MODE={os.environ.get('JOBPULSE_TEST_MODE', 'unset')}", flush=True)

    _step("1. Fetch JD from URL")
    print(f"  URL: {URL}")
    title, company, jd_text = _fetch_jd(URL)
    print(f"  title={title!r}")
    print(f"  company={company!r}")
    print(f"  jd_text_len={len(jd_text)}")

    _step("2. analyze_jd → JobListing")
    from jobpulse.jd_analyzer import analyze_jd

    # platform = source job-board (linkedin/indeed/reed/generic).
    # Anthropic Greenhouse is a direct ATS link — the source is generic.
    # ATS detection (greenhouse) happens inside analyze_jd via detect_ats_platform.
    listing = analyze_jd(
        url=URL,
        title=title,
        company=company,
        platform="generic",
        jd_text=jd_text,
    )
    print(f"  job_id={listing.job_id[:12]}")
    print(f"  ats_platform={listing.ats_platform}")
    print(f"  easy_apply={listing.easy_apply}")
    print(f"  required_skills({len(listing.required_skills)})={listing.required_skills[:6]}")
    print(f"  preferred_skills({len(listing.preferred_skills)})={listing.preferred_skills[:6]}")
    print(f"  location={listing.location}")
    print(f"  seniority={listing.seniority}")

    _step("3. SkillGraphStore.pre_screen_jd (Gates 1-3)")
    from jobpulse.skill_graph_store import SkillGraphStore

    sgs = SkillGraphStore()
    screen = sgs.pre_screen_jd({
        "required_skills": listing.required_skills,
        "preferred_skills": listing.preferred_skills,
        "description_raw": jd_text,
    })
    print(f"  tier={screen.tier}")
    print(f"  gate1_passed={screen.gate1_passed} kill_reason={screen.gate1_kill_reason}")
    print(f"  gate2_passed={screen.gate2_passed}")
    print(f"  gate3_score={screen.gate3_score:.1f}%")
    print(f"  best_projects={[p.name for p in (screen.best_projects or [])][:4]}")

    _step("4. generate_materials (CV + Notion)")
    from jobpulse.scan_pipeline import generate_materials
    from jobpulse.job_db import JobDB

    db = JobDB()
    repos: list[dict] = []
    notion_failures: list[str] = []
    bundle = generate_materials(listing, screen, db, repos, notion_failures)
    print(f"  cv_path={bundle.cv_path}")
    print(f"  cl_path={bundle.cover_letter_path}")
    print(f"  ats_score={bundle.ats_score:.1f}")
    print(f"  notion_page_id={bundle.notion_page_id}")
    print(f"  matched_projects={bundle.matched_project_names}")
    if notion_failures:
        print(f"  notion_failures: {notion_failures}")

    if not bundle.cv_path:
        print("\n[FATAL] No CV generated — apply_job cannot proceed.")
        return 2

    _step("5. apply_job(dry_run=True)")
    from jobpulse.applicator import apply_job, confirm_application
    from jobpulse.scan_pipeline import _build_screening_context

    custom_answers: dict = {"_job_context": _build_screening_context(listing)}

    cl_generator = None
    if not bundle.cover_letter_path:
        def cl_generator():
            try:
                from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
                from jobpulse.project_portfolio import get_best_projects_for_jd
                project_dicts = get_best_projects_for_jd(
                    list(listing.required_skills or []),
                    list(listing.preferred_skills or []),
                )
                return generate_cover_letter_pdf(
                    company=listing.company,
                    role=listing.title,
                    matched_projects=project_dicts,
                    required_skills=list(listing.required_skills or []),
                )
            except Exception as exc:
                print(f"  [cl_generator] {type(exc).__name__}: {exc}")
                return None

    job_context: dict = {
        "job_id": listing.job_id,
        "company": listing.company,
        "title": listing.title,
        "notion_page_id": bundle.notion_page_id,
        "cv_path": str(bundle.cv_path),
        "cover_letter_path": str(bundle.cover_letter_path) if bundle.cover_letter_path else None,
        "match_tier": screen.tier,
        "ats_score": bundle.ats_score,
        "matched_projects": bundle.matched_project_names,
    }

    try:
        result = apply_job(
            url=URL,
            ats_platform="greenhouse",
            cv_path=bundle.cv_path,
            cover_letter_path=bundle.cover_letter_path,
            cl_generator=cl_generator,
            custom_answers=custom_answers,
            job_context=job_context,
            dry_run=True,
        )
    except Exception as exc:
        print(f"\n[apply_job EXCEPTION] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 3

    print("\n  apply_job result:")
    for k in sorted((result or {}).keys()):
        if k.startswith("_"):
            continue
        v = result[k]
        s = repr(v)
        if len(s) > 240:
            s = s[:240] + "…"
        print(f"    {k}: {s}")

    _step("6. confirm_application (mandatory)")
    if not result.get("success"):
        print("  [skip] apply_job did not return success — confirm_application not fired")
    else:
        try:
            confirm_result = confirm_application(
                dry_run_result=result,
                url=URL,
                cv_path=bundle.cv_path,
                cover_letter_path=bundle.cover_letter_path,
                job_context=job_context,
                ats_platform="greenhouse",
            )
            print(f"  confirm_application success={confirm_result.get('success')}")
            for k in sorted(confirm_result.keys()):
                if k in {"success", "screening_results"}:
                    continue
                v = confirm_result[k]
                s = repr(v)
                if len(s) > 240:
                    s = s[:240] + "…"
                print(f"    {k}: {s}")
        except Exception as exc:
            print(f"  [confirm_application EXCEPTION] {type(exc).__name__}: {exc}")
            traceback.print_exc()

    _step("DONE")
    duration = time.time() - start
    print(f"  duration={duration:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
