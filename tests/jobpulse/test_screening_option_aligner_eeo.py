"""EEO-style yes/no alignment — audit-slice S4 / TP-7.

Pre-S4 behaviour: ``OptionAligner.align_answer("No", options)`` against
EEO-style options like::

    [
        "Yes, I have a disability, or have had one in the past",
        "No, I do not have a disability and have not had one in the past",
        "I do not want to answer",
    ]

dropped the answer (returned ``"No"`` unchanged → caller saw it wasn't
in ``options`` and discarded). Root cause: the embedding tier scores
short answers like ``"No"`` at 0.48–0.53 against full option text —
below the 0.70 ``min_score`` floor — and the fuzzy-prefix tier scores
even lower because of the length disparity. So the cascade fell to
"return original answer", which is correct **only** when no alignment
is reasonable; for EEO it should align to the prefix-matching option.

Observed live on Anthropic Greenhouse (run_final 2026-05-10):

    screening answer 'No' did not align to any option for
    'Veteran Status' — dropping

The system only "recovered" via a cache hit on a prior correction
(``intent=ai_assist, option_aligned=True``); on a never-seen-before
profile/job the form would have submitted with the EEO field empty.

Fix: add a yes/no prefix/substring-count tier between exact-match and
embedding-similarity. Self-contained (no delegation back through
BoolFieldHandler to avoid mutual-recursion concerns) and tied to the
first-token-after-punctuation of each option plus a YES/NO substring
fallback for "I am not a protected veteran"-style options that don't
START with "no" but carry the negation semantically.
"""

from jobpulse.screening_option_aligner import OptionAligner


# ── EEO option sets observed live on Anthropic Greenhouse ────────────────

VETERAN_OPTIONS = [
    "I am not a protected veteran",
    "I identify as one or more of the classifications of a protected veteran",
    "I don't wish to answer",
]

DISABILITY_OPTIONS = [
    "Yes, I have a disability, or have had one in the past",
    "No, I do not have a disability and have not had one in the past",
    "I do not want to answer",
]

HISPANIC_LATINO_OPTIONS = [
    "Yes, I am Hispanic or Latino",
    "No, I am not Hispanic or Latino",
    "I do not wish to answer",
]

GENDER_BINARY_OPTIONS = ["Yes", "No"]


class TestEEOYesNoAlignment:
    """``align_answer("No", eeo_options)`` MUST map to the correct option
    on the first pass without delegating to a cache / corrections layer.
    A first-pass drop is what TP-7 observed live."""

    def test_no_aligns_to_veteran_negation(self):
        result = OptionAligner().align_answer(
            "No", VETERAN_OPTIONS, field_type="select",
        )
        assert result == "I am not a protected veteran", (
            f"Expected veteran negation option, got {result!r}"
        )

    def test_no_aligns_to_disability_negation(self):
        result = OptionAligner().align_answer(
            "No", DISABILITY_OPTIONS, field_type="select",
        )
        assert result == "No, I do not have a disability and have not had one in the past", (
            f"Expected disability 'No' option, got {result!r}"
        )

    def test_yes_aligns_to_disability_affirmation(self):
        result = OptionAligner().align_answer(
            "Yes", DISABILITY_OPTIONS, field_type="select",
        )
        assert result == "Yes, I have a disability, or have had one in the past"

    def test_no_aligns_to_hispanic_latino_negation(self):
        result = OptionAligner().align_answer(
            "No", HISPANIC_LATINO_OPTIONS, field_type="select",
        )
        assert result == "No, I am not Hispanic or Latino"

    def test_yes_aligns_to_hispanic_latino_affirmation(self):
        result = OptionAligner().align_answer(
            "Yes", HISPANIC_LATINO_OPTIONS, field_type="select",
        )
        assert result == "Yes, I am Hispanic or Latino"

    def test_simple_yes_no_options_still_work(self):
        """Regression: simple two-option yes/no must still align cleanly
        via the existing exact-match tier (don't accidentally route
        these through the new prefix tier and break the case)."""
        result = OptionAligner().align_answer(
            "No", GENDER_BINARY_OPTIONS, field_type="select",
        )
        assert result == "No"
        result = OptionAligner().align_answer(
            "Yes", GENDER_BINARY_OPTIONS, field_type="select",
        )
        assert result == "Yes"

    def test_normalised_yes_no_aligns(self):
        """Lowercase variants must also align via the new tier."""
        assert OptionAligner().align_answer(
            "no", DISABILITY_OPTIONS, field_type="select",
        ) == "No, I do not have a disability and have not had one in the past"
        assert OptionAligner().align_answer(
            "YES", HISPANIC_LATINO_OPTIONS, field_type="select",
        ) == "Yes, I am Hispanic or Latino"


class TestNoAccidentalAlignmentOnNonYesNoAnswers:
    """A non-yes/no answer must NOT be routed through the new prefix
    tier. The existing cascade (embedding → fuzzy) should still produce
    the correct result for substantive answers."""

    def test_substantive_answer_does_not_collapse_to_yes_no_first_token(self):
        """If the answer is something like 'I prefer not to say', it
        must NOT match a first-token 'I' option just because the
        normalised first token happens to match."""
        result = OptionAligner().align_answer(
            "Prefer not to answer", DISABILITY_OPTIONS, field_type="select",
        )
        # Acceptable: either the prefer-not option or fall-through; what
        # is NOT acceptable is silently picking "Yes, I have a disability"
        # via a broken first-token tiebreak.
        assert "Yes, I have" not in result
        assert "No, I do not" not in result


class TestNoRecursionOnFallback:
    """The new tier must be self-contained — no infinite recursion if
    BoolFieldHandler.resolve falls back through align_answer (which it
    does for ambiguous answers). Sentinel: simulate the worst case."""

    def test_ambiguous_answer_does_not_recurse(self):
        """An answer that's neither yes nor no must complete in bounded
        time (no recursion). If recursion existed, this would
        RecursionError out."""
        import sys
        # Set a low recursion limit just to be sure; restore after.
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(50)
        try:
            result = OptionAligner().align_answer(
                "Maybe — I'm not sure", DISABILITY_OPTIONS, field_type="select",
            )
            assert isinstance(result, str)
        finally:
            sys.setrecursionlimit(old_limit)
