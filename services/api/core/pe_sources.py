"""PE sensor-source lifecycle: declared inactive, activated by data flow.

An active source contributes its region to every vector the PE assembles,
whether or not it has ever been fed. Registering localAI sensors active
therefore changed what an engine perceived the moment localAIStack connected
to it — on a three-engine deployment nine sensors per engine went active
carrying nothing, and the engines' input vectors diverged before any localAI
traffic existed to explain the difference.

The rule these helpers implement: a source is *declared* at registration and
*activated* by its first value. Declaration is safe to fan out to every engine;
activation follows that engine's own data flow and nobody else's.

Both bridges in this service (``reality_bridge`` and
``patient_wellness_bridge``) register PE sources, so this lives here rather
than in either one.
"""

from __future__ import annotations

import time

import httpx
import structlog

log = structlog.get_logger()

# (pe_url, sensorId) -> (written_at_monotonic, ttl_ms) for sources this bridge
# activated. Doubles as the activation memo — activation is idempotent, so the
# entry mainly avoids paying a GET+PATCH on every write — and as the record
# needed to notice when the value behind an activation has lapsed.
_activated: dict[tuple[str, str], tuple[float, float]] = {}


def clear_activation_memo() -> None:
    """Forget which sources are active.

    Call when re-registering: a PE that restarted or was pruned holds inactive
    sources again, and a stale memo would suppress reactivation.
    """
    _activated.clear()


def forget_activation(pe_url: str, sensor_id: str) -> None:
    _activated.pop((pe_url, sensor_id), None)


def get_sensor_sources(client: httpx.Client, pe_url: str) -> dict:
    """``{sensorId: source}`` for the sensor sources this PE holds.

    The full record, not just the id: activation PATCHes the source's own
    ``id`` (which differs from its ``sensorId``), and deciding whether a source
    has ever carried data needs its ``lastValue``.
    """
    try:
        resp = client.get(f"{pe_url}/api/sources")
        resp.raise_for_status()
        return {
            s["sensorId"]: s
            for s in resp.json().get("sources", [])
            if s.get("type") == "sensor" and s.get("sensorId")
        }
    except Exception:
        return {}


def activate_sensor_source(
    client: httpx.Client, pe_url: str, sensor_id: str, ttl_ms: float | None = None
) -> None:
    """Activate a sensor source, at most once per (engine, sensor).

    Call *after* writing the value, never before: activating first would leave
    the source active holding an empty value for the width of a round trip,
    which is the pre-data contribution this whole rule removes.

    `ttl_ms` records how long the value stays good, so `deactivate_lapsed` can
    take the activation back when it does not.
    """
    key = (pe_url, sensor_id)
    already = key in _activated
    # Refresh the write time on every call: the value behind the activation is
    # new even when the activation itself is not.
    _activated[key] = (time.monotonic(), float(ttl_ms) if ttl_ms else 0.0)
    if already:
        return
    source = get_sensor_sources(client, pe_url).get(sensor_id)
    if not source:
        _activated.pop(key, None)
        return
    if ttl_ms is None and source.get("ttlMs"):
        _activated[key] = (time.monotonic(), float(source["ttlMs"]))
    if source.get("active"):
        return
    try:
        r = client.patch(f"{pe_url}/api/sources/{source['id']}", json={"active": True})
        r.raise_for_status()
        log.info("pe_sources.activated", sensor_id=sensor_id, pe_url=pe_url)
    except Exception as exc:
        _activated.pop(key, None)
        log.warning(
            "pe_sources.activate_failed", sensor_id=sensor_id, pe_url=pe_url, error=str(exc)
        )


def deactivate_lapsed(client: httpx.Client, pe_url: str) -> int:
    """Deactivate sources whose value has aged past its TTL.

    Activation without this is one-way, and an expired sensor is not silent: the
    PE returns a zero vector for it and `assemble_vector` writes those zeros
    because the source is still active, so a lapsed sensor stamps zeros over its
    region on every push. An inactive source leaves the region alone. Silence
    and an assertion of zero are different perceptions (#54).

    Lapse is computed from what this bridge wrote and when, so the common case —
    nothing has lapsed — costs no HTTP at all.
    """
    now = time.monotonic()
    lapsed = [
        (key, sid)
        for key, (written_at, ttl_ms) in _activated.items()
        for (url, sid) in [key]
        if url == pe_url and ttl_ms > 0 and (now - written_at) * 1000.0 > ttl_ms
    ]
    if not lapsed:
        return 0

    sources = get_sensor_sources(client, pe_url)
    deactivated = 0
    for key, sensor_id in lapsed:
        source = sources.get(sensor_id)
        if not source:
            _activated.pop(key, None)
            continue
        try:
            r = client.patch(f"{pe_url}/api/sources/{source['id']}", json={"active": False})
            r.raise_for_status()
            _activated.pop(key, None)
            deactivated += 1
            log.info("pe_sources.deactivated_lapsed", sensor_id=sensor_id, pe_url=pe_url)
        except Exception as exc:
            log.warning(
                "pe_sources.deactivate_failed",
                sensor_id=sensor_id,
                pe_url=pe_url,
                error=str(exc),
            )
    return deactivated


def quiesce_valueless_sensors(
    client: httpx.Client, sensor_ids: list[str], existing: dict, pe_url: str
) -> int:
    """Deactivate already-registered sensors that carry no value.

    Registration skips sources that already exist, so a PE carried over from a
    run that registered them active would keep contributing empty regions
    forever. This makes the inactive-until-data rule hold on deployments that
    are already up, not only for freshly created sources.

    A sensor carrying a value is live data flow and is left alone.
    """
    quiesced = 0
    for sensor_id in sensor_ids:
        source = existing.get(sensor_id)
        if not source or not source.get("active") or source.get("lastValue"):
            continue
        try:
            r = client.patch(f"{pe_url}/api/sources/{source['id']}", json={"active": False})
            r.raise_for_status()
            forget_activation(pe_url, sensor_id)
            quiesced += 1
        except Exception as exc:
            log.warning(
                "pe_sources.quiesce_failed", sensor_id=sensor_id, pe_url=pe_url, error=str(exc)
            )
    return quiesced
