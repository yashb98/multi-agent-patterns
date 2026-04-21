import pytest


class TestInjectionTester:
    def test_score_integrity_all_pass(self):
        from shared.adversarial._injection_tester import InjectionTester
        from shared.adversarial._golden_suite import load_golden_suite
        tester = InjectionTester()
        cases = [c for c in load_golden_suite() if c.category == "score_manipulation"]
        results = tester.test_score_integrity(cases)
        assert all(r.passed for r in results), [r for r in results if not r.passed]

    def test_output_sanitization_all_pass(self):
        from shared.adversarial._injection_tester import InjectionTester
        from shared.adversarial._golden_suite import load_golden_suite
        tester = InjectionTester()
        cases = [c for c in load_golden_suite() if c.category == "cross_agent_injection"]
        results = tester.test_output_sanitization(cases)
        assert all(r.passed for r in results), [r for r in results if not r.passed]

    def test_prompt_defense_all_pass(self):
        from shared.adversarial._injection_tester import InjectionTester
        from shared.adversarial._golden_suite import load_golden_suite
        tester = InjectionTester()
        cases = [c for c in load_golden_suite() if c.category == "prompt_injection"]
        results = tester.test_prompt_input_defense(cases)
        assert all(r.passed for r in results), [r for r in results if not r.passed]

    def test_boundary_score_cases_pass(self):
        from shared.adversarial._injection_tester import InjectionTester
        from shared.adversarial._golden_suite import load_golden_suite
        tester = InjectionTester()
        cases = [c for c in load_golden_suite() if c.category == "boundary" and "review" in c.input]
        results = tester.test_score_integrity(cases)
        assert all(r.passed for r in results), [r for r in results if not r.passed]

    def test_returns_test_result_dataclass(self):
        from shared.adversarial._injection_tester import InjectionTester, TestResult
        from shared.adversarial._golden_suite import load_golden_suite
        tester = InjectionTester()
        cases = [c for c in load_golden_suite() if c.category == "score_manipulation"][:1]
        results = tester.test_score_integrity(cases)
        assert isinstance(results[0], TestResult)
        assert results[0].case_id == "sm-001"
