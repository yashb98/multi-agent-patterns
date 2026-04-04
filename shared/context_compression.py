"""Context Compression — prevents context window overflow on multi-iteration runs.

After iteration 1, older research notes are summarised while the latest
note is kept verbatim. Total stays under MAX_RESEARCH_CHARS budget.
"""

from shared.logging_config import get_logger

logger = get_logger(__name__)

MAX_RESEARCH_CHARS = 8000  # Total budget for research_notes context


def compress_research_notes(notes: list[str]) -> list[str]:
    """Compress older research notes if total size exceeds budget.

    Strategy:
    - Keep the latest note verbatim (most relevant)
    - Summarise older notes into condensed bullet points
    - Total stays under MAX_RESEARCH_CHARS
    """
    if not notes:
        return notes

    total_chars = sum(len(n) for n in notes)
    if total_chars <= MAX_RESEARCH_CHARS:
        return notes

    if len(notes) <= 1:
        # Single note exceeds budget — truncate with marker
        return [notes[0][:MAX_RESEARCH_CHARS] + "\n\n[TRUNCATED — original was longer]"]

    # Keep latest note, compress older ones
    latest = notes[-1]
    older = notes[:-1]

    # Budget for older notes: total budget minus latest note size
    older_budget = max(500, MAX_RESEARCH_CHARS - len(latest))

    # Compress each older note to proportional share
    per_note_budget = older_budget // len(older)
    compressed = []
    for note in older:
        if len(note) <= per_note_budget:
            compressed.append(note)
        else:
            # Extract key sentences (first + last paragraph)
            paragraphs = [p.strip() for p in note.split("\n\n") if p.strip()]
            if len(paragraphs) <= 2:
                compressed.append(note[:per_note_budget] + "...")
            else:
                summary = paragraphs[0] + "\n\n[...compressed...]\n\n" + paragraphs[-1]
                compressed.append(summary[:per_note_budget])

    logger.info("Compressed research notes: %d chars → %d chars (%d notes)",
                total_chars, sum(len(n) for n in compressed) + len(latest), len(notes))

    return compressed + [latest]
