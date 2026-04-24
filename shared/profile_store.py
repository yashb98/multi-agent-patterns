"""ProfileStore — Single source of truth for applicant profile data.

All agents read personal data through ``get_profile_store()`` — never
from hardcoded dicts, env vars, or scattered constants.  Same single-
entry-point principle as ``get_llm()`` for LLM calls and
``MemoryManager`` for agent memory.

Architecture:
- **user_profile.db** stores identity, experience, education,
  screening defaults, CV bullets, and certifications.
- **sensitive_fields** table stores DEI answers, salary, and visa
  details encrypted with Fernet (key in ``data/.profile_key`` with
  0o600 permissions).
- Skills and projects stay in MindGraph.db — ``ProfileStore`` delegates
  ``.skills()`` and ``.projects()`` to ``SkillGraphStore``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from shared.logging_config import get_logger

logger = get_logger(__name__)

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_DIR / "data"
_DEFAULT_DB_PATH = _DATA_DIR / "user_profile.db"
_DEFAULT_KEY_PATH = _DATA_DIR / ".profile_key"


# ---------------------------------------------------------------------------
# Dataclasses — typed returns, never bare dicts
# ---------------------------------------------------------------------------

@dataclass
class IdentityInfo:
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    location: str = ""
    education: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def file_name_prefix(self) -> str:
        return f"{self.first_name}_{self.last_name}".strip("_")


@dataclass
class ExperienceEntry:
    title: str
    company: str
    dates: str
    bullets: list[str] = field(default_factory=list)
    location: str = ""


@dataclass
class EducationEntry:
    degree: str
    institution: str
    dates: str
    field_of_study: str = ""
    grade: str = ""
    dissertation: str = ""
    dissertation_url: str = ""
    modules: str = ""


@dataclass
class CertificationEntry:
    name: str
    date: str
    url: str = ""


@dataclass
class CommunityEntry:
    title: str
    text: str


@dataclass
class CVBulletEntry:
    project_name: str
    bullet: str
    variant: str = "default"


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def _load_or_create_key(key_path: Path) -> bytes:
    if key_path.exists():
        os.chmod(str(key_path), stat.S_IRUSR | stat.S_IWUSR)
        raw = key_path.read_bytes().strip()
        try:
            Fernet(raw)
            return raw
        except Exception:
            logger.error("ProfileStore: invalid key at %s — regenerating", key_path)
    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    os.chmod(str(key_path), stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    logger.info("ProfileStore: created encryption key at %s", key_path)
    return key


class _SensitiveStore:
    """Encrypt/decrypt sensitive fields via Fernet."""

    def __init__(self, conn: sqlite3.Connection, key_path: Path):
        self._conn = conn
        key = _load_or_create_key(key_path)
        self._fernet = Fernet(key)

    def get(self, key: str) -> str:
        row = self._conn.execute(
            "SELECT value_encrypted FROM sensitive_fields WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return ""
        try:
            return self._fernet.decrypt(row[0]).decode()
        except InvalidToken:
            logger.error("ProfileStore: failed to decrypt sensitive field '%s'", key)
            return ""

    def set(self, key: str, value: str, category: str = "general") -> None:
        encrypted = self._fernet.encrypt(value.encode())
        self._conn.execute(
            """INSERT INTO sensitive_fields (key, value_encrypted, category)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value_encrypted = excluded.value_encrypted,
                   category = excluded.category""",
            (key, encrypted, category),
        )
        self._conn.commit()

    def get_all(self, category: str | None = None) -> dict[str, str]:
        if category:
            rows = self._conn.execute(
                "SELECT key, value_encrypted FROM sensitive_fields WHERE category = ?",
                (category,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT key, value_encrypted FROM sensitive_fields",
            ).fetchall()
        result: dict[str, str] = {}
        for k, enc in rows:
            try:
                result[k] = self._fernet.decrypt(enc).decode()
            except InvalidToken:
                logger.error("ProfileStore: failed to decrypt '%s'", k)
        return result

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM sensitive_fields WHERE key = ?", (key,))
        self._conn.commit()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS identity (
    id INTEGER PRIMARY KEY DEFAULT 1,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT DEFAULT '',
    linkedin TEXT DEFAULT '',
    github TEXT DEFAULT '',
    portfolio TEXT DEFAULT '',
    location TEXT DEFAULT '',
    education TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS experience (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    dates TEXT NOT NULL,
    location TEXT DEFAULT '',
    bullets TEXT DEFAULT '[]',
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS education (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    degree TEXT NOT NULL,
    institution TEXT NOT NULL,
    dates TEXT NOT NULL,
    field_of_study TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    dissertation TEXT DEFAULT '',
    dissertation_url TEXT DEFAULT '',
    modules TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS certifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    url TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS community (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS screening_defaults (
    question_type TEXT PRIMARY KEY,
    answer TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_experience (
    skill TEXT PRIMARY KEY,
    years REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS role_salary (
    role TEXT PRIMARY KEY,
    salary INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS base_skills (
    category TEXT PRIMARY KEY,
    skills TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cv_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT DEFAULT '',
    bullets TEXT DEFAULT '[]',
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sensitive_fields (
    key TEXT PRIMARY KEY,
    value_encrypted BLOB NOT NULL,
    category TEXT DEFAULT 'general'
);
"""


