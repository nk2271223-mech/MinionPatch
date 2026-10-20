import os
import sys

import pytest

from minionpatch.scanner import ScanError, scan_file, scan_source


def names(src, **kw):
    return [f.bound_name for f in scan_source(src, **kw).findings]


def conf(src, bound):
    return next(f.confidence for f in scan_source(src).findings if f.bound_name == bound)


def test_clean_file_without_imports():
    r = scan_source("x = 1\n")
    assert r.imports_found == 0 and r.findings == []


def test_unused_stdlib_import_flagged():
    r = scan_source("import os\nx = 1\n")
    assert [f.bound_name for f in r.findings] == ["os"]
    assert r.findings[0].confidence == "likely_unused"
    assert r.findings[0].line == 1
    assert "never referenced" in r.findings[0].explanation


def test_used_stdlib_import_not_flagged():
    assert names("import os\nprint(os.getcwd())\n") == []


def test_aliased_import():
    assert names("import numpy as np\n") == ["np"]
    f = scan_source("import numpy as np\n").findings[0]
    assert f.name == "numpy" and f.alias == "np"
    assert names("import numpy as np\nnp.array([])\n") == []


def test_multiple_imports_one_statement():
    assert names("import os, sys\nprint(sys.argv)\n") == ["os"]


def test_dotted_import_binds_top_level_name():
    assert names("import os.path\nos.path.join('a')\n") == []
    assert names("import os.path\n") == ["os"]


def test_from_import_and_alias():
    src = "from pathlib import Path\nfrom a import b, c as helper\nhelper()\n"
    assert names(src) == ["Path", "b"]


def test_used_inside_function_and_class():
    src = "import json\ndef f():\n    return json.dumps({})\nclass A:\n    def g(self):\n        return json\n"
    assert names(src) == []


def test_used_in_annotation_and_string_annotation():
    assert names("from typing import List\ndef f(x: List[int]): ...\n") == []
    assert names("from decimal import Decimal\ndef f(x: 'Decimal'): ...\n") == []
    assert names("from decimal import Decimal\nx: 'Decimal | None' = None\n") == []


def test_used_in_conditional_branch():
    assert names("import os\nif True:\n    os.getcwd()\n") == []


def test_relative_import_needs_caution():
    r = scan_source("from .module import thing\n")
    assert r.findings[0].confidence == "needs_caution"
    assert r.findings[0].name == ".module.thing"


def test_star_import_needs_caution():
    r = scan_source("from os.path import *\n")
    assert r.findings[0].bound_name == "*" and r.findings[0].confidence == "needs_caution"


def test_type_checking_block_needs_caution():
    src = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from foo import Bar\n"
    assert conf(src, "Bar") == "needs_caution"


def test_dunder_all_reexport_not_flagged():
    assert names("from os import path\n__all__ = ['path']\n") == []


def test_dynamic_import_downgrades():
    src = "import os\nimport importlib\nimportlib.import_module('x')\n"
    assert conf(src, "os") == "needs_caution"


def test_noqa_needs_caution():
    assert conf("import os  # noqa: F401\n", "os") == "needs_caution"


def test_try_import_probe_needs_caution():
    src = "try:\n    import ujson\nexcept ImportError:\n    ujson = None\n"
    # `ujson` is assigned (Store) in the handler, so it counts as referenced -> not flagged at all
    assert names(src) == []
    src2 = "try:\n    import ujson\nexcept ImportError:\n    pass\n"
    assert conf(src2, "ujson") == "needs_caution"


def test_future_import_ignored():
    assert names("from __future__ import annotations\n") == []


def test_init_file_downgraded():
    r = scan_source("from .a import b\n", filename="pkg/__init__.py")
    assert r.findings[0].confidence == "needs_caution" and r.notes


def test_sha256_changes_with_content():
    assert scan_source("import os\n").sha256 != scan_source("import sys\n").sha256


def test_files_changed_after_scanning(tmp_path):
    target = tmp_path / "changed.py"
    target.write_text("import os\n", encoding="utf-8")
    initial_scan = scan_file(target)

    # Modify file
    target.write_text("import os\nimport sys\n", encoding="utf-8")
    updated_scan = scan_file(target)

    assert initial_scan.sha256 != updated_scan.sha256
    assert initial_scan.imports_found == 1
    assert updated_scan.imports_found == 2


def test_syntax_error():
    with pytest.raises(ScanError, match="Syntax error"):
        scan_source("def (:\n")


def test_missing_file(tmp_path):
    with pytest.raises(ScanError, match="not found"):
        scan_file(tmp_path / "nope.py")


def test_non_python_extension(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("import os")
    with pytest.raises(ScanError, match="Not a Python"):
        scan_file(p)


def test_invalid_bytes(tmp_path):
    p = tmp_path / "bad.py"
    p.write_bytes(b"x = '\xff\xfe'\n\x00")
    with pytest.raises(ScanError):
        scan_file(p)


@pytest.mark.skipif(sys.platform.startswith("win") or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="chmod-based unreadable-file test is unreliable on Windows/root")
def test_unreadable_file(tmp_path):
    p = tmp_path / "locked.py"
    p.write_text("import os\n")
    p.chmod(0)
    try:
        with pytest.raises(ScanError, match="Cannot read"):
            scan_file(p)
    finally:
        p.chmod(0o600)


def test_scan_file_does_not_execute_target(tmp_path):
    marker = tmp_path / "executed.txt"
    p = tmp_path / "evil.py"
    p.write_text(f"import os\nopen(r'{marker}', 'w').write('x')\n")
    scan_file(p)
    assert not marker.exists()
