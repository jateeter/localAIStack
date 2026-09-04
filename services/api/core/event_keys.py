"""Reading machine structure while the Reality Event rename is in flight.

RealityEngine_CI#220 layer 1 renames the keys that describe a machine's event
structure, in the corpus and in every engine response that echoes one:

    vectors       -> events
    outputVectors -> outputEvents
    nextVectorIds -> nextEventIds

This stack has its own corpus under `data/machines`, loaded by the engines via
`LOCAL_AI_MACHINES_DIR`, and `topology_builder` synthesises machines at runtime.
Both now speak the canonical spelling. The readers accept either, because the
four runtimes still do and machines reach this code from generators, fixtures
and older corpora; layer 1c removes the fallback.

## Why accessors and not `x.get("events") or x.get("vectors")`

Because the failure is silent. Every read of these keys is a `.get()` with an
empty default, so a reader looking for a key that is no longer there does not
raise — it gets an empty list and produces a well-formed, wrong answer.

That is not hypothetical anywhere in this project. When the main corpus was
rewritten, the sibling OpenClaw stack's binding derivation dropped from 1643
output-actor bindings to 1465 with **its full test suite still green**, because
the suite counted machines and behaviours rather than the thing that broke.
"""

from __future__ import annotations

from typing import Any

__all__ = ["sequence_events", "output_events", "next_event_ids"]


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _either(node: Any, canonical: str, legacy: str) -> list:
    """Canonical spelling first, legacy second, empty list last."""
    if not isinstance(node, dict):
        return []
    value = node.get(canonical)
    return _as_list(value if value is not None else node.get(legacy))


def sequence_events(sequence: Any) -> list:
    """The events of a critical event sequence."""
    return _either(sequence, "events", "vectors")


def output_events(event: Any) -> list:
    """The output events a Reality Event fires when it matches."""
    return _either(event, "outputEvents", "outputVectors")


def next_event_ids(event: Any) -> list:
    """The ids of the events this one arms."""
    return _either(event, "nextEventIds", "nextVectorIds")
