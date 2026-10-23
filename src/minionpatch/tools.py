"""OpenMinion tool definitions for MinionPatch.

All tools exposed to OpenMinion agents are strictly READ-ONLY:
- They inspect source files using AST parsing without executing target code.
- They generate unified diff proposals in memory without modifying files on disk.
- To preserve human safety, patch APPLICATION is never delegated to the agent;
  modifications must be explicitly performed by the human operator via the CLI
  (`minionpatch patch <file>`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openminion import tool

from .patcher import PatcherError, create_patch_proposal
from .scanner import ScanError, scan_file


@tool
def scan_unused_imports(file_path: str) -> str:
    """Scan a Python file using AST analysis to detect unused imports.

    Analyzes imports without executing the target file. Returns a JSON string
    containing findings classified by confidence ('likely_unused' vs 'needs_caution')
    and any file-level analysis notes.
    """
    try:
        result = scan_file(file_path)
    except ScanError as exc:
        return json.dumps({
            "success": False,
            "file": str(file_path),
            "error": str(exc),
        }, indent=2)
    except Exception as exc:
        return json.dumps({
            "success": False,
            "file": str(file_path),
            "error": f"Unexpected scan failure: {exc}",
        }, indent=2)

    return json.dumps({
        "success": True,
        "file": result.file,
        "sha256": result.sha256,
        "imports_found": result.imports_found,
        "findings_count": len(result.findings),
        "findings": [f.to_dict() for f in result.findings],
        "notes": list(result.notes),
    }, indent=2)


@tool
def propose_import_patch(file_path: str) -> str:
    """Generate a validated unified diff proposal to remove high-confidence unused imports.

    This tool is strictly read-only and DOES NOT modify the target file.
    Only findings with 'likely_unused' confidence are proposed for removal;
    cautionary findings are preserved. Returns a JSON string with the diff.
    """
    try:
        scan_result = scan_file(file_path)
    except ScanError as exc:
        return json.dumps({
            "success": False,
            "file": str(file_path),
            "error": str(exc),
        }, indent=2)
    except Exception as exc:
        return json.dumps({
            "success": False,
            "file": str(file_path),
            "error": f"Unexpected error during scan: {exc}",
        }, indent=2)

    if not scan_result.findings:
        return json.dumps({
            "success": True,
            "file": scan_result.file,
            "status": "clean",
            "message": "No unused imports found. Target file is clean.",
            "diff": "",
            "candidates": [],
            "excluded": [],
        }, indent=2)

    try:
        proposal = create_patch_proposal(scan_result)
    except PatcherError as exc:
        return json.dumps({
            "success": False,
            "file": scan_result.file,
            "error": str(exc),
        }, indent=2)
    except Exception as exc:
        return json.dumps({
            "success": False,
            "file": scan_result.file,
            "error": f"Unexpected error creating patch proposal: {exc}",
        }, indent=2)

    if not proposal.is_valid:
        return json.dumps({
            "success": False,
            "file": proposal.file,
            "status": "invalid_proposal",
            "error": proposal.error_message,
        }, indent=2)

    if not proposal.candidates:
        return json.dumps({
            "success": True,
            "file": proposal.file,
            "status": "no_safe_candidates",
            "message": "All detected findings require caution and were excluded from automatic removal.",
            "diff": "",
            "candidates": [],
            "excluded": [f.to_dict() for f in proposal.excluded],
        }, indent=2)

    return json.dumps({
        "success": True,
        "file": proposal.file,
        "status": "proposal_generated",
        "original_sha256": proposal.original_sha256,
        "patch_sha256": proposal.patch_sha256,
        "candidates_count": len(proposal.candidates),
        "candidates": [c.to_dict() for c in proposal.candidates],
        "excluded_count": len(proposal.excluded),
        "excluded": [e.to_dict() for e in proposal.excluded],
        "diff": proposal.diff,
        "notice": "This proposal is read-only. To safely apply it, the user must run `minionpatch patch <file>`.",
    }, indent=2)
