"""Dry-run the full job autopilot pipeline for a single LinkedIn URL.

Usage:
    python scripts/dry_run_single_job.py https://www.linkedin.com/jobs/view/4395282747

Steps:
  1. Fetch JD text from LinkedIn guest page
  2. Analyze JD → JobListing
  3. Pre-screen (Gates 0-3)
  4. Gate 4A (JD quality, company blocklist)
  5. Generate CV PDF
  6. ATS Score
  7. Gate 4B (CV scrutiny)
  8. Apply via Ralph Loop (dry_run=True) — stops at Review, never submits
  9. Send results + screenshots to Telegram Jobs bot
"""

from __future__ import annotations

import sys
import time
import re

from shared.logging_config import get_logger

logger = get_logger(__name__)


def fetch_jd_from_linkedin(url: str) -> dict:
    """Fetch job details from LinkedIn guest page (no login needed)."""
    import httpx
    from bs4 import BeautifulSoup

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    headers = {"User-Agent": ua, "Accept-Language": "en-GB,en;q=0.9"}

    with httpx.Client(timeout=20, headers=headers, follow_redirects=True) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"LinkedIn returned status {resp.status_code}")

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        title_el = soup.select_one(
            "h1.top-card-layout__title, h2.top-card-layout__title, "
            "h1.topcard__title, h1"
        )
        title = title_el.get_text(strip=True) if title_el else "Unknown"

        # Company
        company_el = soup.select_one(
            "a.topcard__org-name-link, .top-card-layout__card span.topcard__flavor, "
            "a.top-card-layout__company-url"
        )
        company = company_el.get_text(strip=True) if company_el else "Unknown"

        # Location
        loc_el = soup.select_one(
            ".top-card-layout__bullet, .topcard__flavor--bullet, "
            "span.topcard__flavor:nth-of-type(2)"
        )
        location = loc_el.get_text(strip=True) if loc_el else "United Kingdom"

        # Description
        desc_el = soup.select_one(
            ".show-more-less-html__markup, .description__text, #job-details"
        )
        description = desc_el.get_text(separator="\n", strip=True)[:5000] if desc_el else ""

        # Easy Apply detection
        page_text = soup.get_text().lower()
        easy_apply = "easy apply" in page_text

    return {
        "title": title,
        "company": company,
        "url": url,
        "location": location,
        "description": description,
        "platform": "linkedin",
        "easy_apply": easy_apply,
    }


