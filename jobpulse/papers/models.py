"""Pydantic models for the papers pipeline."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class Paper(BaseModel):
    """A paper from arXiv or HuggingFace."""

    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    pdf_url: str
    arxiv_url: str
    published_at: str
    source: Literal["arxiv", "huggingface", "both"] = "arxiv"
    hf_upvotes: int | None = None
    linked_models: list[str] = Field(default_factory=list)
    linked_datasets: list[str] = Field(default_factory=list)
    model_card_summary: str | None = None
    github_url: str = ""
    github_stars: int = 0
    s2_citation_count: int = 0
    s2_influential_citations: int = 0
    community_buzz: int = 0
    sources: list[str] = Field(default_factory=list)


class FactCheckResult(BaseModel):
    """Result of fact-checking a paper summary."""

    score: float = 0.0
    total_claims: int = 0
    verified_count: int = 0
    issues: list[str] = Field(default_factory=list)
    explanation: str = ""
    repo_health: str | None = None


class RankedPaper(Paper):
    """A paper with ranking scores and summary."""

    fast_score: float = 0.0
    impact_score: float = 0.0
    impact_reason: str = ""
    category_tag: str = ""
    key_technique: str = ""
    practical_takeaway: str = ""
    summary: str = ""
    fact_check: FactCheckResult | None = None


class Chart(BaseModel):
    """A generated chart image for a blog post."""

    chart_type: Literal["bar_comparison", "line_scaling", "radar_multi", "table_image"]
    title: str
    data: dict
    png_path: str
    description: str


class BlogPost(BaseModel):
    """A generated blog post from a paper."""

    title: str
    content: str
    charts: list[Chart] = Field(default_factory=list)
    mermaid_code: str = ""
    diagram_url: str = ""
    word_count: int = 0
    grpo_score: float = 0.0
    fact_check: FactCheckResult | None = None
    paper: Paper
    generated_at: str


class ReadingStats(BaseModel):
    """Paper reading statistics."""

    total: int = 0
    read: int = 0
    unread: int = 0
    this_week: int = 0
    blog_count: int = 0
    with_models: int = 0
