from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.verifier import run_verification, scope_verification_command


def test_none_command_passes(tmp_path: Path):
    """Design: §6.5 verify_passed is True when no command is declared.
    Implementation: command=None.
    Example: passed True, timed_out False.
    """
    o = run_verification(None, tmp_path)
    assert o.passed and not o.timed_out and o.exit_code is None


def test_exit_zero_passes(tmp_path: Path):
    """Design: §6.5 passed = (exit_code == 0).
    Implementation: 'true'.
    Example: passed True.
    """
    assert run_verification("true", tmp_path).passed


def test_nonzero_exit_is_not_passed_no_exception(tmp_path: Path):
    """Design: §6.5 check=False -> a failing command is passed=False, not a raise.
    Implementation: 'false'.
    Example: passed False, exit_code != 0.
    """
    o = run_verification("false", tmp_path)
    assert not o.passed and o.exit_code != 0 and not o.timed_out


def test_timeout_sets_timed_out(tmp_path: Path):
    """Design: §6.5 timeout -> passed=False, timed_out=True (TimeoutExpired caught).
    Implementation: 'sleep 5' with timeout 0.2.
    Example: timed_out True.
    """
    o = run_verification("sleep 5", tmp_path, timeout=0.2)
    assert not o.passed and o.timed_out


def test_output_is_bounded(tmp_path: Path):
    """Design: §6.5 output is captured and bounded.
    Implementation: large stdout, small output_limit.
    Example: len(output) <= limit.
    """
    o = run_verification("for i in $(seq 1 1000); do echo line$i; done", tmp_path, output_limit=200)
    assert len(o.output) <= 200


def test_scope_none_is_passthrough():
    """Design: §1 a None command (no declared gate) has nothing to scope.
    Implementation: scope_verification_command(None) returns (None, []).
    Example: a plan with verification_command omitted stays gate-free.
    """
    assert scope_verification_command(None) == (None, [])


def test_scope_plain_command_byte_identical():
    """Design: §1 a command without a clean-tree gate is left untouched.
    Implementation: split/scan finds nothing to drop, returns the input unchanged.
    Example: 'make setup && make  test' round-trips byte-identical with no drops.
    """
    cmd = "make setup && make  test"
    assert scope_verification_command(cmd) == (cmd, [])


def test_scope_drops_trailing_porcelain_conjunct():
    """Design: §1 the canonical `&& test -z "$(git status --porcelain)"` gate can
        never pass in the never-committing loop, so it is dropped.
    Implementation: the trailing porcelain conjunct is removed; generate/fmt/lint/
        test/build conjuncts are kept.
    Example: '... build && test -z "$(git status --porcelain)"' -> '... build'.
    """
    cmd = (
        "make setup-tools && make setup && make generate fmt lint test build "
        '&& test -z "$(git status --porcelain)"'
    )
    scoped, dropped = scope_verification_command(cmd)
    assert scoped == "make setup-tools && make setup && make generate fmt lint test build"
    assert dropped == ['test -z "$(git status --porcelain)"']


@pytest.mark.parametrize(
    "gate",
    ["git diff --exit-code", "git diff --quiet", "git diff --quiet > /dev/null"],
)
def test_scope_drops_git_diff_tree_state_gate(gate: str):
    """Design: §1 a `git diff` tree-state gate (--exit-code/--quiet, incl. a trailing
        redirect) can never pass in the never-committing loop, so it is dropped.
    Implementation: the diff conjunct fullmatches and is removed; `make gen` is kept.
    Example: 'make gen && git diff --quiet' -> 'make gen'.
    """
    scoped, dropped = scope_verification_command(f"make gen && {gate}")
    assert scoped == "make gen"
    assert dropped == [gate]


@pytest.mark.parametrize(
    "gate",
    ['[ -z "$(git status --porcelain)" ]', '[[ -z "$(git status --porcelain)" ]]'],
)
def test_scope_sole_gate_collapses_to_none(gate: str):
    """Design: §1 a command that is ONLY a clean-tree gate (`[ ]` or `[[ ]]` form) has
        no correctness signal, so the scoped command collapses to None.
    Implementation: the sole conjunct is dropped, leaving no kept segments.
    Example: '[ -z "$(git status --porcelain)" ]' -> (None, [that gate]).
    """
    scoped, dropped = scope_verification_command(gate)
    assert scoped is None
    assert dropped == [gate]


def test_scope_keeps_plain_git_status_no_false_positive():
    """Design: §1 a bare `git status` (no emptiness test) is not a completion gate.
    Implementation: the matcher requires an emptiness/diff assertion, so a plain
        `git status` segment is left in place — no false positive.
    Example: 'git status && make test' round-trips unchanged with no drops.
    """
    cmd = "git status && make test"
    assert scope_verification_command(cmd) == (cmd, [])


