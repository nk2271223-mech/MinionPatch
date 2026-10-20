"""Deterministic integration test and status diagnostic for OpenMinion.

This script:
1. Tests tool registration and execution using OpenMinion's ToolRegistry (100% offline, deterministic).
2. Clearly distinguishes local tool execution from live model-driven tool calls.
3. Diagnoses the currently configured OpenMinion provider without exposing credentials.
4. Explains why the 'echo' provider does not invoke custom tools.
5. Specifies the minimum configuration needed for real model-driven tool execution.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from openminion import Agent, ProviderError, tool


@tool
def check_project() -> str:
    """Return a simple status message about MinionPatch."""
    return "MinionPatch tool is working."


def run_diagnostics() -> None:
    print("==================================================")
    print("MinionPatch <-> OpenMinion Integration Diagnostic")
    print("==================================================")

    # 1. Deterministic direct tool verification
    print("\n[Step 1] Direct Tool Execution (Offline/Deterministic):")
    try:
        direct_output = check_project()
        print(f"  Result: '{direct_output}'")
        assert direct_output == "MinionPatch tool is working."
        print("  Status: PASSED (Function executes correctly)")
    except Exception as exc:
        print(f"  Status: FAILED ({exc})")

    # 2. OpenMinion Tool Registration & Registry Execution (Offline/Deterministic)
    print("\n[Step 2] OpenMinion Tool Registration & Handler Execution (Offline/Deterministic):")
    try:
        with Agent(
            instructions="Use the check_project tool and report its result.",
            tools=[check_project],
            session_id="minionpatch-diagnostic",
        ) as agent:
            runtime = agent._ensure_runtime()
            registered = agent._register_tools_for_run(runtime)
            spec = runtime.tools.get("check_project")
            args_model = spec.args_model()
            registry_output = spec.handler(args_model)
            agent._unregister_tools(runtime, registered)

        print(f"  Registered Tool: '{spec.name}'")
        print(f"  Registry Execution Result: '{registry_output}'")
        assert registry_output == "MinionPatch tool is working."
        print("  Status: PASSED (Tool successfully registered and dispatched via OpenMinion runtime)")
    except Exception as exc:
        print(f"  Status: FAILED ({exc})")

    # 3. Provider Configuration Diagnosis
    print("\n[Step 3] Provider Configuration Check (.openminion/agents.json):")
    cfg_file = Path(".openminion/agents.json")
    provider_name = "unknown"
    demo_mode = True

    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            provider_name = cfg.get("agents", {}).get("openminion", {}).get("provider", "unknown")
            demo_mode = cfg.get("runtime", {}).get("demo_mode", False)
            print(f"  Configured Provider: '{provider_name}'")
            print(f"  Runtime Demo Mode:   {demo_mode}")
        except Exception as exc:
            print(f"  Warning: Could not read configuration: {exc}")
    else:
        print("  Notice: .openminion/agents.json not found.")

    # 4. Live AI vs Mock Analysis
    print("\n[Step 4] Live Model-Driven Tool Execution Status:")
    if provider_name == "echo" or demo_mode:
        print("  * Current Status: ECHO MOCK / DEMO MODE")
        print("  * Explanation: The 'echo' provider is a local mock engine. It echoes input text")
        print("    and does not parse tool specifications or invoke tools.")
        print("    This is why previous runs printed 'openminion: ... (thinking=minimal)'.")
        print("\n  * To enable genuine model-driven tool execution:")
        print("    1. Choose a supported LLM provider: Cortensor, Anthropic, OpenAI, Cerebras, Groq, or OpenRouter.")
        print("    2. Set the provider's API key environment variable (e.g., $env:ANTHROPIC_API_KEY, $env:OPENAI_API_KEY).")
        print("       (Or for local Cortensor, start the local node on http://127.0.0.1:8080).")
        print("    3. In .openminion/agents.json, set 'provider' to the chosen provider and 'demo_mode' to false.")
        print("    4. Note: No credentials or configuration were modified by this test.")
    else:
        # Attempt live run if an actual provider is configured
        print(f"  Attempting live run with provider '{provider_name}'...")
        try:
            with Agent(
                instructions="Use the check_project tool and report its result.",
                tools=[check_project],
                forced_tools=["check_project"],
                session_id="minionpatch-live-test",
            ) as agent:
                res = agent.run("Check whether the MinionPatch tool is working.")
                print(f"  Live Model Response: {res.text}")
        except ProviderError as exc:
            print(f"  Live Provider Error: {exc.code}: {exc.message}")
        except Exception as exc:
            print(f"  Live Run Exception: {exc}")

    print("\n==================================================")
    print("Integration Diagnostic Complete")
    print("==================================================")


if __name__ == "__main__":
    run_diagnostics()
