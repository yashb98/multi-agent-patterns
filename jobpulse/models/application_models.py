"""Pydantic v2 models for the Job Autopilot pipeline.

Covers job listings, ATS scoring, application records, and search configuration.
All models use Field(description=...) on every field so schemas can be fed directly
into Claude tool_use calls as-is.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ApplicationStatus(str, Enum):
    """Lifecycle states for a job application."""

    FOUND = "Found"
    ANALYZING = "Analyzing"
    READY = "Ready"
    PENDING_APPROVAL = "Pending Approval"
    APPLIED = "Applied"
    INTERVIEW = "Interview"
    OFFER = "Offer"
    REJECTED = "Rejected"
    WITHDRAWN = "Withdrawn"
    SKIPPED = "Skipped"


# ---------------------------------------------------------------------------
# JobListing
# ---------------------------------------------------------------------------


class JobListing(BaseModel):
    """A single job posting scraped from a job platform."""

    model_config = ConfigDict(strict=True)

    job_id: str = Field(
        description="SHA-256 hash of the job URL; used as the primary deduplication key."
    )
    title: str = Field(description="Job title as shown on the listing.")
    company: str = Field(description="Company name as shown on the listing.")
    platform: Literal["linkedin", "indeed", "reed", "totaljobs", "glassdoor"] = Field(
        description="Job board the listing was scraped from."
    )
    url: str = Field(description="Canonical URL of the job listing.")
    salary_min: float | None = Field(
        default=None, description="Lower bound of advertised salary in GBP per annum."
    )
    salary_max: float | None = Field(
        default=None, description="Upper bound of advertised salary in GBP per annum."
    )
    location: str = Field(description="Primary work location stated in the listing.")
    remote: bool = Field(
        default=False,
        description="True if the role is fully remote or hybrid-remote.",
    )
    seniority: Literal["intern", "graduate", "junior", "mid"] | None = Field(
        default=None,
        description="Inferred seniority level. None if not determinable.",
    )
    required_skills: list[str] = Field(
        default_factory=list,
        description="Skills explicitly listed as required / essential.",
    )
    preferred_skills: list[str] = Field(
        default_factory=list,
        description="Skills listed as nice-to-have or preferred.",
    )
    description_raw: str = Field(
        description="Full raw text of the job description before any parsing."
    )
    ats_platform: str | None = Field(
        default=None,
        description="Name of the ATS detected from the application URL (e.g. Workday, Greenhouse).",
    )
    found_at: datetime = Field(
        description="UTC timestamp when this listing was first discovered by the scanner."
    )
    easy_apply: bool = Field(
        default=False,
        description="True if the platform offers a one-click / Easy Apply flow.",
    )
    recruiter_email: str | None = Field(
        default=None,
        description="Recruiter or HR contact email extracted from the job description.",
    )


# ---------------------------------------------------------------------------
# ATSScore
# ---------------------------------------------------------------------------


class ATSScore(BaseModel):
    """ATS keyword-match score for a (CV, JobListing) pair."""

    model_config = ConfigDict(strict=True)

    total: float = Field(
        description="Composite ATS score in the range [0, 100]. Passing threshold is 95."
    )
    keyword_score: float = Field(
        description="Keyword match sub-score (0–70 points)."
    )
    section_score: float = Field(
        description="Section presence sub-score — work experience, education, skills (0–20 points)."
    )
    format_score: float = Field(
        description="Formatting / parsability sub-score (0–10 points)."
    )
    missing_keywords: list[str] = Field(
        default_factory=list,
        description="Required keywords present in the JD but absent from the CV.",
    )
    matched_keywords: list[str] = Field(
        default_factory=list,
        description="Required keywords found in both the JD and the CV.",
    )
    passed: bool = Field(
        default=False,
        description="True when total >= 95 — the CV is considered ATS-safe for this role.",
    )

    @model_validator(mode="after")
    def compute_passed(self) -> "ATSScore":
        """Derive `passed` deterministically from `total`; ignore any caller-supplied value."""
        self.passed = self.total >= 95
        return self


# ---------------------------------------------------------------------------
# ApplicationRecord
# ---------------------------------------------------------------------------


class ApplicationRecord(BaseModel):
    """Full lifecycle record for a single job application."""

    model_config = ConfigDict(strict=True)

    job: JobListing = Field(description="The job listing this record tracks.")
    status: ApplicationStatus = Field(
        default=ApplicationStatus.FOUND,
        description="Current lifecycle status of this application.",
    )
    ats_score: float = Field(
        default=0.0,
        description="Most recent composite ATS score (0–100). 0.0 = not yet scored.",
    )
    match_tier: Literal["auto", "review", "skip"] = Field(
        default="skip",
        description=(
            "Routing decision: 'auto' = apply automatically, "
            "'review' = queue for human approval, 'skip' = do not apply."
        ),
    )
    matched_projects: list[str] = Field(
        default_factory=list,
        description="GitHub project names from the portfolio that are relevant to this role.",
    )
    cv_path: Path | None = Field(
        default=None,
        description="Filesystem path to the tailored CV PDF generated for this application.",
    )
    cover_letter_path: Path | None = Field(
        default=None,
        description="Filesystem path to the cover letter PDF generated for this application.",
    )
    applied_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the application was submitted. None = not yet applied.",
    )
    notion_page_id: str | None = Field(
        default=None,
        description="Notion page ID of the corresponding row in the job tracker database.",
    )
    follow_up_date: date | None = Field(
        default=None,
        description="Date to send a follow-up message if no response has been received.",
    )
    custom_answers: dict[str, str] = Field(
        default_factory=dict,
        description="ATS-specific screening question answers keyed by question text.",
    )


# ---------------------------------------------------------------------------
# SearchConfig
# ---------------------------------------------------------------------------

_DEFAULT_EXCLUDE_KEYWORDS: list[str] = [
    "senior",
    "lead",
    "principal",
    "staff",
    "10+ years",
    "8+ years",
    "director",
]


class SearchConfig(BaseModel):
    """Configuration for a job-search run."""

    model_config = ConfigDict(strict=True)

    titles: list[str] = Field(
        description="Job titles to search for (e.g. ['Software Engineer', 'Backend Developer'])."
    )
    location: str = Field(
        default="United Kingdom",
        description="Geographic location filter passed to each job platform.",
    )
    include_remote: bool = Field(
        default=True,
        description="When True, remote and hybrid roles are included in results.",
    )
    salary_min: float = Field(
        default=27000,
        description="Minimum acceptable salary in GBP per annum. Listings below this are skipped.",
    )
    salary_max: float | None = Field(
        default=None,
        description="Maximum salary filter in GBP per annum. None = no upper limit.",
    )
    exclude_companies: list[str] = Field(
        default_factory=list,
        description="Company names to always skip, regardless of other match criteria.",
    )
    exclude_keywords: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_EXCLUDE_KEYWORDS),
        description=(
            "Keywords that, if present in the title or description, cause the listing to be skipped. "
            "Defaults to seniority filters: senior, lead, principal, staff, 10+ years, 8+ years, director."
        ),
    )
