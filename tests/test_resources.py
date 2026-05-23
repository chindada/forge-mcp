"""§R9.1 / §R9.2 unit tests for the resources.py leaf module."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from forge_mcp.resources import (
    _ACTIVE_RUNS,
    _ALLOWED_ARTIFACTS,
    _ResourceScope,
    compute_harness_token,
    decode_uri,
    deregister_active_run,
    encode_uri,
    expand_scope_to_resources,
    list_active_runs,
    match_artifact,
    register_active_run,
    resolve_harness_dir,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with an empty _ACTIVE_RUNS dict.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    _ACTIVE_RUNS.clear()
    yield
    _ACTIVE_RUNS.clear()


class TestUri:
    """Pin strict forge:// URI encoding and decoding."""

    def test_encode_decode_roundtrip_plan(self):
        """Verify plan URI roundtrips.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        uri = encode_uri("aBcDeFgHiJkL", "12345678", "plan/plan.md")
        assert uri == "forge://aBcDeFgHiJkL/12345678/plan/plan.md"
        assert decode_uri(uri) == ("aBcDeFgHiJkL", "12345678", "plan/plan.md")

    def test_encode_decode_roundtrip_iteration(self):
        """Verify iteration URI roundtrips.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        uri = encode_uri("aBcDeFgHiJkL", "deadbeef", "iteration-3/eval.md")
        assert decode_uri(uri) == ("aBcDeFgHiJkL", "deadbeef", "iteration-3/eval.md")

    def test_decode_rejects_non_forge_scheme(self):
        """Reject a non-forge URI scheme.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        with pytest.raises(ValueError):
            decode_uri("https://example/plan/plan.md")

    def test_decode_rejects_query(self):
        """Reject query parts.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        with pytest.raises(ValueError):
            decode_uri("forge://aBcDeFgHiJkL/12345678/plan/plan.md?x=1")

    def test_decode_rejects_fragment(self):
        """Reject fragment parts.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        with pytest.raises(ValueError):
            decode_uri("forge://aBcDeFgHiJkL/12345678/plan/plan.md#frag")

    def test_decode_rejects_short_token(self):
        """Reject bad token shape.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        with pytest.raises(ValueError):
            decode_uri("forge://short/12345678/plan/plan.md")

    def test_decode_rejects_bad_run_id_shape(self):
        """Reject bad run id shape.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        with pytest.raises(ValueError):
            decode_uri("forge://aBcDeFgHiJkL/NOTHEX12/plan/plan.md")

    def test_decode_lstrip_not_naive_split(self):
        """Verify lstrip handles the leading path slash.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        token, rid, sub = decode_uri("forge://aBcDeFgHiJkL/12345678/plan/plan.md")
        assert token == "aBcDeFgHiJkL"
        assert rid == "12345678"
        assert sub == "plan/plan.md"


class TestHarnessToken:
    """Pin harness token stability and shape."""

    def test_token_stability(self, tmp_path):
        """Same path hashes to same token.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        t1 = compute_harness_token(tmp_path / ".harness")
        t2 = compute_harness_token(tmp_path / ".harness")
        assert t1 == t2

    def test_token_shape(self, tmp_path):
        """Token has twelve base64url characters.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        token = compute_harness_token(tmp_path / ".harness")
        assert re.match(r"^[A-Za-z0-9_-]{12}$", token), token

    def test_token_distinct_dirs(self, tmp_path):
        """Distinct paths hash to distinct tokens.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        t1 = compute_harness_token(tmp_path / "a" / ".harness")
        t2 = compute_harness_token(tmp_path / "b" / ".harness")
        assert t1 != t2

    def test_token_symlink_aliases_distinct(self, tmp_path):
        """Abspath identity keeps symlink aliases distinct.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "alias"
        link.symlink_to(real)
        t_real = compute_harness_token(real)
        t_alias = compute_harness_token(link)
        assert t_real != t_alias


