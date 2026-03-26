# Feature: Natural Language Intent Classification

Replace brittle regex patterns with NLP-first intent classification that understands meaning, not just keywords.

## Problem

Current system uses **regex patterns → LLM fallback**. This breaks on natural speech:

| What User Says | Expected | Actual (regex) | Why It Fails |
|---|---|---|---|
| "I put in 6 hours at work today" | log_hours | conversation | No "worked/working" trigger word |
| "how much have I spent this week" | show_budget | conversation | Doesn't match exact pattern |
| "did any recruiter reply to me" | gmail | conversation | Doesn't match "check emails" |
| "what's left on my grocery budget" | show_budget | conversation | Too natural for regex |
| "knock off the dentist task" | complete_task | conversation | "knock off" not in pattern |
| "chuck 50 quid into savings" | log_savings | conversation | British slang |
| "I clocked seven hours monday" | log_hours | conversation | "clocked" not a trigger |

The LLM fallback catches some of these, but it's slow ($0.001 per call) and unreliable (sometimes returns wrong intent).

## Solution: 3-Tier Intent Classification

```
User Message
     │
     ▼
┌─────────────────────────────────┐
│  TIER 1: Regex (instant, free)  │ ← Catches exact commands: "budget", "undo 3", "git status"
│  Matches: ~60% of messages      │
└────────────┬────────────────────┘
             │ no match
             ▼
┌─────────────────────────────────┐
│  TIER 2: Semantic Classifier    │ ← Embeds message, compares to intent examples
│  (instant, free, local)         │   Uses sentence-transformers (384-dim embeddings)
│  Matches: ~35% of messages      │   Confidence threshold: 0.75
└────────────┬────────────────────┘
             │ low confidence
             ▼
┌─────────────────────────────────┐
│  TIER 3: LLM Fallback          │ ← Full gpt-4o-mini call with all intents
│  (~1s, $0.001)                  │   Only for truly ambiguous messages
│  Matches: ~5% of messages       │   Falls to CONVERSATION if still unsure
└─────────────────────────────────┘
```

## Tier 2: Semantic Classifier (The Key Addition)

### How It Works

Each intent gets 10-20 **example phrases**. On startup, these are embedded once using a small local model. When a new message arrives, embed it and find the closest intent by cosine similarity.

```python
INTENT_EXAMPLES = {
    "log_hours": [
        "worked 7 hours today",
        "I put in 6 hours at work",
        "clocked seven hours monday",
        "did a 5 hour shift yesterday",
        "logging 8 hours for tuesday",
        "I was at work for 4 hours",
        "6 and a half hours on the clock",
        "just finished a 3 hour shift",
    ],
    "show_budget": [
        "how much have I spent",
        "what's my budget looking like",
        "show me my spending",
        "weekly budget summary",
        "how much money left",
        "what's left on groceries",
        "am I over budget",
    ],
    "gmail": [
        "did any recruiter reply",
        "check my emails",
        "any interview invites",
        "new recruiter messages",
        "any job responses",
        "has anyone emailed me about the application",
    ],
    "log_spend": [
        "spent 15 on lunch",
        "paid 8 for coffee",
        "bought groceries for 30",
        "just dropped 50 on shoes",
        "uber was 12 quid",
        "cinema cost me 15",
        "chuck 20 on eating out",
    ],
    "complete_task": [
        "mark homework done",
        "finished the bug fix",
        "knock off the dentist task",
        "that's done now",
        "completed the report",
        "tick off apply to jobs",
    ],
    # ... (all 30+ intents get examples)
}
```

### Embedding Model Options

| Model | Size | Speed | Quality | Where It Runs |
|---|---|---|---|---|
| **all-MiniLM-L6-v2** (recommended) | 80MB | 5ms/embed | Good | Local CPU |
| all-mpnet-base-v2 | 420MB | 15ms/embed | Better | Local CPU |
| OpenAI text-embedding-3-small | 0 | 100ms/embed | Best | API ($0.00002/call) |

**Recommendation**: `all-MiniLM-L6-v2` — runs locally, no API cost, 5ms per classification. Good enough for intent matching.

### Classification Flow

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('all-MiniLM-L6-v2')  # loaded once at startup

# Pre-compute intent embeddings (once)
intent_embeddings = {}
for intent, examples in INTENT_EXAMPLES.items():
    intent_embeddings[intent] = model.encode(examples)  # matrix of embeddings

