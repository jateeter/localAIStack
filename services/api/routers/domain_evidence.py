"""Authenticated, opt-in evidence tools; independent of legacy signal routes."""

import hmac
import json
import os
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.domain_evidence import EDITION, EvidenceIndex, Unresolved

router = APIRouter(prefix="/rag/evidence", tags=["domain-evidence"])


def authorize(authorization: str = Header(default="")):
    token = os.environ.get("IFRS_EVIDENCE_TOKEN", "")
    if not token:
        raise HTTPException(503, "IFRS evidence tools are not configured")
    if not hmac.compare_digest(authorization, "Bearer " + token):
        raise HTTPException(401, "Invalid service credential")


@lru_cache
def index():
    from config import get_settings
    from core.embeddings import get_embeddings
    from core.vector_store import ensure_collection, get_qdrant_client

    settings = get_settings()
    state = os.environ.get("IFRS_EVIDENCE_STATE_DIR")
    if not state:
        raise HTTPException(503, "IFRS_EVIDENCE_STATE_DIR is required outside Git")
    if os.environ.get("IFRS_QDRANT_PATH"):
        from qdrant_client import QdrantClient

        client = QdrantClient(path=os.environ["IFRS_QDRANT_PATH"])
    else:
        client = get_qdrant_client()
    return EvidenceIndex(
        state, client, get_embeddings(), settings.embed_model, settings.embed_dim, ensure_collection
    )


class PassageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = "ifrs-accounting"
    documentRole: str = "standard_source"
    edition: str = EDITION
    question: str = Field(min_length=1, max_length=8000)
    documentId: str | None = None
    sourceSHA256: str | None = None
    sectionId: str | None = None
    indexSnapshot: str | None = None
    method: str = "semantic"
    limit: int = Field(default=8, ge=1, le=50)


def guarded(call):
    try:
        return call()
    except Unresolved as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/reconcile", dependencies=[Depends(authorize)])
def reconcile():
    bundle = os.environ.get("IFRS_EVIDENCE_BUNDLE")
    if not bundle:
        raise HTTPException(503, "IFRS_EVIDENCE_BUNDLE is required")
    return guarded(lambda: index().reconcile(bundle))


@router.post("/retrieve", dependencies=[Depends(authorize)])
def retrieve(req: PassageRequest):
    if req.method not in {"semantic", "exact-source"}:
        raise HTTPException(422, "Unsupported retrieval method")
    return guarded(lambda: index().retrieve(req.model_dump(exclude_none=True)))


@router.post("/query", dependencies=[Depends(authorize)])
def query(req: PassageRequest):
    import httpx

    from config import get_settings

    settings = get_settings()

    def generate(messages):
        response = httpx.post(
            settings.ollama_base_url.rstrip("/") + "/api/chat",
            json={"model": settings.llm_model, "messages": messages, "stream": False},
            timeout=180,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    return guarded(
        lambda: index().generate(
            req.model_dump(exclude_none=True),
            generate,
            {"provider": "ollama", "model": settings.llm_model},
        )
    )


@router.get("/receipts/{interaction_id}", dependencies=[Depends(authorize)])
def receipt(interaction_id: str):
    return guarded(lambda: index().receipt(interaction_id))


class EntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entityId: str
    period: str
    assetTypeId: str
    revision: int | None = Field(default=None, ge=1)


def entity_config():
    path = os.environ.get("IFRS_ENTITY_CONNECTOR_CONFIG")
    if not path:
        raise HTTPException(503, "Canonical entity connector is not configured")
    return json.loads(Path(path).read_text())


def projection():
    from core.entity_evidence import EntityProjection

    state = os.environ.get("IFRS_EVIDENCE_STATE_DIR")
    if not state:
        raise HTTPException(503, "IFRS_EVIDENCE_STATE_DIR is required")
    config = entity_config()
    return EntityProjection(
        config.get("stateDirectory", state), evidence_class=config.get("evidenceClass", "actual")
    )


def canonical_call(config, scope, operation, args):
    import httpx

    response = httpx.post(
        config["url"].rstrip("/") + "/tools/execute",
        headers={"Authorization": "Bearer " + config["token"]},
        json={
            "dispatchId": str(uuid.uuid4()),
            "operation": operation,
            "context": scope,
            "arguments": args,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


@router.post("/entity/reconcile", dependencies=[Depends(authorize)])
def reconcile_entities():
    config = entity_config()
    results = []
    for scope in config["scopes"]:
        # Pull the canonical store's state. This endpoint accepts no fabricated
        # caller events and therefore cannot grant approval from an LLM body.
        snapshot = canonical_call(config, scope, "export", {})
        try:
            results.extend(projection().apply(event) for event in snapshot["events"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"results": results, "generationStatus": "NOT_REQUESTED"}


@router.post("/entity/retrieve", dependencies=[Depends(authorize)])
def retrieve_entity(req: EntityRequest):
    from core.entity_evidence import ProjectionLag

    config = entity_config()
    scope = next(
        (
            s
            for s in config["scopes"]
            if s["entityId"] == req.entityId and s["period"] == req.period
        ),
        None,
    )
    if scope is None:
        raise HTTPException(403, "Entity/period is outside configured connector scope")
    try:
        if req.revision is not None:
            return projection().historical(req.entityId, req.period, req.assetTypeId, req.revision)
        return projection().current(
            req.entityId,
            req.period,
            req.assetTypeId,
            lambda e, p, a: canonical_call(config, scope, "inspect", {"assetTypeId": a}),
        )
    except ProjectionLag as exc:
        raise HTTPException(409, str(exc)) from exc
