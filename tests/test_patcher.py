"""Tests for patch proposal generation, safety checks, and application."""
import hashlib
from pathlib import Path
import pytest

from minionpatch.models import LIKELY_UNUSED, NEEDS_CAUTION, ImportFinding, ScanResult
from minionpatch.patcher import (
    PatcherError,
    apply_patch,
    create_patch_proposal,
)
from minionpatch.scanner import scan_file, scan_source


def test_proposal_generation_does_not_modify_source(tmp_path):
    target = tmp_path / "sample.py"
    original_code = "import os\nimport math\nprint(math.sqrt(4))\n"
    target.write_text(original_code, encoding="utf-8")

    scan_res = scan_file(target)
    proposal = create_patch_proposal(scan_res)

    assert target.read_text(encoding="utf-8") == original_code
    assert proposal.is_valid
    assert len(proposal.candidates) == 1
    assert proposal.candidates[0].bound_name == "os"
    assert "import math" in proposal.patched_source
    assert "import os" not in proposal.patched_source
    assert proposal.diff != ""


def test_proposal_diff_generation():
    source = "import os\nimport sys\nprint(sys.argv)\n"
    scan_res = scan_source(source, filename="test.py")
    proposal = create_patch_proposal(scan_res, source_code=source)

    assert proposal.is_valid
    assert "-import os" in proposal.diff or "-import os, sys" in proposal.diff
    assert "import sys" in proposal.patched_source


def test_multiple_imports_on_one_line():
    source = "import os, sys  # preserve comment\nprint(sys.argv)\n"
    scan_res = scan_source(source, filename="multi.py")
    proposal = create_patch_proposal(scan_res, source_code=source)

    assert proposal.is_valid
    assert "import sys  # preserve comment" in proposal.patched_source
    assert "os" not in proposal.patched_source


def test_from_import_multiple_names_on_one_line():
    source = "from math import sqrt, sin, cos\nprint(sqrt(4) + cos(0))\n"
    scan_res = scan_source(source, filename="from_multi.py")
    proposal = create_patch_proposal(scan_res, source_code=source)

    assert proposal.is_valid
    assert "from math import sqrt, cos" in proposal.patched_source
    assert "sin" not in proposal.patched_source


def test_unsafe_findings_excluded_from_automatic_patch():
    source = "from .local import helper\n"
    scan_res = scan_source(source, filename="rel.py")
    assert scan_res.findings[0].confidence == NEEDS_CAUTION

    proposal = create_patch_proposal(scan_res, source_code=source)
    assert len(proposal.candidates) == 0
    assert len(proposal.excluded) == 1
    assert proposal.diff == ""
    assert proposal.patched_source == source


def test_cautionary_duplicate_name_is_preserved_when_another_is_removed():
    # line 1 is likely_unused; line 2 is needs_caution
    source = "import os\nimport os  # noqa\n"
    scan_res = scan_source(source, filename="dupe.py")
    assert len(scan_res.findings) == 2
    assert scan_res.findings[0].confidence == LIKELY_UNUSED
    assert scan_res.findings[1].confidence == NEEDS_CAUTION

    proposal = create_patch_proposal(scan_res, source_code=source)
    assert len(proposal.candidates) == 1
    assert proposal.candidates[0].line == 1
    assert len(proposal.excluded) == 1
    assert proposal.excluded[0].line == 2

    # Patched code must retain the noqa import!
    assert "import os  # noqa" in proposal.patched_source
    assert proposal.patched_source.strip() == "import os  # noqa"


def test_syntax_validation_on_proposal():
    # Construct a malformed scan result pointing to invalid python
    finding = ImportFinding(
        file="bad.py",
        line=1,
        statement="import os",
        name="os",
        alias=None,
        bound_name="os",
        confidence=LIKELY_UNUSED,
        explanation="test",
    )
    res = ScanResult(
        file="bad.py",
        sha256=hashlib.sha256(b"def invalid(:\n").hexdigest(),
        imports_found=1,
        findings=[finding],
    )
    proposal = create_patch_proposal(res, source_code="def invalid(:\n")
    assert not proposal.is_valid
    assert proposal.error_message is not None
    assert "syntax" in proposal.error_message.lower() or "failed" in proposal.error_message.lower()

    # Applying an invalid proposal must raise PatcherError
    with pytest.raises(PatcherError, match="Cannot apply invalid patch"):
        apply_patch(proposal)


def test_stale_hash_rejection(tmp_path):
    target = tmp_path / "stale.py"
    target.write_text("import os\nx = 1\n", encoding="utf-8")
    scan_res = scan_file(target)
    proposal = create_patch_proposal(scan_res)

    # Modify file after proposal generation
    target.write_text("import os\nx = 2\n# user edit\n", encoding="utf-8")

    with pytest.raises(PatcherError, match="Stale patch detected"):
        apply_patch(proposal)


def test_successful_patch_and_verification(tmp_path):
    target = tmp_path / "target.py"
    original = "import os\nimport math\nprint(math.sqrt(9))\n"
    target.write_text(original, encoding="utf-8")

    scan_res = scan_file(target)
    proposal = create_patch_proposal(scan_res)
    verif = apply_patch(proposal)

    assert verif.success
    assert verif.syntax_valid
    assert verif.target_imports_removed
    assert "import os" not in target.read_text(encoding="utf-8")
    assert "import math" in target.read_text(encoding="utf-8")


