"""§S2/§S5 subscription registry and notifier tests."""

from forge_mcp.subscriptions import RegistryNotifier, SubscriptionRegistry


class _Session:
    """Identity-hashable fake session used by subscription tests."""

    def __init__(self) -> None:
        """Initialize a recording session.

        Design: tests need a minimal session object matching the Protocol.
        Implementation: store updated URIs and listChanged count in attributes.
        Example: session.updated == [] initially.
        """
        self.updated: list[str] = []
        self.changed = 0

    async def send_resource_updated(self, uri: object) -> None:
        """Record one resources/updated URI.

        Design: RegistryNotifier calls this method on subscribed sessions.
        Implementation: append the string form of the URI.
        Example: await session.send_resource_updated('u').
        """
        self.updated.append(str(uri))

    async def send_resource_list_changed(self) -> None:
        """Record one listChanged notification.

        Design: resources._broadcast_list_changed fans out to this method.
        Implementation: increment a counter.
        Example: await session.send_resource_list_changed().
        """
        self.changed += 1


def test_subscribe_unsubscribe_and_drop_are_idempotent() -> None:
    """§S2 registry mutators are idempotent and exact-URI scoped.

    Design: hosts can reissue subscribe/unsubscribe during reconnect recovery.
    Implementation: exercise duplicate adds, duplicate removes, and drop.
    Example: subscribed_sessions('other') returns empty.
    """
    reg = SubscriptionRegistry()
    session = _Session()
    reg.subscribe(session, "forge://t/r/eval.md")
    reg.subscribe(session, "forge://t/r/eval.md")
    assert list(reg.subscribed_sessions("forge://t/r/eval.md")) == [session]
    assert list(reg.subscribed_sessions("forge://t/r/other.md")) == []
    reg.unsubscribe(session, "forge://t/r/eval.md")
    reg.unsubscribe(session, "forge://t/r/eval.md")
    assert list(reg.subscribed_sessions("forge://t/r/eval.md")) == []
    reg.subscribe(session, "u")
    reg.drop_session(session)
    assert list(reg._sessions_view()) == []


def test_sessions_view_includes_connected_unsubscribed() -> None:
    """§S6 / S-Decision 10: a connected session is visible without subscribing.

    Design: list_changed is connection-level, so a session that has issued no
        resources/subscribe must still appear in the broadcast fan-out view.
    Implementation: note a session as connected, never subscribe it, and assert
        _sessions_view returns it while subscribed_sessions stays empty.
    Example: registry._sessions_view() contains a never-subscribed session.
    """
    registry = SubscriptionRegistry()
    session = _Session()
    registry.note_connected(session)
    assert session in tuple(registry._sessions_view())
    assert tuple(registry.subscribed_sessions("forge://tokenToken12/abcd1234/state.json")) == ()


def test_drop_session_clears_connected_and_subscribed() -> None:
    """§S7 / S-Inv 5: dropping a session removes it from both tracking sets.

    Design: disconnect cleanup must leave no connection-level or subscription
        residue for the dead session.
    Implementation: connect and subscribe one session, drop it, and assert it is
        absent from _sessions_view and from its prior URI's subscribers.
    Example: after drop_session the registry view is empty.
    """
    registry = SubscriptionRegistry()
    session = _Session()
    uri = "forge://tokenToken12/abcd1234/state.json"
    registry.note_connected(session)
    registry.subscribe(session, uri)
    registry.drop_session(session)
    assert tuple(registry._sessions_view()) == ()
    assert tuple(registry.subscribed_sessions(uri)) == ()


async def test_registry_notifier_reaches_only_subscribers() -> None:
    """§S5.1 RegistryNotifier emits only to exact subscribers.

    Design: resources/updated delivery is URI-exact and fail-soft.
    Implementation: subscribe one URI, notify another, then notify the match.
    Example: session.updated contains only the matching URI.
    """
    reg = SubscriptionRegistry()
    session = _Session()
    reg.subscribe(session, "forge://t/r/eval.md")
    notifier = RegistryNotifier(reg)
    await notifier.notify("forge://t/r/other.md")
    await notifier.notify("forge://t/r/eval.md")
    assert session.updated == ["forge://t/r/eval.md"]
