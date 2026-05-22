"""§R9.3 / §R9.4 — handler-level tests for list_resources / read_resource."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from mcp.types import ListResourcesRequest, ListResourcesResult, PaginatedRequestParams
from pydantic import AnyUrl

from forge_mcp.resources import _ACTIVE_RUNS, _ResourceScope, register_active_run


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """Each test starts without active or configured resource roots.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    _ACTIVE_RUNS.clear()
    monkeypatch.delenv("FORGE_HARNESS_ROOTS", raising=False)
    yield
    _ACTIVE_RUNS.clear()


def _populate_run_dir(harness: Path, run_id: str) -> Path:
    """Create a populated run dir with one artifact from each walk location.

    Design: §R9.3 handler tests need a compact fixture covering top-level,
        inputs, plan, and iteration walks.
    Implementation: write minimal text artifacts under a synthetic harness.
    Example: run_dir = _populate_run_dir(tmp_path / '.harness', '12345678').
    """
    run_dir = harness / run_id
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "plan").mkdir()
    (run_dir / "iteration-1").mkdir()
    (run_dir / "state.json").write_text("{}")
    (run_dir / "status.log").write_text("")
    (run_dir / "inputs" / "design.md").write_text("# spec")
    (run_dir / "plan" / "plan.md").write_text("# plan")
    (run_dir / "iteration-1" / "eval.md").write_text("# e")
    return run_dir


async def _call_list(cursor: str | None = None) -> ListResourcesResult:
    """Call the registered list_resources handler directly.

    Design: §R9.3 tests the in-memory low-level Server handler function.
    Implementation: construct ListResourcesRequest with optional cursor.
    Example: result = await _call_list(cursor='50').
    """
    from forge_mcp.server import list_resources_handler

    params = PaginatedRequestParams(cursor=cursor) if cursor is not None else None
    req = ListResourcesRequest(method="resources/list", params=params)
    handler = cast(
        Callable[[ListResourcesRequest], Awaitable[ListResourcesResult]], list_resources_handler
    )
    return await handler(req)


async def _call_read(uri: str) -> list[ReadResourceContents]:
    """Call the registered read_resource handler directly.

    Design: §R9.3 validates handler rejection and content behavior without a
        subprocess transport.
    Implementation: coerce string to AnyUrl before calling the decorated func.
    Example: contents = await _call_read('forge://token/run/state.json').
    """
    from forge_mcp.server import read_resource_handler

    result = await read_resource_handler(AnyUrl(uri))
    return cast(list[ReadResourceContents], result)


async def test_list_resources_empty(tmp_path, monkeypatch):
    """§R9.3 no active runs and no configured roots returns empty.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    result = await _call_list()
    assert list(result.resources) == []
    assert result.nextCursor is None


async def test_list_resources_one_active(tmp_path, monkeypatch):
    """§R9.3 active run emits artifacts from all walk locations.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    _populate_run_dir(harness, "12345678")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    result = await _call_list()
    uris = sorted(str(r.uri) for r in result.resources)
    assert any(u.endswith("/state.json") for u in uris)
    assert any(u.endswith("/inputs/design.md") for u in uris)
    assert any(u.endswith("/plan/plan.md") for u in uris)
    assert any(u.endswith("/iteration-1/eval.md") for u in uris)


async def test_list_resources_dedup_active_over_config(tmp_path, monkeypatch):
    """§R9.3 active run overrides FORGE_HARNESS_ROOTS on collision.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    _populate_run_dir(harness, "12345678")
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", str(harness))
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    result = await _call_list()
    plan_uris = [str(r.uri) for r in result.resources if str(r.uri).endswith("/plan/plan.md")]
    assert len(plan_uris) == 1


async def test_list_resources_pagination(tmp_path, monkeypatch):
    """§R9.3 large run set paginates with numeric cursors.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    harness = tmp_path / ".harness"
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", str(harness))
    harness.mkdir()
    for i in range(60):
        rid = f"{i:08x}"
        rd = harness / rid
        rd.mkdir()
        (rd / "state.json").write_text("{}")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    p1 = await _call_list()
    assert len(p1.resources) == 50
    assert p1.nextCursor == "50"
    p2 = await _call_list(cursor=p1.nextCursor)
    assert len(p2.resources) == 10
    assert p2.nextCursor is None


async def test_list_resources_stale_cursor_non_numeric(tmp_path, monkeypatch):
    """§R9.3 non-numeric cursor returns first page.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    _populate_run_dir(harness, "12345678")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    result = await _call_list(cursor="abc")
    assert len(result.resources) >= 1


async def test_list_resources_stale_cursor_past_end(tmp_path, monkeypatch):
    """§R9.3 numeric cursor past end returns an empty final page.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    _populate_run_dir(harness, "12345678")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    result = await _call_list(cursor="99999")
    assert list(result.resources) == []
    assert result.nextCursor is None


