"""Deterministic OpenMinion integration tests.

These tests run completely offline without requiring network access,
Cortensor endpoints, or paid API keys.
"""
from pathlib import Path
import pytest

from openminion import Agent, tool
from minionpatch.explainer import check_live_provider_status, explain_finding
from minionpatch.models import LIKELY_UNUSED, ImportFinding


@tool
def sample_test_tool() -> str:
    """A test tool for deterministic verification."""
    return "Test tool executed successfully."


def test_openminion_tool_decorator_direct_call():
    """Verify @tool functions can be executed directly as ordinary Python callables."""
    result = sample_test_tool()
    assert result == "Test tool executed successfully."


def test_openminion_tool_registration_in_runtime():
    """Verify tool registers into OpenMinion's ToolRegistry and dispatches offline."""
    with Agent(instructions="Test tool registration", tools=[sample_test_tool]) as agent:
        runtime = agent._ensure_runtime()
        registered = agent._register_tools_for_run(runtime)
        assert "sample_test_tool" in registered

        spec = runtime.tools.get("sample_test_tool")
        assert spec.name == "sample_test_tool"

        # Deterministic execution via handler
        output = spec.handler(spec.args_model())
        assert output == "Test tool executed successfully."

        # Clean up
        agent._unregister_tools(runtime, registered)

    # After unregister, querying the tool registry raises KeyError
    with pytest.raises(KeyError):
        runtime.tools.get("sample_test_tool")


def test_live_provider_status_detects_echo_mode(tmp_path):
    """Verify provider status correctly identifies Echo/demo mode without secrets."""
    openminion_dir = tmp_path / ".openminion"
    openminion_dir.mkdir()
    agents_json = openminion_dir / "agents.json"
    agents_json.write_text(
        '{"agents": {"openminion": {"provider": "echo"}}, "runtime": {"demo_mode": true}}',
        encoding="utf-8",
    )

    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert not is_live
    assert "demo mode" in msg or "Echo" in msg


def test_explain_finding_deterministic_mode():
    """Verify deterministic explanations work completely offline."""
    finding = ImportFinding(
        file="foo.py",
        line=10,
        statement="import unused_mod",
        name="unused_mod",
        alias=None,
        bound_name="unused_mod",
        confidence=LIKELY_UNUSED,
        explanation="never referenced",
    )
    explanation = explain_finding(finding, use_ai=False)
    assert "[DETERMINISTIC EXPLANATION]" in explanation
    assert "unused_mod" in explanation


def test_explain_finding_ai_fallback_when_live_unavailable(monkeypatch):
    """Verify AI request falls back cleanly to labelled deterministic output when offline."""
    finding = ImportFinding(
        file="foo.py",
        line=10,
        statement="import unused_mod",
        name="unused_mod",
        alias=None,
        bound_name="unused_mod",
        confidence=LIKELY_UNUSED,
        explanation="never referenced",
    )
    # With echo / demo mode active in .openminion, it must fall back to non-AI explanation
    explanation = explain_finding(finding, use_ai=True)
    assert "[NON-AI DETERMINISTIC FALLBACK]" in explanation
    assert "Live AI provider is unavailable" in explanation
    assert "never referenced" in explanation
