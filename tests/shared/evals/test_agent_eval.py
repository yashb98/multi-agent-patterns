from shared.evals import load_canonical_flow_cases, run_canonical_flow_evals


def test_loads_canonical_flows():
    cases = load_canonical_flow_cases()
    assert len(cases) >= 50


def test_canonical_flow_harness_passes_all_cases():
    results = run_canonical_flow_evals()
    assert all(result.passed for result in results), results
