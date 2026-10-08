"""A durable record of how far engines' LLM answers drift apart (RealityEngine_CI#518).

Engines in parity that each initiate the same request (``X-RE-Instance``) each
get their own LLM generation, and generations drift. That drift is kept on
purpose — never cached away to manufacture parity — because it is the input to
difference-net training in the near-miss learning cycles. What was missing is a
record of it: an engine-initiated answer was returned to its engine and gone,
and ``agree()`` only ever compared interactions localAI broadcasts, whose values
are computed once and so cannot drift.

This module observes, it does not intervene:

* ``observe(label, request, reading)`` is called after an engine-initiated
  interaction has been answered. Its request is keyed by content (label plus
  the canonical request, not the engine). Another engine asking the same thing
  within ``LOCALAI_DRIFT_WINDOW_S`` (default 120 s) joins the same group.
* Each time a group gains an engine, the readings are compared with the owner's
  difference metric (``engine_fanout.spread``: identical 0, a split categorical
  or textual answer 1, numbers their difference, structures the mean) and a
  ``compared`` record carrying every engine's full reading is appended to
  ``llm-drift.jsonl`` in ``LOCALAI_STATE_DIR``. The texts are the training data;
  the score is a convenience, and training may compute its own.
* ``record_divergence(record)`` appends a broadcast interaction ``agree()``
  found divergent, so both kinds of near-miss land in one corpus.

Appends are serialised and fsynced, never rewritten. When the directory cannot
be written the record is still kept in memory and the failure logged — a
request never fails for want of its record. Wall-clock times are carried for
reference; cross-engine ordering is to come from engine Lamport ticks.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import threading
import time
from collections import deque
from typing import Any

import structlog

from core.bridge_binding import initiating_instance

log = structlog.get_logger()

_FILE_NAME = "llm-drift.jsonl"
_lock = threading.Lock()
_recent: deque[dict] = deque(maxlen=200)
# key -> {"opened": t, "label": str, "readings": {instance: reading}}
_groups: dict[str, dict[str, Any]] = {}
_stats: dict[str, dict[str, float]] = {}


def _window_s() -> float:
    try:
        return float(os.getenv("LOCALAI_DRIFT_WINDOW_S", "120"))
    except ValueError:
        return 120.0


def _path() -> pathlib.Path:
    return pathlib.Path(os.getenv("LOCALAI_STATE_DIR", "/app/state")) / _FILE_NAME


def request_key(label: str, request: Any) -> str:
    """Content identity of a request: the same question from two engines matches."""
    canonical = json.dumps(request, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{label}\0{canonical}".encode()).hexdigest()


def _append(record: dict) -> None:
    line = json.dumps(record, sort_keys=True, default=str)
    _recent.append(record)
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        log.warning("llm_drift.unpersisted", path=str(path), error=str(exc))


def observe(label: str, request: Any, reading: Any, instance: str | None = None) -> dict | None:
    """Record an engine-initiated answer; compare it with other engines' answers to it.

    Returns the ``compared`` record when this observation completed a comparison,
    else ``None``. Called only for engine-initiated work: a broadcast interaction
    is computed once and compared by ``engine_fanout.agree()``.
    """
    instance = instance or initiating_instance()
    if not instance:
        return None
    from core.engine_fanout import spread

    now = time.time()
    key = request_key(label, request)
    with _lock:
        for k in [k for k, g in _groups.items() if now - g["opened"] > _window_s()]:
            del _groups[k]
        group = _groups.get(key)
        if group is None or instance in group["readings"]:
            # A first answer, or the same engine asking again: a new round.
            group = {"opened": now, "label": label, "readings": {}}
            _groups[key] = group
        group["readings"][instance] = reading
        if len(group["readings"]) < 2:
            return None
        readings = dict(sorted(group["readings"].items()))
        measured = spread(list(readings.values()))
        record = {
            "at": now,
            "event": "compared",
            "origin": "engine-initiated",
            "label": label,
            "requestKey": key,
            "request": request,
            "engines": list(readings),
            "difference": measured,
            "readings": readings,
        }
        entry = _stats.setdefault(label, {"comparisons": 0, "sum": 0.0, "max": 0.0, "drifted": 0})
        entry["comparisons"] += 1
        entry["sum"] += measured
        entry["max"] = max(entry["max"], measured)
        entry["drifted"] += 1 if measured > 0 else 0
        _append(record)
    if measured > 0:
        log.info(
            "llm_drift.observed",
            label=label,
            engines=record["engines"],
            difference=round(measured, 4),
        )
    return record


def record_divergence(divergence: dict) -> None:
    """Persist a broadcast interaction whose engines decoded identical input differently."""
    with _lock:
        _append({**divergence, "event": "diverged", "origin": "broadcast"})


def report() -> dict[str, Any]:
    """Per label: engine-initiated comparisons, how many drifted, mean and max difference."""
    with _lock:
        return {
            label: {
                "comparisons": int(s["comparisons"]),
                "drifted": int(s["drifted"]),
                "mean": s["sum"] / s["comparisons"] if s["comparisons"] else 0.0,
                "max": s["max"],
            }
            for label, s in sorted(_stats.items())
        }


def records(limit: int = 100, label: str | None = None) -> list[dict]:
    """The newest durable records, oldest first; read back from disk when present."""
    path = _path()
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        with _lock:
            rows = list(_recent)
    if label:
        rows = [r for r in rows if r.get("label") == label]
    return rows[-limit:] if limit > 0 else rows


def reset() -> None:
    with _lock:
        _groups.clear()
        _stats.clear()
        _recent.clear()
