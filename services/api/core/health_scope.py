"""Follow HealthKit scope on the PE and keep localAI's health slots in step.

HEALTH_INTEGRATION_ROADMAP T8. The PE is the HealthKit scope authority
(localHealthkitBridge INGEST_CONTRACT.md, "Scope and resync"). One
reconciliation against an engine:

1. reads that engine's scope (``/api/integrations/healthkit/status``) and the
   family sources the bridge wrote (``/api/sources``, matched by lane region);
2. gives each band whose type is in scope a slot in localAI's band — ``active``
   types graded from current data, ``locked`` types held at their last grade,
   ``removed`` or undeclared types given no slot, their slot source removed:
   absent, not zero;
3. writes the worst-band-wins roll-up into personal_health_baseline's input
   window;
4. asks the PE for a resync of in-scope types that have no current data — a
   consumer request, once per type per scope generation.

A bridge that has never declared scope is open: every band whose family has data
is in scope. An engine that predates the scope contract answers 404 for
``/scope`` fields and is treated the same way.
"""

from __future__ import annotations

import os
import threading

import httpx
import structlog

from core.health_bands import (
    GRADE_VALUE,
    BandTable,
    allocate_slots,
    grade_band,
    load_bands,
    rollup,
    rollup_vector,
)
from core.pe_sources import activate_sensor_source, forget_activation

log = structlog.get_logger()

ROLLUP_SENSOR_ID = "localai_health_rollup"
ROLLUP_REGION = {"offset": 7574, "length": 4}
_SLOT_PREFIX = "localai_health_slot_"
_SLOT_TTL_MS = 86_400_000
# The roll-up sensor as reality_bridge registers it: declared inactive at
# startup, activated by its first value (core/pe_sources.py).
ROLLUP_SENSOR = {
    "sensorId": ROLLUP_SENSOR_ID,
    "name": "localai/health/rollup",
    "region": ROLLUP_REGION,
    "ttlMs": _SLOT_TTL_MS,
}
_TIMEOUT = httpx.Timeout(2.0)

_lock = threading.Lock()
# Per engine (pe_url): slot allocation, held grades for locked bands, and the
# (generation, type) pairs a resync has already been requested for.
_slots: dict[str, dict[str, int]] = {}
_held: dict[str, dict[str, str]] = {}
_resync_asked: dict[str, set[tuple[int, str]]] = {}
_last: dict[str, dict] = {}


def _table() -> BandTable:
    return load_bands()


def _bridge_token() -> str | None:
    return os.getenv("HEALTHKIT_BRIDGE_TOKEN") or None


def _family_values(sources: list[dict], region: tuple[int, int]) -> list[float] | None:
    """Current values of the healthkit family at ``region``, or None.

    A source that has lapsed (inactive) carries no current reading.
    """
    for s in sources:
        r = s.get("region") or {}
        if (
            (r.get("offset"), r.get("length")) == region
            and s.get("origin", "healthkit") == "healthkit"
            and s.get("active")
            and s.get("lastValue")
        ):
            return [float(v) for v in s["lastValue"]]
    return None


def reconcile(
    target: dict, client: httpx.Client | None = None, table: BandTable | None = None
) -> dict:
    """One reconciliation against ``target`` (``{"pe_url": ...}``). Never raises."""
    pe_url = target["pe_url"]
    try:
        table = table or _table()
        if client is None:
            from core.reality_bridge import _SSL_VERIFY  # late: reality_bridge imports this

            with httpx.Client(timeout=_TIMEOUT, verify=_SSL_VERIFY) as own:
                return _reconcile(pe_url, own, table)
        return _reconcile(pe_url, client, table)
    except Exception as exc:
        log.warning("health_scope.reconcile_failed", pe_url=pe_url, error=str(exc))
        return {"pe_url": pe_url, "error": str(exc)}


