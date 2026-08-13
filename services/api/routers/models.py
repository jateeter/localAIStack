"""Model registry routes.

``GET /models`` is the "why is this model not working" endpoint: it reports, per
model, whether it is registered, pulled on the Ollama host, selected by .env,
and large enough to need more RAM than this host has.
"""

from fastapi import APIRouter, HTTPException, Query

from core.model_registry import (
    installed_tags,
    load_registry,
    resolve_models,
)

router = APIRouter(prefix="/models", tags=["models"])


def _registry_or_503():
    try:
        return load_registry()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("")
async def list_models(
    role: str | None = Query(None, description="Filter by role: llm | embedding"),
):
    if role is not None and role not in ("llm", "embedding"):
        raise HTTPException(status_code=400, detail="role must be 'llm' or 'embedding'")
    _registry_or_503()
    try:
        return await resolve_models(role=role)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{model_id}")
async def get_model(model_id: str):
    registry = _registry_or_503()
    entry = registry.by_id(model_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"'{model_id}' is not in the model registry. Known ids: {[m.id for m in registry.models]}",
        )

    installed, ollama_status = await installed_tags()
    return {
        **entry.model_dump(),
        "installed": entry.tag in installed or f"{entry.tag}:latest" in installed,
        "ollama_status": ollama_status,
        "pull_command": (
            f"ollama pull {entry.tag}"
            if entry.source.kind == "ollama-library"
            else f"make model-pull ID={entry.id}"
        ),
    }
