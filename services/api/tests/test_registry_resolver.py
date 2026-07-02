"""Unit tests for core.registry_resolver (RealityEngine_CI#44).

No network: _probe_health and _fetch_registry are monkeypatched. Each test
covers one rung of the resolution ladder:

  env targets alive            → env wins (registry never consulted)
  env dead, registry has live  → first healthy running instance
  env dead, registry all dead  → first running instance anyway (visible probes)
  env dead, no registry        → env targets unchanged
"""

from __future__ import annotations

import pytest

from core import registry_resolver


REGISTRY = {
    "host": "192.168.1.16",
    "instances": [
        {
            "id": "cpp-1",
            "runtime": "cpp",
            "re_url": "http://192.168.1.16:5301",
            "pe_url": "http://192.168.1.16:5300",
            "status": "running",
        },
        {
            "id": "scala-1",
            "runtime": "scala",
            "re_url": "http://192.168.1.16:5101",
            "pe_url": "http://192.168.1.16:5100",
            "status": "running",
        },
        {
            "id": "lsp-1",
            "runtime": "lsp",
            "re_url": "http://192.168.1.16:5601",
            "pe_url": "http://192.168.1.16:5600",
            "status": "stopped",
        },
    ],
}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(
        registry_resolver, "_cache", {"at": 0.0, "targets": None}
    )
    monkeypatch.setenv("RE_REGISTRY_URL", "http://registry.test/re-registry.json")


def _patch_probes(monkeypatch, alive: set[str], registry: dict | None = REGISTRY):
    monkeypatch.setattr(
        registry_resolver, "_probe_health", lambda url: url in alive
    )
    monkeypatch.setattr(
        registry_resolver, "_fetch_registry", lambda url: registry
    )


def test_env_targets_win_when_alive(monkeypatch):
    s = registry_resolver.get_settings()
    _patch_probes(monkeypatch, alive={s.re_url, s.pe_url})

    t = registry_resolver.resolve_bridge_targets(force_refresh=True)
    assert t["source"] == "env"
    assert t["re_url"] == s.re_url
    assert t["pe_url"] == s.pe_url
    assert t["instance"] is None


def test_dead_env_retargets_to_first_healthy_registry_instance(monkeypatch):
    _patch_probes(
        monkeypatch,
        alive={"http://192.168.1.16:5101", "http://192.168.1.16:5100"},
    )

    t = registry_resolver.resolve_bridge_targets(force_refresh=True)
    assert t["source"] == "registry"
    assert t["instance"] == "scala-1"
    assert t["re_url"] == "http://192.168.1.16:5101"
    assert t["pe_url"] == "http://192.168.1.16:5100"


def test_dead_env_and_dead_registry_uses_first_running_instance(monkeypatch):
    _patch_probes(monkeypatch, alive=set())

    t = registry_resolver.resolve_bridge_targets(force_refresh=True)
    assert t["source"] == "registry"
    assert t["instance"] == "cpp-1"


def test_stopped_instances_are_skipped(monkeypatch):
    _patch_probes(
        monkeypatch,
        alive={"http://192.168.1.16:5601", "http://192.168.1.16:5600"},
    )

    t = registry_resolver.resolve_bridge_targets(force_refresh=True)
    # lsp-1 is healthy but stopped in the registry — never chosen
    assert t["instance"] != "lsp-1"


def test_no_registry_falls_back_to_env(monkeypatch):
    s = registry_resolver.get_settings()
    _patch_probes(monkeypatch, alive=set(), registry=None)

    t = registry_resolver.resolve_bridge_targets(force_refresh=True)
    assert t["source"] == "env"
    assert t["re_url"] == s.re_url
    assert t["pe_url"] == s.pe_url


def test_result_is_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    def _probe(url):
        calls["n"] += 1
        return False

    monkeypatch.setattr(registry_resolver, "_probe_health", _probe)
    monkeypatch.setattr(registry_resolver, "_fetch_registry", lambda url: None)

    registry_resolver.resolve_bridge_targets(force_refresh=True)
    first = calls["n"]
    registry_resolver.resolve_bridge_targets()
    assert calls["n"] == first, "cached result must not re-probe within TTL"
