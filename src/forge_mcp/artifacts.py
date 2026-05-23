"""Artifact file helpers and markdown rendering (§13, §10.4)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .ids import is_run_id
from .models import DesignFlawGap, EvalResult


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    """Atomically write text with private permissions.

    Design: §13 requires sensitive artifacts to be private and durable enough
        for file handoff without exposing partial writes.
    Implementation: write a same-directory temp file, chmod it, and os.replace
        it into place; cleanup removes temp files on error.
    Example: atomic_write_text(Path('summary.md'), 'done').
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            # §S-Inv 4 — updated-resource notifications fire only after a
            # durable temp-file write followed by os.replace.
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def atomic_write_json(path: Path, obj: Any, mode: int = 0o600) -> None:
    """Atomically write JSON with deterministic formatting.

    Design: §13 uses JSON artifacts as structured handoff files between fresh
        agent sessions, so writes must be complete and private.
    Implementation: json.dumps with indentation then delegate to the text
        atomic writer for permission and replacement behavior.
    Example: atomic_write_json(Path('eval.json'), {'no_gaps': True}).
    """
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n", mode=mode)


def write_sessions_json(path: Path, iteration: int, entries: list[dict[str, Any]]) -> None:
    """Atomically persist phase session-id records (§C2.4).

    Design: sessions.json is a forensic sidecar that records phase order and
        SDK ids without becoming an input to terminal-result decisions.
    Implementation: render iteration plus ordered phases with sort_keys=False,
        then delegate to atomic_write_text for 0600 replacement semantics.
    Example: write_sessions_json(Path('iteration-1/sessions.json'), 1, entries).
    """
    payload = {"iteration": iteration, "phases": entries}
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=False) + "\n")


def create_run_dir(harness_dir: Path, run_id: str) -> Path:
    """Create the initial run directory layout.

    Design: §8.1 and §13 create only run, inputs, and plan directories before
        iteration directories are needed.
    Implementation: mkdir each directory with mode 0700 and chmod explicitly on
        POSIX to avoid process umask surprises.
    Example: run_dir = create_run_dir(Path('.harness'), 'abcd1234').
    """
    run_dir = harness_dir / run_id
    for path in (run_dir, run_dir / "inputs", run_dir / "plan"):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(path, 0o700)
    return run_dir


def prune_old_runs(harness_dir: Path, *, keep_last: int, current_run_id: str | None = None) -> None:
    """Delete all but newest run dirs under harness_dir (§H9).

    Design: §H9 bounds sensitive artifact retention while never deleting the
        current or resumed run directory.
    Implementation: sort run-id-shaped dirs (via is_run_id) by state.json
        started_at with mtime fallback, keep newest keep_last plus current, and
        rmtree the rest.
    Example: prune_old_runs(Path('/repo/.harness'), keep_last=10).
    """
    from .state import read_state

    if keep_last < 0:
        return
    entries: list[tuple[float, Path]] = []
    for path in harness_dir.glob("*"):
        if not path.is_dir() or not is_run_id(path.name):
            continue
        try:
            sort_key = read_state(path / "state.json").started_at.timestamp()
        except Exception:
            try:
                sort_key = path.stat().st_mtime
            except OSError:
                sort_key = 0.0
        entries.append((sort_key, path))
    entries.sort(key=lambda item: item[0], reverse=True)
    keep = {path for _, path in entries[:keep_last]}
    for _, path in entries:
        if path in keep or path.name == current_run_id:
            continue
        shutil.rmtree(path, ignore_errors=True)


def escape_md_inline(value: str) -> str:
    """Escape markdown table-sensitive inline text, collapsing line breaks.

    Design: §10.4 pins backslash-before-pipe escaping so gap tables stay
        parseable. Finding 3: multi-line prose previously broke table rows, so
        all CR/LF variants collapse to a GFM cell line-break (<br>) AFTER the
        backslash/pipe escaping (<br> carries no further-escaped character).
    Implementation: escape backslashes, then pipes, then normalize CRLF/CR/LF
        to <br> so no raw newline survives in a table cell.
    Example: escape_md_inline('a|b\\c\\nd') returns 'a\\|b\\\\c<br>d'.
    """
    escaped = value.replace("\\", "\\\\").replace("|", "\\|")
    return escaped.replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


def escape_md(value: str) -> str:
    """Escape block markdown while guarding accidental headings/lists.

    Design: §10.4 wants evaluator prose rendered as content, not interpreted as
        table breaks, headings, blockquotes, or list structure.
    Implementation: process line-by-line, prefix a backslash when the stripped
        line begins with a markdown structural marker, then escape pipes.
    Example: escape_md('# title') returns '\\# title'.
    """
    escaped: list[str] = []
    for line in value.splitlines():
        stripped = line.lstrip()
        if (
            stripped.startswith("#")
            or stripped.startswith(">")
            or stripped.startswith(("- ", "* ", "+ "))
        ):
            indent = line[: len(line) - len(stripped)]
            line = f"{indent}\\{stripped}"
        escaped.append(line.replace("|", "\\|"))
    return "\n".join(escaped)