async def test_list_resources_poison_per_root(tmp_path, monkeypatch):
    """§R9.3 vanished root does not poison another configured root.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    r1.mkdir()
    r2.mkdir()
    rd = r2 / "12345678"
    rd.mkdir()
    (rd / "state.json").write_text("{}")
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", f"{r1},{r2}")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    r1.rmdir()
    result = await _call_list()
    uris = [str(r.uri) for r in result.resources]
    assert any("12345678/state.json" in u for u in uris)


async def test_read_resource_happy(tmp_path, monkeypatch):
    """§R9.3 known active allowlisted artifact returns content.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    _populate_run_dir(harness, "12345678")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    contents = await _call_read(f"forge://{token}/12345678/plan/plan.md")
    assert len(contents) == 1
    assert contents[0].content == "# plan"
    assert contents[0].mime_type == "text/markdown"


async def test_read_resource_run_log_rejected(tmp_path, monkeypatch):
    """§R-Inv 3 run.log is structurally unreachable.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    rd = _populate_run_dir(harness, "12345678")
    (rd / "run.log").write_text("secret")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    with pytest.raises(McpError) as exc_info:
        await _call_read(f"forge://{token}/12345678/run.log")
    assert exc_info.value.error.code == -32002


async def test_read_resource_wrong_token(tmp_path, monkeypatch):
    """§R9.3 unknown token maps to -32002.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    with pytest.raises(McpError) as exc_info:
        await _call_read("forge://wrongtokennX0/12345678/plan/plan.md")
    assert exc_info.value.error.code == -32002


async def test_read_resource_traversal_rejected(tmp_path, monkeypatch):
    """§R9.3 traversal-shaped URI maps to -32002.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    with pytest.raises(McpError) as exc_info:
        await _call_read("forge://aBcDeFgHiJkL/12345678/iteration-1/../../etc/passwd")
    assert exc_info.value.error.code == -32002


@pytest.mark.skipif(os.geteuid() == 0, reason="symlink semantics root-dependent")
async def test_read_resource_realpath_escape_rejected(tmp_path, monkeypatch):
    """§R-Inv 4 step (ii) rejects symlinked parent escape.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    rd = _populate_run_dir(harness, "12345678")
    outside = tmp_path / "attacker-dir"
    outside.mkdir()
    (outside / "eval.md").write_text("SECRET")
    iter_dir = rd / "iteration-1"
    for child in iter_dir.iterdir():
        child.unlink()
    iter_dir.rmdir()
    iter_dir.symlink_to(outside)
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    with pytest.raises(McpError) as exc_info:
        await _call_read(f"forge://{token}/12345678/iteration-1/eval.md")
    assert exc_info.value.error.code == -32002


@pytest.mark.skipif(os.geteuid() == 0, reason="symlink semantics root-dependent")
async def test_read_resource_leaf_symlink_rejected(tmp_path, monkeypatch):
    """§R-Inv 4 step (iii) rejects leaf symlinks.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    rd = _populate_run_dir(harness, "12345678")
    iter_dir = rd / "iteration-1"
    contract = iter_dir / "contract.md"
    contract.write_text("# contract")
    eval_md = iter_dir / "eval.md"
    eval_md.unlink()
    eval_md.symlink_to(contract)
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    with pytest.raises(McpError) as exc_info:
        await _call_read(f"forge://{token}/12345678/iteration-1/eval.md")
    assert exc_info.value.error.code == -32002


async def test_read_resource_corrupt_json_strict(tmp_path, monkeypatch):
    """§R9.3 corrupt UTF-8 JSON maps to -32002.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    rd = _populate_run_dir(harness, "12345678")
    (rd / "state.json").write_bytes(b"\xff\xfe\x00bad")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    with pytest.raises(McpError) as exc_info:
        await _call_read(f"forge://{token}/12345678/state.json")
    assert exc_info.value.error.code == -32002


async def test_read_resource_corrupt_log_replaced(tmp_path, monkeypatch):
    """§R9.3 corrupt status.log decodes with replacement.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token

    harness = tmp_path / ".harness"
    rd = _populate_run_dir(harness, "12345678")
    (rd / "status.log").write_bytes(b"good\xffbad")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    contents = await _call_read(f"forge://{token}/12345678/status.log")
    assert isinstance(contents[0].content, str)
    assert "good" in contents[0].content
    assert contents[0].mime_type == "application/x-ndjson"


def test_capabilities_resources_subscribe_and_list_changed_advertised():
    """§S1 resource capability flags are advertised by CLI init options.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.cli import build_init_options

    caps = build_init_options().capabilities
    assert caps.resources is not None
    assert caps.resources.subscribe is True
    assert caps.resources.listChanged is True


