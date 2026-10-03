"""A durable record of everything localAI removes from an engine (RealityEngine_CI#518).

localAI removes sources and machines from engines on its own schedule: a live
sensor claims its window and the bootstrap replay over it goes; a HealthKit slot
leaves scope; a slot moves; a legacy sensor is retired; a stamped machine is
replaced by a newer definition. Each removal is right. What must not go with it
is the observation that the thing existed — what it was, where, on which engine,
and why it left — because re-establishing a knowledge line (K-line) later needs
the record of what was once active, not only what is active now.

Each removal is appended to ``source-observations.jsonl`` in ``LOCALAI_STATE_DIR``
(a persistent volume in the compose file), one JSON object per line, never
rewritten. Appends are serialised. When the directory cannot be written the
record is still kept in memory and the failure logged, so a removal never fails
for want of its record.

The entry carries the wall-clock time of the removal; ordering across engines is
to come from engine-specific Lamport ticks (engine UUID + step), not from it.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from collections import deque
from typing import Any

import structlog

log = structlog.get_logger()

_FILE_NAME = "source-observations.jsonl"
_lock = threading.Lock()
_recent: deque[dict] = deque(maxlen=500)


def _path() -> pathlib.Path:
    return pathlib.Path(os.getenv("LOCALAI_STATE_DIR", "/app/state")) / _FILE_NAME


def _instance_for(url: str) -> str | None:
    """The instance registry id of the engine at ``url``, when it is known."""
    try:
        from core.registry_resolver import resolve_all_bridge_targets

        for t in resolve_all_bridge_targets():
            if url in (t.get("pe_url"), t.get("re_url")):
                return t.get("instance")
    except Exception:  # noqa: BLE001 - attribution is best-effort
        pass
    return None


def record_removal(kind: str, engine_url: str, entry: dict[str, Any], reason: str) -> dict:
    """Persist that ``entry`` (a source or machine as it was) left ``engine_url``."""
    record = {
        "at": time.time(),
        "event": "removed",
        "kind": kind,
        "reason": reason,
        "engineUrl": engine_url,
        "instance": _instance_for(engine_url),
        "observed": entry,
    }
    line = json.dumps(record, sort_keys=True, default=str)
    with _lock:
        _recent.append(record)
        path = _path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            log.warning("source_observations.unpersisted", path=str(path), error=str(exc))
    return record


def removals(limit: int = 100, instance: str | None = None) -> list[dict]:
    """The most recent removals, newest last, read back from the durable record."""
    path = _path()
    rows: list[dict] = []
    with _lock:
        try:
            with path.open(encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            rows = []
        except OSError:
            rows = list(_recent)
    if instance is not None:
        rows = [r for r in rows if r.get("instance") == instance]
    return rows[-limit:] if limit else rows