def render_eval_md(eval_result: EvalResult, *, iteration_n: int) -> str:
    """Render an EvalResult as a markdown artifact.

    Design: §10.4 keeps a human-readable evaluator artifact while §15 removes
        all legacy browser-probe reporting from the template.
    Implementation: emit a header, no_gaps flag, summary, and either a table of
        gaps or an explicit no-gaps marker.
    Example: render_eval_md(EvalResult(no_gaps=True, summary='ok'), iteration_n=1).
    """
    lines = [
        f"# Evaluation iteration {iteration_n}",
        "",
        f"**no_gaps:** `{str(eval_result.no_gaps).lower()}`",
        "",
        "## Summary",
        "",
        escape_md(eval_result.summary),
        "",
        "## Gaps",
        "",
    ]
    if not eval_result.gaps:
        lines.append("_no gaps reported_")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| Title | Severity | Design section | Current state | "
            "Expected state | Suggested fix |",
            "|---|---|---|---|---|---|",
        ]
    )
    for gap in eval_result.gaps:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md_inline(gap.title),
                    escape_md_inline(gap.severity),
                    escape_md_inline(gap.design_doc_section),
                    escape_md_inline(escape_md(gap.current_state)),
                    escape_md_inline(escape_md(gap.expected_state)),
                    escape_md_inline(escape_md(gap.suggested_fix)),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_design_fingerprint(inputs_dir: Path, fingerprint: str) -> None:
    """Atomically write inputs/design.fingerprint with 0600 perms (§L2.3).

    Design: §L2.3 uses a glob-friendly one-line fingerprint sidecar next to
        inputs/design.md so later runs can match exact canonical design docs.
    Implementation: write hex digest plus LF through atomic_write_text, inheriting
        same-directory temp replacement and POSIX 0600 permissions.
    Example: write_design_fingerprint(run_dir / "inputs", "a" * 64).
    """
    atomic_write_text(inputs_dir / "design.fingerprint", fingerprint + "\n")


_PRIOR_ATTEMPTS_MAX_BYTES = 32_768


def write_prior_attempts(inputs_dir: Path, text: str) -> Path | None:
    """Write capped inputs/prior_attempts.md plus overflow if needed (§L6.2).

    Design: §L6.2 caps the planner digest so cross-run learning cannot consume
        unbounded context; overflow is preserved as a forensic artifact.
    Implementation: if UTF-8 bytes fit, atomically write the whole file;
        otherwise split at the last newline before the cap, append a pointer to
        the head, and atomically write prior_attempts-overflow.md.
    Example: overflow = write_prior_attempts(run_dir / "inputs", digest).
    """
    encoded = text.encode("utf-8")
    path = inputs_dir / "prior_attempts.md"
    if len(encoded) <= _PRIOR_ATTEMPTS_MAX_BYTES:
        atomic_write_text(path, text)
        return None
    cut = encoded.rfind(b"\n", 0, _PRIOR_ATTEMPTS_MAX_BYTES)
    if cut == -1:
        cut = _PRIOR_ATTEMPTS_MAX_BYTES
        # §I: walk back while the first byte of the right half is a UTF-8
        # continuation byte, leaving both decoded halves on character boundaries.
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
    head_text = encoded[:cut].decode("utf-8", errors="replace")
    tail_text = encoded[cut:].lstrip(b"\n").decode("utf-8", errors="replace")
    atomic_write_text(path, head_text + "\n... truncated; see prior_attempts-overflow.md\n")
    overflow_path = inputs_dir / "prior_attempts-overflow.md"
    atomic_write_text(overflow_path, tail_text)
    return overflow_path


def write_design_flaws(run_dir: Path, flaws: list[DesignFlawGap]) -> None:
    """Atomically write <run_dir>/design_flaws.json sidecar (§L8.4).

    Design: §L8.4 persists full structured design-flaw records before result
        caps truncate in-memory lists, enabling later lineage summaries.
    Implementation: serialize an envelope {"gaps": [...]} using
        model_dump(mode="json") for each flaw and atomic_write_text for 0600
        replacement semantics.
    Example: write_design_flaws(run_dir, ledger.design_flaw_gaps).
    """
    payload = {"gaps": [flaw.model_dump(mode="json") for flaw in flaws]}
    atomic_write_text(run_dir / "design_flaws.json", json.dumps(payload, indent=2) + "\n")
