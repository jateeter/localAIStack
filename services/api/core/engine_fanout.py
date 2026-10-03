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

**Parity is measured, not only judged.** Every interaction ``agree()`` sees is
given a *difference* in [0, 1] — 0 when the engines' readings are identical —
and ``parity_report()`` aggregates it per interaction label. Agreement is the
special case difference == 0; the metric is what makes a near-miss visible as
a small number rather than a binary failure.

**LLM responses are never cached to manufacture parity.** Where engines each
initiate their own LLM call, the generations may drift, and that drift is kept:
it is the raw material for difference-net training in the near-miss learning
cycles (RealityEngine_CI#518). Memoizing a response per request and engine step
would erase exactly the signal those cycles need.
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
# Per interaction label: how many multi-engine interactions, and their
# difference summed and at its largest (see difference()).
_differences: dict[str, dict[str, float]] = {}
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


def difference(a: Any, b: Any) -> float:
    """How far apart two engines' readings are, in [0, 1]; 0 means identical.

    * equal values: 0
    * numbers: their absolute difference, capped at 1 — localAI's signals and
      the regions it decodes are normalised to [0, 1]
    * anything else that differs (a routing decision, a health state, a type
      mismatch, a value against nothing): 1
    * mappings: the mean over the union of their keys
    * sequences: the mean over the longer length, a missing element counting 1
    """
    if a == b:
        return 0.0
    if isinstance(a, bool) or isinstance(b, bool):
        return 1.0
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return min(1.0, abs(float(a) - float(b)))
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a) | set(b)
        return sum(difference(a.get(k), b.get(k)) for k in keys) / len(keys) if keys else 0.0
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        n = max(len(a), len(b))
        if n == 0:
            return 0.0
        return (
            sum(difference(a[i], b[i]) if i < len(a) and i < len(b) else 1.0 for i in range(n)) / n
        )
    return 1.0


def spread(values: list[Any]) -> float:
    """The largest pairwise difference among engines' readings."""
    return max(
        (
            difference(values[i], values[j])
            for i in range(len(values))
            for j in range(i + 1, len(values))
        ),
        default=0.0,
    )


def _record_difference(label: str, value: float) -> None:
    entry = _differences.setdefault(label, {"interactions": 0, "sum": 0.0, "max": 0.0})
    entry["interactions"] += 1
    entry["sum"] += value
    entry["max"] = max(entry["max"], value)


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
    measured = spread([value for _, value in results])
    if all(value == first for _, value in results[1:]):
        with _record_lock:
            _counts["agreed"] += 1
            _record_difference(label, measured)
        return first
    record = {
        "label": label,
        "at": time.time(),
        "difference": measured,
        "readings": {str(instance): value for instance, value in results},
        "proceededWith": results[0][0],
    }
    with _record_lock:
        _counts["diverged"] += 1
        _record_difference(label, measured)
        _divergences.append(record)
    log.warning(
        "engine_fanout.parity_divergence",
        label=label,
        difference=round(measured, 4),
        readings=record["readings"],
        proceeded_with=record["proceededWith"],
    )
    return first


def parity_report() -> dict[str, Any]:
    """Counts, the difference metric per interaction label, and recent divergences.

    ``difference`` per label: multi-engine interactions seen, their mean
    difference and the largest. 0 throughout is parity.
    """
    with _record_lock:
        return {
            "counts": dict(_counts),
            "difference": {
                label: {
                    "interactions": int(d["interactions"]),
                    "mean": d["sum"] / d["interactions"] if d["interactions"] else 0.0,
                    "max": d["max"],
                }
                for label, d in sorted(_differences.items())
            },
            "recentDivergences": list(_divergences),
        }


def reset_parity_report() -> None:
    with _record_lock:
        _divergences.clear()
        _differences.clear()
        for key in _counts:
            _counts[key] = 0
