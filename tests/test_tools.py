"""Focused offline tests for MinionPatch OpenMinion tools and agent integration.

Verifies:
1. Tool execution works deterministically offline without network calls.
2. Tools handle valid files, clean files, missing files, and syntax errors safely.
3. Patch proposals never modify source files on disk.
4. Tools correctly register into OpenMinion's ToolRegistry.
5. No write tools are exposed to the agent (patch application remains human-only).
"""
import hashlib
import json
from pathlib import Path
import pytest

from openminion import Agent, tool
from minionpatch.agent import create_agent
from minionpatch.tools import propose_import_patch, scan_unused_imports


def test_scan_unused_imports_with_findings():
    """Verify scan_unused_imports detects unused imports on a sample file."""
    sample_path = "examples/sample_unused_imports.py"
    raw_result = scan_unused_imports(sample_path)
    data = json.loads(raw_result)

    assert data["success"] is True
    assert data["findings_count"] == 3
    bound_names = {f["bound_name"] for f in data["findings"]}
    assert "os" in bound_names
    assert "np" in bound_names
    assert "Optional" in bound_names


def test_scan_unused_imports_clean_file():
    """Verify scan_unused_imports reports 0 findings on a clean file."""
    sample_path = "examples/sample_clean.py"
    raw_result = scan_unused_imports(sample_path)
    data = json.loads(raw_result)

    assert data["success"] is True
    assert data["findings_count"] == 0
    assert len(data["findings"]) == 0


def test_scan_unused_imports_missing_file():
    """Verify scan_unused_imports handles missing paths gracefully without crashing."""
    raw_result = scan_unused_imports("nonexistent_path_xyz_123.py")
    data = json.loads(raw_result)

    assert data["success"] is False
    assert "error" in data
    assert "File not found" in data["error"]


def test_scan_unused_imports_syntax_error(tmp_path):
    """Verify scan_unused_imports safely reports syntax errors in JSON."""
    bad_file = tmp_path / "bad_syntax.py"
    bad_file.write_text("def broken_code(:\n    pass\n", encoding="utf-8")

    raw_result = scan_unused_imports(str(bad_file))
    data = json.loads(raw_result)

    assert data["success"] is False
    assert "Syntax error" in data["error"]


def test_propose_import_patch_leaves_file_completely_unchanged(tmp_path):
    """Crucial safety test: verify proposing a patch NEVER modifies the file on disk."""
    sample_bytes = b"import os\nimport math\n\nprint(math.sqrt(4))\n"
    target_file = tmp_path / "target.py"
    target_file.write_bytes(sample_bytes)
    original_sha = hashlib.sha256(sample_bytes).hexdigest()

    raw_result = propose_import_patch(str(target_file))
    data = json.loads(raw_result)

    assert data["success"] is True
    assert data["status"] == "proposal_generated"
    assert "-import os" in data["diff"]

    # File on disk MUST be byte-for-byte identical to original
    current_bytes = target_file.read_bytes()
    assert current_bytes == sample_bytes
    assert hashlib.sha256(current_bytes).hexdigest() == original_sha


def test_propose_import_patch_clean_file():
    """Verify propose_import_patch handles clean files with no diff."""
    raw_result = propose_import_patch("examples/sample_clean.py")
    data = json.loads(raw_result)

    assert data["success"] is True
    assert data["status"] == "clean"
    assert data["diff"] == ""


def test_propose_import_patch_cautionary_only(tmp_path):
    """Verify propose_import_patch excludes cautionary findings from proposed diff."""
    code = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import typing_only_mod\n"
    )
    caution_file = tmp_path / "caution.py"
    caution_file.write_text(code, encoding="utf-8")

    raw_result = propose_import_patch(str(caution_file))
    data = json.loads(raw_result)

    assert data["success"] is True
    assert data["status"] == "no_safe_candidates"
    assert data["diff"] == ""
    assert len(data["excluded"]) > 0


def test_propose_import_patch_missing_file():
    """Verify propose_import_patch reports error on missing file without raising unhandled exception."""
    raw_result = propose_import_patch("nonexistent_path_abc_456.py")
    data = json.loads(raw_result)

    assert data["success"] is False
    assert "error" in data


def test_openminion_tool_registry_registration_and_offline_dispatch(tmp_path):
    """Verify tools register with OpenMinion's ToolRegistry and execute offline via handler."""
    sample_file = tmp_path / "check.py"
    sample_file.write_text("import os\nx = 1\n", encoding="utf-8")

    with create_agent(session_id="test-tools-registration") as agent:
        runtime = agent._ensure_runtime()
        registered = agent._register_tools_for_run(runtime)

        assert "scan_unused_imports" in registered
        assert "propose_import_patch" in registered

        # Dispatch scan tool through runtime registry
        scan_spec = runtime.tools.get("scan_unused_imports")
        scan_args = scan_spec.args_model(file_path=str(sample_file))
        scan_output = scan_spec.handler(scan_args)
        scan_data = json.loads(scan_output)
        assert scan_data["success"] is True
        assert scan_data["findings_count"] == 1

        # Dispatch patch proposal tool through runtime registry
        patch_spec = runtime.tools.get("propose_import_patch")
        patch_args = patch_spec.args_model(file_path=str(sample_file))
        patch_output = patch_spec.handler(patch_args)
        patch_data = json.loads(patch_output)
        assert patch_data["success"] is True
        assert patch_data["status"] == "proposal_generated"

        # Unregister tools
        agent._unregister_tools(runtime, registered)

    with pytest.raises(KeyError):
        runtime.tools.get("scan_unused_imports")


def test_no_write_tools_exposed_to_agent():
    """Verify no tool can modify files or apply patches directly through agent tools."""
    import minionpatch.tools as mp_tools

    # Inspect all callables decorated with @tool in minionpatch.tools
    tool_names = []
    for attr_name in dir(mp_tools):
        attr = getattr(mp_tools, attr_name)
        if hasattr(attr, "tool_decl"):
            tool_names.append(attr.tool_decl.name)

    # Strictly ensure apply_patch is NOT an exposed agent tool
    assert "apply_patch" not in tool_names
    assert "apply_import_patch" not in tool_names
    # Only read-only tools are exposed
    assert set(tool_names) == {"scan_unused_imports", "propose_import_patch"}
