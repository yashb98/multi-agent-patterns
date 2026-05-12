"""Journal-specific Pydantic models. Cross-cutting paper types live in jobpulse/papers/models.py."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


DomainTag = Literal["core", "tangent", "out"]
PaperType = Literal["research", "survey", "position", "tutorial", "workshop"]


class BenchResult(BaseModel):
    name: str           # benchmark name, e.g. "MMLU"
    metric: str         # e.g. "accuracy", "F1"
    value: float
    baseline: float | None = None

    @property
    def delta(self) -> float | None:
        if self.baseline is None:
            return None
        return round(self.value - self.baseline, 3)


class ExtractedFacts(BaseModel):
    problem: str
    method_steps: list[str]
    architecture_details: dict[str, str]
    benchmarks: list[BenchResult]
    ablations: list[str]
    limitations: list[str]
    key_insight: str
    raw_excerpts: list[str] = Field(min_length=1)


class VerificationBadge(BaseModel):
    has_results: bool
    peer_reviewed: bool
    has_repo: bool
    independent_citations: bool
    claims_grounded: bool
    reasons: dict[str, str] = Field(default_factory=dict)

    @property
    def score(self) -> int:
        return sum([
            self.has_results, self.peer_reviewed, self.has_repo,
            self.independent_citations, self.claims_grounded,
        ])


class PaperTypeClassification(BaseModel):
    has_results: bool
    paper_type: PaperType
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
