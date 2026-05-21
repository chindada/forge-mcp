import subprocess
import sys
import textwrap
from pathlib import Path


def _run(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    f = tmp_path / "sample.py"
    f.write_text(source)
    return subprocess.run(
        [sys.executable, "scripts/check_docstrings.py", str(f)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_passes_three_section_docstring(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    src = textwrap.dedent(
        '''
        def foo(x):
            """One-liner.

            Design: explains why this exists.
            Implementation: explains how it works.
            Example: foo(1) -> 1.
            """
            return x
    '''
    )
    r = _run(tmp_path, src)
    assert r.returncode == 0, r.stdout + r.stderr


def test_fails_missing_example(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    src = textwrap.dedent(
        '''
        def foo(x):
            """One-liner.

            Design: present.
            Implementation: present.
            """
            return x
    '''
    )
    r = _run(tmp_path, src)
    assert r.returncode != 0
    assert "Example:" in r.stdout + r.stderr


def test_ignores_trivial_body(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    src = textwrap.dedent(
        """
        def stub(): ...
        def passes(): pass
    """
    )
    r = _run(tmp_path, src)
    assert r.returncode == 0


def test_fixtures_exempt(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "sample.py").write_text("def foo(x):\n    return x\n")
    r = subprocess.run(
        [sys.executable, "scripts/check_docstrings.py", str(tmp_path / "fixtures")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
