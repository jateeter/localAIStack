"""Address every registered engine in one interaction, and hold them to parity.

localAI used to write each interaction through one resolved engine: the
initiating engine when it named itself, otherwise whatever single target the
resolver picked. On a multi-engine universe that left every other engine
without localAI's signals, so engines that agreed on corpus and progress
diverged on the inputs localAI fed them — and the divergence surfaced as an
engine disagreement in every cross-engine comparison downstream.

The rule this module implements:

* **An engine that names itself is answered alone.** ``X-RE-Instance`` binds
  the interaction to that engine (``core.bridge_binding``); fanning its request
  out would write one engine's request into the others.
* **Otherwise every registered engine is addressed.** The interaction's values
  are computed **once** — including anything derived from an LLM — and the same
  values are written to each engine. LLM output is non-deterministic, so
  computing per engine would hand engines in parity different stimulus; one
  computation, broadcast, is what keeps localAI from being the source of
  divergence.
* **Engines in parity must answer alike.** Each engine's reading of the result
  (a routing decision, a health state, a session context) is compared with
  ``agree()``. Equal readings are the parity case. Unequal readings are recorded
  as a divergence naming every engine's answer, with none treated as the
  reference, and the interaction proceeds on the first engine in instance
  registry order so its behaviour stays deterministic.

What is compared is what an engine computed from localAI's input, never LLM
text: two engines in parity given identical values must decode identical
states, while two LLM generations need not match and are not asked to.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, TypeVar

import structlog

from core.bridge_binding import bind, initiating_instance
from core.registry_resolver import resolve_all_bridge_targets

log = structlog.get_logger()

T = TypeVar("T")

# Enumerating engines probes each one's health. An interaction makes several
# fan-out calls in a row, so the enumeration is cached briefly; the instance
# registry changes on a deploy, not between two writes of one request.
_TARGETS_TTL_S = 5.0
_targets_cache: dict = {"at": 0.0, "targets": None}
_targets_lock = threading.Lock()

_MAX_DIVERGENCES = 50
_divergences: deque[dict] = deque(maxlen=_MAX_DIVERGENCES)
_counts = {"agreed": 0, "diverged": 0, "single": 0}
_record_lock = threading.Lock()


def _all_healthy_targets() -> list[dict]:
    now = time.monotonic()
    with _targets_lock:
        cached = _targets_cache["targets"]
        if cached is not None and now - _targets_cache["at"] < _TARGETS_TTL_S:
            return cached
    every = [
        {
            "re_url": t["re_url"],
            "pe_url": t["pe_url"],
            "instance": t.get("instance"),
            "healthy": t.get("healthy"),
        }
        for t in resolve_all_bridge_targets()
    ]
    # Healthy engines only: an unreachable one would cost every interaction its
    # probe timeout. With none healthy, the first is still addressed — the same
    # fallback the single-target resolver makes — so the interaction degrades
    # through its usual safe default instead of silently addressing nothing.
    targets = [t for t in every if t["healthy"]] or every[:1]
    for t in targets:
        t.pop("healthy", None)
    with _targets_lock:
        _targets_cache.update({"at": now, "targets": targets})
    return targets


def reset_cache() -> None:
    """Forget the cached engine enumeration (tests, and a deploy changing engines)."""
    with _targets_lock:
        _targets_cache.update({"at": 0.0, "targets": None})


def interaction_targets() -> list[dict]:
    """The engines this interaction addresses, in instance registry order.

    The initiating engine alone when one named itself (empty when it is not
    running — never a substitute); otherwise every healthy registered engine.
    """
    if initiating_instance():
        target = bind()
        return [target] if target else []
    return _all_healthy_targets()


def fan_out(fn: Callable[[dict], T]) -> list[tuple[str | None, T]]:
    """Run ``fn(target)`` against every addressed engine, in registry order."""
    return [(t.get("instance"), fn(t)) for t in interaction_targets()]


def agree(label: str, results: list[tuple[str | None, T]], default: T) -> T:
    """Reduce per-engine readings of one interaction to a single answer.

    Every engine agreeing returns the shared reading. A disagreement is
    recorded with every party's reading and the first engine's is returned, so
    the caller's behaviour stays deterministic while the divergence is visible.
    No engines returns ``default``.
    """
    if not results:
        return default
    first = results[0][1]
    if len(results) == 1:
        with _record_lock:
            _counts["single"] += 1
        return first
    if all(value == first for _, value in results[1:]):
        with _record_lock:
            _counts["agreed"] += 1
        return first
    record = {
        "label": label,
        "at": time.time(),
        "readings": {str(instance): value for instance, value in results},
        "proceededWith": results[0][0],
    }
    with _record_lock:
        _counts["diverged"] += 1
        _divergences.append(record)
    log.warning(
        "engine_fanout.parity_divergence",
        label=label,
        readings=record["readings"],
        proceeded_with=record["proceededWith"],
    )
    return first


def parity_report() -> dict[str, Any]:
    """Counts of agreed, diverged and single-engine interactions, and recent divergences."""
    with _record_lock:
        return {
            "counts": dict(_counts),
            "recentDivergences": list(_divergences),
        }


def reset_parity_report() -> None:
    with _record_lock:
        _divergences.clear()
        for key in _counts:
            _counts[key] = 0
