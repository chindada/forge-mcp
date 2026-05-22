"""§X cross-design aggregation and digest tests."""

import ast
import logging
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

from forge_mcp.drivers import evaluator as evaluator_mod
from forge_mcp.orchestrator import cross_design
from forge_mcp.state import RunState, write_state


def _run(harness: Path, run_id: str, fp: str, kind: str, when: datetime) -> None:
    """Create a synthetic terminal run for cross-design tests.

    Design: aggregator tests should use the real state writer shape.
    Implementation: write state.json, inputs/design.fingerprint, and the
        post-classified design_flaws.json envelope.
    Example: _run(tmp_path, '00000001', 'fp1', 'k', now).
    """
    run_dir = harness / run_id
    (run_dir / "inputs").mkdir(parents=True)
    write_state(
        run_dir / "state.json",
        RunState(
            state="completed",
            run_id=run_id,
            target_dir="/r",
            iteration=1,
            started_at=when,
            last_updated_at=when,
        ),
    )
    (run_dir / "inputs" / "design.fingerprint").write_text(fp + "\n", encoding="utf-8")
    (run_dir / "design_flaws.json").write_text(
        '{"gaps":[{"fault_kind":"' + kind + '","explanation":"example"}]}\n',
        encoding="utf-8",
    )


def test_aggregator_requires_three_recent_distinct_fingerprints(tmp_path: Path) -> None:
    """§X2 gates on recency and distinct design fingerprints.

    Design: a pattern must recur across at least three distinct recent designs
        before becoming a planner prior.
    Implementation: create two eligible runs, then a third, and compare output.
    Example: the third distinct fingerprint surfaces one CrossPattern.
    """
    now = datetime.now(UTC)
    _run(tmp_path, "00000001", "fp1", "missing_validation", now)
    _run(tmp_path, "00000002", "fp2", "missing_validation", now)
    assert cross_design.find_cross_design_patterns(tmp_path) == []
    _run(tmp_path, "00000003", "fp3", "missing_validation", now)
    patterns = cross_design.find_cross_design_patterns(tmp_path)
    assert len(patterns) == 1
    assert patterns[0].fault_kind == "missing_validation"
    assert patterns[0].distinct_fingerprints == 3


def test_aggregator_empty_harness_yields_empty_digest(tmp_path: Path) -> None:
    """§X2: an empty harness yields no patterns and a header-only digest file.

    Design: cold start with zero terminal runs must surface nothing while the
        digest FILE is still written carrying the advisory header for the
        planner (§X-Inv 6 — inputs/cross_design_patterns.md is always written).
    Implementation: run find_cross_design_patterns and render_cross_design_digest
        against an empty tmp_path, write the digest to disk as the orchestrator
        does, and assert the file exists and holds the verbatim header.
    Example: (tmp_path / "cross_design_patterns.md").read_text() == _DIGEST_HEADER.
    """
    assert cross_design.find_cross_design_patterns(tmp_path) == []
    assert cross_design.render_digest([]) == cross_design._DIGEST_HEADER

    # §X-Inv 6: the digest FILE is always written, header-only on empty harness.
    digest = cross_design.render_cross_design_digest(tmp_path, logging.getLogger("test"))
    digest_file = tmp_path / "cross_design_patterns.md"
    digest_file.write_text(digest, encoding="utf-8")
    assert digest_file.exists()
    assert digest_file.read_text(encoding="utf-8") == cross_design._DIGEST_HEADER


def test_aggregator_below_floor_does_not_surface(tmp_path: Path) -> None:
    """§X2: two distinct fingerprints sharing a fault stay below the floor.

    Design: a pattern must recur across at least three distinct designs before
        becoming a prior, so two distinct fingerprints surface nothing.
    Implementation: write two distinct-fingerprint runs with the same fault and
        assert the aggregator returns an empty list.
    Example: two eligible runs -> find_cross_design_patterns == [].
    """
    now = datetime.now(UTC)
    _run(tmp_path, "00000001", "fp1", "missing_validation", now)
    _run(tmp_path, "00000002", "fp2", "missing_validation", now)
    assert cross_design.find_cross_design_patterns(tmp_path) == []


def test_aggregator_at_floor_surfaces(tmp_path: Path) -> None:
    """§X2: three distinct fingerprints sharing a fault surface one pattern.

    Design: reaching the distinct-fingerprint floor promotes a recurring fault
        to a single weak cross-design prior.
    Implementation: write three distinct-fingerprint runs with the same fault
        and assert exactly one pattern with the expected fault kind and count.
    Example: three eligible runs -> one CrossPattern, distinct_fingerprints == 3.
    """
    now = datetime.now(UTC)
    _run(tmp_path, "00000001", "fp1", "missing_validation", now)
    _run(tmp_path, "00000002", "fp2", "missing_validation", now)
    _run(tmp_path, "00000003", "fp3", "missing_validation", now)
    patterns = cross_design.find_cross_design_patterns(tmp_path)
    assert len(patterns) == 1
    assert patterns[0].fault_kind == "missing_validation"
    assert patterns[0].distinct_fingerprints == 3


