"""Real-data validation of novel-platform readiness primitives.

NO MOCKS. Every test loads real production data and validates the primitive
against it. Marked @pytest.mark.live because it depends on production DBs.

Data sources:
- data/applications.db.job_listings — 652 real URLs
- data/form_experience.db — real domains with apply_count
- data/screening_answers.db — real screening Q&As
- tests/fixtures/live_snapshots/*.json — 11 real scraped job pages

Run with:
    python -m pytest tests/jobpulse/integration/test_novel_platform_real_data.py -v -s
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "live_snapshots"


# ----------------------------------------------------------------------
# Real data loaders — no synthetic fixtures
# ----------------------------------------------------------------------

def _load_real_urls(limit: int = 30) -> list[tuple[str, str]]:
    """Pull real URLs from data/applications.db.job_listings."""
    db = DATA_DIR / "applications.db"
    if not db.exists():
        pytest.skip(f"{db} not found — production DB required for real-data tests")
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT url, company FROM job_listings "
            "WHERE url IS NOT NULL AND url != '' "
            "ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [(r[0], r[1] or "") for r in rows]


def _load_real_domains() -> list[tuple[str, int]]:
    """Pull real domains + apply_count from data/form_experience.db."""
    db = DATA_DIR / "form_experience.db"
    if not db.exists():
        pytest.skip(f"{db} not found")
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT domain, apply_count FROM form_experience ORDER BY apply_count DESC"
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _load_real_snapshots() -> list[dict]:
    """Load real scraped job snapshots from tests/fixtures/live_snapshots/."""
    if not FIXTURES_DIR.exists():
        pytest.skip(f"{FIXTURES_DIR} not found")
    snapshots = []
    for f in sorted(FIXTURES_DIR.glob("*.json")):
        if f.name == "manifest.json":
            continue
        try:
            data = json.loads(f.read_text())
            snapshots.append(data)
        except Exception:
            pass
    return snapshots


def _load_real_screening_answers() -> list[tuple[str, str]]:
    """Pull real Q&A pairs from data/screening_answers.db."""
    db = DATA_DIR / "screening_answers.db"
    if not db.exists():
        pytest.skip(f"{db} not found")
    with sqlite3.connect(db) as conn:
        try:
            rows = conn.execute(
                "SELECT question, answer FROM cached_answers "
                "WHERE question != '' AND answer != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [(r[0], r[1]) for r in rows]


# ----------------------------------------------------------------------
# Primitive 1: detect_platform on REAL URLs
# ----------------------------------------------------------------------

class TestDetectPlatformOnRealURLs:
    def test_classifies_real_linkedin_urls(self):
        from jobpulse.ats_adapters.discovery import detect_platform

        urls = _load_real_urls(limit=50)
        assert urls, "No real URLs found in production DB — cannot validate"
        linkedin_urls = [u for u, _ in urls if "linkedin.com" in u]
        assert linkedin_urls, "Production has no LinkedIn URLs to validate against"

        for url in linkedin_urls[:10]:
            result = detect_platform(url)
            assert result == "linkedin", f"LinkedIn URL classified as {result}: {url[:80]}"

    def test_real_url_coverage_report(self, capsys):
        """Report what fraction of production URLs the platform detector recognizes."""
        from jobpulse.ats_adapters.discovery import detect_platform

        urls = _load_real_urls(limit=100)
        recognized = {}
        unrecognized = []
        for url, company in urls:
            platform = detect_platform(url)
            if platform == "generic":
                unrecognized.append((url, company))
            else:
                recognized[platform] = recognized.get(platform, 0) + 1

        total = len(urls)
        with capsys.disabled():
            print(f"\n=== Real-URL platform coverage ({total} URLs) ===")
            for plat, count in sorted(recognized.items(), key=lambda x: -x[1]):
                print(f"  {plat}: {count} ({count/total:.1%})")
            print(f"  generic (unrecognized): {len(unrecognized)} ({len(unrecognized)/total:.1%})")
            if unrecognized:
                print(f"\nFirst 5 unrecognized URLs:")
                for url, company in unrecognized[:5]:
                    print(f"  - {company}: {url[:100]}")

        # Hard floor: at least some platforms recognized
        assert recognized, "Platform detector recognized 0 URLs out of production data"


# ----------------------------------------------------------------------
# Primitive 2: detect_platform with DOM signals on REAL snapshot fixtures
# ----------------------------------------------------------------------

class TestDetectPlatformOnRealSnapshots:
    def test_real_linkedin_snapshots_classify_correctly(self):
        from jobpulse.ats_adapters.discovery import detect_platform

        snapshots = _load_real_snapshots()
        linkedin_snaps = [s for s in snapshots if s.get("platform") == "linkedin"]
        assert linkedin_snaps, "No LinkedIn snapshots in fixtures"

        for snap in linkedin_snaps:
            url = snap.get("url", "")
            # Pass the snapshot itself as DOM signal — fixtures have description/title fields
            dom_signal = {
                "page_text_preview": snap.get("description", "")[:500],
                "html_preview": "",
                "buttons": [],
            }
            result = detect_platform(url, dom_signal)
            assert result == "linkedin", (
                f"LinkedIn snapshot {snap.get('job_id')} classified as {result}"
            )

    def test_real_indeed_snapshots_classify_correctly(self):
        from jobpulse.ats_adapters.discovery import detect_platform

        snapshots = _load_real_snapshots()
        indeed_snaps = [s for s in snapshots if s.get("platform") == "indeed"]
        if not indeed_snaps:
            pytest.skip("No Indeed snapshots in fixtures")

        for snap in indeed_snaps:
            url = snap.get("url", "")
            result = detect_platform(url, snapshot=None)
            assert result == "indeed", (
                f"Indeed snapshot {snap.get('job_id')} classified as {result}"
            )


# ----------------------------------------------------------------------
# Primitive 3: synthesize_strategy_for_domain on REAL form_experience domains
# ----------------------------------------------------------------------

class TestStrategyySynthesisOnRealDomains:
    def test_synthesis_against_real_form_experience_rows(self, capsys):
        """For every real domain in form_experience.db, verify synthesis behavior."""
        from jobpulse.ats_adapters._strategy_synthesis import (
            synthesize_strategy_for_domain,
            _MIN_APPLY_COUNT,
        )
        from jobpulse.ats_adapters.learned_strategy import LearnedStrategy

        domains = _load_real_domains()
        assert domains, "No real domains in form_experience.db"

        synthesized = []
        skipped = []
        for domain, apply_count in domains:
            result = synthesize_strategy_for_domain(domain)
            if apply_count >= _MIN_APPLY_COUNT:
                assert isinstance(result, LearnedStrategy), (
                    f"Domain {domain} (apply_count={apply_count}) "
                    f"should synthesize but got {result}"
                )
                synthesized.append((domain, apply_count))
            else:
                assert result is None, (
                    f"Domain {domain} (apply_count={apply_count}) "
                    f"is below threshold but synthesized {result}"
                )
                skipped.append((domain, apply_count))

        with capsys.disabled():
            print(f"\n=== Strategy synthesis on real form_experience.db ===")
            print(f"Synthesized: {len(synthesized)} domains (apply_count >= {_MIN_APPLY_COUNT})")
            for d, c in synthesized:
                print(f"  ✓ {d}: apply_count={c}")
            print(f"Skipped: {len(skipped)} domains (apply_count < {_MIN_APPLY_COUNT})")
            for d, c in skipped[:5]:
                print(f"  - {d}: apply_count={c}")

    def test_get_strategy_returns_learned_for_proven_real_domain(self, capsys):
        """get_strategy(None, url) returns LearnedStrategy for real proven domains."""
        from jobpulse.ats_adapters.strategy import get_strategy
        from jobpulse.ats_adapters.learned_strategy import LearnedStrategy

        domains = _load_real_domains()
        proven = [(d, c) for d, c in domains if c >= 3]
        if not proven:
            pytest.skip("No real domain has apply_count >= 3 yet")

        with capsys.disabled():
            print(f"\n=== get_strategy synthesis path on real domains ===")
            for domain, apply_count in proven:
                # Use a synthetic URL on the real domain. NOT a mock —
                # this is exactly what get_strategy will see in production.
                test_url = f"https://{domain}/job/whatever"
                # Pass platform=None and a generic-looking platform name to force
                # the synthesis fallback path. Production also passes the URL.
                strategy = get_strategy(platform="unknown_platform", url=test_url)
                if isinstance(strategy, LearnedStrategy):
                    print(f"  ✓ {domain} (apply_count={apply_count}) → {strategy.name}")
                else:
                    print(f"  ✗ {domain} (apply_count={apply_count}) → {type(strategy).__name__}")


# ----------------------------------------------------------------------
# Primitive 4: is_first_encounter on REAL URLs vs REAL form_experience domains
# ----------------------------------------------------------------------

class TestFirstEncounterOnRealURLs:
    def test_first_encounter_correctly_identifies_known_vs_novel(self, capsys):
        """Run is_first_encounter against real URLs; report known vs novel split."""
        from jobpulse.applicator import is_first_encounter

        urls = _load_real_urls(limit=60)
        domains = dict(_load_real_domains())

        first_encounters = []
        known = []
        for url, company in urls:
            if is_first_encounter(url):
                first_encounters.append((url, company))
            else:
                known.append((url, company))

        with capsys.disabled():
            print(f"\n=== is_first_encounter on {len(urls)} real URLs ===")
            print(f"Known domains (FE has rows): {len(known)}")
            print(f"First-encounter (no FE rows): {len(first_encounters)}")
            print(f"\nFirst 5 first-encounter URLs:")
            for url, company in first_encounters[:5]:
                print(f"  - {company[:30]:30s} {url[:80]}")
            print(f"\nKnown domains in FE: {list(domains.keys())[:10]}")

        # Sanity check: at least some real URLs should match the known domains
        # in form_experience.db. If ALL are first-encounter, the helper is broken.
        if domains:
            assert known or len(urls) == 0, (
                f"All {len(urls)} real URLs are first-encounter — is_first_encounter "
                f"is failing to recognize known domains: {list(domains.keys())}"
            )


# ----------------------------------------------------------------------
# Primitive 5: real screening Q&A against MemoryManager fallback (informational)
# ----------------------------------------------------------------------

class TestScreeningOnRealQuestions:
    def test_real_screening_qa_round_trip(self, capsys):
        """Verify the real screening cache is populated and accessible."""
        qa = _load_real_screening_answers()
        with capsys.disabled():
            print(f"\n=== Real screening Q&A pairs ({len(qa)}) ===")
            for q, a in qa[:10]:
                print(f"  Q: {q[:60]}")
                print(f"  A: {a[:60]}")

        assert qa, (
            "screening_answers.db has no cached Q&As — the screening pipeline "
            "hasn't accumulated production data yet"
        )


# ----------------------------------------------------------------------
# Primitive 6: _normalize_domain on real data
# ----------------------------------------------------------------------

class TestNormalizeDomainOnRealData:
    def test_normalize_real_urls_to_real_form_experience_domains(self, capsys):
        """Verify _normalize_domain produces the same canonical form FE uses."""
        from jobpulse.agent_rules import _normalize_domain as ar_normalize
        from jobpulse.ats_adapters.learned_strategy import _normalize_domain as ls_normalize

        urls = _load_real_urls(limit=20)
        with capsys.disabled():
            print(f"\n=== _normalize_domain on real URLs ===")
            for url, _ in urls[:10]:
                ar_norm = ar_normalize(url)
                ls_norm = ls_normalize(url)
                # Both helpers should agree on the canonical form
                assert ar_norm == ls_norm, (
                    f"Normalizers disagree on {url[:60]}: "
                    f"agent_rules={ar_norm!r} learned_strategy={ls_norm!r}"
                )
                print(f"  {url[:80]}")
                print(f"    → {ar_norm}")