# ---------------------------------------------------------------------------
# ProfileStore
# ---------------------------------------------------------------------------

_shared_store: ProfileStore | None = None


class ProfileStore:
    """Single facade for all applicant profile data.

    Read API returns typed dataclasses.  Write API is for migration
    scripts and Telegram admin commands.  Skills and projects delegate
    to SkillGraphStore (MindGraph.db).
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        key_path: str | Path | None = None,
    ):
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._key_path = Path(key_path) if key_path else _DEFAULT_KEY_PATH
        self._conn = self._connect()
        self._ensure_schema()
        self._sensitive = _SensitiveStore(self._conn, self._key_path)

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        if not self._conn.execute("SELECT COUNT(*) FROM identity").fetchone()[0]:
            self._conn.execute("INSERT INTO identity (id) VALUES (1)")
            self._conn.commit()
        # Migration: add education column if missing (for pre-existing DBs)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(identity)").fetchall()}
        if "education" not in cols:
            self._conn.execute("ALTER TABLE identity ADD COLUMN education TEXT DEFAULT ''")
            self._conn.commit()

    # ── Identity ──

    def identity(self) -> IdentityInfo:
        row = self._conn.execute("SELECT * FROM identity WHERE id = 1").fetchone()
        if row is None:
            return IdentityInfo()
        return IdentityInfo(
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"],
            phone=row["phone"],
            linkedin=row["linkedin"],
            github=row["github"],
            portfolio=row["portfolio"],
            location=row["location"],
            education=row["education"] if "education" in row.keys() else "",
        )

    def set_identity(self, **kwargs: str) -> None:
        allowed = {"first_name", "last_name", "email", "phone",
                    "linkedin", "github", "portfolio", "location", "education"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE identity SET {set_clause} WHERE id = 1",
            tuple(updates.values()),
        )
        self._conn.commit()

    # ── Experience ──

    def experience(self) -> list[ExperienceEntry]:
        rows = self._conn.execute(
            "SELECT * FROM experience ORDER BY sort_order, id",
        ).fetchall()
        return [
            ExperienceEntry(
                title=r["title"], company=r["company"], dates=r["dates"],
                location=r["location"], bullets=json.loads(r["bullets"]),
            )
            for r in rows
        ]

    def add_experience(
        self, title: str, company: str, dates: str,
        bullets: list[str] | None = None, location: str = "",
        sort_order: int = 0,
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO experience (title, company, dates, location, bullets, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, company, dates, location, json.dumps(bullets or []), sort_order),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── Education ──

    def education(self) -> list[EducationEntry]:
        rows = self._conn.execute(
            "SELECT * FROM education ORDER BY sort_order, id",
        ).fetchall()
        return [
            EducationEntry(
                degree=r["degree"], institution=r["institution"],
                dates=r["dates"], field_of_study=r["field_of_study"],
                grade=r["grade"], dissertation=r["dissertation"],
                dissertation_url=r["dissertation_url"], modules=r["modules"],
            )
            for r in rows
        ]

    def add_education(
        self, degree: str, institution: str, dates: str, *,
        field_of_study: str = "", grade: str = "",
        dissertation: str = "", dissertation_url: str = "",
        modules: str = "", sort_order: int = 0,
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO education
               (degree, institution, dates, field_of_study, grade,
                dissertation, dissertation_url, modules, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (degree, institution, dates, field_of_study, grade,
             dissertation, dissertation_url, modules, sort_order),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── Certifications ──

    def certifications(self) -> list[CertificationEntry]:
        rows = self._conn.execute(
            "SELECT * FROM certifications ORDER BY sort_order, id",
        ).fetchall()
        return [
            CertificationEntry(name=r["name"], date=r["date"], url=r["url"])
            for r in rows
        ]

    def add_certification(self, name: str, date: str, url: str = "", sort_order: int = 0) -> int:
        cur = self._conn.execute(
            "INSERT INTO certifications (name, date, url, sort_order) VALUES (?, ?, ?, ?)",
            (name, date, url, sort_order),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── Community ──

    def community(self) -> list[CommunityEntry]:
        rows = self._conn.execute(
            "SELECT * FROM community ORDER BY sort_order, id",
        ).fetchall()
        return [CommunityEntry(title=r["title"], text=r["text"]) for r in rows]

    def add_community(self, title: str, text: str, sort_order: int = 0) -> int:
        cur = self._conn.execute(
            "INSERT INTO community (title, text, sort_order) VALUES (?, ?, ?)",
            (title, text, sort_order),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── CV Default Projects ──

    def cv_projects(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM cv_projects ORDER BY sort_order, id",
        ).fetchall()
        return [
            {"title": r["title"], "url": r["url"], "bullets": json.loads(r["bullets"])}
            for r in rows
        ]

    def add_cv_project(
        self, title: str, url: str = "", bullets: list[str] | None = None,
        sort_order: int = 0,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO cv_projects (title, url, bullets, sort_order) VALUES (?, ?, ?, ?)",
            (title, url, json.dumps(bullets or []), sort_order),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── Screening Defaults ──

    def screening_default(self, question_type: str) -> str:
        row = self._conn.execute(
            "SELECT answer FROM screening_defaults WHERE question_type = ?",
            (question_type,),
        ).fetchone()
        return row["answer"] if row else ""

    def all_screening_defaults(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT * FROM screening_defaults").fetchall()
        return {r["question_type"]: r["answer"] for r in rows}

    def set_screening_default(self, question_type: str, answer: str) -> None:
        self._conn.execute(
            """INSERT INTO screening_defaults (question_type, answer)
               VALUES (?, ?)
               ON CONFLICT(question_type) DO UPDATE SET answer = excluded.answer""",
            (question_type, answer),
        )
        self._conn.commit()

    # ── Skill Experience (years per skill) ──

    def skill_experience(self, skill: str | None = None) -> dict[str, float] | float:
        if skill:
            row = self._conn.execute(
                "SELECT years FROM skill_experience WHERE skill = ?",
                (skill.lower(),),
            ).fetchone()
            return row["years"] if row else 0
        rows = self._conn.execute("SELECT * FROM skill_experience").fetchall()
        return {r["skill"]: r["years"] for r in rows}

    def set_skill_experience(self, skill: str, years: float) -> None:
        self._conn.execute(
            """INSERT INTO skill_experience (skill, years)
               VALUES (?, ?)
               ON CONFLICT(skill) DO UPDATE SET years = excluded.years""",
            (skill.lower(), years),
        )
        self._conn.commit()

    # ── Role Salary ──

    def role_salary(self, role: str | None = None) -> dict[str, int] | int:
        if role:
            row = self._conn.execute(
                "SELECT salary FROM role_salary WHERE role = ?",
                (role.lower(),),
            ).fetchone()
            if row:
                return row["salary"]
            default = self._conn.execute(
                "SELECT salary FROM role_salary WHERE role = 'default'",
            ).fetchone()
            return default["salary"] if default else 30000
        rows = self._conn.execute("SELECT * FROM role_salary").fetchall()
        return {r["role"]: r["salary"] for r in rows}

    def set_role_salary(self, role: str, salary: int) -> None:
        self._conn.execute(
            """INSERT INTO role_salary (role, salary)
               VALUES (?, ?)
               ON CONFLICT(role) DO UPDATE SET salary = excluded.salary""",
            (role.lower(), salary),
        )
        self._conn.commit()

    # ── Base Skills (CV categories) ──

    def base_skills(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT * FROM base_skills").fetchall()
        return {r["category"]: r["skills"] for r in rows}

    def set_base_skill_category(self, category: str, skills: str) -> None:
        self._conn.execute(
            """INSERT INTO base_skills (category, skills)
               VALUES (?, ?)
               ON CONFLICT(category) DO UPDATE SET skills = excluded.skills""",
            (category, skills),
        )
        self._conn.commit()

    # ── Sensitive Fields (encrypted) ──

    def sensitive(self, key: str) -> str:
        return self._sensitive.get(key)

    def set_sensitive(self, key: str, value: str, category: str = "general") -> None:
        self._sensitive.set(key, value, category)

    def all_sensitive(self, category: str | None = None) -> dict[str, str]:
        return self._sensitive.get_all(category)

    # ── Compatibility: dict-style profile for existing consumers ──

    def as_applicant_profile(self) -> dict[str, str]:
        ident = self.identity()
        return {
            "first_name": ident.first_name,
            "last_name": ident.last_name,
            "email": ident.email,
            "phone": ident.phone,
            "linkedin": ident.linkedin,
            "github": ident.github,
            "portfolio": ident.portfolio,
            "location": ident.location,
            "education": self._education_summary(),
        }

    def as_work_auth(self) -> dict[str, Any]:
        return {
            "requires_sponsorship": self.sensitive("requires_sponsorship").lower() in ("true", "1"),
            "visa_status": self.sensitive("visa_status"),
            "right_to_work_uk": self.sensitive("right_to_work_uk") != "false",
            "notice_period": self.screening_default("notice_period") or "Immediately",
            "salary_expectation": self.screening_default("salary_expectation") or "",
        }

    def _education_summary(self) -> str:
        entries = self.education()
        if not entries:
            return ""
        return entries[0].degree + ", " + entries[0].institution

    # ── Lifecycle ──

    def close(self) -> None:
        self._conn.close()


import threading

# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_shared_store_lock = threading.Lock()

def get_profile_store(
    db_path: str | Path | None = None,
    key_path: str | Path | None = None,
) -> ProfileStore:
    """Return (or create) the shared ProfileStore singleton.

    All modules should call this instead of constructing their own
    ``ProfileStore()`` — ensures a single source of truth across the
    process lifetime.

    Args:
        db_path: Override DB path (use tmp_path in tests).
        key_path: Override key path (use tmp_path in tests).
    """
    global _shared_store
    if _shared_store is not None and db_path is None:
        return _shared_store
    with _shared_store_lock:
        if _shared_store is None or db_path is not None:
            _shared_store = ProfileStore(db_path=db_path, key_path=key_path)
    return _shared_store


def _reset_shared_store() -> None:
    """Reset the singleton (for tests only)."""
    global _shared_store
    if _shared_store is not None:
        _shared_store.close()
    _shared_store = None