class TestMatchArtifact:
    """Pin the allowlist and permanent exclusions."""

    @pytest.mark.parametrize(
        ("subpath", "mime"),
        [
            ("inputs/design.md", "text/markdown"),
            ("inputs/git-state.txt", "text/plain"),
            ("inputs/git-uncommitted.txt", "text/plain"),
            ("plan/plan.md", "text/markdown"),
            ("plan/sessions.json", "application/json"),
            ("iteration-1/contract.md", "text/markdown"),
            ("iteration-1/summary.md", "text/markdown"),
            ("iteration-2/eval.json", "application/json"),
            ("iteration-2/eval.md", "text/markdown"),
            ("iteration-3/triage.json", "application/json"),
            ("iteration-3/sessions.json", "application/json"),
            ("iteration-4/git-violation.txt", "text/plain"),
            ("iteration-4/verify.txt", "text/plain"),
            ("state.json", "application/json"),
            ("status.log", "application/x-ndjson"),
            ("unresolved-gaps-overflow.md", "text/markdown"),
            ("design-flaw-gaps-overflow.md", "text/markdown"),
        ],
    )
    def test_allowed_match(self, subpath, mime):
        """Allowed artifacts return the expected MIME.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        pat = match_artifact(subpath)
        assert pat is not None, subpath
        assert pat.mime == mime

    def test_iteration_zero_rejected(self):
        """Reject iteration zero.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        assert match_artifact("iteration-0/eval.md") is None

    def test_iteration_leading_zero_rejected(self):
        """Reject leading-zero iterations.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        assert match_artifact("iteration-007/eval.md") is None

    def test_unknown_subpath_rejected(self):
        """Reject unknown subpaths.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        assert match_artifact("foo/bar.md") is None

    def test_traversal_rejected(self):
        """Reject traversal and absolute subpaths.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        assert match_artifact("../foo") is None
        assert match_artifact("iteration-1/../../etc/passwd") is None
        assert match_artifact("/abs/path") is None

    def test_run_log_and_lock_never_allowlisted(self):
        """Pin run.log and run.lock exclusion.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        subpaths = {p.subpath for p in _ALLOWED_ARTIFACTS}
        assert "run.log" not in subpaths
        assert "run.lock" not in subpaths
        forbidden = [
            "run.log",
            "run.lock",
            "iteration-1/run.log",
            "iteration-99/run.log",
            "plan/run.log",
            "inputs/run.log",
        ]
        for p in forbidden:
            assert match_artifact(p) is None, f"{p} unexpectedly allowlisted"


class TestRegistry:
    """Pin active-run registry behavior."""

    def test_register_deregister_leaves_empty(self, tmp_path):
        """Register then deregister empties the registry.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        scope = _ResourceScope(
            run_id="12345678", harness_dir=tmp_path / ".harness", harness_token="aBcDeFgHiJkL"
        )
        register_active_run(scope)
        assert list_active_runs() == [scope]
        deregister_active_run("aBcDeFgHiJkL", "12345678")
        assert list_active_runs() == []

    def test_deregister_idempotent(self):
        """Deregistering a missing key is a no-op.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        deregister_active_run("zzzzzzzzzzzz", "00000000")
        deregister_active_run("zzzzzzzzzzzz", "00000000")

    def test_register_collision_asserts(self, tmp_path):
        """Double-registering a key asserts.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        scope = _ResourceScope("12345678", tmp_path / ".harness", "aBcDeFgHiJkL")
        register_active_run(scope)
        with pytest.raises(AssertionError):
            register_active_run(scope)

    def test_distinct_tokens_same_run_id_independent(self, tmp_path):
        """Same run id under distinct tokens is independent.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        s1 = _ResourceScope("12345678", tmp_path / "a" / ".harness", "tokenAtokenA")
        s2 = _ResourceScope("12345678", tmp_path / "b" / ".harness", "tokenBtokenB")
        register_active_run(s1)
        register_active_run(s2)
        assert len(list_active_runs()) == 2

    def test_list_active_runs_is_snapshot(self, tmp_path):
        """Snapshot list survives later registry mutation.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        scope = _ResourceScope("12345678", tmp_path / ".harness", "tokenXtokenX")
        register_active_run(scope)
        snap = list_active_runs()
        deregister_active_run("tokenXtokenX", "12345678")
        assert snap == [scope]


class TestResolveHarnessDir:
    """Pin token resolution precedence."""

    def test_active_scope_wins(self, tmp_path):
        """Active scope wins over configured roots.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        scope = _ResourceScope("12345678", tmp_path / ".harness", "tokenAtokenA")
        register_active_run(scope)
        result = resolve_harness_dir("tokenAtokenA", {"tokenAtokenA": tmp_path / "other"})
        assert result == tmp_path / ".harness"

    def test_falls_back_to_config(self, tmp_path):
        """Configured root resolves when active scope is absent.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        result = resolve_harness_dir("tokenAtokenA", {"tokenAtokenA": tmp_path / "root"})
        assert result == tmp_path / "root"

    def test_missing_returns_none(self):
        """Unknown token returns None.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        assert resolve_harness_dir("zzzzzzzzzzzz", {}) is None


