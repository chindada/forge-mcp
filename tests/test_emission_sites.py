"""§S emission-site tests (§S5.2 / §S-Decision 7b / §S-Inv 4)."""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path

from pydantic import AnyUrl

import forge_mcp.resources as resources
from forge_mcp.orchestrator.emitter import _Emitter
from forge_mcp.subscriptions import RegistryNotifier, SubscriptionRegistry

# §S5.2 "Deliberately not emitted" set. status.log is covered by task-level
# status fan-out (§S-Decision 7); design.md / design.fingerprint are write-once
# at setup with no later materialization event; run.log is never resource-exposed.
_NOT_EMITTED_SUBPATHS = {
    "inputs/design.md",
    "inputs/design.fingerprint",
    "status.log",
}


def _src_root() -> Path:
    """Locate the installed forge_mcp source package directory.

    Design: the drift guard must scan real source, not the test tree, so it
        derives the package directory from the imported module file.
    Implementation: resolve resources.__file__ and take its parent.
    Example: _src_root() / "orchestrator" / "phases.py" exists.
    """
    return Path(resources.__file__).resolve().parent


def _emission_corpus() -> str:
    """Concatenate every emitter call line across the source package.

    Design: emission sites funnel through _Emitter methods or the lifecycle
        _emit_if_present shim used by shared terminal finalization, so the scan
        includes both call shapes.
    Implementation: read all *.py under the package, keep only lines mentioning
        an emitter call or lifecycle shim call, and join them into one blob.
    Example: "summary.md" in _emission_corpus() once Task 2 lands.
    """
    blob: list[str] = []
    for path in _src_root().rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            else:
                continue
            if name not in {"emit_state", "emit_path", "emit_iteration", "_emit_if_present"}:
                continue
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                blob.append(segment)
    return "\n".join(blob)


def _search_token(pattern: resources._ArtifactPattern) -> str:
    """Compute the string an emission site must contain for one allowlist row.

    Design: exact-subpath rows are emitted via emit_path("<subpath>"); iteration
        regex rows are emitted via emit_iteration(n, "<leaf>"); state.json is
        emitted via emit_state which delegates to emit_path("state.json").
    Implementation: return the literal subpath for exact rows; for regex rows
        extract the trailing literal filename from the compiled pattern.
    Example: _search_token(<eval.json row>) == "eval.json".
    """
    if pattern.subpath is not None:
        return pattern.subpath
    assert pattern.regex is not None
    src = pattern.regex.pattern
    leaf = src.split("/")[-1].rstrip("$")
    return leaf.replace("\\.", ".")


def test_every_allowlisted_artifact_has_emission_site() -> None:
    """§S-Decision 7b: every allowlisted artifact has an emission site.

    Design: prevents silent contract drift — a new subscribable artifact with no
        notify call leaves subscribers permanently unsignaled (the exact defect
        that hid the missing summary.md / eval.json emissions).
    Implementation: for each allowlist row not in the deliberately-not-emitted
        set, assert its search token appears in the emitter-call corpus.
    Example: removing the summary.md emission makes this test fail.
    """
    corpus = _emission_corpus()
    missing: list[str] = []
    for pattern in resources._ALLOWED_ARTIFACTS:
        token = _search_token(pattern)
        subpath = pattern.subpath
        if subpath in _NOT_EMITTED_SUBPATHS:
            continue
        if token not in corpus:
            missing.append(token)
    assert missing == [], f"allowlisted artifacts with no emission site: {missing}"


class RecordingNotifier:
    """Notifier double that records emitted URIs (§S5.1 ResourceNotifier shape).

    Design: emission tests assert which forge:// URIs were notified, so the
        double just appends each URI; it satisfies the ResourceNotifier protocol.
    Implementation: store a list and append in notify.
    Example: RecordingNotifier().uris after a notify call has one entry.
    """

    def __init__(self) -> None:
        """Initialize the empty URI record.

        Design: each test gets a fresh recorder for isolation.
        Implementation: allocate an empty list attribute.
        Example: rec = RecordingNotifier(); assert rec.uris == [].
        """
        self.uris: list[str] = []

    async def notify(self, uri: str) -> None:
        """Record one emitted URI.

        Design: faithful to RegistryNotifier.notify's async signature so the
            emitter path is exercised unchanged.
        Implementation: append the URI string to the record list.
        Example: await rec.notify("forge://t/r/state.json").
        """
        self.uris.append(uri)


