"""Engine affinity for the localAI ↔ RealityEngine bridge.

A bridge interaction is not one call. It writes one or more sensor values,
triggers a PE push, reads the resulting ``perceptualSpace`` out of the push
response, and normalizes that space into a routing decision or a session
context. Every one of those steps used to call ``resolve_bridge_targets()``
independently, and that resolver caches for 30 seconds — so an expiry landing
between the write and the read could retarget the interaction mid-flight:
values written into one engine, routing decoded from a different engine's
perceptual space. Nothing in the returned decision records which engine
produced it, so the mismatch is invisible at the call site and downstream.

This module pins exactly one engine for the lifetime of an interaction:

* ``set_initiating_instance()`` records which engine initiated the work.
  ``EngineAffinityMiddleware`` sets it per request from the ``X-RE-Instance``
  header, so an engine-initiated call is answered through that engine's own
  sources and no other.
* ``bind()`` resolves that instance once and caches the result in a
  ``ContextVar``. Every later ``bind()`` in the same context returns the same
  target, whatever the resolver cache has since done.
* An instance that is named but not running resolves to ``None``, never to a
  substitute. Falling back to a different engine is precisely the cross-talk
  this exists to prevent, so the interaction degrades to its safe default
  instead of writing somewhere it was not addressed to.

Nothing here is per-process global: the binding lives in a ``ContextVar``, so
concurrent requests addressed to different engines do not disturb each other.
"""

from __future__ import annotations

from contextvars import ContextVar

import structlog

from core.registry_resolver import resolve_all_bridge_targets, resolve_bridge_targets

log = structlog.get_logger()

# The header an engine (or the PE dispatching on its behalf) sets to identify
# itself. Matches the registry's instance ids: "cpp-1", "lsp-1", "scala-1".
RE_INSTANCE_HEADER = "x-re-instance"


class _Unresolved:
    """Distinguishes "not resolved yet" from "resolved to nothing".

    Storing ``None`` for both would re-resolve an unknown instance on every
    call in the interaction, re-probing the registry and re-logging the same
    warning several times per request.
    """


_UNRESOLVED = _Unresolved()

_initiating: ContextVar[str | None] = ContextVar("re_initiating_instance", default=None)
_bound: ContextVar[dict | _Unresolved | None] = ContextVar("re_bound_target", default=_UNRESOLVED)


def set_initiating_instance(instance: str | None) -> None:
    """Record the engine that initiated the current interaction.

    Clears any existing binding: a new initiator must resolve its own target
    rather than inherit one resolved for someone else.
    """
    _initiating.set(instance or None)
    _bound.set(_UNRESOLVED)


def initiating_instance() -> str | None:
    """The engine that initiated the current interaction, if it named itself."""
    return _initiating.get()


def reset_binding() -> None:
    """Drop the pinned target, keeping the initiating instance."""
    _bound.set(_UNRESOLVED)


def bind(instance: str | None = None) -> dict | None:
    """Return the engine this interaction is pinned to.

    Shape: ``{"re_url", "pe_url", "instance"}``, or ``None`` when the named
    instance is not running. Resolves at most once per context; the result is
    reused so a write and the read that interprets it cannot land on different
    engines.

    ``instance`` overrides the initiating instance for callers that address an
    engine explicitly (a fan-out loop, a test).
    """
    current = _bound.get()
    if not isinstance(current, _Unresolved) and (
        instance is None or (current is not None and current.get("instance") == instance)
    ):
        return current

    requested = instance if instance is not None else _initiating.get()
    target = _resolve(requested)
    if instance is None:
        # Only an unaddressed bind pins the context. An explicitly addressed
        # one is a caller stepping outside the ambient binding for a single
        # call and must not redirect the interaction around it.
        _bound.set(target)
    return target


def _resolve(instance: str | None) -> dict | None:
    if not instance:
        t = resolve_bridge_targets()
        return {"re_url": t["re_url"], "pe_url": t["pe_url"], "instance": t.get("instance")}

    for t in resolve_all_bridge_targets():
        if t.get("instance") == instance:
            return {"re_url": t["re_url"], "pe_url": t["pe_url"], "instance": instance}

    log.warning(
        "bridge_binding.unknown_instance",
        instance=instance,
        note="named engine is not in the registry; interaction degrades to its "
        "safe default rather than writing to a different engine",
    )
    return None


class EngineAffinityMiddleware:
    """Pure-ASGI middleware binding each request to its initiating engine.

    Deliberately not a ``BaseHTTPMiddleware``: that runs the downstream app in
    a separate task, so a ``ContextVar`` set here would not be visible to the
    endpoint. A pure-ASGI middleware shares the request's task and its context.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        instance = None
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.decode("latin-1").lower() == RE_INSTANCE_HEADER:
                instance = raw_value.decode("latin-1").strip() or None
                break

        # Set unconditionally: an absent header must clear any inherited
        # binding, not silently reuse the previous request's engine.
        set_initiating_instance(instance)
        await self.app(scope, receive, send)
