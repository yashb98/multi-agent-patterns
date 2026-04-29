"""Tests for jobpulse/cv_tailor.py — dataclasses, validation, and alert helper."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from jobpulse.cv_tailor import (
    TailoredCV,
    TailoredCoverLetter,
    TailoredHeader,
    _send_validation_alert,
    validate_cover_letter,
    validate_experience,
    validate_projects,
    validate_summary,
)
from shared.profile_store import ExperienceEntry


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_tailored_header_construction(self):
        h = TailoredHeader(tagline="Data Engineer", summary="Experienced in pipelines")
        assert h.tagline == "Data Engineer"
        assert h.summary == "Experienced in pipelines"

    def test_tailored_cover_letter_construction(self):
        cl = TailoredCoverLetter(
            intro="I am applying to Acme Corp.",
            hook="I built a real-time data pipeline processing 50k events/sec.",
            closing="I look forward to discussing this opportunity.",
        )
        assert cl.intro == "I am applying to Acme Corp."
        assert cl.hook == "I built a real-time data pipeline processing 50k events/sec."
        assert cl.closing == "I look forward to discussing this opportunity."

    def test_tailored_cv_all_none_defaults(self):
        cv = TailoredCV()
        assert cv.tagline is None
        assert cv.summary is None
        assert cv.experience is None
        assert cv.projects is None
        assert cv.cover_letter is None

    def test_tailored_cv_with_values(self):
        entry = ExperienceEntry(
            title="Engineer", company="ACME", dates="2023-2024",
            bullets=["Built 3 services reducing latency by 40%"],
        )
        cv = TailoredCV(tagline="Data Engineer", experience=[entry])
        assert cv.tagline == "Data Engineer"
        assert len(cv.experience) == 1


# ---------------------------------------------------------------------------
# validate_summary
# ---------------------------------------------------------------------------

class TestValidateSummary:
    _VALID = (
        "I am a <b>data engineer</b> with 3 years of experience building scalable "
        "pipelines that process over 50k events per second, reducing costs by 30% "
        "and improving throughput by 2x across distributed systems."
    )

    def test_clean_pass(self):
        assert validate_summary(self._VALID) is None

    def test_too_short(self):
        result = validate_summary("Short <b>text</b>.")
        assert result is not None
        assert "100-500" in result

    def test_too_long(self):
        long_text = "<b>x</b> " + "a" * 500
        result = validate_summary(long_text)
        assert result is not None
        assert "100-500" in result

    def test_soft_skill_detected(self):
        text = (
            "I am a <b>data engineer</b> with strong leadership skills, having built "
            "pipelines processing 50k events/sec and reducing latency by 40% over 3 years "
            "of professional experience in distributed systems and cloud infrastructure."
        )
        result = validate_summary(text)
        assert result is not None
        assert "leadership" in result

    def test_no_bold_tag(self):
        text = (
            "I am a data engineer with 3 years of experience building scalable pipelines "
            "that process over 50k events per second, reducing costs by 30% and improving "
            "throughput by 2x across distributed systems environments."
        )
        result = validate_summary(text)
        assert result is not None
        assert "<b>" in result

    def test_exactly_100_chars_with_bold(self):
        # Boundary: exactly 100 chars should pass length check
        base = "<b>eng</b> " + "a" * 88  # 11 + 88 = 99 chars — still too short, need 100
        text = "<b>eng</b> " + "a" * 89  # 100 chars
        assert len(text) == 100
        result = validate_summary(text)
        # May fail soft-skill or metric check but NOT length
        assert result is None or "100-500" not in result

    def test_exactly_500_chars_passes_length(self):
        filler = "a" * (500 - len("<b>x</b> ") - 1)
        text = "<b>x</b> " + filler + "z"
        assert len(text) == 500
        result = validate_summary(text)
        assert result is None or "100-500" not in result

    def test_501_chars_fails(self):
        text = "<b>x</b> " + "a" * 492
        assert len(text) == 501
        result = validate_summary(text)
        assert result is not None
        assert "100-500" in result


# ---------------------------------------------------------------------------
# validate_experience
# ---------------------------------------------------------------------------

class TestValidateExperience:
    def _make_entry(self, bullets: list[str]) -> ExperienceEntry:
        return ExperienceEntry(
            title="Engineer", company="ACME", dates="2023-2024", bullets=bullets
        )

    def test_clean_pass(self):
        original = [self._make_entry(["Built pipeline processing 50k events/sec, saving 30% cost"])]
        tailored = [self._make_entry(["Built pipeline processing 50k events/sec, saving 30% cost"])]
        assert validate_experience(original, tailored) is None

    def test_count_mismatch(self):
        original = [self._make_entry(["Processed 50k events"]), self._make_entry(["Reduced latency by 40%"])]
        tailored = [self._make_entry(["Processed 50k events"])]
        result = validate_experience(original, tailored)
        assert result is not None
        assert "count mismatch" in result
        assert "expected 2" in result
        assert "got 1" in result

    def test_missing_metric_in_bullet(self):
        original = [self._make_entry(["Did some engineering work"])]
        tailored = [self._make_entry(["Did some engineering work"])]
        result = validate_experience(original, tailored)
        assert result is not None
        assert "missing quantified metric" in result

    def test_bullet_too_long(self):
        long_bullet = "Built a " + "scalable " * 22 + "pipeline processing 50k events"
        assert len(long_bullet) > 200
        original = [self._make_entry([long_bullet])]
        tailored = [self._make_entry([long_bullet])]
        result = validate_experience(original, tailored)
        assert result is not None
        assert "exceeds 200 chars" in result

    def test_multiple_entries_all_valid(self):
        e1 = self._make_entry(["Processed 1M records daily, reducing latency by 40%"])
        e2 = self._make_entry(["Deployed 3 microservices serving 100k users"])
        assert validate_experience([e1, e2], [e1, e2]) is None

    def test_second_entry_fails(self):
        # e1 has a qualifying metric (50k matches \d{2,} via "50"); e2 has no numbers at all
        e1 = self._make_entry(["Processed 50k records daily, reducing latency by 40%"])
        e2 = self._make_entry(["Did good work with no quantified impact"])
        result = validate_experience([e1, e2], [e1, e2])
        assert result is not None
        assert "Entry 1" in result


# ---------------------------------------------------------------------------
# validate_projects
# ---------------------------------------------------------------------------

class TestValidateProjects:
    def _make_project(self, bullets: list[str]) -> dict:
        return {"name": "Test Project", "bullets": bullets}

    def test_clean_pass(self):
        orig = [self._make_project(["Built 3 APIs", "Reduced latency by 40%", "Handled 50k req/day"])]
        tail = [self._make_project(["Built 3 APIs", "Reduced latency by 40%", "Handled 50k req/day"])]
        assert validate_projects(orig, tail) is None

    def test_count_mismatch(self):
        orig = [self._make_project(["50k", "40%", "3x"]), self._make_project(["10x", "20%", "5 services"])]
        tail = [self._make_project(["50k", "40%", "3x"])]
        result = validate_projects(orig, tail)
        assert result is not None
        assert "count mismatch" in result

    def test_missing_metric_number(self):
        orig = [self._make_project(["Built 50 APIs", "Reduced latency by 40%", "Deployed 3 services"])]
        tail = [self._make_project(["Built APIs", "Reduced latency", "Deployed services"])]
        result = validate_projects(orig, tail)
        assert result is not None
        assert "missing metrics" in result

    def test_too_few_bullets(self):
        orig = [self._make_project(["Built 50 APIs", "Handled 40k requests"])]
        tail = [self._make_project(["Built 50 APIs", "Handled 40k requests"])]
        result = validate_projects(orig, tail)
        assert result is not None
        assert "2 bullets" in result
        assert "expected 3-4" in result

    def test_too_many_bullets(self):
        bullets = ["Built 50 APIs", "40% faster", "3 microservices", "100k users", "10 pipelines"]
        orig = [self._make_project(bullets)]
        tail = [self._make_project(bullets)]
        result = validate_projects(orig, tail)
        assert result is not None
        assert "5 bullets" in result
        assert "expected 3-4" in result

    def test_four_bullets_passes(self):
        bullets = ["Built 50 APIs", "Reduced latency 40%", "Deployed 3 services", "Served 100k users"]
        orig = [self._make_project(bullets)]
        tail = [self._make_project(bullets)]
        assert validate_projects(orig, tail) is None


# ---------------------------------------------------------------------------
# validate_cover_letter
# ---------------------------------------------------------------------------

class TestValidateCoverLetter:
    def _valid_cl(self, company: str = "Acme Corp") -> TailoredCoverLetter:
        return TailoredCoverLetter(
            intro=f"I am excited to apply to {company} for the data engineer role, having followed your work in scalable infrastructure.",
            hook="Over the past 3 years I built pipelines processing 50k events/sec, cutting infrastructure costs by 30% for a fintech platform.",
            closing="I would welcome the opportunity to bring my expertise in distributed systems to your engineering team.",
        )

    def test_clean_pass(self):
        assert validate_cover_letter(self._valid_cl(), "Acme Corp") is None

    def test_company_not_in_intro(self):
        cl = TailoredCoverLetter(
            intro="I am excited to apply for the data engineer role at your company, having built scalable pipelines.",
            hook="Over the past 3 years I built pipelines processing 50k events/sec, cutting costs by 30% for fintech.",
            closing="I would welcome the opportunity to bring my expertise in distributed systems to your team.",
        )
        result = validate_cover_letter(cl, "Acme Corp")
        assert result is not None
        assert "Acme Corp" in result

    def test_intro_too_short(self):
        cl = TailoredCoverLetter(
            intro="Applying to Acme.",
            hook="Over the past 3 years I built pipelines processing 50k events/sec, cutting costs by 30% for fintech.",
            closing="I would welcome the opportunity to bring my expertise in distributed systems to your team.",
        )
        result = validate_cover_letter(cl, "Acme")
        assert result is not None
        assert "intro too short" in result

    def test_hook_too_long(self):
        long_hook = "Built pipelines " + "with many features " * 20 + "processing 50k events"
        assert len(long_hook) > 300
        cl = TailoredCoverLetter(
            intro="I am applying to Acme Corp for the data engineer role with great interest in your distributed systems work.",
            hook=long_hook,
            closing="I would welcome the opportunity to bring my expertise in distributed systems to your team.",
        )
        result = validate_cover_letter(cl, "Acme Corp")
        assert result is not None
        assert "hook too long" in result

    def test_soft_skill_in_hook(self):
        cl = TailoredCoverLetter(
            intro="I am applying to Acme Corp for the data engineer role with great interest in your distributed systems work.",
            hook="My strong leadership and teamwork have helped me build pipelines processing 50k events/sec, saving 30% costs.",
            closing="I would welcome the opportunity to bring my expertise in distributed systems to your team.",
        )
        result = validate_cover_letter(cl, "Acme Corp")
        assert result is not None
        assert "soft skill" in result

    def test_closing_too_short(self):
        cl = TailoredCoverLetter(
            intro="I am applying to Acme Corp for the data engineer role, excited about your infrastructure scale.",
            hook="Over 3 years I built pipelines processing 50k events/sec, reducing costs by 30% across fintech infrastructure.",
            closing="Thanks.",
        )
        result = validate_cover_letter(cl, "Acme Corp")
        assert result is not None
        assert "closing too short" in result

    def test_case_insensitive_company_match(self):
        cl = TailoredCoverLetter(
            intro="I am applying to ACME CORP for the data engineer role and am excited about your scale.",
            hook="Over 3 years I built pipelines processing 50k events/sec, reducing costs by 30% across fintech.",
            closing="I would welcome the opportunity to bring my expertise in distributed systems to your team.",
        )
        assert validate_cover_letter(cl, "Acme Corp") is None


# ---------------------------------------------------------------------------
# _send_validation_alert
# ---------------------------------------------------------------------------

class TestSendValidationAlert:
    def test_calls_send_jobs_with_correct_format(self, monkeypatch):
        captured = []

        def fake_send_jobs(text: str) -> bool:
            captured.append(text)
            return True

        monkeypatch.setattr("jobpulse.cv_tailor.send_jobs", fake_send_jobs, raising=False)

        # Patch send_jobs at the module level by importing and patching
        import jobpulse.telegram_bots as tb_module
        monkeypatch.setattr(tb_module, "send_jobs", fake_send_jobs)

        # Directly call with monkeypatched module
        import jobpulse.cv_tailor as cv_tailor_module

        original_send_jobs = None
        try:
            from jobpulse import telegram_bots as _tb
            original_send_jobs = _tb.send_jobs
            _tb.send_jobs = fake_send_jobs
            _send_validation_alert("summary", "Acme Corp", "too short", "short text")
        finally:
            if original_send_jobs is not None:
                _tb.send_jobs = original_send_jobs

        assert len(captured) == 1
        msg = captured[0]
        assert "summary" in msg
        assert "Acme Corp" in msg
        assert "too short" in msg

    def test_send_jobs_receives_truncated_text(self, monkeypatch):
        captured = []

        from jobpulse import telegram_bots as _tb
        original = _tb.send_jobs

        def fake_send_jobs(text: str) -> bool:
            captured.append(text)
            return True

        _tb.send_jobs = fake_send_jobs
        try:
            long_text = "x" * 500
            _send_validation_alert("summary", "TestCo", "too long", long_text)
        finally:
            _tb.send_jobs = original

        assert len(captured) == 1
        # Text is truncated to 200 chars in the message
        assert "x" * 200 in captured[0]
        assert "x" * 201 not in captured[0]

    def test_suppresses_exception_on_telegram_failure(self, monkeypatch):
        from jobpulse import telegram_bots as _tb
        original = _tb.send_jobs

        def failing_send(text: str) -> bool:
            raise RuntimeError("Telegram down")

        _tb.send_jobs = failing_send
        try:
            # Should not raise
            _send_validation_alert("summary", "Acme", "bad", "generated text")
        finally:
            _tb.send_jobs = original
