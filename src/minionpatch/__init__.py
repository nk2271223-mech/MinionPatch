"""MinionPatch - Conservative unused-import detector and safe patch engine."""

from .models import (
    LIKELY_UNUSED,
    NEEDS_CAUTION,
    ImportFinding,
    PatchProposal,
    ScanResult,
    VerificationResult,
)
from .patcher import apply_patch, create_patch_proposal
from .scanner import scan_file, scan_source
from .tools import propose_import_patch, scan_unused_imports
from .agent import create_agent, run_agent_workflow

__all__ = [
    "LIKELY_UNUSED",
    "NEEDS_CAUTION",
    "ImportFinding",
    "PatchProposal",
    "ScanResult",
    "VerificationResult",
    "apply_patch",
    "create_agent",
    "create_patch_proposal",
    "propose_import_patch",
    "run_agent_workflow",
    "scan_file",
    "scan_source",
    "scan_unused_imports",
]