def test_aggregator_drops_old_and_duplicate_fingerprints(tmp_path: Path) -> None:
    """§X2 excludes old observations and repeated same-design runs.

    Design: repeated terminal attempts for the same design must not masquerade
        as a cross-design pattern.
    Implementation: write duplicate fingerprints plus one 31-day-old run.
    Example: no pattern qualifies.
    """
    now = datetime.now(UTC)
    old = now - timedelta(days=31)
    for index in range(4):
        _run(tmp_path, f"0000000{index + 1}", "same", "k", now)
    _run(tmp_path, "00000005", "fp-old", "k", old)
    assert cross_design.find_cross_design_patterns(tmp_path) == []


def test_aggregator_recency_window_drops_old(tmp_path: Path) -> None:
    """§X2: observations older than the recency window do not count.

    Design: a fault must recur across three recent designs; a 31-day-old run
        falls outside the 30-day window and cannot push a fault to the floor.
    Implementation: write two recent distinct-fingerprint runs plus one
        31-day-old distinct run and assert the aggregator returns [].
    Example: two recent + one stale run -> find_cross_design_patterns == [].
    """
    now = datetime.now(UTC)
    old = now - timedelta(days=31)
    _run(tmp_path, "00000001", "fp1", "missing_validation", now)
    _run(tmp_path, "00000002", "fp2", "missing_validation", now)
    _run(tmp_path, "00000003", "fp-old", "missing_validation", old)
    assert cross_design.find_cross_design_patterns(tmp_path) == []


def test_aggregator_distinct_fingerprints_not_runs(tmp_path: Path) -> None:
    """§X2: repeated runs of one design count as a single fingerprint.

    Design: many terminal attempts for the same design must not masquerade as a
        cross-design pattern; the gate counts distinct fingerprints, not runs.
    Implementation: write four runs sharing one fingerprint and the same fault
        and assert the aggregator returns [].
    Example: four same-fingerprint runs -> find_cross_design_patterns == [].
    """
    now = datetime.now(UTC)
    for index in range(4):
        _run(tmp_path, f"0000000{index + 1}", "same", "missing_validation", now)
    assert cross_design.find_cross_design_patterns(tmp_path) == []


def test_digest_header_and_caps() -> None:
    """§X3 digest always carries anti-anchoring header and byte caps.

    Design: advisory framing appears even with no patterns, and total output is
        bounded before reaching the planner context.
    Implementation: render empty and many large patterns.
    Example: digest length stays under 8 KiB by default.
    """
    assert cross_design.render_digest([]).startswith("# Cross-design patterns (advisory)\n")
    assert "statistical priors" in cross_design.render_digest([])
    now = datetime.now(UTC)
    patterns = [
        cross_design.CrossPattern(f"k{i}", 3, now - timedelta(days=i), "x" * 2000)
        for i in range(20)
    ]
    digest = cross_design.render_digest(patterns)
    assert len(digest.encode("utf-8")) <= 8 * 1024
    assert "k0" in digest


def test_digest_header_verbatim() -> None:
    """§X3: an empty pattern set renders exactly the verbatim advisory header.

    Design: the anti-anchoring header must appear verbatim even with no
        patterns so the planner always sees the advisory framing.
    Implementation: render_digest([]) and assert it equals the module's
        _DIGEST_HEADER constant exactly.
    Example: render_digest([]) == cross_design._DIGEST_HEADER.
    """
    assert cross_design.render_digest([]) == cross_design._DIGEST_HEADER


def test_digest_total_cap() -> None:
    """§X3: many patterns render within the cap, dropping oldest first.

    Design: total digest output is bounded before reaching the planner context,
        and excess patterns are dropped last-seen-first — the newest survive and
        the oldest are the first to fall out of the budget.
    Implementation: synthesize 20 large patterns whose last_seen ages with index
        (k0 newest, k19 oldest), render, and assert the byte cap holds, the
        newest survives, dropping occurred, and the oldest is absent.
    Example: "## Pattern: k0" in render_digest(...) and "## Pattern: k19" not in it.
    """
    now = datetime.now(UTC)
    patterns = [
        cross_design.CrossPattern(f"k{i}", 3, now - timedelta(days=i), "x" * 2000)
        for i in range(20)
    ]
    digest = cross_design.render_digest(patterns)
    assert len(digest.encode("utf-8")) <= cross_design._TOTAL_DIGEST_BYTES_CAP

    # §X3: newest survives; dropping happened; oldest dropped last-seen-first.
    surviving = [f"k{i}" for i in range(20) if f"## Pattern: k{i}\n" in digest]
    assert "k0" in surviving
    assert len(surviving) < 20
    assert "k19" not in surviving


