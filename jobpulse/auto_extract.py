"""Auto-extraction hooks — runs knowledge extraction after significant agent events.

Wired into agents so that after every meaningful action, entities and
relationships are automatically extracted and added to the knowledge graph.

Only extracts from content that's likely to contain useful knowledge:
- Research paper summaries (rich in entities + relationships)
- Email classifications with company/role context
- Agent conversation transcripts
- Manually uploaded text

Does NOT extract from:
- Simple status messages ("No commits yesterday")
- Calendar event titles (too short)
- Budget transactions (numbers, not knowledge)
"""

from mindgraph_app.extractor import extract_from_text
from jobpulse import event_logger
from shared.logging_config import get_logger

logger = get_logger(__name__)


def extract_from_email(sender: str, subject: str, category: str, body_snippet: str):
    """Extract company/role entities from a classified recruiter email."""
    text = f"Recruiter email from {sender}. Subject: {subject}. Category: {category}. {body_snippet}"

    # Only extract from recruiter emails (categories 1-3), not OTHER
    if category == "OTHER":
        return

    try:
        result = extract_from_text(text, filename=f"email_{subject[:30]}")
        if result.get("status") == "ok" and result.get("entities_extracted", 0) > 0:
            event_logger.log_event(
                event_type="knowledge_extracted",
                agent_name="auto_extract",
                action="email_extraction",
                content=f"Extracted {result['entities_extracted']} entities from email: {subject[:50]}",
                metadata=result,
            )
    except Exception as e:
        logger.warning("Email extraction failed: %s", e)


def extract_from_paper_summary(title: str, authors: str, summary: str, arxiv_id: str = ""):
    """Extract knowledge from a research paper summary."""
    text = f"Research Paper: {title}\nAuthors: {authors}\narXiv: {arxiv_id}\n\n{summary}"

    try:
        result = extract_from_text(text, filename=f"paper_{arxiv_id or title[:30]}")
        if result.get("status") == "ok" and result.get("entities_extracted", 0) > 0:
            event_logger.log_event(
                event_type="knowledge_extracted",
                agent_name="auto_extract",
                action="paper_extraction",
                content=f"Extracted {result['entities_extracted']} entities from paper: {title[:50]}",
                metadata={**result, "arxiv_id": arxiv_id, "title": title},
            )
    except Exception as e:
        logger.warning("Paper extraction failed: %s", e)


def extract_from_conversation(transcript: str, topic: str = "", agents: list[str] = None):
    """Extract knowledge from a multi-agent conversation transcript."""
    prefix = f"Multi-agent conversation about: {topic}\nAgents: {', '.join(agents or [])}\n\n"
    text = prefix + transcript

    try:
        result = extract_from_text(text, filename=f"conversation_{topic[:30]}")
        if result.get("status") == "ok" and result.get("entities_extracted", 0) > 0:
            event_logger.log_event(
                event_type="knowledge_extracted",
                agent_name="auto_extract",
                action="conversation_extraction",
                content=f"Extracted {result['entities_extracted']} entities from conversation: {topic[:50]}",
                metadata=result,
            )
    except Exception as e:
        logger.warning("Conversation extraction failed: %s", e)


def extract_from_text_input(text: str, source: str = "manual"):
    """Extract knowledge from manually provided text."""
    try:
        result = extract_from_text(text, filename=source)
        if result.get("status") == "ok":
            event_logger.log_event(
                event_type="knowledge_extracted",
                agent_name="auto_extract",
                action="manual_extraction",
                content=f"Extracted {result.get('entities_extracted', 0)} entities from {source}",
                metadata=result,
            )
        return result
    except Exception as e:
        logger.warning("Manual extraction failed: %s", e)
        return {"status": "error", "error": str(e)}
