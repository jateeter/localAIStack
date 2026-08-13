"""Model registry: what the stack can run, what it selected, what is installed.

Three facts about a model live in three different places, and debugging model
problems means joining them:

  *available*  ``config/models.registry.json`` — curated metadata (this module)
  *selected*   ``.env`` → ``config.Settings.llm_model`` / ``embed_model``
  *installed*  the live Ollama host (``GET /api/tags``)

``load_registry()`` reads the first. ``resolve_models()`` joins all three and is
what ``GET /models`` serves, so a missing pull, a typo'd tag, and a model too
big for the host are all distinguishable from one response.

The registry is data, not policy: nothing here downloads or selects a model.
``scripts/lib/models_registry.sh`` reads the same file for ``setup.sh``
validation and ``make model-pull``.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import httpx
from pydantic import BaseModel, Field

from config import get_settings

_CACHE_TTL_S = 30.0
_cache: dict = {"at": 0.0, "registry": None, "path": None}

_TAGS_TIMEOUT_S = 3.0

# services/api/core/model_registry.py → services/api/core → services/api → services → <root>
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# In Docker, services/api is mounted at /app and config/ alongside it at
# /app/config, so the repo-root walk above overshoots. Try both layouts.
_DEFAULT_PATHS = (
    _REPO_ROOT / "config" / "models.registry.json",
    pathlib.Path("/app/config/models.registry.json"),
)


class ModelSource(BaseModel):
    kind: str  # "ollama-library" | "modelfile"
    ref: str | None = None
    modelfile: str | None = None
    gguf: str | None = None
    download_url: str | None = None


class ModelEntry(BaseModel):
    id: str
    tag: str
    aliases: list[str] = Field(default_factory=list)
    name: str
    publisher: str
    family: str
    role: str  # "llm" | "embedding"
    source: ModelSource
    parameters: str | None = None
    quantization: str | None = None
    size_gb: float | None = None
    context_length: int | None = None
    embed_dim: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    min_ram_gb: int | None = None
    install: str = "on-demand"  # "selected" | "on-demand" | "modelfile"
    status: str = "supported"  # "supported" | "experimental" | "broken"
    license: str | None = None
    notes: str | None = None


class ModelRegistry(BaseModel):
    version: str
    runtime: str = "ollama"
    roles: dict = Field(default_factory=dict)
    models: list[ModelEntry] = Field(default_factory=list)

    def by_id(self, model_id: str) -> ModelEntry | None:
        return next((m for m in self.models if m.id == model_id), None)

    def by_tag(self, tag: str) -> ModelEntry | None:
        """Look up by Ollama tag, honouring aliases and the implicit ``:latest``."""
        wanted = {tag, tag.removesuffix(":latest")}
        for m in self.models:
            known = {m.tag, *m.aliases}
            if wanted & (known | {t.removesuffix(":latest") for t in known}):
                return m
        return None

    def default_for(self, role: str) -> str | None:
        entry = self.roles.get(role) or {}
        return entry.get("default")


def registry_path() -> pathlib.Path:
    """Resolve the registry file.

    Order: ``MODELS_REGISTRY_PATH`` env (compose sets it), then the same key
    from ``.env`` via settings, then the repo-root / container layouts.
    """
    override = os.getenv("MODELS_REGISTRY_PATH") or get_settings().models_registry_path
    if override:
        return pathlib.Path(override)
    return next((p for p in _DEFAULT_PATHS if p.is_file()), _DEFAULT_PATHS[0])


def load_registry(force_refresh: bool = False) -> ModelRegistry:
    """Parse the registry file, cached for a short TTL.

    Raises FileNotFoundError when the file is missing and ValueError when it
    does not parse — a malformed registry is a deploy error worth surfacing,
    not something to paper over with an empty model list.
    """
    path = registry_path()
    now = time.monotonic()
    cached = _cache["registry"]
    fresh = (
        not force_refresh
        and isinstance(cached, ModelRegistry)
        and _cache["path"] == str(path)
        and now - _cache["at"] < _CACHE_TTL_S
    )
    if fresh and isinstance(cached, ModelRegistry):
        return cached

    if not path.is_file():
        raise FileNotFoundError(
            f"Model registry not found at {path}. Set MODELS_REGISTRY_PATH or mount config/ into the container."
        )

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model registry at {path} is not valid JSON: {exc}") from exc

    # Strip the `_comment` documentation keys used throughout config/.
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    registry = ModelRegistry.model_validate(raw)

    _cache.update({"at": now, "registry": registry, "path": str(path)})
    return registry


async def installed_tags(base_url: str | None = None) -> tuple[list[str], str]:
    """Tags currently pulled on the Ollama host.

    Returns ``(tags, status)`` where status is ``"ok"`` or ``"unreachable: ..."``
    so callers can tell "not installed" apart from "could not check".
    """
    s = get_settings()
    url = base_url or s.ollama_base_url
    try:
        async with httpx.AsyncClient(timeout=_TAGS_TIMEOUT_S) as c:
            r = await c.get(f"{url}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])], "ok"
    except Exception as exc:
        return [], f"unreachable: {str(exc)[:160]}"


def _tag_matches(entry_tag: str, installed: list[str]) -> bool:
    """Ollama reports bare tags as ``name:latest``; match either spelling."""
    wanted = {entry_tag, f"{entry_tag}:latest"} if ":" not in entry_tag else {entry_tag}
    return any(t in wanted for t in installed)


def host_ram_gb() -> float | None:
    """Physical RAM of the host running the API, or None when undetectable.

    In Docker this is the container's view, which on Docker Desktop is the VM
    allocation rather than the Mac's RAM — set ``HOST_RAM_GB`` to correct it.
    """
    override = os.getenv("HOST_RAM_GB")
    if override:
        try:
            return float(override)
        except ValueError:
            return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
    except (ValueError, OSError, AttributeError):
        return None


async def resolve_models(role: str | None = None) -> dict:
    """Join registry metadata with .env selection and live Ollama state."""
    registry = load_registry()
    s = get_settings()
    installed, ollama_status = await installed_tags()
    ram = host_ram_gb()

    selected = {"llm": s.llm_model, "embedding": s.embed_model}

    entries = [m for m in registry.models if role is None or m.role == role]
    resolved = []
    for m in entries:
        selected_for = [r for r, tag in selected.items() if registry.by_tag(tag) is m]
        resolved.append(
            {
                **m.model_dump(),
                "installed": _tag_matches(m.tag, installed),
                "selected": bool(selected_for),
                "selected_as": selected_for or None,
                "fits_host": None if ram is None or m.min_ram_gb is None else ram >= m.min_ram_gb,
            }
        )

    # A .env selection pointing at a tag nobody registered is the failure mode
    # this endpoint exists to catch — surface it rather than silently omitting.
    unregistered = {r: tag for r, tag in selected.items() if registry.by_tag(tag) is None}

    return {
        "registry_version": registry.version,
        "registry_path": str(registry_path()),
        "runtime": registry.runtime,
        "ollama": {"status": ollama_status, "base_url": s.ollama_base_url},
        "host_ram_gb": None if ram is None else round(ram, 1),
        "selected": selected,
        "unregistered_selections": unregistered or None,
        "defaults": {r: registry.default_for(r) for r in ("llm", "embedding")},
        "count": len(resolved),
        "models": resolved,
    }
