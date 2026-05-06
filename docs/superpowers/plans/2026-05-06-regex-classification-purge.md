# F5 Deep Purge — Migrate Regex Classification to Embeddings/LLM

> Standalone follow-up plan. Original "F5 — regex purge" in the universal
> dynamic-form-fill plan reviewed `consent_policy.py` and
> `screening_detector.py` and found they already comply. The four
> remaining violations are bigger refactors and warrant dedicated
> sessions per the advisor's "don't over-iterate" warning.

## Why now (still)

The Dynamic-Over-Hardcoded rule (`.claude/rules/seven-principles.md` §8)
says regex MUST NOT be used for: intent routing, question
categorization, consent detection, field matching, command parsing, or
screening question classification. Regex stays for: text normalization,
security sanitization, structural format validation (email/phone/date
patterns), number extraction.

Four files still violate. Each is its own focused PR — a single session
could land one file safely; bundling them risks regressions across
unrelated subsystems.

---

## File 1 — `jobpulse/screening_answers.py` (highest priority)

### Problem

Lines 117-220+: `COMMON_ANSWERS: dict[str, str | None] = { … }` is a
50+ entry dict keyed by regex patterns mapping to canned answers.
Lookup at line 752 is `re.search(pattern, normalised, re.IGNORECASE)`.

This is exactly the "regex for classification" rule violation: the keys
are paraphrase-detection regexes, not structural format checks.
Variations like `r"current.*salary|salary.*current|present.*salary|…"`
literally enumerate paraphrase alternatives — embeddings handle this
cleanly.

Other regex sites in the file:
- `:299` `re.search(pat, normalised)` — skill-experience extraction
  patterns. Mixed: structural format extraction is allowed; the
  question-class detection part is not.
- `:359, 366` `_re.findall(r"[a-z]{3,}", title)` — token tokenization,
  this is allowed (text normalization).
- `:655` `_re.search(_rule.get("pattern", ""), normalised)` — runs
  user-loaded rule regex; treat as user-data, not classification code.
- `:672, 752` `re.search(pattern, …)` — loops over `COMMON_ANSWERS`
  keys (the violation).

### Migration

Convert `COMMON_ANSWERS` into a list of `(anchor_phrases: list[str],
answer_template: str)` tuples. At module load, embed each anchor's
phrases. Lookup becomes:

```python
def lookup_canned_answer(question: str) -> str | None:
    from shared.semantic_utils import best_semantic_match
    qnorm = question.strip()
    for anchors, answer in CANNED_ANSWERS:
        match, score = best_semantic_match(qnorm, anchors, min_score=0.72)
        if match is not None:
            return _resolve_placeholder(answer, qnorm, …)
    return None
```

Each `anchors` list is the **paraphrases** the original regex was
matching. So `r"current.*salary|salary.*current|present.*salary|current.*compensation|current.*base"`
becomes:
```python
(
    [
        "What is your current salary?",
        "What is your present salary?",
        "What is your current compensation?",
        "What is your current base salary?",
    ],
    "CURRENT_SALARY",
),
```

