"""
HEALTH_INTEGRATION_ROADMAP T4 and T6, and the follower/simulator boundary.

  (1) T4: chat's health state comes from the scope follower first (no network),
      then from an RE read cached per engine.
  (2) The follower retracts only a roll-up it asserted itself, so the
      simulator's push_health_signal() is not undone 30 s later.
  (3) T6: /health reports CareKit state, the CareKit sensor count, and the
      follower's last reconciliation (needs fastapi; skips without it).
"""

from __future__ import annotations

import asyncio

import pytest

from core import health_scope, reality_bridge
from tests.test_health_scope import PE, FakePE, _rollup, _sleep


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from core import pe_sources

    health_scope.reset()
    pe_sources.clear_activation_memo()
    reality_bridge._health_state_cache.clear()
    monkeypatch.setattr(reality_bridge, "bind", lambda: {"pe_url": PE, "re_url": "http://re"})
    yield
    health_scope.reset()


# ── (1) T4 ──────────────────────────────────────────────────────────────────


def test_chat_state_comes_from_the_follower_without_network(monkeypatch):
    pe = FakePE(scope=None)
    pe.family(4340, _sleep(5.5))  # sleep watch → balanced
    health_scope.reconcile({"pe_url": PE}, client=pe)

    def _no_network():
        raise AssertionError("fell back to the RE although the follower knew the state")

    monkeypatch.setattr(reality_bridge, "get_current_health_state", _no_network)
    assert reality_bridge.current_health_state() == "balanced"


def test_chat_state_falls_back_to_a_cached_re_read(monkeypatch):
    calls = []

    def _read():
        calls.append(1)
        return "watch"

    monkeypatch.setattr(reality_bridge, "get_current_health_state", _read)
    assert reality_bridge.current_health_state() == "watch"
    assert reality_bridge.current_health_state() == "watch"
    assert len(calls) == 1, "second turn within the TTL must not re-read the RE"


def test_cache_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(
        reality_bridge, "get_current_health_state", lambda: calls.append(1) or "thriving"
    )
    monkeypatch.setattr(reality_bridge, "_HEALTH_STATE_CACHE_S", 0.0)
    reality_bridge.current_health_state()
    reality_bridge.current_health_state()
    assert len(calls) == 2


# ── (2) follower vs simulator ───────────────────────────────────────────────


def test_follower_does_not_retract_a_rollup_it_did_not_assert():
    """push_health_signal wrote 'watch'; no HealthKit data; the follower leaves it."""
    pe = FakePE(scope=None)
    health_scope.write_rollup(pe, PE, "watch")  # what push_health_signal does
    assert _rollup(pe) == [0.0, 0.0, 1.0, 0.0]
    s = health_scope.reconcile({"pe_url": PE}, client=pe)
    assert s["state"] is None
    assert _rollup(pe) == [0.0, 0.0, 1.0, 0.0]


def test_follower_still_retracts_its_own_state():
    pe = FakePE(scope=None)
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    pe.family(4340, _sleep(7.5), active=False)
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert _rollup(pe) == [0.0, 0.0, 0.0, 0.0]
    # Retracted once; a later simulator write is again not the follower's.
    health_scope.write_rollup(pe, PE, "thriving")
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert _rollup(pe) == [1.0, 0.0, 0.0, 0.0]


# ── (3) T6 ──────────────────────────────────────────────────────────────────


class _AsyncResp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _AsyncClient:
    def __init__(self, routes):
        self._routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **_):
        for suffix, body in self._routes.items():
            if url.endswith(suffix):
                return _AsyncResp(body)
        raise AssertionError(url)


def test_health_reports_carekit_state_and_scope(monkeypatch):
    pytest.importorskip("fastapi")
    from routers import health as health_router

    ps = [0.0] * 7680
    ps[7578] = 1.0  # health: thriving
    ps[7588] = 1.0  # carekit output [7586:7590] → lapsed
    routes = {
        "/api/perceptual-simulation/state": {"state": {"perceptualSpace": ps}},
        "/api/machines": {"machines": [{"name": "x"}]},
        "/api/sources": {
            "sources": [
                {"sensorId": "localai_health_rollup"},
                {"sensorId": "localai_carekit_med_adherence"},
                {"sensorId": "localai_carekit_task_completion"},
            ]
        },
    }
    monkeypatch.setattr(health_router.httpx, "AsyncClient", lambda *a, **k: _AsyncClient(routes))
    pe = FakePE(scope=None)
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)

    re = asyncio.run(health_router._check_re("http://re", True))
    assert re["health_state"] == "thriving"
    assert re["carekit_state"] == reality_bridge.get_carekit_state(ps)
    assert re["carekit_state"] is not None

    pe_status = asyncio.run(health_router._check_pe(PE, True))
    assert pe_status["carekit_sensors"] == 2
    assert pe_status["health_scope"]["state"] == "thriving"
