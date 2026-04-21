import pytest


class TestGoldenSuite:
    def test_loads_all_cases(self):
        from shared.adversarial._golden_suite import load_golden_suite
        cases = load_golden_suite()
        assert len(cases) >= 30

    def test_all_categories_present(self):
        from shared.adversarial._golden_suite import load_golden_suite
        cases = load_golden_suite()
        categories = {c.category for c in cases}
        assert categories == {"score_manipulation", "cross_agent_injection", "prompt_injection", "boundary"}

    def test_all_cases_have_required_fields(self):
        from shared.adversarial._golden_suite import load_golden_suite
        for case in load_golden_suite():
            assert case.id
            assert case.category
            assert case.input is not None
            assert case.expected_behavior
            assert case.severity in ("critical", "high", "medium")

    def test_no_duplicate_ids(self):
        from shared.adversarial._golden_suite import load_golden_suite
        cases = load_golden_suite()
        ids = [c.id for c in cases]
        assert len(ids) == len(set(ids))