Tests: `tests/jobpulse/test_screening_canned_answer_embedding.py`.
- Each canonical question matches its expected answer at score ≥ 0.85.
- Out-of-vocabulary questions return None.
- Paraphrases the regex would have caught (e.g. "Tell me about your
  present remuneration") still match.
- Negative tests: "What is your current employer" must NOT match
  CURRENT_SALARY.

Ship as one commit. ~2 hours focused work — most of the cost is
hand-curating the anchor lists from the regex alternations.

---

## File 2 — `jobpulse/email_preclassifier.py`

### Problem

Lines 203, 211, 231, 239, 276, 284: `re.search(pattern, body_lower)`
and `re.search(pattern, subject_lower)`. The patterns come from a
rules-loaded dict; the patterns themselves are paraphrase-detection,
not structural format checks.

### Migration

Three-tier lookup that matches the existing `screening_detector.py`
shape:

1. **Embedding tier (primary)** — `nlp_classifier.py` already exposes
   `classify_intent(text, intent_examples)`. Add an `email_intent`
   wrapper. Each rule's intent label gets an `examples: list[str]` of
   labeled subjects/bodies; embed them once at module load. Classify by
   nearest-anchor cosine.

2. **Structural validation tier** — keep the existing email-format,
   from-domain, and reply-id regex *as structural format detection*
   (allowed by the rule). Move them out of `_RULES` into a dedicated
   `_STRUCTURAL_RULES` table so the boundary is explicit.

3. **LLM fallback tier** — already exists at `email_preclassifier.py`'s
   bottom. Unchanged.

Tests: `tests/jobpulse/test_email_preclassifier_embedding.py`.
- Recruiter cold outreach correctly classified.
- Rejection emails correctly classified.
- Newsletter / marketing correctly classified.
- Subject-line variants the regex would catch still match.

Ship as one commit. ~1 day.

---

## File 3 — `jobpulse/screening_decomposer.py`

### Problem

Lines 24, 32, 95, 121, 113, 208: regex compound-question detection.
`_COMPOUND_INDICATORS = re.compile(...)` matches conjunctions and
listing patterns — paraphrase detection, not structural format.

### Migration

The decomposer's job is to detect when one form field contains
multiple distinct questions (e.g. "What is your salary expectation
and notice period?") so screening can split + answer each part.

Migration:
1. Replace `_COMPOUND_INDICATORS` regex with an LLM classifier:
   ```python
   def is_compound_question(text: str) -> bool:
       cached = self._compound_cache.get(text)
       if cached is not None:
           return cached
       result = cognitive_llm_call(
           task=f"Is this question asking for >1 distinct piece of "
                f"information? Answer yes or no.\n\n{text}",
           domain="screening_decomposer",
           stakes="low",
       )
       answer = "yes" in (result or "").lower()
       self._compound_cache[text] = answer
       return answer
   ```
2. Cache key: SHA-256 of normalized question text. Persist cache in
   `data/screening_decomposer_cache.db`.
3. Fall back to a single-question return when LLM unavailable.

Tests: `tests/jobpulse/test_screening_decomposer_llm.py`.
- Compound questions classified `True`.
- Atomic questions classified `False`.
- Cache hit on second call (LLM not re-invoked).

Ship as one commit. ~half day.

---

## File 4 — `jobpulse/dispatcher.py:279-598`

### Problem

The flat dispatcher's command-parsing branch does regex matching on
Telegram-message text to route to handlers. Lines 279-598 are 300+
lines of `re.match(r"^/?(budget|tasks|jobs|…)", text, re.I)` style
checks. This is intent classification.

### Migration

`jobpulse/nlp_classifier.py` already exists and supports embedding-tier
classification. Migration:

1. Each regex branch maps to a single intent name. Convert each to
   `(intent_name, [example_phrases])` registered with `nlp_classifier`.
2. The dispatcher's main routing function becomes:
   ```python
   from jobpulse.nlp_classifier import classify
   intent = classify(text)  # already exists
   handler = get_handler_map().get(intent)
   if handler:
       return await handler(text, …)
   ```
3. Both `dispatcher.py` and `swarm_dispatcher.py` consume
   `get_handler_map()` so this fix lands in one place.
4. Verify: regex tier in `nlp_classifier.py` is *legacy* (per rules);
   migration must add **embedding examples** for any regex-only
   intents. Don't add new regex examples.

Tests: `tests/jobpulse/test_dispatcher_embedding_routing.py`.
- Each intent's canonical command routes correctly.
- Voice-transcribed variants (Whisper-style punctuation) route
  correctly.
- Out-of-vocabulary commands fall through to LLM tier (existing
  behavior).
- Both dispatchers route identically (regression test pattern from
  CLAUDE.md).

Ship as one commit. ~1 day — most of the cost is curating example
phrases per intent and adding NLP examples without breaking the
existing flat-dispatcher fallback chain.

---

## Execution order

Strict ordering by **risk × impact**:

1. **File 3 — `screening_decomposer.py`** first. Smallest scope,
   self-contained, lowest blast radius. Validates the LLM-cache
   pattern.
2. **File 1 — `screening_answers.py`** second. Highest user-visible
   impact (powers every form-fill cache lookup). Uses the same
   embedding-anchor pattern as `consent_policy.py` and
   `screening_detector.py` — already-validated approach.
3. **File 2 — `email_preclassifier.py`** third. Different subsystem,
   different test surface. Daily-run code (Gmail polling) — needs the
   full embedding regression suite passing first.
4. **File 4 — `dispatcher.py`** last. Highest coupling (touches both
   flat + swarm dispatch). Validate by running both dispatchers
   against the same set of test commands and confirming identical
   routing.

Total: 3-4 dedicated sessions, one file per session. Validate by:

- All four files'-specific tests pass.
- `git grep -nE "re\.(search|match|findall)\(.+, normalised|_lower"
  jobpulse/` finds zero matches outside the four files' explicit
  structural-format extraction blocks.
- Live one-app run after each file ships: confirm screening cache
  hits, email classification, command routing all work as before.

---

## Out of scope

- The remaining structural-format regex (email format, phone format,
  URL parsing, date extraction). These are explicitly allowed by the
  rule.
- The deny-list regex in `consent_policy.py:34-61`. Already documented
  as a justified safety blocklist.
- The verification-wall pattern detection in `playwright_driver.py`.
  Cloudflare/reCAPTCHA selector literals — security-feature
  detection, not classification heuristic.

## Acceptance per file

Same checklist as F-phases:
- All new tests pass.
- One live end-to-end run exercising the migrated path (a Telegram
  command for File 4, a Gmail batch for File 2, a Revolut form fill
  for Files 1 & 3) completes without behavior regression.
- `git grep` confirms the targeted regex patterns are gone from the
  classification code paths.
- Cache (where added) actually persists rows after the run.

The session ends when all four files have shipped and the
seven-principles.md "REMAINING violations" list is empty for the
classification category.