def test_subscribe_capability_flag_true():
    """§S1: resources.subscribe capability flag is advertised as True.

    Design: §-Tests enumerates this single-flag pin so a regression that
        drops the subscribe handler is caught by name; subsumed-by
        test_capabilities_resources_subscribe_and_list_changed_advertised.
    Implementation: assert build_init_options advertises subscribe=True, then
        pin the get_capabilities() mechanism named by the design — the installed
        SDK hardcodes subscribe=False, so build_init_options is the component
        that upgrades it; assert both halves so the upgrade can't be dropped.
    Example: build_init_options().capabilities.resources.subscribe is True.
    """
    from mcp.server.lowlevel import NotificationOptions

    from forge_mcp.cli import build_init_options
    from forge_mcp.server import server

    # End-to-end advertised capability (what clients receive).
    caps = build_init_options().capabilities
    assert caps.resources is not None
    assert caps.resources.subscribe is True

    # §S1: the installed SDK hardcodes subscribe=False on raw get_capabilities;
    # build_init_options upgrades it. Pin both halves.
    raw = server.get_capabilities(
        notification_options=NotificationOptions(resources_changed=True),
        experimental_capabilities={},
    )
    assert raw.resources is not None
    assert raw.resources.subscribe is False


def test_subscribe_capability_listChanged_true():
    """§S1: resources.listChanged capability flag is advertised as True.

    Design: §-Tests enumerates this single-flag pin so a regression that
        drops listChanged advertisement is caught by name; subsumed-by
        test_capabilities_resources_subscribe_and_list_changed_advertised.
    Implementation: assert build_init_options advertises listChanged=True and
        also assert it through the design's named server.get_capabilities()
        mechanism, which honors NotificationOptions(resources_changed=True).
    Example: build_init_options().capabilities.resources.listChanged is True.
    """
    from mcp.server.lowlevel import NotificationOptions

    from forge_mcp.cli import build_init_options
    from forge_mcp.server import server

    caps = build_init_options().capabilities
    assert caps.resources is not None
    assert caps.resources.listChanged is True

    # §S1: assert through the design's named mechanism.
    raw = server.get_capabilities(
        notification_options=NotificationOptions(resources_changed=True),
        experimental_capabilities={},
    )
    assert raw.resources is not None
    assert raw.resources.listChanged is True


