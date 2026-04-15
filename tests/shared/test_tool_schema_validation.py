"""Tests for tool parameter validation."""

from shared.tool_integration import ToolDefinition, RiskLevel, ToolExecutor


def _dummy_tool():
    return ToolDefinition(
        name="test_tool",
        description="A test tool",
        category="test",
        actions={
            "greet": {
                "description": "Say hello",
                "risk": RiskLevel.LOW,
                "params": {"name": "str", "count": "int"},
            },
        },
        execute_fn=lambda action, params: {"status": "success", "msg": f"Hi {params['name']}"},
    )


def test_valid_params_pass():
    """Correct types should pass validation."""
    tool = _dummy_tool()
    executor = ToolExecutor()
    executor.register(tool)
    result = executor.execute("test_tool", "greet", {"name": "Alice", "count": 3})
    assert result["status"] == "success"


def test_wrong_type_rejected():
    """String where int expected should be rejected."""
    tool = _dummy_tool()
    executor = ToolExecutor()
    executor.register(tool)
    result = executor.execute("test_tool", "greet", {"name": "Alice", "count": "not_a_number"})
    assert result["status"] == "error"
    assert "validation" in result["message"].lower() or "type" in result["message"].lower()
