"""Unit table for the canonical run-id matcher (finding 7)."""

from __future__ import annotations

import pytest

from forge_mcp.ids import RUN_ID_PATTERN, is_run_id


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("abcd1234", True),
        ("00000000", True),
        ("0123abcd", True),
        ("ABCD1234", False),
        ("abcd123", False),
        ("abcd12345", False),
        ("abcdwxyz", False),
        (".gitignore", False),
        ("run.lock", False),
        ("", False),
    ],
)
def test_is_run_id_table(name: str, expected: bool) -> None:
    """Pin the run-id shape across hex/length/case edge cases.

    Design: finding 7 — one matcher must accept exactly ^[0-9a-f]{8}$ so prune
        and the lineage/resume globs cannot act on non-run dirs.
    Implementation: drive is_run_id over a table of representative names.
    Example: pytest asserts is_run_id('abcd1234') is True.
    """
    assert is_run_id(name) is expected


def test_run_id_pattern_is_anchored_hex8() -> None:
    """Pin the exported pattern string used by state.py Field.

    Design: finding 7 — state.json keeps the SoT shape, now shared via the leaf.
    Implementation: assert the literal pattern value.
    Example: RUN_ID_PATTERN == r'^[0-9a-f]{8}$'.
    """
    assert RUN_ID_PATTERN == r"^[0-9a-f]{8}$"


def test_ids_module_imports_only_re() -> None:
    """Pin ids.py as a stdlib-only leaf so resources.py may import it.

    Design: finding 7 — the canonical matcher must not pull in mcp/orchestrator/
        drivers/logging, preserving the §R9.1 resources.py isolation guarantee.
    Implementation: AST-parse ids.py and assert every import is `re`.
    Example: pytest asserts no forbidden import appears in ids.py.
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "forge_mcp" / "ids.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".", 1)[0] in {"re", "__future__"}
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".", 1)[0] in {"re", "__future__"}