class TestExpandScope:
    """Pin scope expansion walks and error behavior."""

    def test_emits_one_per_existing_allowlisted(self, tmp_path):
        """Emit one row per existing allowlisted artifact.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        harness = tmp_path / ".harness"
        run_dir = harness / "12345678"
        (run_dir / "inputs").mkdir(parents=True)
        (run_dir / "plan").mkdir()
        (run_dir / "iteration-1").mkdir()
        (run_dir / "iteration-2").mkdir()
        (run_dir / "state.json").write_text("{}")
        (run_dir / "status.log").write_text("")
        (run_dir / "inputs" / "design.md").write_text("# spec")
        (run_dir / "plan" / "plan.md").write_text("# plan")
        (run_dir / "iteration-1" / "contract.md").write_text("# c")
        (run_dir / "iteration-2" / "eval.md").write_text("# e")
        scope = _ResourceScope("12345678", harness, "tokenAtokenA")
        rows = expand_scope_to_resources(scope)
        subpaths = sorted(sp for _, _, _, sp in rows)
        assert subpaths == [
            "inputs/design.md",
            "iteration-1/contract.md",
            "iteration-2/eval.md",
            "plan/plan.md",
            "state.json",
            "status.log",
        ]
        uri, name, mime, subpath = next(r for r in rows if r[3] == "plan/plan.md")
        assert uri == "forge://tokenAtokenA/12345678/plan/plan.md"
        assert name == "plan.md"
        assert mime == "text/markdown"
        assert subpath == "plan/plan.md"

    def test_non_iteration_dirs_skipped(self, tmp_path):
        """Skip non-strict iteration directories.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        harness = tmp_path / ".harness"
        run_dir = harness / "12345678"
        run_dir.mkdir(parents=True)
        (run_dir / "iteration-0").mkdir()
        (run_dir / "iteration-0" / "eval.md").write_text("# decoy")
        (run_dir / "scratch").mkdir()
        (run_dir / "scratch" / "eval.md").write_text("# decoy")
        rows = expand_scope_to_resources(_ResourceScope("12345678", harness, "tokenAtokenA"))
        assert rows == []

    def test_runs_propagate_oserror(self, tmp_path, monkeypatch):
        """Propagate OSError raised by iteration scanning.

        Design: §R tests pin the resource-surface behavior required by the plan.
        Implementation: The test constructs focused fixtures and asserts direct outputs.
        Example: pytest runs this test in the non-slow suite.
        """
        scope = _ResourceScope("12345678", tmp_path / "missing", "tokenAtokenA")
        assert expand_scope_to_resources(scope) == []
        harness = tmp_path / ".harness"
        run_dir = harness / "12345678"
        run_dir.mkdir(parents=True)

        def boom(self):
            """Raise for the synthetic run-root scan only.

            Design: §R3.1 helper errors must propagate for handler-level skip logic.
            Implementation: replace Path.iterdir with a narrow test double.
            Example: expand_scope_to_resources(scope) raises OSError.
            """
            if self == run_dir:
                raise OSError("synthetic")
            return original_iterdir(self)

        original_iterdir = Path.iterdir
        monkeypatch.setattr(Path, "iterdir", boom)
        with pytest.raises(OSError):
            expand_scope_to_resources(_ResourceScope("12345678", harness, "tokenAtokenA"))


