"""Unit tests for core.model_registry.

No network and no Ollama: installed_tags is monkeypatched. Two groups —

  the shipped config/models.registry.json parses and stays internally coherent
  the available/selected/installed join reports each state distinguishably

resolve_models is a coroutine; tests drive it through asyncio.run rather than
pulling in pytest-asyncio, since pytest is the repo's only dev dependency.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from core import model_registry as mr

FIXTURE = {
    "_comment": ["stripped by the loader"],
    "version": "9.9",
    "runtime": "ollama",
    "roles": {"llm": {"default": "big"}, "embedding": {"default": "embedder"}},
    "models": [
        {
            "id": "big",
            "tag": "big:70b",
            "name": "Big",
            "publisher": "Test",
            "family": "test",
            "role": "llm",
            "source": {"kind": "ollama-library", "ref": "library/big"},
            "size_gb": 40.0,
            "min_ram_gb": 64,
            "install": "on-demand",
            "status": "supported",
        },
        {
            "id": "small",
            "tag": "small:3b",
            "aliases": ["small:3b-instruct-q4_K_M"],
            "name": "Small",
            "publisher": "Test",
            "family": "test",
            "role": "llm",
            "source": {"kind": "ollama-library", "ref": "library/small"},
            "size_gb": 2.0,
            "min_ram_gb": 8,
            "install": "selected",
            "status": "supported",
        },
        {
            "id": "embedder",
            "tag": "embedder",
            "name": "Embedder",
            "publisher": "Test",
            "family": "test",
            "role": "embedding",
            "source": {"kind": "ollama-library", "ref": "library/embedder"},
            "embed_dim": 768,
            "min_ram_gb": 8,
            "install": "selected",
            "status": "supported",
        },
    ],
}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(mr, "_cache", {"at": 0.0, "registry": None, "path": None})


@pytest.fixture
def fixture_registry(tmp_path, monkeypatch):
    path = tmp_path / "models.registry.json"
    path.write_text(json.dumps(FIXTURE))
    monkeypatch.setenv("MODELS_REGISTRY_PATH", str(path))
    return path


# ── The shipped registry ──────────────────────────────────────────────────────


def test_shipped_registry_loads():
    reg = mr.load_registry(force_refresh=True)
    assert reg.models, "registry ships with no models"
    assert reg.runtime == "ollama"


def test_shipped_registry_ids_and_tags_are_unique():
    reg = mr.load_registry(force_refresh=True)
    ids = [m.id for m in reg.models]
    tags = [m.tag for m in reg.models]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"
    assert len(tags) == len(set(tags)), f"duplicate tags: {tags}"


def test_shipped_registry_fields_are_in_range():
    reg = mr.load_registry(force_refresh=True)
    for m in reg.models:
        assert m.role in ("llm", "embedding"), f"{m.id}: bad role {m.role}"
        assert m.install in ("selected", "on-demand", "modelfile"), f"{m.id}: bad install"
        assert m.status in ("supported", "experimental", "broken"), f"{m.id}: bad status"
        assert m.source.kind in ("ollama-library", "modelfile"), f"{m.id}: bad source kind"
        if m.role == "embedding":
            assert m.embed_dim, f"{m.id}: embedding model must declare embed_dim"
        if m.source.kind == "modelfile":
            assert m.source.modelfile, f"{m.id}: modelfile source must name a Modelfile"


def test_shipped_registry_role_defaults_exist():
    reg = mr.load_registry(force_refresh=True)
    for role in ("llm", "embedding"):
        default = reg.default_for(role)
        assert default, f"no default declared for role {role}"
        entry = reg.by_id(default)
        assert entry is not None, f"default '{default}' is not a registered id"
        assert entry.role == role


def test_nemotron_is_registered():
    reg = mr.load_registry(force_refresh=True)
    entry = reg.by_id("nemotron-3-nano-4b")
    assert entry is not None, "NVIDIA Nemotron is missing from the registry"
    assert entry.tag == "nemotron-3-nano:4b"
    assert entry.publisher == "NVIDIA"
    assert entry.role == "llm"
    # Registered specifically for tool-using agent work on a 16 GB host.
    assert "tools" in entry.capabilities
    assert entry.min_ram_gb is not None and entry.min_ram_gb <= 16


# ── Loader behaviour ──────────────────────────────────────────────────────────


def test_comment_keys_are_stripped(fixture_registry):
    reg = mr.load_registry(force_refresh=True)
    assert reg.version == "9.9"
    assert len(reg.models) == 3


def test_missing_file_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("MODELS_REGISTRY_PATH", str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError):
        mr.load_registry(force_refresh=True)


def test_malformed_file_raises(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    monkeypatch.setenv("MODELS_REGISTRY_PATH", str(bad))
    with pytest.raises(ValueError):
        mr.load_registry(force_refresh=True)


def test_result_is_cached_within_ttl(fixture_registry):
    first = mr.load_registry(force_refresh=True)
    fixture_registry.write_text(json.dumps({**FIXTURE, "version": "0.0"}))
    assert mr.load_registry().version == first.version == "9.9"
    assert mr.load_registry(force_refresh=True).version == "0.0"


def test_by_tag_honours_aliases_and_latest(fixture_registry):
    reg = mr.load_registry(force_refresh=True)
    assert reg.by_tag("small:3b") is not None
    assert reg.by_tag("small:3b-instruct-q4_K_M").id == "small"
    assert reg.by_tag("embedder:latest").id == "embedder"
    assert reg.by_tag("unregistered:1b") is None


# ── The available / selected / installed join ─────────────────────────────────


def _patch_env(monkeypatch, installed, ram, llm="small:3b", embed="embedder"):
    async def _tags(base_url=None):
        return installed, "ok"

    monkeypatch.setattr(mr, "installed_tags", _tags)
    monkeypatch.setattr(mr, "host_ram_gb", lambda: ram)

    s = mr.get_settings()
    monkeypatch.setattr(s, "llm_model", llm)
    monkeypatch.setattr(s, "embed_model", embed)


def test_resolve_marks_installed_selected_and_fit(fixture_registry, monkeypatch):
    _patch_env(monkeypatch, installed=["small:3b", "embedder:latest"], ram=16.0)

    out = asyncio.run(mr.resolve_models())
    by_id = {m["id"]: m for m in out["models"]}

    assert by_id["small"]["installed"] is True
    assert by_id["small"]["selected"] is True
    assert by_id["small"]["selected_as"] == ["llm"]
    assert by_id["small"]["fits_host"] is True

    # Bare tag installed as ":latest" still counts as installed.
    assert by_id["embedder"]["installed"] is True
    assert by_id["embedder"]["selected_as"] == ["embedding"]

    # Registered but never pulled, and too big for a 16 GB host.
    assert by_id["big"]["installed"] is False
    assert by_id["big"]["selected"] is False
    assert by_id["big"]["fits_host"] is False

    assert out["unregistered_selections"] is None


def test_resolve_flags_unregistered_selection(fixture_registry, monkeypatch):
    _patch_env(monkeypatch, installed=[], ram=16.0, llm="typo3.1:8b")

    out = asyncio.run(mr.resolve_models())
    assert out["unregistered_selections"] == {"llm": "typo3.1:8b"}
    assert all(not m["selected"] for m in out["models"] if m["role"] == "llm")


def test_resolve_filters_by_role(fixture_registry, monkeypatch):
    _patch_env(monkeypatch, installed=[], ram=16.0)

    out = asyncio.run(mr.resolve_models(role="embedding"))
    assert out["count"] == 1
    assert out["models"][0]["id"] == "embedder"


def test_unknown_host_ram_leaves_fit_undecided(fixture_registry, monkeypatch):
    _patch_env(monkeypatch, installed=[], ram=None)

    out = asyncio.run(mr.resolve_models())
    assert out["host_ram_gb"] is None
    assert all(m["fits_host"] is None for m in out["models"])


def test_unreachable_ollama_is_reported_not_raised(fixture_registry, monkeypatch):
    async def _tags(base_url=None):
        return [], "unreachable: connection refused"

    monkeypatch.setattr(mr, "installed_tags", _tags)

    out = asyncio.run(mr.resolve_models())
    assert out["ollama"]["status"].startswith("unreachable")
    assert all(m["installed"] is False for m in out["models"])
