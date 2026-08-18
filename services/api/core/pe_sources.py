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

import httpx
import structlog

log = structlog.get_logger()

# (pe_url, sensorId) pairs already activated by a write — a memo, not state:
# activation is idempotent, this only avoids paying a GET+PATCH on every write.
_activated: set[tuple[str, str]] = set()


def clear_activation_memo() -> None:
    """Forget which sources are active.

    Call when re-registering: a PE that restarted or was pruned holds inactive
    sources again, and a stale memo would suppress reactivation.
    """
    _activated.clear()


def forget_activation(pe_url: str, sensor_id: str) -> None:
    _activated.discard((pe_url, sensor_id))


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


def activate_sensor_source(client: httpx.Client, pe_url: str, sensor_id: str) -> None:
    """Activate a sensor source, at most once per (engine, sensor).

    Call *after* writing the value, never before: activating first would leave
    the source active holding an empty value for the width of a round trip,
    which is the pre-data contribution this whole rule removes.
    """
    key = (pe_url, sensor_id)
    if key in _activated:
        return
    source = get_sensor_sources(client, pe_url).get(sensor_id)
    if not source:
        return
    if source.get("active"):
        _activated.add(key)
        return
    try:
        r = client.patch(f"{pe_url}/api/sources/{source['id']}", json={"active": True})
        r.raise_for_status()
        _activated.add(key)
        log.info("pe_sources.activated", sensor_id=sensor_id, pe_url=pe_url)
    except Exception as exc:
        log.warning(
            "pe_sources.activate_failed", sensor_id=sensor_id, pe_url=pe_url, error=str(exc)
        )


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
