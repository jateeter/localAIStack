"""Unit tests for engine affinity and inactive-until-data source registration.

Two properties, both observed failing on the live three-engine stack:

  * lsp-1 and scala-1 each held nine localAI sensor sources that were **active
    and carrying no value**. An active source contributes its region to every
    assembled vector, so connecting localAIStack changed what those engines
    perceived before any localAI traffic existed — 27 cells of input-vector
    divergence across engines, all inside localAI sensor regions.

  * The write, the push, and the read of the resulting perceptual space each
    resolved a target independently, through a resolver that caches for 30
    seconds. An expiry between them decoded one engine's routing from a vector
    written into another's.

No network anywhere: httpx clients are fakes.

Run: cd services/api && python3 -m pytest tests/test_bridge_binding.py
"""

from __future__ import annotations

import pytest

from core import bridge_binding, pe_sources

CPP = {"re_url": "http://cpp:5301", "pe_url": "http://cpp:5300", "instance": "cpp-1"}
LSP = {"re_url": "http://lsp:5601", "pe_url": "http://lsp:5600", "instance": "lsp-1"}


@pytest.fixture(autouse=True)
def _clean_context():
    bridge_binding.set_initiating_instance(None)
    pe_sources.clear_activation_memo()
    yield
    bridge_binding.set_initiating_instance(None)
    pe_sources.clear_activation_memo()


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(
        bridge_binding,
        "resolve_all_bridge_targets",
        lambda: [dict(CPP, healthy=True), dict(LSP, healthy=True)],
    )
    monkeypatch.setattr(bridge_binding, "resolve_bridge_targets", lambda: dict(CPP))


# ── engine affinity ──────────────────────────────────────────────────────────


def test_initiating_engine_is_bound(registry):
    bridge_binding.set_initiating_instance("lsp-1")
    assert bridge_binding.bind()["pe_url"] == LSP["pe_url"]


def test_binding_survives_resolver_change(registry, monkeypatch):
    """The write and the read that interprets it must land on one engine.

    Re-resolving mid-interaction is the actual failure: the resolver's cache
    expires on a timer that has nothing to do with request boundaries.
    """
    bridge_binding.set_initiating_instance("lsp-1")
    first = bridge_binding.bind()

    # The resolver now says something different — a cache expiry, or the
    # registry changing under us.
    monkeypatch.setattr(bridge_binding, "resolve_all_bridge_targets", lambda: [dict(CPP)])
    monkeypatch.setattr(bridge_binding, "resolve_bridge_targets", lambda: dict(CPP))

    assert bridge_binding.bind() == first


def test_unknown_instance_does_not_fall_back(registry):
    """A named engine that is not running resolves to nothing.

    Substituting a different engine is exactly the cross-talk this prevents;
    the caller degrades to its safe default instead.
    """
    bridge_binding.set_initiating_instance("scala-1")
    assert bridge_binding.bind() is None


def test_unknown_instance_resolves_once(monkeypatch):
    """Resolved-to-nothing is a result, not a reason to keep asking.

    Every step of an interaction calls bind(); re-resolving each time re-probes
    the registry and re-logs the same warning several times per request.
    """
    calls = []
    monkeypatch.setattr(
        bridge_binding, "resolve_all_bridge_targets", lambda: calls.append(1) or [dict(CPP)]
    )
    bridge_binding.set_initiating_instance("scala-1")
    for _ in range(4):
        assert bridge_binding.bind() is None
    assert len(calls) == 1


def test_no_initiator_uses_the_selected_target(registry):
    assert bridge_binding.bind()["instance"] == "cpp-1"


def test_explicit_instance_does_not_redirect_the_context(registry):
    """An addressed call is one call, not a change of engine for the request."""
    bridge_binding.set_initiating_instance("lsp-1")
    assert bridge_binding.bind()["instance"] == "lsp-1"
    assert bridge_binding.bind(instance="cpp-1")["instance"] == "cpp-1"
    assert bridge_binding.bind()["instance"] == "lsp-1"


def test_new_initiator_rebinds(registry):
    bridge_binding.set_initiating_instance("cpp-1")
    assert bridge_binding.bind()["instance"] == "cpp-1"
    bridge_binding.set_initiating_instance("lsp-1")
    assert bridge_binding.bind()["instance"] == "lsp-1"


# ── inactive until data flow ─────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload or {}
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Minimal httpx.Client stand-in recording PATCHes."""

    def __init__(self, sources):
        self.sources = sources
        self.patches: list[tuple[str, dict]] = []

    def get(self, url):
        return FakeResponse({"sources": self.sources})

    def patch(self, url, json):
        self.patches.append((url, json))
        for s in self.sources:
            if url.endswith(f"/api/sources/{s['id']}"):
                s.update(json)
        return FakeResponse()


def _source(sid, active, last_value):
    return {
        "id": f"src-{sid}",
        "type": "sensor",
        "sensorId": sid,
        "active": active,
        "lastValue": last_value,
    }


def test_activation_is_one_patch_per_engine_and_sensor():
    client = FakeClient([_source("localai_rag_retrieval", False, [])])
    for _ in range(3):
        pe_sources.activate_sensor_source(client, CPP["pe_url"], "localai_rag_retrieval")
    assert client.patches == [
        (f"{CPP['pe_url']}/api/sources/src-localai_rag_retrieval", {"active": True})
    ]


def test_activation_is_per_engine():
    """cpp seeing data says nothing about whether lsp has."""
    cpp_client = FakeClient([_source("localai_rag_retrieval", False, [])])
    lsp_client = FakeClient([_source("localai_rag_retrieval", False, [])])
    pe_sources.activate_sensor_source(cpp_client, CPP["pe_url"], "localai_rag_retrieval")
    pe_sources.activate_sensor_source(lsp_client, LSP["pe_url"], "localai_rag_retrieval")
    assert len(cpp_client.patches) == 1
    assert len(lsp_client.patches) == 1


def test_quiesce_deactivates_active_sources_carrying_nothing():
    """The exact state found on lsp-1 and scala-1: active, never written."""
    sources = [
        _source("a", True, []),
        _source("b", True, [0.5]),  # carrying data — live flow, left alone
        _source("c", False, []),  # already correct
    ]
    client = FakeClient(sources)
    existing = {s["sensorId"]: s for s in sources}

    n = pe_sources.quiesce_valueless_sensors(client, ["a", "b", "c"], existing, CPP["pe_url"])

    assert n == 1
    assert client.patches == [(f"{CPP['pe_url']}/api/sources/src-a", {"active": False})]


def test_quiesce_clears_the_activation_memo():
    """A quiesced source must be activatable again by its next write."""
    sources = [_source("a", True, [])]
    client = FakeClient(sources)
    pe_sources.activate_sensor_source(client, CPP["pe_url"], "a")  # memoized as active

    pe_sources.quiesce_valueless_sensors(
        client, ["a"], {s["sensorId"]: s for s in sources}, CPP["pe_url"]
    )
    client.patches.clear()
    pe_sources.activate_sensor_source(client, CPP["pe_url"], "a")

    assert client.patches == [(f"{CPP['pe_url']}/api/sources/src-a", {"active": True})]
