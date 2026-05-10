"""Slice S1 live evidence collector.

Runs the real ScreeningPipeline against UK + US JD contexts (and a
profile-state-mutation case) and writes real rows to:
  - data/screening_semantic_cache.db
  - data/db_observability.db (via observe_lookup)

Audit rule 1 acceptable evidence:
  - "A row in a real data/*.db file (queried with sqlite3, not mocked)."

This script does NOT mock anything. It uses the production cache, the
production embedder (BGE-M3 1024-dim), and the production Qdrant.
Output: a JSON evidence summary + sqlite3 query results printed to
stdout.

Run from repo root::

    python scripts/audit_s1_live_evidence.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Ensure repo root on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env so KimiAI_API_KEY etc. are available — runner does this for us
# normally, but a direct script needs it explicit.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

from jobpulse.screening_pipeline import ScreeningPipeline


VISA_QUESTION = "Will you now or in the future require employment visa sponsorship?"


# Profile A: UK-based candidate, Graduate Visa (current production state).
PROFILE_UK_GRAD = {
    "visa_status": "Graduate Visa",
    "visa_expiry": "2028-05-01",
    "right_to_work": True,
    "visa_type": "Graduate Visa",
    "current_salary": "20000",
    "expected_salary": "38000",
    "notice_period": "1 month",
    "location": "Dundee, UK",
    "willing_to_relocate": True,
    "languages": "English, Hindi",
    "english_proficiency": "Native",
}


# Profile B: same person hypothetically on ILR — invalidation test.
PROFILE_UK_ILR = dict(PROFILE_UK_GRAD, visa_status="Indefinite Leave to Remain", visa_expiry="")


JOB_CTX_UK = {
    "country": "United Kingdom",
    "location": "London, UK",
    "currency": "GBP",
    "role_level": "mid",
}


JOB_CTX_US = {
    "country": "United States",
    "location": "San Francisco, CA, USA",
    "currency": "USD",
    "role_level": "mid",
}


def _row_count(db: Path, table: str) -> int:
    if not db.exists():
        return 0
    with sqlite3.connect(str(db)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _lookup_rows(db: Path, since_id: int) -> list[dict[str, Any]]:
    """Pull rows added since a baseline row id."""
    if not db.exists():
        return []
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, db_name, table_name, key_hash, hit, value_repr, status,"
            " field_label, intended, actual, consumed_ts"
            " FROM lookups WHERE id > ? ORDER BY id",
            (since_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def _cache_rows_for(db: Path, qid_substr: str) -> list[dict[str, Any]]:
    if not db.exists():
        return []
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT qdrant_id, question_text, answer, profile_state_hash,"
            " jd_context_hash, intent, created_at"
            " FROM screening_semantic_cache"
            " WHERE question_text LIKE ?",
            (f"%{qid_substr}%",),
        )
        return [dict(r) for r in cur.fetchall()]


def main() -> int:
    cache_db = ROOT / "data" / "screening_semantic_cache.db"
    obs_db = ROOT / "data" / "db_observability.db"

    baseline_obs_id = 0
    if obs_db.exists():
        with sqlite3.connect(str(obs_db)) as conn:
            row = conn.execute("SELECT MAX(id) FROM lookups").fetchone()
            baseline_obs_id = row[0] or 0

    baseline_cache_count = _row_count(cache_db, "screening_semantic_cache")

    print(f"== Slice S1 live evidence ==")
    print(f"baseline screening_semantic_cache rows: {baseline_cache_count}")
    print(f"baseline db_observability.lookups max id: {baseline_obs_id}")
    print()

    # 1. UK pipeline
    print("--- (1) UK pipeline: question '%s' ---" % VISA_QUESTION[:60])
    p_uk = ScreeningPipeline(profile=PROFILE_UK_GRAD)
    print(f"profile_state_hash (UK Grad)  = {p_uk._profile_state_hash}")
    print(f"jd_context_hash    (UK)       = {p_uk._jd_context_hash(JOB_CTX_UK)}")
    r_uk = p_uk.answer(VISA_QUESTION, job_context=JOB_CTX_UK)
    print(f"answer={r_uk['answer']!r} source={r_uk['source']} confidence={r_uk['confidence']:.3f}")
    p_uk.record_outcome(
        question=VISA_QUESTION,
        answer=r_uk["answer"],
        success=True,
        job_context=JOB_CTX_UK,
    )
    print()

    # 2. US pipeline (same profile, different JD context)
    print("--- (2) US pipeline: same profile, US JD context ---")
    p_us = ScreeningPipeline(profile=PROFILE_UK_GRAD)
    print(f"profile_state_hash (UK Grad)  = {p_us._profile_state_hash}")
    print(f"jd_context_hash    (US)       = {p_us._jd_context_hash(JOB_CTX_US)}")
    r_us = p_us.answer(VISA_QUESTION, job_context=JOB_CTX_US)
    print(f"answer={r_us['answer']!r} source={r_us['source']} confidence={r_us['confidence']:.3f}")
    p_us.record_outcome(
        question=VISA_QUESTION,
        answer=r_us["answer"],
        success=True,
        job_context=JOB_CTX_US,
    )
    print()

    # 3. Profile-state invalidation: same JD, different profile state.
    print("--- (3) Profile-state mutation: UK Grad → ILR, same UK JD ---")
    p_ilr = ScreeningPipeline(profile=PROFILE_UK_ILR)
    print(f"profile_state_hash (UK ILR)   = {p_ilr._profile_state_hash}")
    print(f"jd_context_hash    (UK)       = {p_ilr._jd_context_hash(JOB_CTX_UK)}")
    r_ilr = p_ilr.answer(VISA_QUESTION, job_context=JOB_CTX_UK)
    print(f"answer={r_ilr['answer']!r} source={r_ilr['source']} confidence={r_ilr['confidence']:.3f}")
    print()

    # 4. Re-query UK to confirm cache hit (now there's a UK-scoped entry).
    print("--- (4) UK re-query: should now hit cache ---")
    p_uk2 = ScreeningPipeline(profile=PROFILE_UK_GRAD)
    r_uk2 = p_uk2.answer(VISA_QUESTION, job_context=JOB_CTX_UK)
    print(f"answer={r_uk2['answer']!r} source={r_uk2['source']} confidence={r_uk2['confidence']:.3f}")
    print()

    # 5. Evidence: cache rows for the visa question
    print("=== Evidence: screening_semantic_cache rows for visa question ===")
    visa_rows = _cache_rows_for(cache_db, "visa sponsorship")
    distinct_qids = set()
    distinct_jd_hashes = set()
    distinct_profile_hashes = set()
    for r in visa_rows:
        distinct_qids.add(r["qdrant_id"])
        distinct_jd_hashes.add(r["jd_context_hash"])
        distinct_profile_hashes.add(r["profile_state_hash"])
        print(
            f"  qid={r['qdrant_id'][:20]:<20} "
            f"profile_hash={r['profile_state_hash'][:12] or '(empty)':<12} "
            f"jd_hash={r['jd_context_hash'][:12] or '(empty)':<12} "
            f"answer={(r['answer'] or '')[:30]!r}"
        )
    print()
    print(f"distinct qdrant_ids:    {len(distinct_qids)}")
    print(f"distinct jd_hashes:     {len(distinct_jd_hashes)}")
    print(f"distinct profile_hashes:{len(distinct_profile_hashes)}")
    print()

    # 6. Evidence: db_observability lookups for screening cache
    print("=== Evidence: db_observability.lookups distinct key_hash since baseline ===")
    new_lookups = _lookup_rows(obs_db, baseline_obs_id)
    screening_lookups = [r for r in new_lookups if r["db_name"] == "screening_semantic_cache"]
    distinct_key_hashes = sorted({r["key_hash"] for r in screening_lookups if r["key_hash"]})
    for r in screening_lookups[:10]:
        print(
            f"  id={r['id']} hit={r['hit']} key_hash={r['key_hash']!r:<22} "
            f"value_repr={(r['value_repr'] or '')[:40]!r}"
        )
    print(f"... ({len(screening_lookups)} new lookups, {len(distinct_key_hashes)} distinct key_hashes)")
    print()

    # 7. Pass / fail summary
    summary: dict[str, Any] = {
        "uk_profile_hash": p_uk._profile_state_hash,
        "us_profile_hash": p_us._profile_state_hash,
        "ilr_profile_hash": p_ilr._profile_state_hash,
        "uk_jd_hash": p_uk._jd_context_hash(JOB_CTX_UK),
        "us_jd_hash": p_us._jd_context_hash(JOB_CTX_US),
        "uk_answer": r_uk["answer"],
        "us_answer": r_us["answer"],
        "ilr_answer": r_ilr["answer"],
        "uk_re_source": r_uk2["source"],
        "distinct_cache_qids": len(distinct_qids),
        "distinct_jd_hashes_in_cache": len(distinct_jd_hashes),
        "distinct_profile_hashes_in_cache": len(distinct_profile_hashes),
        "new_screening_lookups": len(screening_lookups),
        "distinct_key_hashes_in_observability": len(distinct_key_hashes),
    }
    print("=== Summary ===")
    print(json.dumps(summary, indent=2))

    pass_uk_us_distinct = (
        p_uk._jd_context_hash(JOB_CTX_UK)
        != p_us._jd_context_hash(JOB_CTX_US)
    )
    pass_invalidation = (
        p_uk._profile_state_hash != p_ilr._profile_state_hash
    )
    pass_observability = len(distinct_key_hashes) >= 2

    print()
    print(f"PASS  UK/US JD hashes distinct:        {pass_uk_us_distinct}")
    print(f"PASS  Profile mutation invalidates:    {pass_invalidation}")
    print(f"PASS  Observability shows distinct keys: {pass_observability}")

    return 0 if (pass_uk_us_distinct and pass_invalidation and pass_observability) else 1


if __name__ == "__main__":
    raise SystemExit(main())
