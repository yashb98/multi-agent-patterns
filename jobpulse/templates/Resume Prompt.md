# Resume Prompt — DEPRECATED

This template is **no longer consumed by any code path** (verified via grep
across `jobpulse/`, `shared/`, `scripts/` on 2026-05-04 — zero references).

**Why scrubbed:** the previous version contained the applicant's full identity
inline (name, email, links, education, employment history) which violates
`pii-policy.md`. All applicant data now lives in `data/user_profile.db` —
see `shared/profile_store.py:ProfileStore`.

**Where the data is now:**

| Field | Source |
|---|---|
| Identity (name, email, phone, links) | `user_profile.db.identity` via `ProfileStore.identity()` |
| Education | `user_profile.db.education` via `ProfileStore.education()` |
| Experience | `user_profile.db.experience` via `ProfileStore.experience()` |
| Projects | `user_profile.db.cv_projects` via `ProfileStore.cv_projects()` |
| Project archetype variants | `user_profile.db.cv_variants` |
| Skills | `user_profile.db.base_skills` / `skill_experience` |
| DEI / sensitive | `user_profile.db.sensitive_fields` via `ProfileStore.sensitive(key)` |
| Visa / work auth | `config.WORK_AUTH` (env-var-backed) |

**If you need a CV-generation prompt template:** retrieve all data via
`ProfileStore` at runtime and assemble the prompt dynamically. See
`jobpulse/cv_tailor.py` for the canonical pattern.

This file is preserved (rather than deleted) so any external link or commit
reference keeps resolving, but its content is intentionally empty of PII.
