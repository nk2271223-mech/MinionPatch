"""Command-line interface for MinionPatch.

Exit codes:
  0 = clean (no findings, or patch applied cleanly with no remaining findings)
  1 = findings detected (scan findings, proposed patch, or cancelled patch)
  2 = error (syntax error, missing file, stale hash, verification failure)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .explainer import explain_finding
from .models import LIKELY_UNUSED, PatchProposal, VerificationResult
from .patcher import PatcherError, apply_patch, create_patch_proposal
from .scanner import ScanError, scan_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minionpatch",
        description="Conservative unused-import detection and safe human-approved patching.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. scan command
    scan_parser = sub.add_parser("scan", help="Scan a Python file (offline, no modification).")
    scan_parser.add_argument("path", help="Path to the Python file to scan.")
    scan_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    # 2. explain command
    explain_parser = sub.add_parser("explain", help="Explain unused-import findings.")
    explain_parser.add_argument("path", help="Path to the Python file to inspect.")
    explain_parser.add_argument(
        "--ai",
        action="store_true",
        help="Use OpenMinion for AI explanations (falls back safely if unavailable).",
    )
    explain_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    # 3. patch command
    patch_parser = sub.add_parser("patch", help="Propose and safely apply an approved patch.")
    patch_parser.add_argument("path", help="Path to the Python file to patch.")
    patch_parser.add_argument(
        "--dry-run",
        "--propose",
        dest="dry_run",
        action="store_true",
        help="Generate and display the proposed patch without applying it.",
    )
    patch_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Explicitly approve the patch application without interactive prompt.",
    )
    patch_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        return _handle_scan(args)
    elif args.command == "explain":
        return _handle_explain(args)
    elif args.command == "patch":
        return _handle_patch(args)

    return 2


def _handle_scan(args: argparse.Namespace) -> int:
    try:
        result = scan_file(args.path)
    except ScanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Scanned {result.file}: {result.imports_found} import name(s), {len(result.findings)} finding(s).")
        for note in result.notes:
            print(f"  note: {note}")
        for f in result.findings:
            tag = "LIKELY UNUSED" if f.confidence == LIKELY_UNUSED else "NEEDS CAUTION"
            print(f"  line {f.line}: [{tag}] {f.statement}  ->  {f.bound_name}")
            print(f"      {f.explanation}")

    return 1 if result.findings else 0


def _handle_explain(args: argparse.Namespace) -> int:
    try:
        result = scan_file(args.path)
    except ScanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not result.findings:
        if args.json:
            print(json.dumps({"file": result.file, "findings": [], "notes": result.notes}, indent=2))
        else:
            print(f"Scanned {result.file}: No unused import findings detected. File is clean.")
        return 0

    explanations = []
    for f in result.findings:
        text = explain_finding(f, use_ai=args.ai)
        explanations.append({"finding": f.to_dict(), "explanation_text": text})

    if args.json:
        print(json.dumps({
            "file": result.file,
            "used_ai": args.ai,
            "findings_count": len(result.findings),
            "explanations": explanations,
        }, indent=2))
    else:
        print(f"Explanations for {result.file} ({len(result.findings)} finding(s)):")
        for item in explanations:
            print("-" * 50)
            print(item["explanation_text"])

    return 1


def _handle_patch(args: argparse.Namespace) -> int:
    try:
        scan_result = scan_file(args.path)
    except ScanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not scan_result.findings:
        if args.json:
            print(json.dumps({"file": scan_result.file, "status": "clean", "message": "No findings detected."}, indent=2))
        else:
            print(f"File is clean: {scan_result.file} (no unused imports found). No patch needed.")
        return 0

    try:
        proposal = create_patch_proposal(scan_result)
    except PatcherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not proposal.is_valid:
        print(f"error: Invalid patch proposal: {proposal.error_message}", file=sys.stderr)
        return 2

    # Check if we have candidates for removal
    if not proposal.candidates:
        msg = "No safe patch candidates found. All findings require caution and were not automatically proposed."
        if args.json:
            print(json.dumps({
                "file": scan_result.file,
                "status": "no_safe_candidates",
                "message": msg,
                "excluded": [f.to_dict() for f in proposal.excluded],
            }, indent=2))
        else:
            print(msg)
            for f in proposal.excluded:
                print(f"  line {f.line}: [NEEDS CAUTION] {f.statement}  ->  {f.bound_name}")
                print(f"      {f.explanation}")
        return 1

    # Show proposal
    if not args.json:
        print(f"--- Patch Proposal for {proposal.file} ---")
        print(f"Candidates for automatic removal ({len(proposal.candidates)}):")
        for c in proposal.candidates:
            print(f"  - line {c.line}: {c.statement} (bound: `{c.bound_name}`)")
        if proposal.excluded:
            print(f"Excluded from automatic removal ({len(proposal.excluded)}):")
            for e in proposal.excluded:
                print(f"  - line {e.line}: [NEEDS CAUTION] {e.statement}")
        print("\nProposed unified diff:")
        print(proposal.diff if proposal.diff else "(No textual diff generated)")

    # Proposal-only / dry-run mode
    if args.dry_run:
        if args.json:
            print(json.dumps({
                "file": proposal.file,
                "status": "proposed",
                "diff": proposal.diff,
                "candidates": [f.to_dict() for f in proposal.candidates],
                "excluded": [f.to_dict() for f in proposal.excluded],
            }, indent=2))
        else:
            print("\n[DRY RUN / PROPOSAL ONLY] No changes were made to the file.")
        return 1

    # Explicit approval verification
    approved = False
    if args.yes:
        approved = True
    else:
        # Non-interactive environment check
        if not sys.stdin.isatty():
            err_msg = "Non-interactive environment detected. Explicit confirmation required: pass --yes to apply patch."
            print(f"error: {err_msg}", file=sys.stderr)
            return 1

        try:
            prompt = "\nApply this patch? Type 'yes' to confirm: "
            response = input(prompt).strip()
            if response.lower() == "yes":
                approved = True
            else:
                approved = False
        except (EOFError, KeyboardInterrupt):
            approved = False

    if not approved:
        if args.json:
            print(json.dumps({
                "file": proposal.file,
                "status": "cancelled",
                "message": "Patch application was declined or cancelled.",
            }, indent=2))
        else:
            print("\nPatch application cancelled. Original file remains unchanged.")
        return 1

    # Apply patch safely
    try:
        verification = apply_patch(proposal)
    except PatcherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not verification.success:
        print(f"error: {verification.message}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "file": proposal.file,
            "status": "applied",
            "verification": verification.to_dict(),
        }, indent=2))
    else:
        print(f"\nPatch successfully applied to {proposal.file}.")
        print("Post-patch verification passed: AST syntax is valid.")
        print("Candidate unused imports successfully removed.")
        if verification.remaining_findings:
            print(f"Remaining findings requiring caution: {len(verification.remaining_findings)}")
            for rem in verification.remaining_findings:
                print(f"  - line {rem.line}: [NEEDS CAUTION] {rem.statement}")

    return 0 if not verification.remaining_findings else 1


if __name__ == "__main__":
    sys.exit(main())
