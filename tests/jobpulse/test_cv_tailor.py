"""Tests for jobpulse/cv_tailor.py — dataclasses, validation, and alert helper."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json

import pytest

from jobpulse.cv_tailor import (
    TailoredCV,
    TailoredCoverLetter,
    TailoredHeader,
    _parse_llm_json,
    _record_validation_failure,
    tailor_all_sections,
    tailor_cover_letter_prose,
    tailor_experience_bullets,
    tailor_project_bullets,
    tailor_summary_and_tagline,
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
    # 36 words, contains <b>, has metrics, no soft-skill words
    _VALID = (
        "<b>Data Engineer</b> with 3 years building scalable pipelines that process "
        "50k events per second, cutting costs by 30% and doubling throughput across "
        "distributed systems for fintech and e-commerce platforms in production."
    )

    def test_clean_pass(self):
        assert validate_summary(self._VALID) is None

    def test_too_short(self):
        result = validate_summary("<b>Short</b> two words")
        assert result is not None
        assert "30-50" in result

    def test_too_long(self):
        long_text = "<b>x</b> " + " ".join(["word"] * 60)
        result = validate_summary(long_text)
        assert result is not None
        assert "30-50" in result

    def test_soft_skill_detected(self):
        text = (
            "<b>Data Engineer</b> with strong leadership skills, building pipelines "
            "processing 50k events per second and reducing latency by 40% over three years "
            "of experience in distributed systems and cloud infrastructure across teams."
        )
        result = validate_summary(text)
        assert result is not None
        assert "leadership" in result

    def test_no_bold_tag(self):
        text = (
            "Data engineer with three years of experience building scalable pipelines "
            "that process 50k events per second, reducing costs by 30% and doubling "
            "throughput across distributed systems for fintech and e-commerce."
        )
        result = validate_summary(text)
        assert result is not None
        assert "<b>" in result

    def test_exactly_30_words_passes_length(self):
        # 30 words including the <b>-wrapped one (markup is stripped before counting)
        words = ["<b>Engineer</b>"] + ["word"] * 29
        text = " ".join(words)
        result = validate_summary(text)
        # May still fail soft-skill or other checks, but NOT length
        assert result is None or "30-50" not in result

    def test_exactly_50_words_passes_length(self):
        words = ["<b>Engineer</b>"] + ["word"] * 49
        text = " ".join(words)
        result = validate_summary(text)
        assert result is None or "30-50" not in result

    def test_51_words_fails(self):
        words = ["<b>Engineer</b>"] + ["word"] * 50
        text = " ".join(words)
        result = validate_summary(text)
        assert result is not None
        assert "30-50" in result


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

    def test_missing_metric_when_no_bullet_has_one(self):
        """Per-entry rule (loosened 2026-05-04): only fails when NO bullet
        in the entry has a metric. The previous per-bullet rule produced
        too many false positives where the LLM dropped a metric from a
        supporting bullet but kept the headline number elsewhere."""
        original = [self._make_entry(["Did some engineering work", "Another bullet"])]
        tailored = [self._make_entry(["Did some engineering work", "Another bullet"])]
        result = validate_experience(original, tailored)
        assert result is not None
        assert "no quantified metric" in result

    def test_one_metric_per_entry_passes(self):
        """If at least one bullet has a metric, the entry validates."""
        e = self._make_entry([
            "Headline: shipped 12 features, +30% throughput",
            "Supporting: refactored architecture",
        ])
        assert validate_experience([e], [e]) is None

    def test_bullet_too_long(self):
        # Threshold loosened to 220 chars (from 200) to absorb minor LLM
        # overshoot without false positives.
        long_bullet = "Built a " + "scalable " * 30 + "pipeline processing 50k events"
        assert len(long_bullet) > 220
        original = [self._make_entry([long_bullet])]
        tailored = [self._make_entry([long_bullet])]
        result = validate_experience(original, tailored)
        assert result is not None
        assert "exceeds 220 chars" in result

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
# _record_validation_failure (replaced _send_validation_alert 2026-05-04;
# Telegram per-failure alerts caused a flood — now logger-only)
# ---------------------------------------------------------------------------

class TestRecordValidationFailure:
    def test_logs_with_section_company_reason_and_text(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="jobpulse.cv_tailor"):
            _record_validation_failure(
                "summary", "Acme Corp", "too short", "short text",
            )
        msg = caplog.records[-1].getMessage()
        assert "summary" in msg
        assert "Acme Corp" in msg
        assert "too short" in msg

    def test_truncates_long_generated_text_in_log(self, caplog):
        import logging
        long_text = "x" * 500
        with caplog.at_level(logging.WARNING, logger="jobpulse.cv_tailor"):
            _record_validation_failure(
                "summary", "TestCo", "too long", long_text,
            )
        msg = caplog.records[-1].getMessage()
        # Truncated to 200 chars in the log
        assert "x" * 200 in msg
        assert "x" * 201 not in msg

    def test_does_not_send_telegram(self, monkeypatch):
        # Sentinel: send_jobs MUST NOT be called from cv_tailor anymore.
        called = []

        def boom(*_a, **_kw):
            called.append(True)
            return True

        from jobpulse import telegram_bots as _tb
        monkeypatch.setattr(_tb, "send_jobs", boom)

        _record_validation_failure("summary", "Acme", "bad", "generated text")
        assert called == [], "cv_tailor should not call send_jobs anymore"


# ---------------------------------------------------------------------------
# Helpers shared across tailoring tests
# ---------------------------------------------------------------------------

_VALID_SUMMARY = (
    "I am a <b>data scientist</b> with 3 years of experience building scalable "
    "ML pipelines that process 50k events per second, reducing inference costs by 30% "
    "and improving model accuracy by 2x across distributed systems."
)

_VALID_TAGLINE = "MSc Computer Science (UOD) | 2+ YOE | Data Scientist | Python, ML, NLP"


# ---------------------------------------------------------------------------
# tailor_summary_and_tagline
# ---------------------------------------------------------------------------

class TestTailorSummaryAndTagline:
    def test_tailor_summary_and_tagline_success(self, monkeypatch):
        payload = json.dumps({"tagline": _VALID_TAGLINE, "summary": _VALID_SUMMARY})
        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kw: payload)

        alerts = []
        monkeypatch.setattr(
            "jobpulse.telegram_bots.send_jobs",
            lambda msg: alerts.append(msg),
            raising=False,
        )

        result = tailor_summary_and_tagline(
            jd_title="Data Scientist",
            jd_description="Looking for a data scientist.",
            company="Acme Corp",
            required_skills=["Python", "ML", "NLP"],
            preferred_skills=["TensorFlow"],
        )

        assert result is not None
        assert isinstance(result, TailoredHeader)
        assert result.tagline == _VALID_TAGLINE
        assert result.summary == _VALID_SUMMARY
        assert alerts == []

    def test_tailor_summary_and_tagline_llm_failure(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("LLM down")

        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", boom)

        result = tailor_summary_and_tagline(
            jd_title="Data Scientist",
            jd_description="desc",
            company="Acme Corp",
            required_skills=["Python"],
            preferred_skills=[],
        )
        assert result is None

    def test_tailor_summary_and_tagline_bad_json(self, monkeypatch):
        monkeypatch.setattr(
            "jobpulse.cv_tailor.cognitive_llm_call",
            lambda **kw: "this is not json at all!!!",
        )

        result = tailor_summary_and_tagline(
            jd_title="Data Scientist",
            jd_description="desc",
            company="Acme Corp",
            required_skills=["Python"],
            preferred_skills=[],
        )
        assert result is None

    def test_tailor_summary_validation_failure_logs_no_telegram(self, monkeypatch, caplog):
        """Validation failure must log a warning but NOT call send_jobs.
        The retry path returns the same short summary so the second
        attempt also fails — the result is still returned (not None) so
        the caller can fall back to template values."""
        import logging
        short_summary = "<b>Engineer</b> short."
        payload = json.dumps({"tagline": _VALID_TAGLINE, "summary": short_summary})
        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kw: payload)

        alerts: list[str] = []
        from jobpulse import telegram_bots as _tb
        monkeypatch.setattr(_tb, "send_jobs", lambda msg: alerts.append(msg))

        with caplog.at_level(logging.WARNING, logger="jobpulse.cv_tailor"):
            result = tailor_summary_and_tagline(
                jd_title="Data Scientist",
                jd_description="desc",
                company="Acme Corp",
                required_skills=["Python"],
                preferred_skills=[],
            )

        assert result is not None
        assert result.summary == short_summary
        # No Telegram alert
        assert alerts == []
        # But the failure was logged
        assert any("Acme Corp" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# tailor_experience_bullets
# ---------------------------------------------------------------------------

class TestTailorExperienceBullets:
    def _make_entry(self, title="Engineer", company="ACME", bullets=None):
        return ExperienceEntry(
            title=title,
            company=company,
            dates="2023-2024",
            bullets=bullets or ["Built pipeline processing 50k events/sec, saving 30% cost"],
            location="London",
        )

    def test_tailor_experience_success(self, monkeypatch):
        original = [self._make_entry()]
        response_data = [
            {
                "title": "Engineer",
                "company": "ACME",
                "dates": "2023-2024",
                "bullets": ["Engineered pipeline handling 50k events/sec, reducing cost by 30%"],
            }
        ]
        monkeypatch.setattr(
            "jobpulse.cv_tailor.cognitive_llm_call",
            lambda **kw: json.dumps(response_data),
        )

        result = tailor_experience_bullets(
            experience=original,
            jd_title="Data Scientist",
            required_skills=["Python", "ML"],
            preferred_skills=[],
            company="Acme Corp",
        )

        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], ExperienceEntry)
        assert result[0].title == "Engineer"
        assert result[0].company == "ACME"
        assert result[0].location == "London"
        # Metric preserved
        assert "50k" in result[0].bullets[0]

    def test_tailor_experience_llm_failure(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("LLM down")

        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", boom)

        result = tailor_experience_bullets(
            experience=[self._make_entry()],
            jd_title="Data Scientist",
            required_skills=["Python"],
            preferred_skills=[],
            company="Acme Corp",
        )
        assert result is None


# ---------------------------------------------------------------------------
# tailor_project_bullets
# ---------------------------------------------------------------------------

class TestTailorProjectBullets:
    def _make_project(self, title="RealtimePipeline", bullets=None, url="https://github.com/test/proj"):
        return {
            "title": title,
            "url": url,
            "bullets": bullets or [
                "Built 3 REST APIs serving 50k daily requests",
                "Reduced latency by 40% via caching layer",
                "Deployed 5 microservices on Kubernetes",
            ],
        }

    def test_tailor_projects_success(self, monkeypatch):
        original = [self._make_project()]
        response_data = [
            {
                "title": "RealtimePipeline",
                "bullets": [
                    "Engineered 3 ML-powered REST APIs serving 50k daily requests",
                    "Optimised inference latency by 40% using Redis caching",
                    "Deployed 5 containerised microservices on Kubernetes",
                ],
            }
        ]
        monkeypatch.setattr(
            "jobpulse.cv_tailor.cognitive_llm_call",
            lambda **kw: json.dumps(response_data),
        )

        result = tailor_project_bullets(
            projects=original,
            jd_title="Data Scientist",
            required_skills=["Python", "ML"],
            preferred_skills=["Kubernetes"],
            company="Acme Corp",
        )

        assert result is not None
        assert len(result) == 1
        # Original URL preserved
        assert result[0]["url"] == "https://github.com/test/proj"
        # Original title preserved from input
        assert result[0]["title"] == "RealtimePipeline"
        # Metrics preserved
        assert any("50k" in b for b in result[0]["bullets"])
        assert any("40" in b for b in result[0]["bullets"])

    def test_tailor_projects_llm_failure(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("LLM down")

        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", boom)

        result = tailor_project_bullets(
            projects=[self._make_project()],
            jd_title="Data Scientist",
            required_skills=["Python"],
            preferred_skills=[],
            company="Acme Corp",
        )
        assert result is None


# ---------------------------------------------------------------------------
# tailor_cover_letter_prose
# ---------------------------------------------------------------------------

class TestTailorCoverLetterProse:
    _VALID_INTRO = (
        "I am excited to apply to Acme Corp for the Data Scientist role, "
        "having followed your work in scalable ML infrastructure with great interest."
    )
    _VALID_HOOK = (
        "Over 3 years I built pipelines processing 50k events/sec, cutting infrastructure "
        "costs by 30% for a fintech platform."
    )
    _VALID_CLOSING = (
        "I would welcome the opportunity to discuss how my expertise can contribute "
        "to Acme Corp's engineering goals."
    )

    def test_tailor_cover_letter_prose_success(self, monkeypatch):
        payload = json.dumps({
            "intro": self._VALID_INTRO,
            "hook": self._VALID_HOOK,
            "closing": self._VALID_CLOSING,
        })
        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", lambda **kw: payload)

        result = tailor_cover_letter_prose(
            company="Acme Corp",
            role="Data Scientist",
            required_skills=["Python", "ML"],
            matched_projects=[{"title": "RealtimePipeline"}],
        )

        assert result is not None
        assert isinstance(result, TailoredCoverLetter)
        assert "Acme Corp" in result.intro
        assert result.hook == self._VALID_HOOK
        assert result.closing == self._VALID_CLOSING

    def test_tailor_cover_letter_prose_llm_failure(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("LLM down")

        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", boom)

        result = tailor_cover_letter_prose(
            company="Acme Corp",
            role="Data Scientist",
            required_skills=["Python"],
            matched_projects=[],
        )
        assert result is None


# ---------------------------------------------------------------------------
# tailor_all_sections
# ---------------------------------------------------------------------------

class _ListingProxy:
    title = "Data Scientist"
    company = "Acme Corp"
    required_skills = ["Python", "ML", "NLP"]
    preferred_skills = ["TensorFlow"]
    description_raw = "Looking for a data scientist."


class TestTailorAllSections:
    def _make_experience(self):
        return [
            ExperienceEntry(
                title="Engineer",
                company="ACME",
                dates="2023-2024",
                bullets=["Built pipeline processing 50k events/sec, saving 30% cost"],
            )
        ]

    def _make_projects(self):
        return [
            {
                "title": "RealtimePipeline",
                "url": "https://github.com/test/proj",
                "bullets": [
                    "Built 3 REST APIs serving 50k daily requests",
                    "Reduced latency by 40% via caching layer",
                    "Deployed 5 microservices on Kubernetes",
                ],
            }
        ]

    def test_tailor_all_sections_parallel(self, monkeypatch):
        call_log = []

        def fake_cognitive_llm_call(**kw):
            task = kw.get("task", "")
            call_log.append(task)
            if "tagline" in task:
                return json.dumps({"tagline": _VALID_TAGLINE, "summary": _VALID_SUMMARY})
            if "experience" in task.lower() and "cover letter" not in task.lower():
                return json.dumps([
                    {
                        "title": "Engineer",
                        "company": "ACME",
                        "dates": "2023-2024",
                        "bullets": ["Built pipeline handling 50k events/sec, cutting cost by 30%"],
                    }
                ])
            if "cover letter" in task.lower():
                return json.dumps({
                    "intro": (
                        "I am excited to apply to Acme Corp for the Data Scientist role, "
                        "having followed your innovation in ML infrastructure."
                    ),
                    "hook": (
                        "Over 3 years I built ML pipelines processing 50k events/sec, "
                        "cutting costs by 30% for a fintech platform."
                    ),
                    "closing": (
                        "I would welcome the opportunity to discuss how my expertise "
                        "can contribute to Acme Corp's goals."
                    ),
                })
            if "project" in task.lower():
                return json.dumps([
                    {
                        "title": "RealtimePipeline",
                        "bullets": [
                            "Engineered 3 ML APIs serving 50k daily requests",
                            "Reduced latency by 40% via caching",
                            "Deployed 5 microservices on Kubernetes",
                        ],
                    }
                ])
            return json.dumps({})

        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", fake_cognitive_llm_call)

        result = tailor_all_sections(
            listing=_ListingProxy(),
            matched_projects=self._make_projects(),
            experience=self._make_experience(),
        )

        assert isinstance(result, TailoredCV)
        assert result.tagline is not None
        assert result.summary is not None
        assert result.experience is not None
        assert result.projects is not None
        assert result.cover_letter is not None
        # All 4 LLM calls were made
        assert len(call_log) == 4

    def test_tailor_all_sections_partial_failure(self, monkeypatch):
        def fake_cognitive_llm_call(**kw):
            task = kw.get("task", "")
            if "tagline" in task:
                return json.dumps({"tagline": _VALID_TAGLINE, "summary": _VALID_SUMMARY})
            if "experience" in task.lower() and "cover letter" not in task.lower():
                raise RuntimeError("LLM down for experience")
            if "cover letter" in task.lower():
                return json.dumps({
                    "intro": (
                        "I am excited to apply to Acme Corp for the Data Scientist role, "
                        "having followed your innovation in ML infrastructure."
                    ),
                    "hook": (
                        "Over 3 years I built ML pipelines processing 50k events/sec, "
                        "cutting costs by 30% for a fintech platform."
                    ),
                    "closing": (
                        "I would welcome the opportunity to discuss how my expertise "
                        "can contribute to Acme Corp's goals."
                    ),
                })
            if "project" in task.lower():
                return json.dumps([
                    {
                        "title": "RealtimePipeline",
                        "bullets": [
                            "Engineered 3 ML APIs serving 50k daily requests",
                            "Reduced latency by 40% via caching",
                            "Deployed 5 microservices on Kubernetes",
                        ],
                    }
                ])
            return json.dumps({})

        monkeypatch.setattr("jobpulse.cv_tailor.cognitive_llm_call", fake_cognitive_llm_call)

        result = tailor_all_sections(
            listing=_ListingProxy(),
            matched_projects=self._make_projects(),
            experience=self._make_experience(),
        )

        assert isinstance(result, TailoredCV)
        # Experience failed — must be None
        assert result.experience is None
        # Other 3 sections succeeded
        assert result.tagline is not None
        assert result.projects is not None
        assert result.cover_letter is not None


# ---------------------------------------------------------------------------
# _parse_llm_json — robust JSON extraction (regression for Tier-2 fix)
# ---------------------------------------------------------------------------

class TestParseLlmJson:
    def test_clean_object(self):
        assert _parse_llm_json('{"a": 1}') == {"a": 1}

    def test_clean_array(self):
        assert _parse_llm_json('[1, 2, 3]') == [1, 2, 3]

    def test_markdown_fenced_json(self):
        raw = '```json\n{"tagline": "x", "summary": "y"}\n```'
        assert _parse_llm_json(raw) == {"tagline": "x", "summary": "y"}

    def test_markdown_fenced_no_lang(self):
        raw = '```\n[{"a": 1}]\n```'
        assert _parse_llm_json(raw) == [{"a": 1}]

    def test_prose_prefix_then_object(self):
        raw = 'Here is the JSON:\n{"intro": "hello"}'
        assert _parse_llm_json(raw) == {"intro": "hello"}

    def test_prose_prefix_then_array(self):
        raw = 'Sure! [{"title": "x", "bullets": []}]'
        assert _parse_llm_json(raw) == [{"title": "x", "bullets": []}]

    def test_empty_string_raises_decode_error(self):
        # Caller handles JSONDecodeError specifically — must keep that contract.
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_json("")

    def test_none_raises_decode_error(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_json(None)

    def test_whitespace_only_raises_decode_error(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_json("   \n  ")

    def test_no_json_in_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_json("the model refused to answer")

    def test_unwraps_single_key_object_wrapping_array(self):
        """OpenAI's response_format={"type":"json_object"} forces a top-level
        object even when the prompt asks for an array — the LLM wraps with a
        single key like {"experience": [...]}. Unwrap when the only value is
        a list so callers expecting arrays see them directly.
        """
        wrapped = '{"experience": [{"title": "Engineer", "bullets": ["b1"]}]}'
        result = _parse_llm_json(wrapped)
        assert isinstance(result, list)
        assert result == [{"title": "Engineer", "bullets": ["b1"]}]

    def test_does_not_unwrap_multi_key_object(self):
        """Multi-key dicts (e.g. {"intro":..., "hook":..., "closing":...} for
        cover letters) must be returned as-is — only single-key objects get
        unwrapped.
        """
        multi = '{"intro": "hi", "hook": "there", "closing": "bye"}'
        result = _parse_llm_json(multi)
        assert isinstance(result, dict)
        assert set(result.keys()) == {"intro", "hook", "closing"}

    def test_does_not_unwrap_when_value_is_not_list(self):
        single_scalar = '{"answer": "yes"}'
        result = _parse_llm_json(single_scalar)
        assert isinstance(result, dict)
        assert result == {"answer": "yes"}


class TestTailorParsesMarkdownFencedJson:
    """Regression: 4 cv_tailor functions used to fail every run because the
    cognitive engine wraps JSON in markdown fences. Now they tolerate fences,
    prose prefixes, and a few other realistic shapes from the LLM.
    """
    def test_summary_and_tagline_accepts_markdown_fence(self, monkeypatch):
        wrapped = '```json\n{"tagline": "MSc CS | 3+ YOE | Data Engineer", "summary": "Strong data engineer with experience in Python and SQL."}\n```'
        monkeypatch.setattr(
            "jobpulse.cv_tailor.cognitive_llm_call",
            lambda **kwargs: wrapped,
        )
        result = tailor_summary_and_tagline(
            jd_title="Data Engineer",
            jd_description="Build data pipelines",
            company="Acme",
            required_skills=["python", "sql"],
            preferred_skills=[],
        )
        assert result is not None
        assert result.tagline.startswith("MSc CS")
        assert "Acme" not in result.summary or "data" in result.summary.lower()

    def test_summary_and_tagline_returns_none_on_empty_response(self, monkeypatch):
        # Empty response from cognitive engine used to crash json.loads with
        # "Expecting value: line 1 column 1 (char 0)". Now we return None
        # cleanly so the caller can fall back to defaults.
        monkeypatch.setattr(
            "jobpulse.cv_tailor.cognitive_llm_call",
            lambda **kwargs: "",
        )
        result = tailor_summary_and_tagline(
            jd_title="Data Engineer",
            jd_description="Build data pipelines",
            company="Acme",
            required_skills=["python"],
            preferred_skills=[],
        )
        assert result is None