def classify_semantic(text: str) -> tuple[str, float]:
    """Returns (intent_name, confidence_score)."""
    query_embedding = model.encode([text])

    best_intent = "unknown"
    best_score = 0.0

    for intent, embeddings in intent_embeddings.items():
        # Cosine similarity against all examples for this intent
        similarities = cosine_similarity(query_embedding, embeddings)[0]
        max_sim = max(similarities)
        if max_sim > best_score:
            best_score = max_sim
            best_intent = intent

    return (best_intent, best_score)
```

### Confidence Thresholds

| Score | Action |
|---|---|
| >= 0.85 | High confidence — use directly |
| 0.75 - 0.85 | Good confidence — use but log for review |
| 0.60 - 0.75 | Low confidence — fall through to LLM |
| < 0.60 | No match — fall through to LLM |

## Training the Classifier

### Initial Setup (One-Time)

1. Write 10-20 example phrases per intent (manually)
2. Embed all examples → save to `data/intent_embeddings.npz`
3. Takes ~2 seconds for all 30 intents × 15 examples = 450 embeddings

### Continuous Learning

After every successful LLM classification (Tier 3), the message + resolved intent is stored as a new training example:

```python
# After LLM resolves "chuck 50 quid into savings" → log_savings
add_training_example("log_savings", "chuck 50 quid into savings")
# Re-embed periodically (every 100 new examples)
```

Over time, the semantic classifier learns slang, shortcuts, and personal patterns — reducing LLM fallback usage from 40% → <5%.

## Files to Create

| File | Purpose |
|------|---------|
| `jobpulse/nlp_classifier.py` | Semantic intent classifier with embedding model |
| `data/intent_examples.json` | Initial training examples per intent |
| `data/intent_embeddings.npz` | Pre-computed embeddings (generated on first run) |

## Files to Modify

| File | Change |
|------|--------|
| `jobpulse/command_router.py` | Insert Tier 2 between regex and LLM in `classify()` |
| `requirements.txt` | Add `sentence-transformers>=3.0.0` |

## Updated classify() Flow

```python
def classify(text: str) -> ParsedCommand:
    # Strip punctuation, bot mentions
    text = clean(text)

    # Tier 1: Regex (instant, free)
    result = classify_rule_based(text)
    if result:
        return result

    # Multi-line → tasks
    if is_task_list(text):
        return ParsedCommand(intent=Intent.CREATE_TASKS, args=text, raw=text)

    # Tier 2: Semantic classifier (5ms, free, local)
    intent, confidence = classify_semantic(text)
    if confidence >= 0.75:
        return ParsedCommand(intent=Intent(intent), args=text, raw=text)

    # Tier 3: LLM fallback (~1s, $0.001)
    result = classify_llm(text)

    # Learn from LLM result for future Tier 2 matches
    if result.intent != Intent.UNKNOWN:
        add_training_example(result.intent.value, text)

    # Unknown → conversation
    if result.intent == Intent.UNKNOWN:
        return ParsedCommand(intent=Intent.CONVERSATION, args=text, raw=text)

    return result
```

## Cost & Performance

| Tier | Latency | Cost | Accuracy |
|---|---|---|---|
| Regex | <1ms | Free | 100% (but low coverage) |
| Semantic | ~5ms | Free | ~90% (with good examples) |
| LLM | ~800ms | $0.001 | ~95% |
| **Combined** | **<10ms avg** | **~$0.001/day** | **~98%** |

Currently: ~40% of messages hit the LLM at $0.001 each.
After NLP: ~5% hit the LLM. **Saves ~$0.02/day, 8x faster average response.**

## Dependencies

```
sentence-transformers>=3.0.0    # ~200MB install (includes torch)
# OR for lighter alternative:
# onnxruntime + optimum         # ~50MB, same model but faster inference
```

Note: `sentence-transformers` pulls in PyTorch (~2GB). If that's too heavy, use the ONNX runtime alternative which is ~50MB and runs the same model.

## Phased Implementation

### Phase 1: Intent Examples + Semantic Classifier
- Write examples for all 30+ intents
- Build `nlp_classifier.py` with `classify_semantic()`
- Wire into `classify()` as Tier 2

### Phase 2: Continuous Learning
- Store LLM-resolved intents as new training examples
- Periodic re-embedding (every 100 new examples)
- Track accuracy: log Tier 2 decisions vs LLM decisions

### Phase 3: Voice Optimization
- Whisper transcriptions have specific patterns (capitalization, punctuation)
- Add Whisper-specific training examples
- Pre-process Whisper output before classification

## Success Metrics

- Tier 2 match rate (target: 35% → 60% over 4 weeks)
- LLM fallback rate (target: 40% → <5%)
- Average classification latency (target: <10ms)
- Intent accuracy (target: 98%+)
- New training examples/week from continuous learning
