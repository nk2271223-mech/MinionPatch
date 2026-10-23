"""Minimal OpenMinion Agent integration for MinionPatch.

Equips an OpenMinion Agent with read-only tools to scan Python files
and propose clean import patches, adhering to the local Ollama configuration.
"""
from __future__ import annotations

from typing import Optional

from openminion import Agent, AgentRunResult

from .tools import propose_import_patch, scan_unused_imports

DEFAULT_AGENT_INSTRUCTIONS = (
    "You are the MinionPatch Assistant, an expert Python static analysis and "
    "clean-code advisor powered by OpenMinion.\n\n"
    "Your capabilities:\n"
    "1. Use `scan_unused_imports` to inspect Python files for unused imports.\n"
    "2. Use `propose_import_patch` to generate validated unified diffs that remove "
    "high-confidence unused imports while preserving cautionary imports.\n\n"
    "Safety Rules:\n"
    "- Your tools are strictly read-only. You cannot and must not modify any files.\n"
    "- Report your findings clearly, highlighting line numbers and confidence ratings.\n"
    "- If the user wants to apply the proposed changes, instruct them to run "
    "`minionpatch patch <filepath>` from their terminal for human-supervised application."
)


def create_agent(
    instructions: Optional[str] = None,
    session_id: Optional[str] = None,
    config_path: Optional[str] = None,
) -> Agent:
    """Create a read-only OpenMinion Agent configured with MinionPatch tools.

    Reads provider settings from `.openminion/agents.json` (defaults to local Ollama).
    Does NOT modify files and does NOT require external API keys.
    """
    return Agent(
        instructions=instructions or DEFAULT_AGENT_INSTRUCTIONS,
        tools=[scan_unused_imports, propose_import_patch],
        session_id=session_id or "minionpatch-agent-session",
        config_path=config_path,
    )


def run_agent_workflow(
    message: str,
    session_id: Optional[str] = None,
    config_path: Optional[str] = None,
) -> AgentRunResult:
    """Run a single turn with the MinionPatch OpenMinion Agent."""
    with create_agent(session_id=session_id, config_path=config_path) as agent:
        return agent.run(message)
