"""research_journal — daily curated ML/LLM/SLM/VLM research feed.

Pipeline: ingest -> domain classify -> hard-filter (results) -> rank -> verify
        -> summarize (3-agent: Extract -> Write -> Hallucination Guard)
        -> publish to Notion + Telegram.
"""

from research_journal.pipeline import JournalPipeline

__all__ = ["JournalPipeline"]