def test_resources_module_imports_stdlib_only():
    """Pin resources.py to stdlib-only imports.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    src = (Path(__file__).resolve().parents[1] / "src" / "forge_mcp" / "resources.py").read_text()
    tree = ast.parse(src)
    allowed_top_levels = {
        "hashlib",
        "base64",
        "os",
        "pathlib",
        "re",
        "urllib",
        "dataclasses",
        "typing",
        "__future__",
        "asyncio",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                assert top in allowed_top_levels, f"forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module is None:
                names = {alias.name for alias in node.names}
                assert names == {"subscriptions"}
                continue
            if node.level == 1 and node.module == "ids":
                # finding 7 — ids is a stdlib-only leaf; the §R9.1 guarantee holds.
                continue
            top = (node.module or "").split(".", 1)[0]
            assert top in allowed_top_levels, f"forbidden from-import: {node.module}"


async def test_fire_list_changed_anchors_then_clears(monkeypatch) -> None:
    """§S6 / finding 5 — the broadcast task is strongly referenced then cleared.

    Design: a bare create_task may be GC'd before running; the documented idiom
        holds a strong ref in a module set and discards it on done.
    Implementation: stub the broadcast coro, fire it, assert the set is
        non-empty while pending, then await and assert the done-callback empties
        the set.
    Example: pytest asserts _BROADCAST_TASKS is empty after the task finishes.
    """
    import asyncio

    import forge_mcp.resources as resources_mod

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_broadcast() -> None:
        """Wait until released so the anchor can be observed.

        Design: the test needs a pending task between schedule and completion.
        Implementation: signal started and wait on an event controlled by test.
        Example: await fake_broadcast() blocks until release.set().
        """
        started.set()
        await release.wait()

    monkeypatch.setattr(resources_mod, "_broadcast_list_changed", fake_broadcast)
    resources_mod._BROADCAST_TASKS.clear()
    resources_mod._fire_list_changed()
    await started.wait()
    assert len(resources_mod._BROADCAST_TASKS) == 1
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(resources_mod._BROADCAST_TASKS) == 0


def test_allowlist_includes_lineage_artifacts() -> None:
    """§L9.2 — allowlist entries cover lineage artifacts.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.resources import match_artifact

    fingerprint = match_artifact("inputs/design.fingerprint")
    prior_attempts = match_artifact("inputs/prior_attempts.md")
    overflow = match_artifact("inputs/prior_attempts-overflow.md")
    design_flaws = match_artifact("design_flaws.json")
    assert fingerprint is not None and fingerprint.mime == "text/plain"
    assert prior_attempts is not None and prior_attempts.mime == "text/markdown"
    assert overflow is not None
    assert design_flaws is not None and design_flaws.mime == "application/json"


def test_allowlist_still_excludes_run_log_lineage() -> None:
    """§R9.1 — run.log MUST remain absent.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.resources import match_artifact

    assert match_artifact("run.log") is None


def test_expand_scope_includes_lineage_artifacts(tmp_path: Path) -> None:
    """§L9.2 — expand_scope_to_resources lists lineage artifacts when present.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.resources import _ResourceScope, expand_scope_to_resources

    harness = tmp_path / "harness"
    run_id = "abcd1234"
    run_root = harness / run_id
    (run_root / "inputs").mkdir(parents=True)
    (run_root / "inputs" / "design.md").write_text("d")
    (run_root / "inputs" / "design.fingerprint").write_text("a" * 64 + "\n")
    (run_root / "inputs" / "prior_attempts.md").write_text("# p\n")
    (run_root / "inputs" / "prior_attempts-overflow.md").write_text("tail\n")
    (run_root / "design_flaws.json").write_text('{"gaps":[]}')
    (run_root / "state.json").write_text("{}")
    (run_root / "status.log").write_text("")
    scope = _ResourceScope(run_id=run_id, harness_dir=harness, harness_token="aBcDeFgHiJkL")
    subpaths = {row[3] for row in expand_scope_to_resources(scope)}
    assert "inputs/design.fingerprint" in subpaths
    assert "inputs/prior_attempts.md" in subpaths
    assert "inputs/prior_attempts-overflow.md" in subpaths
    assert "design_flaws.json" in subpaths
