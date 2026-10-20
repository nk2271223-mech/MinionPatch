"""Data models shared by the scanner, CLI, patcher and OpenMinion tools."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

# Confidence levels. We deliberately never use the word "definite":
# static analysis cannot prove an import is safe to remove.
LIKELY_UNUSED = "likely_unused"
NEEDS_CAUTION = "needs_caution"


@dataclass(frozen=True)
class ImportFinding:
    file: str
    line: int
    statement: str          # e.g. "import numpy as np"
    name: str               # name as written in the import, e.g. "numpy"
    alias: Optional[str]    # "np" or None
    bound_name: str         # the name the import creates in the namespace
    confidence: str         # LIKELY_UNUSED or NEEDS_CAUTION
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanResult:
    file: str
    sha256: str                          # lets us detect "file changed after scan"
    imports_found: int
    findings: list[ImportFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # file-level caveats

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "sha256": self.sha256,
            "imports_found": self.imports_found,
            "findings": [f.to_dict() for f in self.findings],
            "notes": list(self.notes),
        }


@dataclass
class PatchProposal:
    file: str
    original_sha256: str
    patch_sha256: str
    original_source: str
    patched_source: str
    diff: str
    candidates: list[ImportFinding] = field(default_factory=list)
    excluded: list[ImportFinding] = field(default_factory=list)
    is_valid: bool = True
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "original_sha256": self.original_sha256,
            "patch_sha256": self.patch_sha256,
            "diff": self.diff,
            "candidates": [f.to_dict() for f in self.candidates],
            "excluded": [f.to_dict() for f in self.excluded],
            "is_valid": self.is_valid,
            "error_message": self.error_message,
        }


@dataclass
class VerificationResult:
    file: str
    syntax_valid: bool
    target_imports_removed: bool
    remaining_findings: list[ImportFinding] = field(default_factory=list)
    success: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "syntax_valid": self.syntax_valid,
            "target_imports_removed": self.target_imports_removed,
            "remaining_findings": [f.to_dict() for f in self.remaining_findings],
            "success": self.success,
            "message": self.message,
        }
