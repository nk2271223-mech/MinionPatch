"""AST-based unused-import scanner.

The scanner NEVER executes the target file. It only parses it.

Known limitations (also documented in the README):
  * Name usage is matched by identifier only. Shadowing (a local variable with
    the same name as an import) can hide an unused import, so we may MISS some.
  * Usage inside string annotations is handled; usage inside arbitrary strings
    (e.g. eval/exec code, templates) is not.
  * Side-effect imports (``import matplotlib.pyplot``) look unused but may matter.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Iterable

from .models import LIKELY_UNUSED, NEEDS_CAUTION, ImportFinding, ScanResult

DYNAMIC_NAMES = {"__import__", "importlib", "globals", "locals", "eval", "exec", "getattr"}


class ScanError(Exception):
    """Raised for expected, user-facing failures (missing file, bad syntax...)."""


def scan_file(path: str | Path) -> ScanResult:
    p = Path(path)
    if not p.exists():
        raise ScanError(f"File not found: {p}")
    if not p.is_file():
        raise ScanError(f"Not a file: {p}")
    if p.suffix not in {".py", ".pyi"}:
        raise ScanError(f"Not a Python source file (.py/.pyi expected): {p}")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise ScanError(f"Cannot read {p}: {exc}") from exc
    return _scan_bytes(data, str(p), is_init=p.name == "__init__.py")


def scan_source(source: str, filename: str = "<string>") -> ScanResult:
    """Scan source text directly (used heavily by tests)."""
    return _scan_bytes(source.encode("utf-8"), filename, is_init=Path(filename).name == "__init__.py")


def _scan_bytes(data: bytes, filename: str, is_init: bool) -> ScanResult:
    sha = hashlib.sha256(data).hexdigest()
    try:
        tree = ast.parse(data, filename=filename)
    except SyntaxError as exc:
        raise ScanError(f"Syntax error in {filename}, line {exc.lineno}: {exc.msg}") from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise ScanError(f"Cannot decode/parse {filename}: {exc}") from exc

    lines = data.decode("utf-8", errors="replace").splitlines()
    _annotate_parents(tree)

    used = _collect_used_names(tree)
    exported = _collect_dunder_all(tree)
    dynamic = sorted(used & DYNAMIC_NAMES)

    notes: list[str] = []
    if dynamic:
        notes.append(
            "File uses dynamic features (" + ", ".join(dynamic) + "); "
            "usage cannot be fully determined statically, so findings are downgraded to 'needs_caution'."
        )
    if is_init:
        notes.append("__init__.py: imports are often intentional re-exports; findings are downgraded to 'needs_caution'.")

    findings: list[ImportFinding] = []
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue  # compiler directives, never "unused"
        stmt_text = _statement_text(node)
        line_text = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
        context = _context(node)

        for alias in node.names:
            count += 1
            if alias.name == "*":
                findings.append(ImportFinding(
                    filename, node.lineno, stmt_text, "*", None, "*", NEEDS_CAUTION,
                    "Star import: which names it provides cannot be determined without importing the module, "
                    "so MinionPatch cannot say whether it is used."))
                continue

            written = _written_name(node, alias)
            bound = alias.asname or alias.name.split(".")[0]
            if bound in used or bound in exported:
                continue

            reasons: list[str] = []
            if context == "type_checking":
                reasons.append("it is inside an `if TYPE_CHECKING:` block (often used only in string annotations or type comments)")
            if context == "try_import":
                reasons.append("it is inside a try/except ImportError probe (existence check may be the purpose)")
            if "noqa" in line_text.lower():
                reasons.append("the line carries a `# noqa` marker, suggesting it is intentional")
            if dynamic:
                reasons.append("the file uses dynamic features such as " + ", ".join(dynamic))
            if is_init:
                reasons.append("it is in an __init__.py (possible re-export)")
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                reasons.append("it is a relative import that may be a package re-export")
            if alias.asname and alias.asname == alias.name.split(".")[-1]:
                reasons.append("redundant alias form `x as x` is a common explicit re-export convention")

            base = f"`{bound}` is never referenced in this file"
            if reasons:
                conf = NEEDS_CAUTION
                expl = base + ", but be careful: " + "; ".join(reasons) + "."
            else:
                conf = LIKELY_UNUSED
                expl = base + ". Removing it is probably safe, but module import side effects cannot be ruled out statically."
            findings.append(ImportFinding(
                filename, node.lineno, stmt_text, written, alias.asname, bound, conf, expl))

    findings.sort(key=lambda f: (f.line, f.bound_name))
    return ScanResult(filename, sha, count, findings, notes)


# ---------------------------------------------------------------- helpers

def _annotate_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]


def _context(node: ast.AST) -> str:
    cur = getattr(node, "_parent", None)
    while cur is not None:
        if isinstance(cur, ast.If) and _is_type_checking(cur.test):
            return "type_checking"
        if isinstance(cur, ast.Try) and any(_catches_import_error(h) for h in cur.handlers):
            return "try_import"
        cur = getattr(cur, "_parent", None)
    return "normal"


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True
    names: Iterable[ast.expr] = t.elts if isinstance(t, ast.Tuple) else [t]
    return any(isinstance(n, ast.Name) and n.id in {"ImportError", "ModuleNotFoundError", "Exception"} for n in names)


def _collect_used_names(tree: ast.AST) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        for ann in _annotations_of(node):
            for sub in ast.walk(ann):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    try:
                        parsed = ast.parse(sub.value.strip(), mode="eval")
                    except SyntaxError:
                        continue
                    used.update(n.id for n in ast.walk(parsed) if isinstance(n, ast.Name))
    return used


def _annotations_of(node: ast.AST) -> list[ast.expr]:
    out: list[ast.expr] = []
    if isinstance(node, ast.arg) and node.annotation:
        out.append(node.annotation)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
        out.append(node.returns)
    elif isinstance(node, ast.AnnAssign):
        out.append(node.annotation)
    return out


def _collect_dunder_all(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets, value = [node.target], node.value
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets) and isinstance(value, (ast.List, ast.Tuple)):
            names.update(e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
    return names


def _written_name(node: ast.Import | ast.ImportFrom, alias: ast.alias) -> str:
    if isinstance(node, ast.ImportFrom):
        return "." * node.level + (node.module + "." if node.module else "") + alias.name
    return alias.name


def _statement_text(node: ast.Import | ast.ImportFrom) -> str:
    return ast.unparse(node)
