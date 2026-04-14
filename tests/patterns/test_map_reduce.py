import pytest


class TestMapReduceState:
    def test_initial_state_defaults(self):
        from patterns.map_reduce import create_initial_state
        state = create_initial_state("test topic")
        assert state["topic"] == "test topic"
        assert state["chunks"] == []
        assert state["map_results"] == []
        assert state["reduced_output"] == ""
        assert state["needs_reconciliation"] is False
        assert state["final_output"] == ""


class TestSplitterNode:
    def test_splits_by_item(self, monkeypatch):
        from patterns.map_reduce import splitter_node, create_initial_state
        import json

        items = ["paper A", "paper B", "paper C"]
        monkeypatch.setattr("patterns.map_reduce.smart_llm_call", lambda *a, **kw: json.dumps(items))
        monkeypatch.setattr("patterns.map_reduce.get_llm", lambda **kw: None)

        state = create_initial_state("summarize these papers")
        result = splitter_node(state)
        assert len(result["chunks"]) == 3

    def test_caps_at_max_chunks(self, monkeypatch):
        from patterns.map_reduce import splitter_node, create_initial_state, MAX_CHUNKS
        import json

        items = [f"item {i}" for i in range(30)]
        monkeypatch.setattr("patterns.map_reduce.smart_llm_call", lambda *a, **kw: json.dumps(items))
        monkeypatch.setattr("patterns.map_reduce.get_llm", lambda **kw: None)

        state = create_initial_state("big batch")
        result = splitter_node(state)
        assert len(result["chunks"]) <= MAX_CHUNKS


class TestMapNode:
    def test_map_produces_one_result_per_chunk(self, monkeypatch):
        from patterns.map_reduce import map_node, create_initial_state

        monkeypatch.setattr("patterns.map_reduce.smart_llm_call", lambda *a, **kw: "Analyzed chunk")
        monkeypatch.setattr("patterns.map_reduce.get_llm", lambda **kw: None)

        state = create_initial_state("test")
        state["chunks"] = ["chunk A", "chunk B"]
        result = map_node(state)
        assert len(result["map_results"]) == 2


class TestReducerNode:
    def test_reducer_produces_output(self, monkeypatch):
        from patterns.map_reduce import reducer_node, create_initial_state

        monkeypatch.setattr("patterns.map_reduce.smart_llm_call", lambda *a, **kw: "Reduced summary")
        monkeypatch.setattr("patterns.map_reduce.get_llm", lambda **kw: None)

        state = create_initial_state("test")
        state["chunks"] = ["A", "B"]
        state["map_results"] = ["Result A", "Result B"]
        result = reducer_node(state)
        assert result["reduced_output"] == "Reduced summary"


class TestReconcilerNode:
    def test_reconciler_produces_final_output(self, monkeypatch):
        from patterns.map_reduce import reconciler_node, create_initial_state

        monkeypatch.setattr("patterns.map_reduce.smart_llm_call", lambda *a, **kw: "Reconciled output")
        monkeypatch.setattr("patterns.map_reduce.get_llm", lambda **kw: None)

        state = create_initial_state("test")
        state["reduced_output"] = "Raw reduction with [CONTRADICTION] conflicts"
        state["needs_reconciliation"] = True
        result = reconciler_node(state)
        assert result["final_output"] == "Reconciled output"

    def test_reconciler_passes_through_when_no_conflicts(self):
        from patterns.map_reduce import reconciler_node, create_initial_state

        state = create_initial_state("test")
        state["reduced_output"] = "Clean reduction"
        state["needs_reconciliation"] = False
        result = reconciler_node(state)
        assert result["final_output"] == "Clean reduction"


class TestMapReduceGraph:
    def test_graph_builds(self):
        from patterns.map_reduce import build_map_reduce_graph
        graph = build_map_reduce_graph()
        assert graph is not None

    def test_run_map_reduce_end_to_end(self, monkeypatch):
        from patterns.map_reduce import run_map_reduce
        import json

        call_count = {"n": 0}

        def mock_llm(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:  # splitter
                return json.dumps(["item A", "item B"])
            return "Mock output"

        monkeypatch.setattr("patterns.map_reduce.smart_llm_call", mock_llm)
        monkeypatch.setattr("patterns.map_reduce.get_llm", lambda **kw: None)

        result = run_map_reduce("Summarize items")
        assert isinstance(result, dict)
        assert result.get("final_output")