def test_subscribe_handler_registered():
    """§S3 resources/subscribe handler is registered.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from mcp import types

    from forge_mcp.server import server

    assert types.SubscribeRequest in server.request_handlers


async def test_subscribe_handler_rejects_unallowed_uri() -> None:
    """§S4: subscribing to a non-allowlisted URI raises McpError(-32002).

    Design: subscribe shares read_resource's decode+allowlist gate, so a URI
        that resolves to no allowlisted artifact is rejected with the same
        -32002 code and the same "resource not found: <uri>" message shape.
    Implementation: call subscribe_resource_handler with a valid-shape forge URI
        whose subpath (run.log) is intentionally not allowlisted; assert the
        raised McpError code and message.
    Example: subscribing forge://.../run.log raises -32002.
    """
    from forge_mcp.server import subscribe_resource_handler

    uri = AnyUrl("forge://tokenToken12/abcd1234/run.log")
    with pytest.raises(McpError) as exc_info:
        await subscribe_resource_handler(uri)
    assert exc_info.value.error.code == -32002
    assert exc_info.value.error.message == f"resource not found: {uri}"


async def test_subscribe_handler_accepts_allowed_uri_and_registers(monkeypatch) -> None:
    """§S4/§S5: subscribing to an allowlisted URI registers the current session.

    Design: a readable artifact URI must be subscribable; the handler inserts the
        in-flight request session into the in-memory subscription registry.
    Implementation: stub server.request_context with a fake carrying a recording
        session, call the handler with an allowlisted state.json URI, and assert
        the registry now lists that session as a subscriber of the exact URI.
    Example: after subscribe, subscribed_sessions(uri) contains the session.
    """
    from types import SimpleNamespace

    from forge_mcp import subscriptions
    from forge_mcp.server import server, subscribe_resource_handler

    class _RecordingSession:
        """Identity-hashable fake session for the subscribe accept path.

        Design: the registry keys sessions by identity; a bare object suffices.
        Implementation: no behavior needed beyond being hashable by identity.
        Example: session = _RecordingSession().
        """

    session = _RecordingSession()
    fake_ctx = SimpleNamespace(session=session)
    monkeypatch.setattr(type(server), "request_context", property(lambda _self: fake_ctx))

    uri = AnyUrl("forge://tokenToken12/abcd1234/state.json")
    fresh = subscriptions.SubscriptionRegistry()
    monkeypatch.setattr(subscriptions, "_REGISTRY", fresh)

    await subscribe_resource_handler(uri)

    assert session in tuple(fresh.subscribed_sessions(str(uri)))


async def test_unsubscribe_handler_is_idempotent(monkeypatch) -> None:
    """§S4: unsubscribing a never-subscribed URI is a no-op, twice over.

    Design: hosts may reissue unsubscribe during reconnect recovery, so the
        handler must tolerate a URI that was never subscribed without raising
        or adding a URI subscription; handler-level counterpart to the
        registry-level test_subscribe_unsubscribe_and_drop_are_idempotent.
    Implementation: stub server.request_context with a fake session, swap in a
        fresh empty SubscriptionRegistry, call unsubscribe_resource_handler
        twice on an unsubscribed URI, and assert no raise, no URI subscribers,
        and one connection-level session record.
    Example: two unsubscribe calls leave subscribed_sessions(uri) empty.
    """
    from types import SimpleNamespace

    from forge_mcp import subscriptions
    from forge_mcp.server import server, unsubscribe_resource_handler

    class _RecordingSession:
        """Identity-hashable fake session for the unsubscribe idempotency pin.

        Design: the registry keys sessions by identity; a bare object suffices.
        Implementation: no behavior needed beyond being hashable by identity.
        Example: session = _RecordingSession().
        """

    session = _RecordingSession()
    fake_ctx = SimpleNamespace(session=session)
    monkeypatch.setattr(type(server), "request_context", property(lambda _self: fake_ctx))

    fresh = subscriptions.SubscriptionRegistry()
    monkeypatch.setattr(subscriptions, "_REGISTRY", fresh)

    uri = AnyUrl("forge://tokenToken12/abcd1234/state.json")
    await unsubscribe_resource_handler(uri)
    await unsubscribe_resource_handler(uri)

    assert list(fresh.subscribed_sessions(str(uri))) == []
    assert list(fresh._sessions_view()) == [session]


async def test_list_resources_poison_per_scope(tmp_path, monkeypatch, caplog):
    """§R9.3 per-scope OSError is logged and the sibling scope still serves.

    Design: §R9.3 pins that an OSError raised from expand_scope_to_resources
        for one scope must NOT poison the whole list — the handler logs one
        `_RESOURCE_LOGGER.warning("expand failed: ...")` entry and continues
        with the remaining scopes. This pins the per-scope try/except in §R6.
    Implementation: register two active scopes (one valid + one whose expand
        will raise), monkeypatch the `expand_scope_to_resources` symbol that
        server.py imports so it raises OSError for the doomed scope and
        delegates to the real implementation for the healthy scope; assert
        the healthy scope's URIs surface, the doomed scope's URIs do not,
        and exactly one WARNING record with prefix `expand failed:` is
        captured against logger `forge_mcp.resources`.
    Example: pytest tests/test_resources_handlers.py::test_list_resources_poison_per_scope -v.
    """
    # §R9.3 — per-scope OSError swallow + sibling-scope survival.
    from forge_mcp.config import RunConfig
    from forge_mcp.resources import compute_harness_token
    from forge_mcp.resources import expand_scope_to_resources as real_expand

    harness_good = tmp_path / "good" / ".harness"
    harness_bad = tmp_path / "bad" / ".harness"
    _populate_run_dir(harness_good, "11111111")
    _populate_run_dir(harness_bad, "22222222")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())

    token_good = compute_harness_token(harness_good)
    token_bad = compute_harness_token(harness_bad)
    register_active_run(_ResourceScope("11111111", harness_good, token_good))
    register_active_run(_ResourceScope("22222222", harness_bad, token_bad))

    def _expand_with_poison(scope):
        """Raise OSError for the doomed scope, delegate otherwise.

        Design: §R9.3 needs one poisoned scope without changing production code.
        Implementation: compare the scope token and raise only for the bad one.
        Example: _expand_with_poison(scope) returns resources or raises OSError.
        """
        if scope.harness_token == token_bad:
            raise OSError("simulated vanish")
        return real_expand(scope)

    monkeypatch.setattr("forge_mcp.server.expand_scope_to_resources", _expand_with_poison)

    caplog.set_level("WARNING", logger="forge_mcp.resources")
    result = await _call_list()

    uris = [str(r.uri) for r in result.resources]
    # Healthy scope's URIs survived.
    assert any(f"forge://{token_good}/11111111/state.json" == u for u in uris)
    assert any(f"forge://{token_good}/11111111/plan/plan.md" == u for u in uris)
    # Doomed scope contributed nothing.
    assert not any(token_bad in u for u in uris)
    # Exactly one warning, with the expected prefix, on the right logger.
    warn_records = [
        r for r in caplog.records if r.name == "forge_mcp.resources" and r.levelname == "WARNING"
    ]
    assert len(warn_records) == 1
    assert warn_records[0].getMessage().startswith("expand failed:")
