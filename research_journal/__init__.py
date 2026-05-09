"""research_journal — daily curated ML/LLM/SLM/VLM research feed.

Pipeline: ingest -> domain classify -> hard-filter (results) -> rank -> verify
        -> summarize (3-agent: Extract -> Write -> Hallucination Guard)
        -> publish to Notion + Telegram.

The orchestrator JournalPipeline is exported from .pipeline (added in Task 30).
"""