def run_full_pipeline(url: str) -> None:
    """Run the complete job autopilot pipeline in dry-run mode."""
    from jobpulse.telegram_bots import send_jobs, send_jobs_photo
    from pathlib import Path

    send_jobs(f"🔬 FULL DRY RUN starting for:\n{url}")
    start = time.monotonic()

    # --- Step 1: Fetch JD ---
    print("\n" + "=" * 60)
    print("STEP 1: Fetching JD from LinkedIn...")
    print("=" * 60)
    try:
        raw = fetch_jd_from_linkedin(url)
    except Exception as exc:
        msg = f"❌ Failed to fetch JD: {exc}"
        print(msg)
        send_jobs(msg)
        return

    print(f"  Title:    {raw['title']}")
    print(f"  Company:  {raw['company']}")
    print(f"  Location: {raw['location']}")
    print(f"  Easy Apply: {raw['easy_apply']}")
    print(f"  JD length: {len(raw['description'])} chars")
    send_jobs(
        f"📋 JD Fetched:\n"
        f"Title: {raw['title']}\n"
        f"Company: {raw['company']}\n"
        f"Location: {raw['location']}\n"
        f"Easy Apply: {raw['easy_apply']}\n"
        f"JD: {len(raw['description'])} chars"
    )

    if not raw["description"]:
        msg = "❌ Empty JD text — LinkedIn may have blocked guest access. Aborting."
        print(msg)
        send_jobs(msg)
        return

    # --- Step 2: Analyze JD ---
    print("\n" + "=" * 60)
    print("STEP 2: Analyzing JD (skill extraction)...")
    print("=" * 60)
    from jobpulse.jd_analyzer import analyze_jd
    listing = analyze_jd(
        url=raw["url"],
        title=raw["title"],
        company=raw["company"],
        platform="linkedin",
        jd_text=raw["description"],
        apply_url=raw["url"],
    )
    # Force easy_apply from our detection
    listing.easy_apply = raw["easy_apply"]

    print(f"  Required skills: {listing.required_skills[:10]}")
    print(f"  Preferred skills: {listing.preferred_skills[:5]}")
    print(f"  ATS platform: {listing.ats_platform}")
    send_jobs(
        f"🔍 JD Analysis:\n"
        f"Required: {', '.join(listing.required_skills[:8])}\n"
        f"Preferred: {', '.join(listing.preferred_skills[:5])}\n"
        f"ATS: {listing.ats_platform}"
    )

    # --- Step 3: Pre-screen (Gates 0-3) ---
    print("\n" + "=" * 60)
    print("STEP 3: Pre-screening (Gates 0-3)...")
    print("=" * 60)

    # Gate 0
    from jobpulse.recruiter_screen import gate0_title_relevance
    from jobpulse.job_scanner import load_search_config
    search_config = load_search_config()
    gate0_config = {
        "titles": search_config.titles if hasattr(search_config, "titles") else search_config.get("titles", []),
        "exclude_keywords": search_config.exclude_keywords if hasattr(search_config, "exclude_keywords") else search_config.get("exclude_keywords", []),
    }
    gate0_pass = gate0_title_relevance(listing.title, raw["description"][:500], gate0_config)
    print(f"  Gate 0 (title relevance): {'✅ PASS' if gate0_pass else '❌ FAIL'}")
    if not gate0_pass:
        send_jobs(f"🚫 Gate 0 REJECTED: Title '{listing.title}' not relevant to search config")
        print("  ⚠️  Gate 0 failed but continuing for analysis...")

    # Gates 1-3
    try:
        from jobpulse.skill_graph_store import SkillGraphStore
        store = SkillGraphStore()
        screen = store.pre_screen_jd(listing)
        print(f"  Gate 1 (kill signals): {'✅ PASS' if not screen.gate1_kill_reason else '❌ ' + screen.gate1_kill_reason}")
        print(f"  Gate 2 (must-haves): {'✅ PASS' if not screen.gate2_fail_reason else '❌ ' + screen.gate2_fail_reason}")
        print(f"  Gate 3 (competitiveness): {screen.gate3_score}/100 → tier: {screen.tier}")
        print(f"  Matched skills: {len(screen.matched_skills)} | Missing: {len(screen.missing_skills)}")
        if screen.best_projects:
            print(f"  Top projects: {[p.name for p in screen.best_projects[:3]]}")

        send_jobs(
            f"🏗️ Pre-Screen (Gates 0-3):\n"
            f"Gate 0: {'✅' if gate0_pass else '❌'}\n"
            f"Gate 1: {'✅' if not screen.gate1_kill_reason else '❌ ' + screen.gate1_kill_reason}\n"
            f"Gate 2: {'✅' if not screen.gate2_fail_reason else '❌ ' + screen.gate2_fail_reason}\n"
            f"Gate 3: {screen.gate3_score}/100 → {screen.tier}\n"
            f"Matched: {len(screen.matched_skills)} | Missing: {len(screen.missing_skills)}"
        )
    except Exception as exc:
        print(f"  ⚠️  Pre-screen failed: {exc} — continuing without")
        screen = None

    # --- Step 4: Gate 4A ---
    print("\n" + "=" * 60)
    print("STEP 4: Gate 4A (JD quality + company check)...")
    print("=" * 60)

    from jobpulse.gate4_quality import check_jd_quality, check_company_background
    from jobpulse.company_blocklist import detect_spam_company, BlocklistCache

    jd_text_for_check = getattr(listing, "description_raw", "") or raw["description"]
    jd_quality = check_jd_quality(
        jd_text_for_check, listing.required_skills + listing.preferred_skills
    )
    print(f"  JD quality: {'✅ PASS' if jd_quality.passed else '❌ FAIL — ' + jd_quality.reason}")

    blocklist = BlocklistCache()
    try:
        blocklist.refresh()
    except Exception:
        pass
    blocked = blocklist.is_blocked(listing.company)
    print(f"  Company blocklist: {'❌ BLOCKED' if blocked else '✅ Not blocked'}")

    spam = detect_spam_company(listing.company)
    print(f"  Spam detection: {'❌ SPAM — ' + spam.reason if spam.is_spam else '✅ Clean'}")

    from jobpulse.job_db import JobDB
    db = JobDB()
    try:
        past_apps = db.get_applications_by_company(listing.company)
    except (AttributeError, Exception):
        past_apps = []
    bg = check_company_background(listing.company, past_apps)
    print(f"  Previously applied: {bg.previously_applied}")
    print(f"  Generic name: {bg.is_generic}")

    gate4a_pass = jd_quality.passed and not blocked and not spam.is_spam
    send_jobs(
        f"🔎 Gate 4A:\n"
        f"JD quality: {'✅' if jd_quality.passed else '❌ ' + jd_quality.reason}\n"
        f"Blocklist: {'❌' if blocked else '✅'}\n"
        f"Spam: {'❌ ' + spam.reason if spam.is_spam else '✅'}\n"
        f"Previously applied: {bg.previously_applied}"
    )

    # --- Step 5: Generate CV ---
    print("\n" + "=" * 60)
    print("STEP 5: Generating CV PDF...")
    print("=" * 60)

    from jobpulse.config import DATA_DIR
    from jobpulse.cv_templates.generate_cv import generate_cv_pdf, build_extra_skills, get_role_profile, BASE_SKILLS, EDUCATION, EXPERIENCE

    # Sync Notion Skill Tracker
    try:
        from jobpulse.skill_tracker_notion import sync_verified_to_profile
        sync_verified_to_profile()
        print("  Notion Skill Tracker synced")
    except Exception as exc:
        print(f"  ⚠️  Notion sync failed: {exc}")

    # Dynamic project selection
    from jobpulse.project_portfolio import get_best_projects_for_jd
    matched_projects = get_best_projects_for_jd(
        listing.required_skills, listing.preferred_skills,
    )
    print(f"  Matched projects: {[p['title'] for p in matched_projects[:4]]}")

    extra_skills = build_extra_skills(listing.required_skills, listing.preferred_skills)

    role_profile = get_role_profile(listing.title)
    cv_path = generate_cv_pdf(
        company=listing.company,
        location=listing.location or "United Kingdom",
        tagline=role_profile.get("tagline"),
        summary=role_profile.get("summary"),
        projects=matched_projects,
        extra_skills=extra_skills if extra_skills else None,
        output_dir=str(DATA_DIR / "applications" / listing.job_id),
    )
    print(f"  CV generated: {cv_path}")

    # --- Step 6: ATS Score ---
    print("\n" + "=" * 60)
    print("STEP 6: ATS Scoring...")
    print("=" * 60)
    from jobpulse.ats_scorer import score_ats

    cv_parts = [
        "PROFESSIONAL SUMMARY Software Engineer Python AI ML",
        "TECHNICAL SKILLS " + " ".join(BASE_SKILLS.values()),
    ]
    if extra_skills:
        cv_parts.append(" ".join(extra_skills.values()))
    cv_parts.append("PROJECTS " + " ".join(
        p["title"] + " " + " ".join(p["bullets"]) for p in matched_projects
    ))
    cv_parts.append("EXPERIENCE " + " ".join(
        e["title"] + " " + " ".join(e["bullets"]) for e in EXPERIENCE
    ))
    cv_parts.append("EDUCATION " + " ".join(
        e["degree"] + " " + e["institution"] for e in EDUCATION
    ))
    cv_text = " ".join(cv_parts)
    ats_score_obj = score_ats(jd_skills, cv_text)
    ats_score = ats_score_obj.total
    print(f"  ATS Score: {ats_score:.1f}%")
    print(f"  Breakdown: keyword={ats_score_obj.keyword_score:.1f} section={ats_score_obj.section_score:.1f} format={ats_score_obj.format_score:.1f}")

    from jobpulse.cv_tailor import determine_match_tier
    tier = determine_match_tier(ats_score)
    print(f"  Match tier: {tier}")

    send_jobs(
        f"📊 CV Generated + ATS Score:\n"
        f"CV: {cv_path}\n"
        f"ATS: {ats_score:.1f}% → {tier}\n"
        f"Projects: {', '.join(p['title'] for p in matched_projects[:3])}"
    )

    # Upload CV screenshot to Telegram
    if cv_path and Path(cv_path).exists():
        send_jobs_photo(str(cv_path), caption=f"CV for {listing.company} — ATS {ats_score:.0f}%")

    # --- Step 7: Gate 4B (CV scrutiny) ---
    print("\n" + "=" * 60)
    print("STEP 7: Gate 4B (CV quality scrutiny)...")
    print("=" * 60)
    from jobpulse.gate4_quality import scrutinize_cv_deterministic, scrutinize_cv_llm

    b1_result = scrutinize_cv_deterministic(cv_text)
    print(f"  B1 (deterministic): {b1_result.status}")
    if b1_result.warnings:
        print(f"    Warnings: {'; '.join(b1_result.warnings[:3])}")

    b2_notes = ""
    if b1_result.status in ("clean", "acceptable"):
        try:
            b2_result = scrutinize_cv_llm(
                cv_text, listing.title, listing.company,
                listing.required_skills, listing.preferred_skills,
            )
            print(f"  B2 (LLM FAANG review): {b2_result.score}/10")
            if b2_result.weaknesses:
                print(f"    Weaknesses: {'; '.join(b2_result.weaknesses[:3])}")
            if b2_result.needs_review:
                b2_notes = f"Score {b2_result.score}/10 — {'; '.join(b2_result.weaknesses[:2])}"
        except Exception as exc:
            print(f"  ⚠️  B2 LLM review failed: {exc}")

    send_jobs(
        f"✍️ Gate 4B (CV Scrutiny):\n"
        f"B1: {b1_result.status} ({len(b1_result.warnings)} warnings)\n"
        f"B2: {b2_notes if b2_notes else '✅ Passed'}"
    )

    # --- Step 8: Apply via Ralph Loop (dry run) ---
    print("\n" + "=" * 60)
    print("STEP 8: Ralph Loop Apply (DRY RUN — will NOT submit)...")
    print("=" * 60)

    from jobpulse.applicator import classify_action
    action = classify_action(ats_score, listing.easy_apply)
    print(f"  Action: {action} (ATS={ats_score:.1f}%, easy_apply={listing.easy_apply})")

    if not listing.easy_apply:
        print("  Note: Not Easy Apply — pipeline will click Apply, capture redirect, and use external ATS adapter")

    send_jobs(
        f"🚀 Starting Ralph Loop Apply (DRY RUN)\n"
        f"Action: {action}\n"
        f"Will stop at Review — waiting for human check"
    )

    # Generate cover letter lazily (same as production)
    from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
    def _cl_generator():
        return generate_cover_letter_pdf(
            company=listing.company,
            role=listing.title,
            location=listing.location or "United Kingdom",
            matched_projects=matched_projects,
            required_skills=listing.required_skills + listing.preferred_skills,
            output_dir=str(DATA_DIR / "applications" / listing.job_id),
        )

    from jobpulse.ralph_loop.loop import ralph_apply_sync
    result = ralph_apply_sync(
        url=url,
        ats_platform=listing.ats_platform or "linkedin",
        cv_path=Path(cv_path),
        cl_generator=_cl_generator,
        custom_answers=None,
        dry_run=True,
    )

    elapsed = time.monotonic() - start

    # --- Final Summary ---
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    success = result.get("success", False)
    error = result.get("error", "")
    iterations = result.get("ralph_iterations", 1)
    dry_run_flag = result.get("dry_run", False)

    print(f"  Success: {success}")
    print(f"  Dry run: {dry_run_flag}")
    print(f"  Iterations: {iterations}")
    print(f"  Error: {error or 'None'}")
    print(f"  Total time: {elapsed:.1f}s")

    verdict_emoji = "✅" if success else "❌"
    summary = (
        f"{verdict_emoji} FULL DRY RUN COMPLETE\n"
        f"Job: {listing.title} @ {listing.company}\n"
        f"ATS: {ats_score:.1f}% | Tier: {tier}\n"
        f"Success: {success}\n"
        f"Iterations: {iterations}\n"
        f"Error: {error or 'None'}\n"
        f"Time: {elapsed:.1f}s"
    )
    send_jobs(summary)

    # Send final screenshot
    screenshot = result.get("screenshot")
    if screenshot and Path(str(screenshot)).exists():
        send_jobs_photo(str(screenshot), caption=f"Final state — {listing.company}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/dry_run_single_job.py <linkedin_url>")
        sys.exit(1)
    run_full_pipeline(sys.argv[1])
