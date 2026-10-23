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


def test_explain_finding_ai_fallback_when_live_unavailable(tmp_path):
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
    openminion_dir = tmp_path / ".openminion"
    openminion_dir.mkdir()
    (openminion_dir / "agents.json").write_text(
        '{"agents": {"openminion": {"provider": "echo"}}, "runtime": {"demo_mode": true}}',
        encoding="utf-8",
    )
    explanation = explain_finding(finding, use_ai=True, config_dir=openminion_dir)
    assert "[NON-AI DETERMINISTIC FALLBACK]" in explanation
    assert "Live AI provider is unavailable" in explanation
    assert "never referenced" in explanation


def test_check_live_provider_status_ollama_reachable(tmp_path, monkeypatch):
    """Verify Ollama status check reports reachable when endpoint socket connects."""
    openminion_dir = tmp_path / ".openminion"
    openminion_dir.mkdir()
    (openminion_dir / "agents.json").write_text(
        '{"agents": {"openminion": {"provider": "ollama"}}, "providers": {"ollama": {"base_url": "http://127.0.0.1:11434"}}, "runtime": {"demo_mode": false}}',
        encoding="utf-8",
    )

    class DummyConn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr("socket.create_connection", lambda addr, timeout=1: DummyConn())
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is True
    assert "Ollama endpoint reachable at 127.0.0.1:11434" in msg


def test_check_live_provider_status_ollama_unreachable(tmp_path, monkeypatch):
    """Verify Ollama status check reports unreachable when endpoint is offline."""
    openminion_dir = tmp_path / ".openminion"
    openminion_dir.mkdir()
    (openminion_dir / "agents.json").write_text(
        '{"agents": {"openminion": {"provider": "ollama"}}, "providers": {"ollama": {"base_url": "http://127.0.0.1:11434"}}, "runtime": {"demo_mode": false}}',
        encoding="utf-8",
    )

    def fake_connect(addr, timeout=1):
        raise OSError("Connection refused")

    monkeypatch.setattr("socket.create_connection", fake_connect)
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is False
    assert "Ollama endpoint 127.0.0.1:11434 is unreachable" in msg


def test_check_live_provider_status_ollama_invalid_port(tmp_path):
    """Verify Ollama status check safely rejects malformed endpoint ports."""
    openminion_dir = tmp_path / ".openminion"
    openminion_dir.mkdir()
    (openminion_dir / "agents.json").write_text(
        '{"agents": {"openminion": {"provider": "ollama"}}, "providers": {"ollama": {"base_url": "http://127.0.0.1:bad_port"}}, "runtime": {"demo_mode": false}}',
        encoding="utf-8",
    )
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is False
    assert "Invalid Ollama endpoint port" in msg


def test_check_live_provider_status_cortensor_router(tmp_path, monkeypatch):
    """Verify Cortensor direct router status check handles reachable and unreachable states."""
    openminion_dir = tmp_path / ".openminion"
    openminion_dir.mkdir()
    (openminion_dir / "agents.json").write_text(
        '{"agents": {"openminion": {"provider": "cortensor"}}, "providers": {"cortensor": {"base_url": "http://127.0.0.1:8080/api/v2/completions"}}, "runtime": {"demo_mode": false}}',
        encoding="utf-8",
    )

    class DummyConn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr("socket.create_connection", lambda addr, timeout=1.0: DummyConn())
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is True
    assert "Cortensor endpoint reachable at 127.0.0.1:8080" in msg

    def fake_connect(addr, timeout=1.0):
        raise OSError("Connection refused")

    monkeypatch.setattr("socket.create_connection", fake_connect)
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is False
    assert "unreachable" in msg


