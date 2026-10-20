"""Conservative, AST-validated patch generation and safe application.

Rules enforced:
1. NEVER executes the target Python file.
2. Only high-confidence findings ('likely_unused') are proposed for removal.
3. Findings marked 'needs_caution' are explicitly excluded from automated removal.
4. Generates standard unified diff without touching the target file.
5. Preserves comments, indentation, and unrelated imports on the same line.
6. Verifies syntax (ast.parse) before proposing any patch.
7. Re-checks the file's SHA-256 hash before applying to reject stale patches.
8. Writes to a temporary file and atomically replaces the target.
9. Post-application verification: checks syntax and re-runs scanner.
10. Automatic rollback if verification fails.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Optional

from .models import (
    LIKELY_UNUSED,
    ImportFinding,
    PatchProposal,
    ScanResult,
    VerificationResult,
)
from .scanner import ScanError, scan_file, scan_source


class PatcherError(Exception):
    """Raised for patch generation or application failures."""


def create_patch_proposal(
    scan_result: ScanResult,
    source_code: Optional[str] = None,
) -> PatchProposal:
    """Generate a validated patch proposal without modifying the source file."""
    filepath = scan_result.file

    if source_code is None:
        p = Path(filepath)
        if not p.exists():
            raise PatcherError(f"Target file does not exist: {filepath}")
        if not p.is_file():
            raise PatcherError(f"Target path is not a file: {filepath}")
        try:
            source_bytes = p.read_bytes()
            # Decode with utf-8-sig to cleanly strip BOM for AST parsing while preserving detection
            source_code = source_bytes.decode("utf-8-sig")
        except OSError as exc:
            raise PatcherError(f"Cannot read target file {filepath}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise PatcherError(f"Cannot decode target file {filepath}: {exc}") from exc
    else:
        source_bytes = source_code.encode("utf-8")

    current_sha = hashlib.sha256(source_bytes).hexdigest()
    if current_sha != scan_result.sha256:
        raise PatcherError(
            f"File content has changed since the scan was performed. "
            f"Scan SHA-256: {scan_result.sha256[:8]}, Current SHA-256: {current_sha[:8]}. "
            f"Please run a fresh scan before generating a patch."
        )

    # Separate candidates: strictly high-confidence only
    candidates = [f for f in scan_result.findings if f.confidence == LIKELY_UNUSED]
    excluded = [f for f in scan_result.findings if f.confidence != LIKELY_UNUSED]

    if not candidates:
        return PatchProposal(
            file=filepath,
            original_sha256=current_sha,
            patch_sha256=current_sha,
            original_source=source_code,
            patched_source=source_code,
            diff="",
            candidates=[],
            excluded=excluded,
            is_valid=True,
        )

    try:
        patched_source = _generate_patched_source(source_code, candidates, filepath)
    except Exception as exc:
        return PatchProposal(
            file=filepath,
            original_sha256=current_sha,
            patch_sha256=current_sha,
            original_source=source_code,
            patched_source=source_code,
            diff="",
            candidates=candidates,
            excluded=excluded,
            is_valid=False,
            error_message=f"Failed to generate patch: {exc}",
        )

    # Validate syntax of proposed code
    try:
        ast.parse(patched_source, filename=filepath)
    except SyntaxError as exc:
        return PatchProposal(
            file=filepath,
            original_sha256=current_sha,
            patch_sha256=current_sha,
            original_source=source_code,
            patched_source=patched_source,
            diff="",
            candidates=candidates,
            excluded=excluded,
            is_valid=False,
            error_message=f"Generated patch produces invalid syntax at line {exc.lineno}: {exc.msg}",
        )

    patch_sha = hashlib.sha256(patched_source.encode("utf-8")).hexdigest()
    diff = _generate_unified_diff(source_code, patched_source, filepath)

    return PatchProposal(
        file=filepath,
        original_sha256=current_sha,
        patch_sha256=patch_sha,
        original_source=source_code,
        patched_source=patched_source,
        diff=diff,
        candidates=candidates,
        excluded=excluded,
        is_valid=True,
    )


def apply_patch(proposal: PatchProposal) -> VerificationResult:
    """Safely apply an approved patch proposal and verify the result.
    
    If verification fails or an error occurs, the original file is preserved.
    """
    if not proposal.is_valid:
        raise PatcherError(f"Cannot apply invalid patch proposal: {proposal.error_message}")

    target_path = Path(proposal.file)
    if not target_path.exists():
        raise PatcherError(f"Target file does not exist: {target_path}")
    if not target_path.is_file():
        raise PatcherError(f"Target is not a regular file: {target_path}")

    # No-op if no candidates
    if not proposal.candidates:
        return VerificationResult(
            file=str(target_path),
            syntax_valid=True,
            target_imports_removed=True,
            remaining_findings=proposal.excluded,
            success=True,
            message="No patch candidates to apply. File remains unchanged.",
        )

    # 1. Prepare and validate temporary file before replacement
    temp_path: Optional[Path] = None
    try:
        # Check original file stat to preserve permissions and detect BOM
        try:
            orig_stat = target_path.stat()
            orig_mode = orig_stat.st_mode
        except OSError:
            orig_mode = None

        try:
            original_bytes = target_path.read_bytes()
        except OSError as exc:
            raise PatcherError(f"Cannot read target file for hash verification: {exc}") from exc

        has_bom = original_bytes.startswith(b"\xef\xbb\xbf")
        output_bytes = proposal.patched_source.encode("utf-8")
        if has_bom and not output_bytes.startswith(b"\xef\xbb\xbf"):
            output_bytes = b"\xef\xbb\xbf" + output_bytes

        temp_dir = target_path.parent
        temp_fd, temp_path_str = tempfile.mkstemp(
            prefix=".minionpatch_tmp_",
            suffix=".py",
            dir=temp_dir,
        )
        temp_path = Path(temp_path_str)
        with os.fdopen(temp_fd, "wb") as f:
            f.write(output_bytes)

        # Preserve file permissions/mode where supported
        if orig_mode is not None:
            try:
                os.chmod(temp_path, orig_mode)
            except OSError:
                pass

        # Validate temporary file syntax before touching original target
        ast.parse(temp_path.read_bytes(), filename=str(target_path))

        # 2. Check for stale proposal immediately prior to replacement
        # Re-check hash of target right before atomic replace to minimize race window
        current_bytes = target_path.read_bytes()
        current_sha = hashlib.sha256(current_bytes).hexdigest()
        if current_sha != proposal.original_sha256:
            raise PatcherError(
                f"Stale patch detected! File '{target_path}' was modified after this patch "
                f"proposal was generated.\n"
                f"  Proposal expected SHA-256: {proposal.original_sha256}\n"
                f"  Current file SHA-256:     {current_sha}\n"
                f"Aborting patch application to protect against overwriting unexpected changes. "
                f"Please run a fresh scan."
            )

        # Atomic replacement
        os.replace(temp_path, target_path)
        temp_path = None
    except Exception as exc:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        if isinstance(exc, PatcherError):
            raise
        raise PatcherError(f"Failed to write patch safely: {exc}") from exc

    # 3. Post-patch verification
    try:
        ast.parse(target_path.read_bytes(), filename=str(target_path))
        syntax_valid = True
    except SyntaxError:
        syntax_valid = False

    try:
        new_scan = scan_file(target_path)
        removed_bound_names = {c.bound_name for c in proposal.candidates}
        remaining_bound_names = {f.bound_name for f in new_scan.findings}
        target_imports_removed = removed_bound_names.isdisjoint(remaining_bound_names)
        remaining_findings = new_scan.findings
    except ScanError:
        target_imports_removed = False
        remaining_findings = []

    if syntax_valid and target_imports_removed:
        return VerificationResult(
            file=str(target_path),
            syntax_valid=True,
            target_imports_removed=True,
            remaining_findings=remaining_findings,
            success=True,
            message="Patch applied and verified successfully. Target syntax is valid.",
        )

    # Safe recovery: rollback to original content
    try:
        target_path.write_bytes(original_bytes)
        rollback_msg = "Original file was restored."
    except Exception as rb_exc:
        rollback_msg = f"CRITICAL: Failed to rollback original file: {rb_exc}."

    return VerificationResult(
        file=str(target_path),
        syntax_valid=syntax_valid,
        target_imports_removed=target_imports_removed,
        remaining_findings=remaining_findings,
        success=False,
        message=f"Post-patch verification failed! {rollback_msg}",
    )


# ---------------------------------------------------------------- helpers


def _generate_patched_source(
    source_code: str,
    candidates: list[ImportFinding],
    filename: str,
) -> str:
    """Rewrite source code to remove candidate unused imports cleanly."""
    tree = ast.parse(source_code, filename=filename)
    lines = source_code.splitlines(keepends=True)

    # Candidate set indexed strictly by (line, bound_name) to avoid removing
    # identically named imports that appear on other lines under cautionary conditions.
    candidate_pairs = {(c.line, c.bound_name) for c in candidates}

    replacements: list[tuple[int, int, list[str]]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue

        # Check which aliases in this statement are candidates
        to_remove: list[ast.alias] = []
        to_keep: list[ast.alias] = []

        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            if (node.lineno, bound) in candidate_pairs:
                to_remove.append(alias)
            else:
                to_keep.append(alias)

        if not to_remove:
            continue

        start_line_idx = node.lineno - 1
        end_line_idx = node.end_lineno - 1 if node.end_lineno else start_line_idx

        # If every import in the statement is unused, remove the whole statement
        if not to_keep:
            replacements.append((start_line_idx, end_line_idx, []))
            continue

        # Otherwise, reconstruct the statement keeping only used imports
        start_line = lines[start_line_idx]
        indent = start_line[: node.col_offset]

        # Preserve trailing comment from the statement's end line if present
        end_line = lines[end_line_idx]
        remainder = end_line[node.end_col_offset:].strip() if node.end_col_offset else ""
        comment = f"  {remainder}" if remainder.startswith("#") else ""
        if end_line.endswith("\r\n"):
            nl = "\r\n"
        elif end_line.endswith("\n"):
            nl = "\n"
        else:
            nl = ""

        kept_strs = [
            f"{a.name} as {a.asname}" if a.asname else a.name
            for a in to_keep
        ]
        joined_names = ", ".join(kept_strs)

        if isinstance(node, ast.Import):
            new_stmt = f"{indent}import {joined_names}{comment}{nl}"
        else:
            dots = "." * node.level
            mod = node.module or ""
            new_stmt = f"{indent}from {dots}{mod} import {joined_names}{comment}{nl}"

        replacements.append((start_line_idx, end_line_idx, [new_stmt]))

    # Apply replacements from bottom to top so line indices remain consistent
    replacements.sort(key=lambda r: r[0], reverse=True)
    new_lines = list(lines)
    for start_idx, end_idx, rep_lines in replacements:
        new_lines[start_idx : end_idx + 1] = rep_lines

    return "".join(new_lines)


def _generate_unified_diff(
    original: str,
    patched: str,
    filepath: str,
) -> str:
    """Generate a clean unified diff string."""
    orig_lines = original.splitlines(keepends=True)
    patch_lines = patched.splitlines(keepends=True)
    diff = difflib.unified_diff(
        orig_lines,
        patch_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
    )
    return "".join(diff)