@pytest.mark.parametrize("sep", [";", "||", "&"])
def test_scope_non_amp_separator_is_safe_noop(sep: str):
    """Design: §1 a gate glued on with a non-`&&` separator (`;`/`||`/`&`) must not
        collapse the whole command — that would null the gate and pass verification
        vacuously (worse than the original bug). The gate must BE the whole conjunct.
    Implementation: the segment is not a standalone gate, so nothing is dropped and
        the command is returned unchanged (a safe no-op, not None) — in BOTH the
        gate-after-work and gate-before-work directions.
    Example: 'make build ; test -z "$(git status --porcelain)"' round-trips unchanged.
    """
    cmd = f'make build {sep} test -z "$(git status --porcelain)"'
    assert scope_verification_command(cmd) == (cmd, [])
    # gate-BEFORE real work must ALSO be a no-op: the gate is not the whole conjunct,
    # so dropping it would silently remove the work after the separator.
    gate_first = f"git diff --quiet {sep} make build"
    assert scope_verification_command(gate_first) == (gate_first, [])


def test_scope_subshell_amp_not_corrupted():
    """Design: §1 a gate buried inside a quoted subshell that itself contains `&&`
        must not be half-dropped into a syntactically broken survivor.
    Implementation: the mis-split fragment is not a standalone gate, so nothing is
        dropped and the command is returned unchanged (fail-safe).
    Example: 'make build && bash -c "git diff --quiet && true"' round-trips unchanged.
    """
    cmd = 'make build && bash -c "git diff --quiet && true"'
    assert scope_verification_command(cmd) == (cmd, [])


def test_scope_multistatement_segment_keeps_real_conjunct():
    """Design: §1 a conjunct that chains statements with `;`/`|` and merely mentions a
        matching flag must not be dropped — that would silently remove real work.
    Implementation: the gate pattern may not span `;`/`|`, so the segment is not a
        standalone gate and is kept; the command is returned unchanged.
    Example: 'git diff --stat | tee log; pytest --quiet && make build' is unchanged.
    """
    cmd = "git diff --stat | tee log; pytest --quiet && make build"
    assert scope_verification_command(cmd) == (cmd, [])


def test_scope_drops_middle_gate_bridges_survivors():
    """Design: §1 a gate in the MIDDLE is dropped while the non-adjacent survivors
        on either side are bridged — the real decompose path, not a trailing trim.
    Implementation: the middle conjunct is dropped; kept[0] and kept[2] rejoin.
    Example: 'make a && git diff --quiet && make b' -> 'make a && make b'.
    """
    scoped, dropped = scope_verification_command("make a && git diff --quiet && make b")
    assert scoped == "make a && make b"
    assert dropped == ["git diff --quiet"]


def test_scope_drops_git_status_short_flag_forms():
    """Design: §1 `git status -s` and `--short` are clean-tree emptiness gates too.
    Implementation: both short-status flag forms match and are dropped.
    Example: '[ -z "$(git status -s)" ] && make test' -> 'make test'.
    """
    scoped, dropped = scope_verification_command('[ -z "$(git status -s)" ] && make test')
    assert scoped == "make test"
    assert dropped == ['[ -z "$(git status -s)" ]']
    scoped2, dropped2 = scope_verification_command('make test && [ -z "$(git status --short)" ]')
    assert scoped2 == "make test"
    assert dropped2 == ['[ -z "$(git status --short)" ]']


def test_scope_keeps_porcelain_diagnostic_without_emptiness_test():
    """Design: §1 `git status --porcelain` used as a diagnostic (no `-z` emptiness
        test) is real work, not a completion gate — keep it.
    Implementation: the matcher requires the test/`[`-z emptiness shape, absent here,
        so nothing is dropped.
    Example: 'git status --porcelain > out.txt && make test' is unchanged.
    """
    cmd = "git status --porcelain > out.txt && make test"
    assert scope_verification_command(cmd) == (cmd, [])


def test_scope_drops_gate_without_spaces_around_amp():
    """Design: §1 `&&` need not be surrounded by spaces for the gate to be dropped.
    Implementation: split on the bare `&&` token, the trailing gate is dropped.
    Example: 'make build&&test -z "$(git status --porcelain)"' -> 'make build'.
    """
    scoped, dropped = scope_verification_command('make build&&test -z "$(git status --porcelain)"')
    assert scoped == "make build"
    assert dropped == ['test -z "$(git status --porcelain)"']