def _reconcile(pe_url: str, client: httpx.Client, table: BandTable) -> dict:
    status_resp = client.get(f"{pe_url}/api/integrations/healthkit/status")
    status = status_resp.json() if status_resp.status_code == 200 else {}
    scope = status.get("scope") or {"declared": False, "generation": 0, "types": {}}
    bridge_id = status.get("bridgeId") or "healthkit-ios-bridge"
    declared = bool(scope.get("declared"))
    generation = int(scope.get("generation") or 0)
    type_state = {t: (v or {}).get("state") for t, v in (scope.get("types") or {}).items()}

    sources_resp = client.get(f"{pe_url}/api/sources")
    raw = sources_resp.json() if sources_resp.status_code == 200 else {}
    sources = raw if isinstance(raw, list) else raw.get("sources", [])

    with _lock:
        held = _held.setdefault(pe_url, {})
        asked = _resync_asked.setdefault(pe_url, set())
        grades: dict[str, str] = {}
        needs_data: set[str] = set()
        for band in table.bands:
            if band.dormant:
                continue
            state = type_state.get(band.hk_type) if declared else "open"
            family = _family_values(sources, band.lane_region) if band.lane_region else None
            if state in ("active", "open"):
                g = grade_band(band, family, table.min_confidence)
                if g is not None:
                    grades[band.id] = g
                    held[band.id] = g
                elif state == "active":
                    needs_data.add(band.hk_type)
            elif state == "locked" and band.id in held:
                grades[band.id] = held[band.id]  # locked: hold the last grade
            else:
                held.pop(band.id, None)  # removed / not in scope: nothing held
        wanted = sorted(grades)
        before = _slots.get(pe_url, {})
        slots = allocate_slots(before, wanted, table.slot_capacity)
        _slots[pe_url] = slots
        over_capacity = [b for b in wanted if b not in slots]
        resync_types = sorted(t for t in needs_data if (generation, t) not in asked)

    existing = {
        s.get("sensorId"): s for s in sources if s.get("type") == "sensor" and s.get("sensorId")
    }
    # Freed slots leave the PE: absent, not zero.
    for band_id in set(before) - set(slots):
        src = existing.get(_SLOT_PREFIX + band_id)
        if src and src.get("id"):
            client.delete(f"{pe_url}/api/sources/{src['id']}")
            forget_activation(pe_url, _SLOT_PREFIX + band_id)
    for band_id, index in slots.items():
        sid = _SLOT_PREFIX + band_id
        region = {"offset": table.slot_offset + index, "length": 1}
        src = existing.get(sid)
        if src and (src.get("region") or {}) != region and src.get("id"):
            client.delete(f"{pe_url}/api/sources/{src['id']}")  # moved slot: re-declare
            forget_activation(pe_url, sid)
            src = None
        if src is None:
            client.post(
                f"{pe_url}/api/sources",
                json={
                    "type": "sensor",
                    "name": f"localai/health/slot/{band_id}",
                    "region": region,
                    "active": False,
                    "sensorId": sid,
                    "lastValue": [],
                    "lastUpdated": None,
                    "ttlMs": _SLOT_TTL_MS,
                },
            )
        client.post(f"{pe_url}/api/sensors/{sid}", json={"values": [GRADE_VALUE[grades[band_id]]]})
        activate_sensor_source(client, pe_url, sid)

    state = rollup([grades[b] for b in slots], table.rollup)
    write_rollup(client, pe_url, state, existing)

    resync = None
    if resync_types:
        headers = {"Authorization": f"Bearer {_bridge_token()}"} if _bridge_token() else {}
        r = client.post(
            f"{pe_url}/api/integrations/healthkit/resync",
            headers=headers,
            json={
                "bridgeId": bridge_id,
                "types": resync_types,
                "requestedBy": "localAIStack",
                "reason": "in scope with no current data",
            },
        )
        resync = {"status": r.status_code, "types": resync_types}
        # Asked once the PE has answered: 202 accepted, 409 refused (locked or
        # out of scope). Anything else, 401 above all, is retried next pass,
        # and loudly, because a request that never lands looks like silence.
        if r.status_code in (202, 409):
            with _lock:
                asked.update((generation, t) for t in resync_types)
        else:
            log.warning(
                "health_scope.resync_failed",
                pe_url=pe_url,
                status=r.status_code,
                types=resync_types,
                token_configured=bool(_bridge_token()),
            )

    summary = {
        "pe_url": pe_url,
        "bridgeId": bridge_id,
        "declared": declared,
        "generation": generation,
        "slots": {
            b: {"slot": table.slot_offset + i, "grade": grades[b]} for b, i in sorted(slots.items())
        },
        "overCapacity": over_capacity,
        "state": state,
        "resync": resync,
    }
    with _lock:
        _last[pe_url] = summary
    log.info(
        "health_scope.reconciled",
        **{k: v for k, v in summary.items() if k != "slots"},
        slots=len(slots),
    )
    return summary


def write_rollup(
    client: httpx.Client, pe_url: str, state: str | None, existing: dict | None = None
) -> None:
    """Declare the roll-up sensor if absent, write ``state`` one-hot, activate it.

    With no state, what to do depends on whether a state was ever asserted.
    A roll-up that is active holds the last state, so it is retracted with an
    all-zero write; nothing matches, and the old state stops firing. A roll-up
    that never carried a state stays silent. Activating it only to hold zeros
    would change what every engine perceives before any health data exists,
    which is the pre-data contribution core/pe_sources.py rules out.
    """
    if existing is None:
        raw = client.get(f"{pe_url}/api/sources").json()
        items = raw if isinstance(raw, list) else raw.get("sources", [])
        existing = {s.get("sensorId"): s for s in items if s.get("type") == "sensor"}
    if state is None and not (existing.get(ROLLUP_SENSOR_ID) or {}).get("active"):
        return
    if ROLLUP_SENSOR_ID not in existing:
        client.post(
            f"{pe_url}/api/sources",
            json={
                "type": "sensor",
                **ROLLUP_SENSOR,
                "active": False,
                "lastValue": [],
                "lastUpdated": None,
            },
        )
    client.post(f"{pe_url}/api/sensors/{ROLLUP_SENSOR_ID}", json={"values": rollup_vector(state)})
    activate_sensor_source(client, pe_url, ROLLUP_SENSOR_ID)


def last_summary(pe_url: str) -> dict | None:
    with _lock:
        return _last.get(pe_url)


def reset() -> None:
    """Forget all per-engine state (tests, and a universe restart)."""
    with _lock:
        _slots.clear()
        _held.clear()
        _resync_asked.clear()
        _last.clear()