def test_check_live_provider_status_cortensor_portal(tmp_path, monkeypatch):
    """Verify Cortensor Portal requires CORTENSOR_API_KEY when pointing to hosted API gateway."""
    openminion_dir = tmp_path / ".openminion"
    openminion_dir.mkdir()
    (openminion_dir / "agents.json").write_text(
        '{"agents": {"openminion": {"provider": "cortensor"}}, "providers": {"cortensor": {"base_url": "https://api.cortensor.app/v1", "api_key_env": "CORTENSOR_API_KEY"}}, "runtime": {"demo_mode": false}}',
        encoding="utf-8",
    )

    # Missing API key
    monkeypatch.delenv("CORTENSOR_API_KEY", raising=False)
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is False
    assert "CORTENSOR_API_KEY" in msg and "unset" in msg

    # Key present and endpoint reachable on port 443
    monkeypatch.setenv("CORTENSOR_API_KEY", "dummy-cortensor-key")
    class DummyConn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr("socket.create_connection", lambda addr, timeout=1.0: DummyConn())
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is True
    assert "Cortensor endpoint reachable at api.cortensor.app:443" in msg


def test_check_live_provider_status_cloud_env_var(tmp_path, monkeypatch):
    """Verify cloud providers check required API key environment variables without leaking values."""
    openminion_dir = tmp_path / ".openminion"
    openminion_dir.mkdir()
    (openminion_dir / "agents.json").write_text(
        '{"agents": {"openminion": {"provider": "anthropic"}}, "runtime": {"demo_mode": false}}',
        encoding="utf-8",
    )

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is False
    assert "ANTHROPIC_API_KEY" in msg and "unset" in msg

    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-secret-key")
    is_live, msg = check_live_provider_status(config_dir=openminion_dir)
    assert is_live is True
    assert "ANTHROPIC_API_KEY" in msg
    assert "dummy-secret-key" not in msg  # Never leaks secret values!


def test_explain_finding_ai_live_success(monkeypatch):
    """Verify live AI explanation formatting when provider responds successfully."""
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

    monkeypatch.setattr(
        "minionpatch.explainer.check_live_provider_status",
        lambda *args, **kwargs: (True, "Provider active"),
    )

    class DummyClient:
        def complete(self, messages, **kwargs):
            class DummyResponse:
                output_text = "The import unused_mod is never referenced."
            return DummyResponse()

    class DummyRuntime:
        llm_runtime = type("LLMRuntime", (), {"client": DummyClient()})()
        def close(self):
            pass

    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        lambda *args, **kwargs: DummyRuntime(),
    )

    result = explain_finding(finding, use_ai=True)
    assert "[AI EXPLANATION (OpenMinion)]" in result
    assert "The import unused_mod is never referenced." in result
    assert "Advisory:" in result


def test_explain_finding_ai_query_exception_fallback(monkeypatch):
    """Verify graceful fallback when live provider query encounters an unexpected exception."""
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

    monkeypatch.setattr(
        "minionpatch.explainer.check_live_provider_status",
        lambda *args, **kwargs: (True, "Provider active"),
    )

    def crash(*args, **kwargs):
        raise RuntimeError("Simulated network drop")

    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        crash,
    )

    result = explain_finding(finding, use_ai=True)
    assert "[NON-AI DETERMINISTIC FALLBACK]" in result
    assert "Failed to query OpenMinion" in result
    assert "Simulated network drop" in result


def test_explain_finding_secret_sanitized_in_exception(monkeypatch):
    """Verify that any secret key present in exception messages is masked and never exposed."""
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

    secret_key = "super-secret-cortensor-key-xyz"
    monkeypatch.setenv("CORTENSOR_API_KEY", secret_key)
    monkeypatch.setattr(
        "minionpatch.explainer.check_live_provider_status",
        lambda *args, **kwargs: (True, "Provider active"),
    )

    def crash_with_secret(*args, **kwargs):
        raise RuntimeError(f"Failed connecting to server with token: {secret_key}")

    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        crash_with_secret,
    )

    result = explain_finding(finding, use_ai=True)
    assert "[NON-AI DETERMINISTIC FALLBACK]" in result
    assert secret_key not in result
    assert "***" in result
