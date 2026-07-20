# tests/test_scaffold.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_imports():
    """Design: §15 the package must import without the SDKs installed.
    Implementation: import forge_mcp and assert __version__ exists.
    Example: pytest tests/test_scaffold.py::test_package_imports.
    """
    import forge_mcp

    assert isinstance(forge_mcp.__version__, str)


def test_docstring_checker_flags_missing():
    """Design: §16 Rule-21 enforcement must reject a non-trivial def with no docstring.
    Implementation: run check_file over a temp file lacking the three sections.
    Example: returns a non-empty defect list.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_docstrings

    tmp = ROOT / "tests" / "_tmp_bad.py"
    tmp.write_text("def f(x):\n    return x + 1\n")
    try:
        assert check_docstrings.check_file(tmp)  # missing docstring -> defects
    finally:
        tmp.unlink()


def test_ci_script_executable():
    """Design: §17 the CI gate is one script.
    Implementation: assert scripts/ci.sh exists and is executable.
    Example: returns True for the bundled script.
    """
    ci = ROOT / "scripts" / "ci.sh"
    assert ci.exists() and (ci.stat().st_mode & 0o111)
