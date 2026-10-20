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

    # Check for Cortensor local endpoint check
    if provider == "cortensor":
        cortensor_cfg = data.get("providers", {}).get("cortensor", {})
        base_url = cortensor_cfg.get("base_url", "http://127.0.0.1:8080")
        host = "127.0.0.1"
        port = 8080
        if "://" in base_url:
            host_part = base_url.split("://")[1].split("/")[0]
            if ":" in host_part:
                host, port_str = host_part.split(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 8080
            else:
                host = host_part
                port = 80

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            res = sock.connect_ex((host, port))
            sock.close()
            if res != 0:
                return False, f"Cortensor endpoint {host}:{port} is unreachable"
        except Exception:
            return False, f"Cortensor endpoint {host}:{port} connection check failed"

        return True, f"Cortensor endpoint reachable at {host}:{port}"

    # For cloud providers, check if the required API key environment variable is set
    env_var = SUPPORTED_KEY_ENVS.get(provider)
    if env_var:
        if not os.environ.get(env_var):
            return False, f"Provider '{provider}' requires environment variable '{env_var}', which is unset"
        return True, f"Provider '{provider}' configured with {env_var}"

    return False, f"Provider '{provider}' is not a recognized live provider"


def explain_finding(
    finding: ImportFinding,
    source_context: Optional[str] = None,
    use_ai: bool = False,
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

    is_live, reason = check_live_provider_status()

    if not is_live:
        return (
            f"[NON-AI DETERMINISTIC FALLBACK] Line {finding.line}: `{finding.statement}`\n"
            f"  Bound name: `{finding.bound_name}` (confidence: {finding.confidence})\n"
            f"  Reason: {finding.explanation}\n"
            f"  Notice: Live AI provider is unavailable ({reason}). "
            f"Falling back to deterministic rule-based analysis."
        )

    # Attempt query to live OpenMinion agent
    try:
        from openminion import Agent  # type: ignore[import-untyped]

        prompt = (
            f"Explain why the import `{finding.statement}` (binding `{finding.bound_name}`) "
            f"at line {finding.line} might be unused, and what caution the developer should take."
        )
        with Agent(session_id="minionpatch-explain") as agent:
            result = agent.run(prompt)
            ai_text = result.text.strip() if hasattr(result, "text") else str(result).strip()

        return (
            f"[AI EXPLANATION (OpenMinion)] Line {finding.line}: `{finding.statement}`\n"
            f"{ai_text}\n\n"
            f"  Advisory: AI explanations are advisory only and must not be treated as "
            f"proof that an import is safe to remove."
        )
    except Exception as exc:
        return (
            f"[NON-AI DETERMINISTIC FALLBACK] Line {finding.line}: `{finding.statement}`\n"
            f"  Reason: {finding.explanation}\n"
            f"  Notice: Failed to query OpenMinion ({exc}). "
            f"Falling back to deterministic rule-based analysis."
        )
