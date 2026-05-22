"""§S5.3 orchestrator-internal resource update emitter."""

from __future__ import annotations

from ..subscriptions import ResourceNotifier


class _NullNotifier:
    """No-op notifier for direct tests and legacy construction paths (§S5.3).

    Design: production passes RegistryNotifier, but existing focused tests can
        still construct an Orchestrator without MCP session machinery.
    Implementation: async notify method returns None.
    Example: await _NullNotifier().notify('forge://t/r/state.json').
    """

    async def notify(self, uri: str) -> None:
        """Ignore one notification URI.

        Design: a no-op notifier preserves old direct-call behavior where no
            host subscribed to resource updates.
        Implementation: deliberately do nothing with the string argument.
        Example: await notifier.notify('forge://t/r/state.json').
        """
        _ = uri


class _Emitter:
    """Build forge:// URIs and delegate to a ResourceNotifier (§S5.3).

    Design: one URI-construction site inside orchestrator code prevents drift
        from the resource-surface forge:// scheme and keeps MCP SDK imports out.
    Implementation: compose token/run/subpath strings and expose convenience
        wrappers for state and iteration artifacts.
    Example: await _Emitter(n, 'TOK', 'abcd1234').emit_iteration(1, 'eval.md').
    """

    def __init__(self, notifier: ResourceNotifier, harness_token: str | None, run_id: str) -> None:
        """Store notifier and URI identity components.

        Design: harness_token may be absent in legacy direct-call tests; in that
            case emissions are silently suppressed because no forge:// URI exists.
        Implementation: assign all values without I/O.
        Example: emitter = _Emitter(notifier, 'tokenToken12', 'abcd1234').
        """
        self._notifier = notifier
        self._harness_token = harness_token
        self._run_id = run_id

    def _uri(self, subpath: str) -> str | None:
        """Compose a forge:// URI for a run-relative subpath (§R1.2).

        Design: no token means the run is not resource-addressable, matching the
            existing optional URI companions in RunResult artifacts.
        Implementation: f-string when harness_token is present, else None.
        Example: emitter._uri('state.json') returns a forge URI or None.
        """
        if self._harness_token is None:
            return None
        return f"forge://{self._harness_token}/{self._run_id}/{subpath}"

    async def emit_path(self, subpath: str) -> None:
        """Emit an update for an arbitrary run-relative artifact (§S5.2).

        Design: materialization sites call this after durable writes; missing
            harness token suppresses emission for non-resource-addressable runs.
        Implementation: build URI and await notifier.notify when available.
        Example: await emitter.emit_path('inputs/git-state.txt').
        """
        uri = self._uri(subpath)
        if uri is not None:
            await self._notifier.notify(uri)

    async def emit_state(self) -> None:
        """Emit an update for state.json (§S5.2).

        Design: state updates are frequent enough to deserve a named wrapper
            that documents the canonical subpath.
        Implementation: delegate to emit_path('state.json').
        Example: await emitter.emit_state().
        """
        await self.emit_path("state.json")

    async def emit_iteration(self, n: int, filename: str) -> None:
        """Emit an update for an iteration-N artifact (§S5.2).

        Design: centralizing the iteration path keeps every phase on the same
            strict iteration-N/<filename> shape.
        Implementation: format the subpath and delegate to emit_path.
        Example: await emitter.emit_iteration(3, 'summary.md').
        """
        await self.emit_path(f"iteration-{n}/{filename}")
