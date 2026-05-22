"""§S2 subscription registry and notification seam."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

from pydantic import AnyUrl

_S5_LOGGER = logging.getLogger("forge_mcp.subscriptions")


class SessionHandle(Protocol):
    """Minimal session protocol used by subscription fan-out (§S2.1).

    Design: keeps this module independent of the MCP SDK's concrete session
        type while still documenting the methods the notifier invokes.
    Implementation: production passes ServerSession instances; tests pass small
        duck-typed recording sessions.
    Example: await session.send_resource_updated(AnyUrl('forge://t/r/state.json')).
    """

    async def send_resource_updated(self, uri: AnyUrl) -> None: ...

    async def send_resource_list_changed(self) -> None: ...


class SubscriptionRegistry:
    """In-memory subscription state keyed by session (§S2).

    Design: subscriptions are ephemeral per process and per session, which fits
        stdio while keeping an HTTP transport path possible later.
    Implementation: a dict maps identity-hashable sessions to exact URI string
        sets; all mutators are idempotent except normal Python hash failures.
    Example: reg.subscribe(session, 'forge://tok/run/state.json').
    """

    _by_session: dict[SessionHandle, set[str]]
    _connected: set[SessionHandle]

    def __init__(self) -> None:
        """Initialize an empty registry.

        Design: §S2 chooses in-memory state only; no durable subscription file
            exists because hosts must re-subscribe after reconnect.
        Implementation: allocate the session-to-URI dictionary and connected set eagerly.
        Example: registry = SubscriptionRegistry().
        """
        self._by_session = {}
        self._connected = set()  # §S6 — connection-level set for list_changed fan-out.

    def note_connected(self, session: SessionHandle) -> None:
        """Record a session as connection-level present (§S6 / S-Decision 10).

        Design: list_changed is a connection-level signal broadcast to every
            session, independent of whether it has subscribed to any URI, so the
            registry must track liveness separately from subscriptions.
        Implementation: idempotently add the session to the _connected set;
            handler entry points call this so polling-only hosts are reached.
        Example: reg.note_connected(server.request_context.session).
        """
        self._connected.add(session)

    def subscribe(self, session: SessionHandle, uri: str) -> None:
        """Idempotently add one URI subscription for a session (§S2.2).

        Design: repeated subscribe requests are harmless so hosts can recover
            from uncertain connection state by reissuing subscriptions.
        Implementation: setdefault(session, set()).add(uri), then mark connected.
        Example: reg.subscribe(session, 'forge://t/r/eval.md').
        """
        self._by_session.setdefault(session, set()).add(uri)
        self._connected.add(session)  # §S6 — subscribers are connection-level present too.

    def unsubscribe(self, session: SessionHandle, uri: str) -> None:
        """Idempotently remove one URI subscription (§S2.2).

        Design: unsubscribe intentionally validates nothing and treats missing
            entries as no-ops so cleanup paths can be defensive.
        Implementation: lookup the set and discard the URI when present.
        Example: reg.unsubscribe(session, 'forge://t/r/eval.md').
        """
        subscribed = self._by_session.get(session)
        if subscribed is not None:
            subscribed.discard(uri)

    def drop_session(self, session: SessionHandle) -> None:
        """Remove all subscriptions for a disconnected session (§S7).

        Design: no zombie subscriptions may outlive a session, and dropping an
            already-drained session must remain harmless.
        Implementation: pop the session key and discard from connection tracking.
        Example: reg.drop_session(session).
        """
        self._by_session.pop(session, None)
        self._connected.discard(session)  # §S7 / S-Inv 5 — no zombie connection entry.

    def subscribed_sessions(self, uri: str) -> Iterable[SessionHandle]:
        """Return sessions subscribed to exactly uri (§S5.1).

        Design: §S5 uses exact URI subscriptions, not glob or prefix matching,
            so notification recipients are deterministic.
        Implementation: return a tuple snapshot so callers can mutate registry
            during notification error handling without changing iteration.
        Example: tuple(reg.subscribed_sessions('forge://t/r/eval.md')).
        """
        return tuple(session for session, uris in self._by_session.items() if uri in uris)

    def _sessions_view(self) -> Iterable[SessionHandle]:
        """Return a defensive snapshot of every known session (§S6 / §S7).

        Design: the listChanged broadcast and lifespan teardown need ALL
            sessions — connection-level present plus any with subscriptions —
            while the ordinary API stays URI-oriented. Connection-level
            membership is what makes list_changed reach unsubscribed sessions
            (S-Decision 10).
        Implementation: return a deduplicated tuple over the union of
            _by_session keys and the _connected set.
        Example: for session in reg._sessions_view(): reg.drop_session(session).
        """
        return tuple({*self._by_session.keys(), *self._connected})


class ResourceNotifier(Protocol):
    """Orchestrator-facing notification seam free of MCP SDK imports (§S5.1).

    Design: orchestrator code only knows it can notify a forge:// URI; the SDK
        session fan-out remains isolated in RegistryNotifier.
    Implementation: Protocol with one async notify method.
    Example: await notifier.notify('forge://tok/run/state.json').
    """

    async def notify(self, uri: str) -> None: ...


class RegistryNotifier:
    """Concrete notifier that fans out through SubscriptionRegistry (§S5.1).

    Design: emission is observability-only and fail-soft; a dead client must not
        perturb the orchestrator's durable artifact writes.
    Implementation: iterate exact subscribers, coerce the URI to AnyUrl, and log
        WARN while swallowing any send exception.
    Example: await RegistryNotifier(_REGISTRY).notify('forge://t/r/eval.md').
    """

    def __init__(self, registry: SubscriptionRegistry) -> None:
        """Store the backing registry.

        Design: dependency injection keeps tests isolated from the module-level
            production singleton.
        Implementation: assign the registry to a private attribute.
        Example: notifier = RegistryNotifier(SubscriptionRegistry()).
        """
        self._registry = registry

    async def notify(self, uri: str) -> None:
        """Emit resources/updated to all exact subscribers of uri (§S5.4).

        Design: notifications happen after durable writes and failures are only
            logged, never re-raised into run control flow.
        Implementation: call send_resource_updated(AnyUrl(uri)) for each
            subscribed session under a broad exception guard.
        Example: await notifier.notify('forge://tok/run/state.json').
        """
        for session in self._registry.subscribed_sessions(uri):
            try:
                await session.send_resource_updated(AnyUrl(uri))
            except Exception:  # noqa: BLE001  # §S-Decision 6 fail-soft
                _S5_LOGGER.warning("send_resource_updated failed", exc_info=True)


_REGISTRY = SubscriptionRegistry()
