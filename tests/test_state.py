from __future__ import annotations

import json
from pathlib import Path

from forge_mcp.state import durable_append, durable_replace, light_replace, write_json


def test_durable_replace_overwrites_atomically(tmp_path: Path):
    """Design: §13 whole-file replace is the state.json/spec.md writer.
    Implementation: write twice; the second fully replaces the first.
    Example: file content == last write.
    """
    p = tmp_path / "state.json"
    durable_replace(p, "first")
    durable_replace(p, "second")
    assert p.read_text() == "second"
    assert (p.stat().st_mode & 0o777) == 0o600


def test_durable_append_preserves_prior_entries(tmp_path: Path):
    """Design: §13/I3 the audit log must NOT be replaced wholesale.
    Implementation: two appends accumulate.
    Example: both lines present.
    """
    p = tmp_path / "spec_amendments.md"
    durable_append(p, "entry-1\n")
    durable_append(p, "entry-2\n")
    assert p.read_text() == "entry-1\nentry-2\n"


def test_light_replace_writes(tmp_path: Path):
    """Design: §13 light tier for fingerprints/artifacts.
    Implementation: bytes round-trip.
    Example: file content matches.
    """
    p = tmp_path / "fp.json"
    light_replace(p, b'["a|high"]')
    assert p.read_bytes() == b'["a|high"]'


def test_write_json_durable_and_light(tmp_path: Path):
    """Design: §13 write_json routes a dict/model to the chosen tier.
    Implementation: durable=True and False both produce valid JSON.
    Example: json.loads round-trips.
    """
    p = tmp_path / "x.json"
    write_json(p, {"k": 1}, durable=True)
    assert json.loads(p.read_text()) == {"k": 1}
    write_json(p, {"k": 2}, durable=False)
    assert json.loads(p.read_text()) == {"k": 2}


def test_write_json_indent_pretty_prints_for_humans(tmp_path: Path):
    """Design: §13 indent is presentation-only — indent=2 pretty-prints the
        human-read artifacts (eval.json/triage.json); the default stays compact.
    Implementation: a nested dict with indent=2 is multi-line and indented yet
        round-trips; the default has no newlines.
    Example: indented output contains a newline; compact output does not.
    """
    p = tmp_path / "x.json"
    write_json(p, {"a": 1, "b": [2, 3]}, durable=False, indent=2)
    pretty = p.read_text()
    assert "\n  " in pretty  # multi-line and indented
    assert json.loads(pretty) == {"a": 1, "b": [2, 3]}
    write_json(p, {"a": 1, "b": [2, 3]}, durable=False)
    assert "\n" not in p.read_text()  # default stays compact single-line
