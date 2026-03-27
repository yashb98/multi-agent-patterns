---
paths: ["jobpulse/**/*.py"]
description: "JobPulse agent conventions — output trimming, context management"
---

# JobPulse Agent Conventions

## Output Trimming

All external API responses MUST be truncated before accumulating in context:
- Gmail body: max 500 chars
- Commit messages: max 100 chars per message
- Descriptions: max 80 chars
- File reads: max 10KB

## Context Management

- Never pass full API responses upstream — extract only relevant fields
- Include metadata (dates, source, agent name) in structured outputs
- Place key findings at the beginning of aggregated inputs (mitigate lost-in-the-middle)

## Financial Operations

- Amount validation is PROGRAMMATIC (not prompt-based): reject <= 0 or > 100,000
- Category classification: keyword match FIRST, LLM fallback SECOND
- All transactions go through: parse → validate → classify → store → sync

## Voice Input

- Strip trailing punctuation (`.!?`) before pattern matching
- Whisper adds proper punctuation that breaks exact regex anchors
