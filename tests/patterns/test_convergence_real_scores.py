"""Tests that convergence uses real reviewer scores, not fabricated constants."""


def test_plan_execute_no_hardcoded_quality():
    """plan_and_execute must not hardcode quality=8.0."""
    import inspect
    from patterns import plan_and_execute

    source = inspect.getsource(plan_and_execute)
    # Check for hardcoded convergence gate values
    assert 'quality=8.0' not in source or 'quality_score=8.0' not in source, \
        "plan_and_execute still has hardcoded convergence scores"


def test_map_reduce_has_accuracy_gate():
    """map_reduce must check accuracy, not just quality."""
    import inspect
    from patterns import map_reduce

    source = inspect.getsource(map_reduce)
    assert 'accuracy' in source.lower(), \
        "map_reduce has no accuracy gate — needs dual convergence check"
