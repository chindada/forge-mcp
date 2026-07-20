#!/usr/bin/env python3
"""Enforce Rule 21 three-section docstrings (Design / Implementation / Example).

Each non-trivial def/async def (body more than `...`/`pass`) must carry a
docstring whose text contains the labels `Design:`, `Implementation:`,
`Example:`, each followed by >=5 non-whitespace characters. Files under any
`fixtures/` path are exempt.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

LABELS = ("Design:", "Implementation:", "Example:")
BOILERPLATE = "Exercise behavior pinned by the forge-mcp implementation plan."


def _is_trivial(body: list[ast.stmt]) -> bool:
    """Return True when a function body is just `pass` / `...` / a docstring.

    Design: §16 Rule 21 only requires the three-section docstring on bodies
        that actually do work — trivial stubs (Protocol declarations, abstract
        methods, no-op placeholders) are exempt.
    Implementation: a body of length 1 whose only node is `pass`, an
        `Expr(Constant(Ellipsis))`, or a docstring `Expr(Constant(str))` is
        considered trivial.
    Example: _is_trivial([ast.Pass()]) returns True.
    """
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return stmt.value.value is Ellipsis or isinstance(stmt.value.value, str)
    return isinstance(stmt, ast.Pass)


def _label_ok(doc: str, label: str) -> bool:
    """Return True if one label has enough following non-whitespace text.

    Design: §16 Rule 21 requires Design, Implementation, and Example labels to
        each be substantive rather than present as empty headings.
    Implementation: find the label, read until the next blank-line-delimited
        section, and require at least five non-whitespace characters.
    Example: _label_ok('Design: useful context', 'Design:') returns True.
    """
    match = re.search(re.escape(label), doc)
    if not match:
        return False
    rest = doc[match.end() :].split("\n\n", 1)[0]
    return len(re.sub(r"\s+", "", rest)) >= 5


def check_file(path: Path) -> list[str]:
    """Return docstring defects for one Python source file.

    Design: §16 enforces Rule 21 in CI while exempting fixtures and trivial
        declarations so protocols can stay concise.
    Implementation: parse the AST, walk function nodes, and emit path/line/name
        strings for missing, incomplete, or boilerplate docstrings.
    Example: check_file(Path('src/forge_mcp/state.py')) returns [] when clean.
    """
    if "fixtures" in path.parts:
        return []
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: SyntaxError: {exc}"]
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if _is_trivial(node.body):
            continue
        doc = ast.get_docstring(node)
        if doc is None:
            errors.append(f"{path}:{node.lineno}: {node.name}: missing docstring")
            continue
        if BOILERPLATE in doc:
            errors.append(f"{path}:{node.lineno}: {node.name}: boilerplate template docstring")
            continue
        for label in LABELS:
            if not _label_ok(doc, label):
                errors.append(f"{path}:{node.lineno}: {node.name}: missing/short '{label}'")
    return errors


def walk(roots: list[str]) -> list[Path]:
    """Expand input roots into Python files while skipping fixtures.

    Design: §16 exempts fixture trees because generated or sample code may not
        follow production docstring policy.
    Implementation: accept individual `.py` files or recursively enumerate
        directories with `Path.rglob`, filtering any segment named fixtures.
    Example: walk(['src']) returns the production Python files under src.
    """
    out: list[Path] = []
    for root in roots:
        path = Path(root)
        if path.is_file() and path.suffix == ".py":
            out.append(path)
        elif path.is_dir():
            out.extend(q for q in path.rglob("*.py") if "fixtures" not in q.parts)
    return out


def main(argv: list[str]) -> int:
    """Run the docstring checker CLI and return a process status.

    Design: §17 includes this script in the CI gate, so violations must be
        printed plainly and reflected in a nonzero exit status.
    Implementation: validate arguments, aggregate `check_file` results across
        walked paths, print each defect, and return 1 when any exist.
    Example: main(['src', 'tests']) returns 0 for a clean tree.
    """
    if not argv:
        print("usage: check_docstrings.py <path> [<path> ...]", file=sys.stderr)
        return 2
    errors: list[str] = []
    for path in walk(argv):
        errors.extend(check_file(path))
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
