"""AI-powered and deterministic explanation generation for scanner findings.

Enforces clear separation:
- Offline scanner NEVER depends on OpenMinion or network access.
- AI explanations are strictly advisory and never treated as proof of safety.
- Fallback to labelled deterministic explanations when OpenMinion is not configured.
- Echo responses are never represented as real AI model explanations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from typing import Optional
from urllib.parse import urlparse

from .models import ImportFinding

SUPPORTED_KEY_ENVS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "cortensor": "CORTENSOR_API_KEY",
}


def check_live_provider_status(config_dir: Optional[Path] = None) -> tuple[bool, str]:
    """Check whether a genuine model-driven AI provider is reachable.
    
    Returns (is_available, status_description).
    Does NOT print or leak any keys, tokens, or credentials.
    """
    if config_dir is None:
        config_dir = Path(".openminion")

    agents_file = config_dir / "agents.json"
    if not agents_file.exists():
        return False, "OpenMinion configuration (.openminion/agents.json) not found"

    try:
        data = json.loads(agents_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Failed to parse .openminion/agents.json: {exc}"

    provider = data.get("agents", {}).get("openminion", {}).get("provider", "echo")
    demo_mode = data.get("runtime", {}).get("demo_mode", False)

    if provider == "echo" or demo_mode:
        return False, f"OpenMinion is configured in demo mode with provider '{provider}' (Echo does not perform AI reasoning)"

    # Check for local Ollama endpoint
    if provider == "ollama":
        ollama_cfg = data.get("providers", {}).get("ollama", {})
        base_url = ollama_cfg.get("base_url", "http://127.0.0.1:11434")

        host = "127.0.0.1"
        port = 11434

        if "://" in base_url:
            host_part = base_url.split("://", 1)[1].split("/", 1)[0]
            if ":" in host_part:
                host, port_str = host_part.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    return False, "Invalid Ollama endpoint port"

        try:
            with socket.create_connection((host, port), timeout=1):
                pass
            return True, f"Ollama endpoint reachable at {host}:{port}"
        except OSError:
            return False, f"Ollama endpoint {host}:{port} is unreachable"

    # Check for Cortensor endpoint (hosted Portal or direct Router)
    if provider == "cortensor":
        cortensor_cfg = data.get("providers", {}).get("cortensor", {})
        base_url = os.environ.get("CORTENSOR_API_URL") or cortensor_cfg.get("base_url", "http://127.0.0.1:8080/api/v2/completions")
        api_key_env = cortensor_cfg.get("api_key_env", "CORTENSOR_API_KEY")
        api_key = cortensor_cfg.get("api_key") or os.environ.get(api_key_env, "")

        is_hosted_portal = "cortensor.app" in base_url or base_url.startswith("https://")
        if is_hosted_portal and not api_key:
            return False, f"Cortensor Portal requires environment variable '{api_key_env}', which is unset"

        parsed = urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        try:
            with socket.create_connection((host, port), timeout=1.0):
                pass
            return True, f"Cortensor endpoint reachable at {host}:{port}"
        except OSError:
            return False, f"Cortensor endpoint {host}:{port} is unreachable"

    # For cloud providers, check if the required API key environment variable is set
    env_var = SUPPORTED_KEY_ENVS.get(provider)
    if env_var:
        if not os.environ.get(env_var):
            return False, f"Provider '{provider}' requires environment variable '{env_var}', which is unset"
        return True, f"Provider '{provider}' configured with {env_var}"

    return False, f"Provider '{provider}' is not a recognized live provider"


def _sanitize_error(exc: Exception) -> str:
    """Sanitize error messages to ensure API keys and secret values are never exposed."""
    msg = str(exc)
    for env_var in SUPPORTED_KEY_ENVS.values():
        val = os.environ.get(env_var)
        if val and len(val) >= 4 and val in msg:
            msg = msg.replace(val, "***")
    return msg


def explain_finding(
    finding: ImportFinding,
    source_context: Optional[str] = None,
    use_ai: bool = False,
    config_dir: Optional[Path] = None,
) -> str:
    """Generate an explanation for an import finding.
    
    If use_ai is False: returns the deterministic AST-based explanation.
    If use_ai is True:
        - If a genuine live provider is available, queries OpenMinion.
        - If not available, returns an explicitly labelled deterministic fallback.
    """
    if not use_ai:
        return (
            f"[DETERMINISTIC EXPLANATION] Line {finding.line}: `{finding.statement}`\n"
            f"  Bound name: `{finding.bound_name}` (confidence: {finding.confidence})\n"
            f"  Reason: {finding.explanation}"
        )

    is_live, reason = check_live_provider_status(config_dir=config_dir)

    if not is_live:
        return (
            f"[NON-AI DETERMINISTIC FALLBACK] Line {finding.line}: `{finding.statement}`\n"
            f"  Bound name: `{finding.bound_name}` (confidence: {finding.confidence})\n"
            f"  Reason: {finding.explanation}\n"
            f"  Notice: Live AI provider is unavailable ({reason}). "
            f"Falling back to deterministic rule-based analysis."
        )

    # Attempt query to live OpenMinion agent / runtime
    try:
        from openminion.api.runtime import APIRuntime
        from openminion.modules.llm.schemas import Message

        prompt = (
            f"Explain why the import `{finding.statement}` (binding `{finding.bound_name}`) "
            f"at line {finding.line} might be unused, and what caution the developer should take. "
            f"Keep your explanation concise (2-3 sentences)."
        )
        agents_file = (config_dir / "agents.json") if config_dir else Path(".openminion/agents.json")
        rt = APIRuntime.from_config_path(str(agents_file), logging_mode="quiet")
        try:
            resp = rt.llm_runtime.client.complete(
                messages=[Message(role="user", content=prompt)]
            )
            ai_text = resp.output_text.strip()
        finally:
            rt.close()

        if not ai_text:
            return (
                f"[NON-AI DETERMINISTIC FALLBACK] Line {finding.line}: `{finding.statement}`\n"
                f"  Bound name: `{finding.bound_name}` (confidence: {finding.confidence})\n"
                f"  Reason: {finding.explanation}\n"
                f"  Notice: Live AI provider returned an empty response (verify API key and endpoint). "
                f"Falling back to deterministic rule-based analysis."
            )

        return (
            f"[AI EXPLANATION (OpenMinion)] Line {finding.line}: `{finding.statement}`\n"
            f"{ai_text}\n\n"
            f"  Advisory: AI explanations are advisory only and must not be treated as "
            f"proof that an import is safe to remove."
        )
    except Exception as exc:
        sanitized_msg = _sanitize_error(exc)
        return (
            f"[NON-AI DETERMINISTIC FALLBACK] Line {finding.line}: `{finding.statement}`\n"
            f"  Reason: {finding.explanation}\n"
            f"  Notice: Failed to query OpenMinion ({sanitized_msg}). "
            f"Falling back to deterministic rule-based analysis."
        )