def _recording_emitter() -> tuple[_Emitter, RecordingNotifier]:
    """Build an _Emitter wired to a RecordingNotifier with a real token.

    Design: a non-None harness_token is required or the emitter suppresses all
        URIs; tests need real forge:// URIs to assert on.
    Implementation: construct RecordingNotifier and wrap it in _Emitter.
    Example: emitter, rec = _recording_emitter().
    """
    rec = RecordingNotifier()
    return _Emitter(rec, "tokenToken12", "abcd1234"), rec


async def test_emission_on_state_write() -> None:
    """§S5.2: emit_state notifies the state.json URI.

    Design: pins that the state.json materialization site emits exactly the
        forge://<t>/<r>/state.json URI.
    Implementation: call emit_state on a recording emitter and assert the URI.
    Example: rec.uris == ["forge://tokenToken12/abcd1234/state.json"].
    """
    emitter, rec = _recording_emitter()
    await emitter.emit_state()
    assert rec.uris == ["forge://tokenToken12/abcd1234/state.json"]


async def test_emission_failure_is_swallowed(caplog) -> None:
    """§S-Decision 6: send_resource_updated failure is logged and swallowed.

    Design: a dead client must not perturb durable-write control flow, so notify
        catches every send exception and only logs WARN.
    Implementation: subscribe a session whose send_resource_updated raises, then
        assert notify returns without raising and a WARN record is emitted.
    Example: notify completes; caplog has one WARNING from forge_mcp.subscriptions.
    """

    class BoomSession:
        """Session double that raises from every resource notification method.

        Design: fail-soft notification tests need a subscribed session that
            behaves like a disconnected host.
        Implementation: both async methods raise RuntimeError.
        Example: await BoomSession().send_resource_updated(uri) raises.
        """

        async def send_resource_updated(self, uri: AnyUrl) -> None:
            """Raise from one resource update notification.

            Design: exercises RegistryNotifier's exception guard.
            Implementation: ignore the URI and raise RuntimeError.
            Example: await session.send_resource_updated(uri) raises.
            """
            _ = uri
            raise RuntimeError("client gone")

        async def send_resource_list_changed(self) -> None:
            """Raise from one listChanged notification.

            Design: keeps the test double complete for the SessionHandle shape.
            Implementation: always raise RuntimeError.
            Example: await session.send_resource_list_changed() raises.
            """
            raise RuntimeError("client gone")

    registry = SubscriptionRegistry()
    session = BoomSession()
    uri = "forge://tokenToken12/abcd1234/state.json"
    registry.subscribe(session, uri)
    with caplog.at_level(logging.WARNING, logger="forge_mcp.subscriptions"):
        await RegistryNotifier(registry).notify(uri)
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_status_log_not_emitted() -> None:
    """§S-Decision 7: status.log is never emitted per-append.

    Design: status.log is covered by task-level status fan-out; per-append
        emission would be O(thousands) on a 10-hour run.
    Implementation: assert "status.log" does not appear in the emitter corpus.
    Example: no emit_path("status.log") call exists anywhere in src.
    """
    assert "status.log" not in _emission_corpus()