def test_digest_per_pattern_cap() -> None:
    """§X3: a single pattern body is truncated to 1 KiB plus the marker.

    Design: per-pattern caps stop one verbose fault from dominating the planner
        digest budget.
    Implementation: build one CrossPattern with a >1 KiB rendered_summary, render
        with the default per-pattern cap, and assert the marker and byte cap.
    Example: rendered digest contains "…[truncated]" for the oversize pattern.
    """
    marker = "\n…[truncated]"
    pattern = cross_design.CrossPattern(
        "verbose_fault", 3, datetime.now(UTC), "x" * (cross_design._PER_PATTERN_BYTES_CAP * 2)
    )
    digest = cross_design.render_digest([pattern])
    assert marker in digest
    body = digest.split("ADVISORY ONLY", maxsplit=1)[1].split("\n\n", maxsplit=1)[1].rstrip("\n")
    assert len(body.encode("utf-8")) <= cross_design._PER_PATTERN_BYTES_CAP + len(
        marker.encode("utf-8")
    )


def _planner_system_prompt() -> str:
    """Load the packaged planner system prompt text.

    Design: prompt pins must inspect the shipped resource, not a duplicated test
        literal, so packaging drift is caught.
    Implementation: use importlib.resources.files on forge_mcp.prompts.
    Example: _planner_system_prompt() contains planner instructions.
    """
    return (files("forge_mcp.prompts") / "planner_system.md").read_text(encoding="utf-8")


def test_planner_system_includes_cross_design_directive() -> None:
    """§X: planner_system.md carries the cross-design anti-anchoring block.

    Design: the planner must treat cross-design patterns as weak priors, so the
        prompt must name cross_design_patterns.md and frame it advisorily.
    Implementation: read planner_system.md and assert the directive substring is
        present in the packaged prompt.
    Example: "cross_design_patterns.md" in the planner system prompt.
    """
    text = _planner_system_prompt()
    assert "cross_design_patterns.md" in text
    assert "statistical priors" in text


def test_priority_directive_present() -> None:
    """§X: planner prompt ranks prior_attempts.md above cross_design_patterns.md.

    Design: sibling-run history is a stronger signal than cross-design priors;
        the prompt must encode that precedence to prevent mis-anchoring.
    Implementation: read planner_system.md and assert both filenames appear and
        the stronger-signal wording is present.
    Example: "stronger signal" appears with prior_attempts.md named first.
    """
    text = _planner_system_prompt()
    assert "prior_attempts.md" in text and "cross_design_patterns.md" in text
    assert "stronger signal than `cross_design_patterns.md`" in text


def test_aggregator_failure_emits_empty_digest(monkeypatch) -> None:
    """§X / X-Inv 6: aggregator failure yields a header-only digest, no raise.

    Design: cross-design learning is advisory; an aggregator fault must never
        block planning, so failure degrades to an empty header-only digest.
    Implementation: monkeypatch find_cross_design_patterns to raise, call
        render_cross_design_digest, and assert the result is exactly the header.
    Example: digest == cross_design._DIGEST_HEADER on aggregator failure.
    """

    def boom(harness_dir: Path) -> list[cross_design.CrossPattern]:
        """Raise from the aggregator seam.

        Design: tests need a deterministic aggregator fault.
        Implementation: ignore the harness path and raise RuntimeError.
        Example: boom(Path('.harness')) raises RuntimeError.
        """
        _ = harness_dir
        raise RuntimeError("aggregator boom")

    monkeypatch.setattr(cross_design, "find_cross_design_patterns", boom)
    digest = cross_design.render_cross_design_digest(Path("/missing"), logging.getLogger(__name__))
    assert digest == cross_design._DIGEST_HEADER


def test_resume_re_aggregates(tmp_path: Path) -> None:
    """§X: a resume regenerates the cross-design digest from scratch.

    Design: a resumed run must see terminal runs that finished after the original
        cold start, so the digest is rebuilt rather than reused.
    Implementation: keep the test at aggregator level, render before and after
        adding enough terminal runs to cross the surfacing floor.
    Example: second render includes a pattern absent from the first.
    """
    now = datetime.now(UTC)
    first = cross_design.render_cross_design_digest(tmp_path, logging.getLogger(__name__))
    _run(tmp_path, "00000001", "fp1", "resume_fault", now)
    _run(tmp_path, "00000002", "fp2", "resume_fault", now)
    _run(tmp_path, "00000003", "fp3", "resume_fault", now)
    second = cross_design.render_cross_design_digest(tmp_path, logging.getLogger(__name__))
    assert first != second
    assert "resume_fault" in second


def test_cross_design_not_consumed_by_evaluator() -> None:
    """X-Inv 3: the evaluator does not consume cross_design_patterns.md.

    Design: cross-design priors are planner-only; feeding them to the gatekeeper
        as an explicit file would re-introduce the anchoring risk §X excludes.
    Implementation: inspect evaluator build_options(add_dirs=...) calls and
        assert no add_dirs entry names cross_design_patterns.md itself.
    Example: "cross_design_patterns.md" is absent from every evaluator add_dirs entry.
    """
    source = Path(evaluator_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    add_dirs_segments: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "add_dirs":
                segment = ast.get_source_segment(source, keyword.value)
                if segment is not None:
                    add_dirs_segments.append(segment)
    assert add_dirs_segments
    assert all("cross_design_patterns.md" not in segment for segment in add_dirs_segments)
