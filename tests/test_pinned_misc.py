"""§18 miscellaneous pinned behavior tests."""

from __future__ import annotations


def test_schema_retry_suffix_verbatim() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.drivers._claude import SCHEMA_RETRY_SUFFIX

    expected = (
        "\n\nIMPORTANT: your previous response did not match the required "
        "output schema. Re-emit the full structured object that satisfies the "
        "schema exactly. Do not include any prose outside the structured channel."
    )
    assert SCHEMA_RETRY_SUFFIX.strip() == expected.strip()


def test_legacy_citation_patterns_absent_from_src() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    import pathlib
    import re

    pattern = re.compile(r"spec §\d+ line \d+|2026-05-\d{2}-")
    offenders = [
        str(path)
        for path in pathlib.Path("src/forge_mcp").rglob("*.py")
        if pattern.search(path.read_text())
    ]
    assert not offenders