def test_emission_only_after_fsync() -> None:
    """§S-Inv 4 / §S-Decision 8: plan emission follows durable plan write.

    Design: a host must be able to fetch durable content the instant the
        notification arrives, so the representative plan/plan.md emission must
        appear after planner output has been durably materialized.
    Implementation: use source-order pinning because the plan write happens in
        the planner driver; assert write_plan precedes emit_path("plan/plan.md").
    Example: phases.py orders write_plan before emit_path("plan/plan.md").
    """
    path = _src_root() / "orchestrator" / "phases.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    write_line = next(index for index, line in enumerate(lines) if ".write_plan(" in line)
    emit_line = next(
        index for index, line in enumerate(lines) if 'emit_path("plan/plan.md")' in line
    )
    assert write_line < emit_line


def test_session_drop_clears_registry() -> None:
    """§S7: dropping a session removes all its subscriptions (no zombies).

    Design: no subscription may outlive its session; drop_session must purge the
        session key entirely.
    Implementation: subscribe two URIs for a session, drop it, and assert the
        session is absent from the all-sessions view.
    Example: after drop_session, _sessions_view() does not contain the session.
    """
    registry = SubscriptionRegistry()
    session = _ListChangedSession()
    registry.subscribe(session, "forge://tokenToken12/abcd1234/state.json")
    registry.subscribe(session, "forge://tokenToken12/abcd1234/iteration-1/eval.md")
    registry.drop_session(session)
    assert session not in tuple(registry._sessions_view())


class _ListChangedSession:
    """Recording session for §S6 listChanged lifecycle tests.

    Design: resources._broadcast_list_changed fans out to every connected
        session, so tests need a tiny session with a counter.
    Implementation: increment changed on send_resource_list_changed.
    Example: session.changed increases after a registry mutation broadcast.
    """

    def __init__(self) -> None:
        """Initialize recorded resource notifications.

        Design: each listChanged test needs isolated counters.
        Implementation: store an updated list and changed integer.
        Example: session.changed == 0 initially.
        """
        self.updated: list[str] = []
        self.changed = 0

    async def send_resource_updated(self, uri: AnyUrl) -> None:
        """Record a resource update URI.

        Design: the global registry expects sessions to satisfy SessionHandle.
        Implementation: append the URI string.
        Example: await session.send_resource_updated(uri).
        """
        self.updated.append(str(uri))

    async def send_resource_list_changed(self) -> None:
        """Record one listChanged broadcast.

        Design: §S6 broadcasts are counted without involving MCP transport.
        Implementation: increment the changed counter.
        Example: await session.send_resource_list_changed().
        """
        self.changed += 1


async def test_list_changed_on_register() -> None:
    """§S6 / S-Decision 10: registration broadcasts listChanged to all sessions.

    Design: list_changed is connection-level; every connected session must get a
        resources/list_changed notification when a run becomes discoverable,
        regardless of whether it has subscribed to any URI.
    Implementation: note a session as connected (no subscribe), register a unique
        active run, yield to the scheduled task, then deregister and assert the
        register broadcast reached the connected session.
    Example: session.changed is at least one after register_active_run.
    """
    session = _ListChangedSession()
    from forge_mcp import subscriptions

    subscriptions._REGISTRY.note_connected(session)
    token = "listChangeAA"
    run_id = "abc12345"
    try:
        resources.register_active_run(
            resources._ResourceScope(
                run_id=run_id, harness_dir=Path("/tmp/harness"), harness_token=token
            )
        )
        await asyncio.sleep(0)
        assert session.changed >= 1
    finally:
        resources.deregister_active_run(token, run_id)
        subscriptions._REGISTRY.drop_session(session)


async def test_list_changed_reaches_connected_unsubscribed_session() -> None:
    """§S6 / S-Decision 10: a connected-but-unsubscribed session gets listChanged.

    Design: the divergence to guard is a session that connected but never issued
        resources/subscribe; under connection-level semantics it must still
        receive list_changed on register/deregister.
    Implementation: note one session connected without ever subscribing any URI,
        register and deregister a unique run, and assert the change counter rose.
    Example: an observer-only host sees discoverability changes.
    """
    session = _ListChangedSession()
    from forge_mcp import subscriptions

    subscriptions._REGISTRY.note_connected(session)
    token = "listChangeCC"
    run_id = "ccc12345"
    try:
        # Sanity: this session is in no URI subscription set.
        assert (
            tuple(
                subscriptions._REGISTRY.subscribed_sessions(f"forge://{token}/{run_id}/state.json")
            )
            == ()
        )
        resources.register_active_run(
            resources._ResourceScope(
                run_id=run_id, harness_dir=Path("/tmp/harness"), harness_token=token
            )
        )
        await asyncio.sleep(0)
        assert session.changed >= 1
    finally:
        resources.deregister_active_run(token, run_id)
        subscriptions._REGISTRY.drop_session(session)


async def test_list_changed_not_on_completed_until_prune() -> None:
    """§S6 / §S-Decision 11: completed runs notify only when pruned/deregistered.

    Design: terminal completion that remains resource-visible must not emit a
        listChanged; removal from the active/discoverable set does emit one.
    Implementation: register a run, drain that broadcast, do no completion
        mutation, then deregister and assert only deregistration adds a count.
    Example: changed count is stable until deregister_active_run.
    """
    session = _ListChangedSession()
    from forge_mcp import subscriptions

    subscriptions._REGISTRY.note_connected(session)
    token = "listChangeBB"
    run_id = "def67890"
    try:
        resources.register_active_run(
            resources._ResourceScope(
                run_id=run_id, harness_dir=Path("/tmp/harness"), harness_token=token
            )
        )
        await asyncio.sleep(0)
        after_register = session.changed
        await asyncio.sleep(0)
        assert session.changed == after_register
        resources.deregister_active_run(token, run_id)
        await asyncio.sleep(0)
        assert session.changed == after_register + 1
    finally:
        resources.deregister_active_run(token, run_id)
        subscriptions._REGISTRY.drop_session(session)
