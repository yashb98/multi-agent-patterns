"""Tests for shared.profile_store — ProfileStore facade."""

from __future__ import annotations

import json

import pytest

from shared.profile_store import (
    CertificationEntry,
    CommunityEntry,
    EducationEntry,
    ExperienceEntry,
    IdentityInfo,
    ProfileStore,
    _reset_shared_store,
    get_profile_store,
)


@pytest.fixture()
def store(tmp_path):
    """Fresh ProfileStore backed by tmp_path (never touches data/)."""
    db = tmp_path / "user_profile.db"
    key = tmp_path / ".profile_key"
    _reset_shared_store()
    s = ProfileStore(db_path=db, key_path=key)
    yield s
    s.close()
    _reset_shared_store()


# ── Identity ──


class TestIdentity:
    def test_default_identity_empty(self, store):
        ident = store.identity()
        assert ident.first_name == ""
        assert ident.full_name == ""
        assert ident.file_name_prefix == ""

    def test_set_and_get(self, store):
        store.set_identity(first_name="Yash", last_name="Bishnoi", email="test@example.com")
        ident = store.identity()
        assert ident.first_name == "Yash"
        assert ident.last_name == "Bishnoi"
        assert ident.full_name == "Yash Bishnoi"
        assert ident.file_name_prefix == "Yash_Bishnoi"
        assert ident.email == "test@example.com"

    def test_partial_update(self, store):
        store.set_identity(first_name="A", last_name="B")
        store.set_identity(phone="+44123456789")
        ident = store.identity()
        assert ident.first_name == "A"
        assert ident.phone == "+44123456789"

    def test_ignores_invalid_keys(self, store):
        store.set_identity(first_name="X", hacker_field="drop table")
        assert store.identity().first_name == "X"

    def test_as_applicant_profile(self, store):
        store.set_identity(first_name="Y", last_name="B", email="y@b.com")
        store.add_education("MSc CS", "University of Dundee", "2025-2026")
        profile = store.as_applicant_profile()
        assert profile["first_name"] == "Y"
        assert profile["email"] == "y@b.com"
        assert "MSc CS" in profile["education"]


# ── Experience ──


class TestExperience:
    def test_add_and_list(self, store):
        store.add_experience("SWE", "Google", "2024-2025", ["Built X", "Shipped Y"])
        entries = store.experience()
        assert len(entries) == 1
        assert isinstance(entries[0], ExperienceEntry)
        assert entries[0].title == "SWE"
        assert entries[0].bullets == ["Built X", "Shipped Y"]

    def test_sort_order(self, store):
        store.add_experience("B", "Co2", "2023", sort_order=2)
        store.add_experience("A", "Co1", "2024", sort_order=1)
        entries = store.experience()
        assert entries[0].title == "A"
        assert entries[1].title == "B"


# ── Education ──


class TestEducation:
    def test_add_and_list(self, store):
        store.add_education(
            "MSc CS", "Dundee", "2025-2026",
            dissertation="3D Faces", modules="ML | SE",
        )
        entries = store.education()
        assert len(entries) == 1
        assert isinstance(entries[0], EducationEntry)
        assert entries[0].dissertation == "3D Faces"
        assert entries[0].modules == "ML | SE"


# ── Certifications ──


class TestCertifications:
    def test_add_and_list(self, store):
        store.add_certification("IBM ML", "Jul 2023", "https://example.com")
        certs = store.certifications()
        assert len(certs) == 1
        assert isinstance(certs[0], CertificationEntry)
        assert certs[0].name == "IBM ML"


# ── Community ──


class TestCommunity:
    def test_add_and_list(self, store):
        store.add_community("Quackathon", "Built a prototype in 4 hours.")
        entries = store.community()
        assert len(entries) == 1
        assert isinstance(entries[0], CommunityEntry)


# ── CV Projects ──


class TestCVProjects:
    def test_add_and_list(self, store):
        store.add_cv_project("Velox AI", "https://github.com/x", ["bullet1", "bullet2"])
        projects = store.cv_projects()
        assert len(projects) == 1
        assert projects[0]["title"] == "Velox AI"
        assert projects[0]["bullets"] == ["bullet1", "bullet2"]


# ── Screening Defaults ──


class TestScreeningDefaults:
    def test_set_and_get(self, store):
        store.set_screening_default("notice_period", "Immediately")
        assert store.screening_default("notice_period") == "Immediately"

    def test_missing_returns_empty(self, store):
        assert store.screening_default("nonexistent") == ""

    def test_all_defaults(self, store):
        store.set_screening_default("a", "1")
        store.set_screening_default("b", "2")
        all_defaults = store.all_screening_defaults()
        assert all_defaults == {"a": "1", "b": "2"}

    def test_upsert(self, store):
        store.set_screening_default("x", "old")
        store.set_screening_default("x", "new")
        assert store.screening_default("x") == "new"