def test_rejection_leaves_file_byte_identical(tmp_path):
    target = tmp_path / "untouched.py"
    content = "import os\nprint('hello')\n"
    target.write_text(content, encoding="utf-8")
    original_bytes = target.read_bytes()

    scan_res = scan_file(target)
    proposal = create_patch_proposal(scan_res)

    # Simulate rejection: proposal created, apply never called
    assert target.read_bytes() == original_bytes


def test_preservation_of_unrelated_code_and_comments(tmp_path):
    code = (
        "# Top comment\n"
        "import os\n"
        "# Middle comment\n"
        "def compute(val: int) -> int:\n"
        "    # Inside func\n"
        "    return val * 2\n"
    )
    target = tmp_path / "comments.py"
    target.write_text(code, encoding="utf-8")

    scan_res = scan_file(target)
    proposal = create_patch_proposal(scan_res)
    verif = apply_patch(proposal)

    assert verif.success
    new_code = target.read_text(encoding="utf-8")
    assert "# Top comment" in new_code
    assert "# Middle comment" in new_code
    assert "def compute" in new_code
    assert "# Inside func" in new_code
    assert "import os" not in new_code


def test_patch_does_not_execute_target(tmp_path):
    marker = tmp_path / "executed_patch.txt"
    evil_code = f"import os\nopen(r'{marker}', 'w').write('executed')\nprint(1)\n"
    target = tmp_path / "evil_patch.py"
    target.write_text(evil_code, encoding="utf-8")

    scan_res = scan_file(target)
    proposal = create_patch_proposal(scan_res)
    verif = apply_patch(proposal)

    assert verif.success
    assert not marker.exists()


def test_post_patch_verification_failure_triggers_rollback(monkeypatch, tmp_path):
    target = tmp_path / "rollback_test.py"
    original_code = "import os\nimport math\nprint(math.sqrt(16))\n"
    target.write_text(original_code, encoding="utf-8")
    original_bytes = target.read_bytes()

    scan_res = scan_file(target)
    proposal = create_patch_proposal(scan_res)

    # Simulate post-patch verification scan failure
    from minionpatch.scanner import ScanError
    def mock_scan_file(p):
        raise ScanError("Simulated scanner verification failure")

    monkeypatch.setattr("minionpatch.patcher.scan_file", mock_scan_file)

    verif = apply_patch(proposal)
    assert not verif.success
    assert "verification failed" in verif.message.lower()
    # Confirm rollback restored original content
    assert target.read_bytes() == original_bytes


def test_crlf_line_endings_preserved():
    source = "import os, sys\r\nprint(sys.argv)\r\n"
    scan_res = scan_source(source, filename="crlf.py")
    proposal = create_patch_proposal(scan_res, source_code=source)

    assert proposal.is_valid
    assert "\r\n" in proposal.patched_source
    assert proposal.patched_source == "import sys\r\nprint(sys.argv)\r\n"


def test_utf8_bom_preserved(tmp_path):
    target = tmp_path / "bom.py"
    original_bytes = b"\xef\xbb\xbfimport os\nimport math\nprint(math.sqrt(4))\n"
    target.write_bytes(original_bytes)

    scan_res = scan_file(target)
    proposal = create_patch_proposal(scan_res)
    verif = apply_patch(proposal)

    assert verif.success
    patched_bytes = target.read_bytes()
    assert patched_bytes.startswith(b"\xef\xbb\xbf")
    assert b"import os" not in patched_bytes
    assert b"import math" in patched_bytes


def test_multi_import_one_likely_one_cautionary():
    # 'sys as sys' is a redundant alias flagged as needs_caution; 'os' is likely_unused
    source = "import os, sys as sys\n"
    scan_res = scan_source(source, filename="multi_caution.py")
    assert len(scan_res.findings) == 2
    by_bound = {f.bound_name: f.confidence for f in scan_res.findings}
    assert by_bound["os"] == LIKELY_UNUSED
    assert by_bound["sys"] == NEEDS_CAUTION

    proposal = create_patch_proposal(scan_res, source_code=source)
    assert len(proposal.candidates) == 1
    assert proposal.candidates[0].bound_name == "os"
    assert len(proposal.excluded) == 1
    assert proposal.excluded[0].bound_name == "sys"

    # 'os' removed; 'sys as sys' preserved!
    assert "import sys as sys" in proposal.patched_source
    assert "os" not in proposal.patched_source


def test_syntax_broken_by_removal_marked_invalid():
    # Removing 'import os' from this function body creates an empty function body (IndentationError)
    source = "def empty_func():\n    import os\n"
    scan_res = scan_source(source, filename="broken.py")
    proposal = create_patch_proposal(scan_res, source_code=source)

    # Must be recognized as invalid syntax and rejected before any application
    assert not proposal.is_valid
    assert "syntax" in proposal.error_message.lower() or "indent" in proposal.error_message.lower()

