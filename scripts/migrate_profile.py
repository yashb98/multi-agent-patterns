#!/usr/bin/env python3
"""One-time migration: seed user_profile.db from data/profile_seed.json.

All personal data lives in data/profile_seed.json (gitignored via data/* rule).
This script reads from that file and populates ProfileStore.

Run once:  python scripts/migrate_profile.py
Verify:    python scripts/migrate_profile.py --verify

Safe to re-run — clears and re-inserts list-based tables.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.profile_store import ProfileStore, get_profile_store

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "profile_seed.json"


def _load_seed() -> dict:
    if not SEED_PATH.exists():
        print(f"ERROR: Seed file not found at {SEED_PATH}")
        print("Create data/profile_seed.json with your profile data (see PII policy).")
        sys.exit(1)
    return json.loads(SEED_PATH.read_text())


def migrate(store: ProfileStore) -> None:
    seed = _load_seed()

    for table in ("experience", "education", "certifications", "community", "cv_projects"):
        store._conn.execute(f"DELETE FROM {table}")
    store._conn.commit()

    # Identity
    ident = seed["identity"]
    store.set_identity(
        first_name=ident["first_name"], last_name=ident["last_name"],
        email=ident["email"], phone=ident.get("phone", ""),
        linkedin=ident.get("linkedin", ""), github=ident.get("github", ""),
        portfolio=ident.get("portfolio", ""), location=ident.get("location", ""),
        education=ident.get("education", ""),
    )

    # Experience
    for i, exp in enumerate(seed.get("experience", [])):
        store.add_experience(
            exp["title"], exp["company"], exp["dates"],
            bullets=exp.get("bullets", []), sort_order=i,
        )

    # Education
    for i, edu in enumerate(seed.get("education", [])):
        store.add_education(
            edu["degree"], edu["institution"], edu["dates"],
            dissertation=edu.get("dissertation"),
            dissertation_url=edu.get("dissertation_url"),
            modules=edu.get("modules"),
            grade=edu.get("grade"),
            sort_order=i,
        )

    # Certifications
    for i, cert in enumerate(seed.get("certifications", [])):
        store.add_certification(cert["name"], cert["date"], cert["url"], sort_order=i)

    # Community
    for i, item in enumerate(seed.get("community", [])):
        store.add_community(item["title"], item["text"], sort_order=i)

    # CV Projects
    for i, proj in enumerate(seed.get("cv_projects", [])):
        store.add_cv_project(proj["title"], proj["url"], proj["bullets"], sort_order=i)

    # Base Skills
    for category, skills in seed.get("base_skills", {}).items():
        store.set_base_skill_category(category, skills)

    # Skill Experience
    for skill, years in seed.get("skill_experience", {}).items():
        store.set_skill_experience(skill, years)

    # Role Salary
    for role, salary in seed.get("role_salary", {}).items():
        store.set_role_salary(role, salary)

    # Screening Defaults
    for q_type, answer in seed.get("screening_defaults", {}).items():
        store.set_screening_default(q_type, answer)

    # Sensitive Fields (grouped by category)
    for category, fields in seed.get("sensitive", {}).items():
        for key, value in fields.items():
            store.set_sensitive(key, value, category)

    print(f"Migration complete. Profile DB at: {store._db_path}")
    _print_stats(store)


def _print_stats(store: ProfileStore) -> None:
    ident = store.identity()
    print(f"  Identity: {ident.full_name} ({ident.email})")
    print(f"  Experience: {len(store.experience())} entries")
    print(f"  Education: {len(store.education())} entries")
    print(f"  Certifications: {len(store.certifications())} entries")
    print(f"  Community: {len(store.community())} entries")
    print(f"  CV Projects: {len(store.cv_projects())} entries")
    print(f"  Base Skills: {len(store.base_skills())} categories")
    print(f"  Skill Experience: {len(store.skill_experience())} skills")
    print(f"  Role Salary: {len(store.role_salary())} roles")
    print(f"  Screening Defaults: {len(store.all_screening_defaults())} entries")
    print(f"  Sensitive Fields: {len(store.all_sensitive())} entries (encrypted)")


def verify(store: ProfileStore) -> None:
    ident = store.identity()
    assert ident.full_name, "Identity: full_name is empty"
    assert ident.email, "Identity: email is empty"
    assert len(store.experience()) >= 2, "Experience: expected >= 2 entries"
    assert len(store.education()) >= 2, "Education: expected >= 2 entries"
    assert len(store.certifications()) >= 7, "Certifications: expected >= 7"
    assert len(store.cv_projects()) >= 4, "CV Projects: expected >= 4"
    assert len(store.base_skills()) >= 5, "Base Skills: expected >= 5 categories"
    se = store.skill_experience()
    assert isinstance(se, dict) and len(se) >= 50, f"Skill Experience: expected >= 50, got {len(se)}"
    rs = store.role_salary()
    assert isinstance(rs, dict) and len(rs) >= 20, f"Role Salary: expected >= 20, got {len(rs)}"
    assert store.sensitive("gender"), "Sensitive: gender is empty"
    assert store.sensitive("visa_status"), "Sensitive: visa_status is empty"
    assert store.sensitive("current_salary"), "Sensitive: salary is empty"
    print("All verification checks passed.")
    _print_stats(store)


if __name__ == "__main__":
    store = get_profile_store()
    if "--verify" in sys.argv:
        verify(store)
    else:
        migrate(store)