# ── Skill Experience ──


class TestSkillExperience:
    def test_set_and_get_single(self, store):
        store.set_skill_experience("python", 3)
        assert store.skill_experience("python") == 3

    def test_case_insensitive(self, store):
        store.set_skill_experience("Python", 3)
        assert store.skill_experience("python") == 3

    def test_missing_returns_zero(self, store):
        assert store.skill_experience("haskell") == 0

    def test_get_all(self, store):
        store.set_skill_experience("python", 3)
        store.set_skill_experience("sql", 3)
        all_skills = store.skill_experience()
        assert all_skills == {"python": 3, "sql": 3}


# ── Role Salary ──


class TestRoleSalary:
    def test_set_and_get(self, store):
        store.set_role_salary("data scientist", 38000)
        assert store.role_salary("data scientist") == 38000

    def test_default_fallback(self, store):
        store.set_role_salary("default", 30000)
        assert store.role_salary("unknown role") == 30000

    def test_get_all(self, store):
        store.set_role_salary("swe", 35000)
        store.set_role_salary("ds", 38000)
        all_salaries = store.role_salary()
        assert all_salaries == {"swe": 35000, "ds": 38000}


# ── Base Skills ──


class TestBaseSkills:
    def test_set_and_get(self, store):
        store.set_base_skill_category("Languages:", "Python | SQL | JS")
        skills = store.base_skills()
        assert skills["Languages:"] == "Python | SQL | JS"


# ── Sensitive Fields (encrypted) ──


class TestSensitiveFields:
    def test_set_and_get(self, store):
        store.set_sensitive("gender", "Male", category="dei")
        assert store.sensitive("gender") == "Male"

    def test_missing_returns_empty(self, store):
        assert store.sensitive("nonexistent") == ""

    def test_encrypted_at_rest(self, store):
        store.set_sensitive("salary", "22000", category="financial")
        raw = store._conn.execute(
            "SELECT value_encrypted FROM sensitive_fields WHERE key = 'salary'"
        ).fetchone()[0]
        assert raw != b"22000"
        assert isinstance(raw, bytes)

    def test_get_all_by_category(self, store):
        store.set_sensitive("gender", "Male", "dei")
        store.set_sensitive("ethnicity", "Indian", "dei")
        store.set_sensitive("salary", "22000", "financial")
        dei = store.all_sensitive("dei")
        assert dei == {"gender": "Male", "ethnicity": "Indian"}
        assert "salary" not in dei

    def test_get_all_no_category(self, store):
        store.set_sensitive("a", "1")
        store.set_sensitive("b", "2")
        assert len(store.all_sensitive()) == 2

    def test_delete(self, store):
        store.set_sensitive("tmp", "val")
        assert store.sensitive("tmp") == "val"
        store._sensitive.delete("tmp")
        assert store.sensitive("tmp") == ""

    def test_upsert(self, store):
        store.set_sensitive("k", "old")
        store.set_sensitive("k", "new")
        assert store.sensitive("k") == "new"


# ── Work Auth ──


class TestWorkAuth:
    def test_as_work_auth(self, store):
        store.set_sensitive("requires_sponsorship", "false", "immigration")
        store.set_sensitive("visa_status", "Graduate Visa", "immigration")
        store.set_sensitive("right_to_work_uk", "true", "immigration")
        store.set_screening_default("notice_period", "1 month")
        auth = store.as_work_auth()
        assert auth["requires_sponsorship"] is False
        assert auth["visa_status"] == "Graduate Visa"
        assert auth["right_to_work_uk"] is True
        assert auth["notice_period"] == "1 month"


# ── Singleton ──


class TestSingleton:
    def test_get_profile_store_returns_same_instance(self, tmp_path):
        _reset_shared_store()
        db = tmp_path / "test.db"
        key = tmp_path / ".key"
        s1 = get_profile_store(db_path=db, key_path=key)
        s2 = get_profile_store()
        assert s1 is s2
        s1.close()
        _reset_shared_store()

    def test_override_path_creates_new(self, tmp_path):
        _reset_shared_store()
        db1 = tmp_path / "a.db"
        db2 = tmp_path / "b.db"
        key = tmp_path / ".key"
        s1 = get_profile_store(db_path=db1, key_path=key)
        s2 = get_profile_store(db_path=db2, key_path=key)
        assert s1 is not s2
        s1.close()
        s2.close()
        _reset_shared_store()


# ── Key File Permissions ──


class TestKeyFilePermissions:
    def test_key_file_created_with_600(self, tmp_path):
        import os
        import stat
        db = tmp_path / "test.db"
        key = tmp_path / ".profile_key"
        s = ProfileStore(db_path=db, key_path=key)
        assert key.exists()
        mode = os.stat(str(key)).st_mode
        assert mode & 0o777 == stat.S_IRUSR | stat.S_IWUSR  # 0o600
        s.close()
