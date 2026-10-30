import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from core.bridge_binding import EngineAffinityMiddleware
from routers import chat, graph, health, models, observations, patient_wellness, rag
from routers import domain_evidence
from routers.graphql_endpoint import events_router as graphql_events_router
from routers.graphql_endpoint import graphql_app

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    log.info(
        "localAIStack API starting",
        llm_model=s.llm_model,
        embed_model=s.embed_model,
        ollama_url=s.ollama_base_url,
    )
    # The embedder first: its real output dimension is the fact every collection is
    # checked against. Metadata is not trusted (ollama show reports 512 for a model
    # that returns 768), so probe it.
    try:
        from core.embeddings import probe_dimension

        probed = probe_dimension()
        if probed != s.embed_dim:
            log.error(
                "embedding_dimension_mismatch",
                embed_model=s.embed_model,
                configured=s.embed_dim,
                probed=probed,
                note="set EMBED_DIM to the probed value and re-embed (scripts/reembed.py)",
            )
        else:
            log.info("Embedder ready", embed_model=s.embed_model, dim=probed)
    except Exception as e:
        log.warning("Embedder not reachable at startup", embed_model=s.embed_model, error=str(e))
    # Warm up vector store connection on startup. A collection filled by another
    # model is an error, not a warning: answering from it would be silently wrong.
    try:
        from core.collections import CollectionMismatch
        from core.vector_store import get_health_vector_store, get_vector_store

        try:
            get_vector_store()
            get_health_vector_store()
            log.info("Qdrant vector store ready", collection=s.collection_name)
        except CollectionMismatch as e:
            log.error("collection_embedder_mismatch", error=str(e))
    except Exception as e:
        log.warning("Vector store not ready at startup", error=str(e))
    # The default RAG corpus (data/health/documents) into both collections the
    # graphs read; a reconcile, so a restart re-adds nothing (core/rag_seed.py).
    if s.rag_seed_on_startup:
        try:
            from core.rag_seed import seed_default_corpus

            for r in await asyncio.to_thread(seed_default_corpus):
                log.info("rag_default_corpus", **r.as_dict())
        except Exception as e:
            log.warning("rag_default_corpus_not_seeded", error=str(e))
    # Register Reality Engine sensor sources (graceful — PE may not be running)
    try:
        from core.reality_bridge import (
            bind_graph_topology,
            import_carekit_machine,
            import_domain_machines,
            import_health_machines,
            import_machine_if_missing,
            import_session_machines,
            register_sensors,
            verify_machine_offsets,
        )

        # Pure local-file structural check; runs before any network call so
        # drift surfaces even when the PE/RE are unreachable.
        offset_mismatches = verify_machine_offsets()
        bridge_steps = {
            "offsets": not offset_mismatches,
            "sensors": register_sensors(),
            "rag_machine": import_machine_if_missing(),
            "session_machines": import_session_machines(),
            "health_machine": import_health_machines(),
            "carekit_machine": import_carekit_machine(),
            "domain_machines": import_domain_machines(),
            "topology": bind_graph_topology(),
        }
        failed_steps = [name for name, ok in bridge_steps.items() if not ok]
        if failed_steps:
            log.warning(
                "Reality Engine bridge degraded",
                failed_steps=failed_steps,
                pe_url=s.pe_url,
                re_url=s.re_url,
            )
        else:
            log.info(
                "Reality Engine bridge ready",
                pe_url=s.pe_url,
                re_url=s.re_url,
            )
    except Exception as e:
        log.warning("Reality Engine bridge not available", error=str(e))
    follower = None
    if s.health_scope_interval_s > 0:
        follower = asyncio.create_task(_follow_health_scope(s.health_scope_interval_s))
    yield
    if follower:
        follower.cancel()
    log.info("localAIStack API shutting down")


async def _follow_health_scope(interval_s: float) -> None:
    """Keep localAI's health slots in step with HealthKit scope on every PE.

    Scope changes (add / lock / remove) arrive at the PE from the owner's
    authorization workflow at any time, so this polls rather than reacting to
    startup alone. Never raises: a PE that is down is retried next interval.
    """
    from core.reality_bridge import follow_health_scope

    while True:
        try:
            await asyncio.to_thread(follow_health_scope)
        except Exception as e:
            log.warning("health scope follower failed", error=str(e))
        await asyncio.sleep(interval_s)


app = FastAPI(
    title="localAIStack",
    description="Local LLM + RAG + LangGraph orchestration API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pins each request to the engine that initiated it (X-RE-Instance), so the
# whole interaction — sensor writes, the push, and the perceptual space it is
# normalized from — stays on one engine.
app.add_middleware(EngineAffinityMiddleware)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(models.router)
app.include_router(rag.router)
app.include_router(domain_evidence.router)
app.include_router(graph.router)
app.include_router(patient_wellness.router)
app.include_router(observations.router)
app.include_router(graphql_app, prefix="/graphql")
app.include_router(graphql_events_router)


@app.get("/")
async def root():
    s = get_settings()
    return {
        "service": "localAIStack",
        "llm_model": s.llm_model,
        "embed_model": s.embed_model,
        "docs": "/docs",
        "health": "/health",
    }
