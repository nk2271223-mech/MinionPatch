"""Tests for the MinionPatch CLI commands: scan, explain, and patch."""
import json
from pathlib import Path
import sys

from minionpatch.cli import main

EX = Path(__file__).resolve().parent.parent / "examples"


def test_cli_scan_findings_exit_1(capsys):
    assert main(["scan", str(EX / "sample_unused_imports.py")]) == 1
    out = capsys.readouterr().out
    assert "numpy as np" in out and "LIKELY UNUSED" in out


def test_cli_scan_clean_exit_0(capsys):
    assert main(["scan", str(EX / "sample_clean.py")]) == 0


def test_cli_scan_missing_file_exit_2(capsys):
    assert main(["scan", "does_not_exist.py"]) == 2
    assert "not found" in capsys.readouterr().err


def test_cli_scan_json(capsys):
    assert main(["scan", str(EX / "sample_unused_imports.py"), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert {f["bound_name"] for f in data["findings"]} == {"os", "np", "Optional"}


def test_cli_explain_clean_exit_0(capsys):
    assert main(["explain", str(EX / "sample_clean.py")]) == 0
    assert "File is clean" in capsys.readouterr().out


def test_cli_explain_findings_exit_1(capsys):
    assert main(["explain", str(EX / "sample_unused_imports.py")]) == 1
    out = capsys.readouterr().out
    assert "[DETERMINISTIC EXPLANATION]" in out


def test_cli_explain_ai_fallback_exit_1(capsys):
    assert main(["explain", str(EX / "sample_unused_imports.py"), "--ai"]) == 1
    out = capsys.readouterr().out
    assert "[NON-AI DETERMINISTIC FALLBACK]" in out


def test_cli_explain_json(capsys):
    assert main(["explain", str(EX / "sample_unused_imports.py"), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["findings_count"] == 3
    assert len(data["explanations"]) == 3


def test_cli_patch_clean_exit_0(capsys):
    assert main(["patch", str(EX / "sample_clean.py")]) == 0
    assert "File is clean" in capsys.readouterr().out


def test_cli_patch_dry_run_leaves_file_unchanged(tmp_path, capsys):
    target = tmp_path / "sample.py"
    target.write_text("import os\nimport math\nprint(math.sqrt(4))\n", encoding="utf-8")
    original_bytes = target.read_bytes()

    exit_code = main(["patch", str(target), "--dry-run"])
    assert exit_code == 1
    assert target.read_bytes() == original_bytes
    out = capsys.readouterr().out
    assert "DRY RUN / PROPOSAL ONLY" in out
    assert "Proposed unified diff" in out


def test_cli_patch_dry_run_json(tmp_path, capsys):
    target = tmp_path / "sample.py"
    target.write_text("import os\nprint(1)\n", encoding="utf-8")

    exit_code = main(["patch", str(target), "--dry-run", "--json"])
    assert exit_code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "proposed"
    assert len(data["candidates"]) == 1


def test_cli_patch_non_interactive_without_yes_refuses(tmp_path, monkeypatch, capsys):
    target = tmp_path / "sample.py"
    target.write_text("import os\nprint(1)\n", encoding="utf-8")
    original_bytes = target.read_bytes()

    # Simulate non-interactive environment (isatty returns False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    exit_code = main(["patch", str(target)])
    assert exit_code == 1
    assert target.read_bytes() == original_bytes
    err = capsys.readouterr().err
    assert "Non-interactive environment detected" in err


def test_cli_patch_interactive_approval(tmp_path, monkeypatch, capsys):
    target = tmp_path / "sample.py"
    target.write_text("import os\nimport math\nprint(math.sqrt(4))\n", encoding="utf-8")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "yes")

    exit_code = main(["patch", str(target)])
    assert exit_code == 0
    new_content = target.read_text(encoding="utf-8")
    assert "import os" not in new_content
    assert "import math" in new_content
    out = capsys.readouterr().out
    assert "Patch successfully applied" in out
    assert "Post-patch verification passed" in out


def test_cli_patch_interactive_rejection(tmp_path, monkeypatch, capsys):
    target = tmp_path / "sample.py"
    original = "import os\nprint(1)\n"
    target.write_text(original, encoding="utf-8")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "no")

    exit_code = main(["patch", str(target)])
    assert exit_code == 1
    assert target.read_text(encoding="utf-8") == original
    out = capsys.readouterr().out
    assert "Patch application cancelled" in out


def test_cli_patch_yes_flag(tmp_path, capsys):
    target = tmp_path / "sample.py"
    target.write_text("import os\nprint('hello')\n", encoding="utf-8")

    exit_code = main(["patch", str(target), "--yes"])
    assert exit_code == 0
    assert "import os" not in target.read_text(encoding="utf-8")
    assert "print('hello')" in target.read_text(encoding="utf-8")


def test_cli_patch_caution_only_no_candidates(tmp_path, capsys):
    target = tmp_path / "caution.py"
    target.write_text("from .local import helper\n", encoding="utf-8")

    exit_code = main(["patch", str(target)])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "No safe patch candidates found" in out
    assert "NEEDS CAUTION" in out
