"""
HealthKit scope follower — core/health_scope.py (HEALTH_INTEGRATION_ROADMAP T8).

A fake PE holds sources and a scope block. Each test drives one reconcile()
and checks what reached the PE: which slot sources exist, what the roll-up
says, and whether a resync was asked for.
"""

from __future__ import annotations

import itertools

import pytest

from core import health_scope

PE = "http://pe"
BP = "HKCorrelationTypeIdentifierBloodPressure"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
WORKOUT = "HKWorkoutTypeIdentifierWorkout"


class _Resp:
    def __init__(self, status: int = 200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class FakePE:
    """Just enough PE: /status, /api/sources CRUD, /api/sensors, /resync."""

    def __init__(self, scope: dict | None = None, status_code: int = 200):
        self.scope = scope
        self.status_code = status_code
        self.sources: dict[str, dict] = {}
        self.resyncs: list[dict] = []
        self._ids = itertools.count(1)

    def family(self, offset: int, values: list[float], active: bool = True):
        sid = f"hk-{offset}"
        self.sources[sid] = {
            "id": sid,
            "type": "sensor",
            "sensorId": f"healthkit.{offset}",
            "origin": "healthkit",
            "region": {"offset": offset, "length": 4},
            "active": active,
            "lastValue": values,
        }

    def by_sensor(self, sensor_id: str) -> dict | None:
        return next((s for s in self.sources.values() if s.get("sensorId") == sensor_id), None)

    # httpx.Client surface
    def get(self, url, **_):
        if url.endswith("/api/integrations/healthkit/status"):
            if self.status_code != 200:
                return _Resp(self.status_code, {"error": "not found"})
            body = {"bridgeId": "healthkit-ios-bridge"}
            if self.scope is not None:
                body["scope"] = self.scope
            return _Resp(200, body)
        if url.endswith("/api/sources"):
            return _Resp(200, {"sources": list(self.sources.values())})
        return _Resp(404)

    def post(self, url, json=None, headers=None, **_):
        if url.endswith("/api/sources"):
            sid = f"src-{next(self._ids)}"
            self.sources[sid] = {**json, "id": sid}
            return _Resp(201, self.sources[sid])
        if "/api/sensors/" in url:
            src = self.by_sensor(url.rsplit("/", 1)[1])
            if not src:
                return _Resp(404)
            src["lastValue"] = json["values"]
            return _Resp(200)
        if url.endswith("/api/integrations/healthkit/resync"):
            self.resyncs.append({"body": json, "headers": headers or {}})
            return _Resp(202, {"success": True})
        return _Resp(404)

    def patch(self, url, json=None, **_):
        self.sources[url.rsplit("/", 1)[1]].update(json)
        return _Resp(200)

    def delete(self, url, **_):
        self.sources.pop(url.rsplit("/", 1)[1], None)
        return _Resp(204)


@pytest.fixture(autouse=True)
def _clean():
    from core import pe_sources

    health_scope.reset()
    pe_sources.clear_activation_memo()
    yield
    health_scope.reset()


def _bp(pulse: float, sys: float = 118, dia: float = 76, conf: float = 1.0):
    return [sys / 200, dia / 120, pulse / 200, conf]


def _sleep(hours: float, conf: float = 1.0):
    return [hours / 10, 0.2, 0.4, conf]


def _slots(pe: FakePE) -> dict[str, dict]:
    return {
        s["sensorId"][len("localai_health_slot_") :]: s
        for s in pe.sources.values()
        if (s.get("sensorId") or "").startswith("localai_health_slot_")
    }


def _rollup(pe: FakePE) -> list[float] | None:
    src = pe.by_sensor("localai_health_rollup")
    return src["lastValue"] if src else None


def _scope(generation: int, **types: str) -> dict:
    return {
        "declared": True,
        "generation": generation,
        "types": {t: {"state": st} for t, st in types.items()},
        "resyncRequests": [],
    }


# ── open until first declaration ──────────────────────────────────────────────


def test_undeclared_bridge_is_open_every_band_with_data_gets_a_slot():
    pe = FakePE(scope={"declared": False, "generation": 0, "types": {}})
    pe.family(4320, _bp(72))
    pe.family(4340, _sleep(7.5))
    s = health_scope.reconcile({"pe_url": PE}, client=pe)
    assert set(_slots(pe)) == {"pulse", "blood_pressure", "sleep"}
    assert s["state"] == "thriving"
    assert _rollup(pe) == [1.0, 0.0, 0.0, 0.0]
    assert pe.resyncs == []  # open: nothing is owed data


def test_engine_without_scope_contract_is_treated_as_open():
    pe = FakePE(status_code=404)
    pe.family(4340, _sleep(5.5))
    s = health_scope.reconcile({"pe_url": PE}, client=pe)
    assert set(_slots(pe)) == {"sleep"}
    assert s["state"] == "balanced"


def test_slot_sources_are_activated_after_their_first_value():
    pe = FakePE(scope=None)
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    slot = _slots(pe)["sleep"]
    assert slot["lastValue"] == [1.0]
    assert slot["active"] is True
    assert slot["region"] == {"offset": 7600, "length": 1}


def test_no_graded_band_ever_leaves_the_rollup_silent():
    """Nothing to grade and no prior state: declared, not activated, no value."""
    pe = FakePE(scope=None)
    pe.family(4340, _sleep(7.5, conf=0.1))  # below the confidence floor
    s = health_scope.reconcile({"pe_url": PE}, client=pe)
    assert _slots(pe) == {}
    assert s["state"] is None
    rollup = pe.by_sensor("localai_health_rollup")
    assert rollup is None or (not rollup.get("active") and not rollup.get("lastValue"))


def test_losing_every_graded_band_retracts_the_rollup_with_zeros():
    """A state was asserted; its data is gone. Zeros retract it so it stops firing."""
    pe = FakePE(scope=None)
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert _rollup(pe) == [1.0, 0.0, 0.0, 0.0]
    pe.family(4340, _sleep(7.5), active=False)  # lapsed
    s = health_scope.reconcile({"pe_url": PE}, client=pe)
    assert s["state"] is None
    assert _rollup(pe) == [0.0, 0.0, 0.0, 0.0]
    assert _slots(pe) == {}  # the sleep slot left with its data


# ── add / lock / remove ───────────────────────────────────────────────────────


def test_declared_scope_limits_slots_to_active_types():
    pe = FakePE(scope=_scope(1, **{SLEEP: "active"}))
    pe.family(4320, _bp(72))
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert set(_slots(pe)) == {"sleep"}


def test_locked_type_holds_its_last_grade():
    pe = FakePE(scope=_scope(1, **{BP: "active"}))
    pe.family(4320, _bp(110))  # watch
    health_scope.reconcile({"pe_url": PE}, client=pe)
    pe.scope = _scope(2, **{BP: "locked"})
    pe.family(4320, _bp(72), active=False)  # no current reading while locked
    s = health_scope.reconcile({"pe_url": PE}, client=pe)
    assert s["slots"]["pulse"]["grade"] == "watch"
    assert _slots(pe)["pulse"]["lastValue"] == [0.5]
    assert pe.resyncs == []  # locked is not owed data


def test_removed_type_loses_its_slot_absent_not_zero():
    pe = FakePE(scope=_scope(1, **{BP: "active", SLEEP: "active"}))
    pe.family(4320, _bp(72))
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert set(_slots(pe)) == {"pulse", "blood_pressure", "sleep"}
    sleep_slot = _slots(pe)["sleep"]["region"]

    pe.scope = _scope(2, **{BP: "removed", SLEEP: "active"})
    del pe.sources["hk-4320"]  # the PE removes the type's sources on remove
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert set(_slots(pe)) == {"sleep"}
    assert _slots(pe)["sleep"]["region"] == sleep_slot  # survivors keep their slot


def test_re_added_type_takes_the_lowest_free_slot():
    pe = FakePE(scope=_scope(1, **{SLEEP: "active"}))
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    pe.scope = _scope(2, **{SLEEP: "active", BP: "active"})
    pe.family(4320, _bp(72))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    offsets = sorted(s["region"]["offset"] for s in _slots(pe).values())
    assert offsets == [7600, 7601, 7602]


# ── resync: a consumer request, through the PE ────────────────────────────────


def test_active_type_without_data_asks_for_resync_once_per_generation(monkeypatch):
    monkeypatch.setenv("HEALTHKIT_BRIDGE_TOKEN", "tok")
    pe = FakePE(scope=_scope(3, **{SLEEP: "active", WORKOUT: "active"}))
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert len(pe.resyncs) == 1
    req = pe.resyncs[0]
    assert req["body"]["types"] == [WORKOUT]
    assert req["body"]["requestedBy"] == "localAIStack"
    assert req["headers"]["Authorization"] == "Bearer tok"

    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert len(pe.resyncs) == 1  # same generation: not asked again

    pe.scope = _scope(4, **{SLEEP: "active", WORKOUT: "active"})
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert len(pe.resyncs) == 2  # new generation: asked again


def test_reconcile_never_raises():
    class _Down:
        def get(self, *a, **k):
            raise RuntimeError("connection refused")

    s = health_scope.reconcile({"pe_url": PE}, client=_Down())
    assert "error" in s


def test_reconcile_never_raises_on_a_missing_band_table(monkeypatch):
    def _missing():
        raise FileNotFoundError("data/health/health_bands.json")

    monkeypatch.setattr(health_scope, "_table", _missing)
    s = health_scope.reconcile({"pe_url": PE}, client=FakePE())
    assert "health_bands.json" in s["error"]
