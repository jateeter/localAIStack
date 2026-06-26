from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from routers import chat, graph, health, patient_wellness, rag
from routers.graphql_endpoint import events_router as graphql_events_router
from routers.graphql_endpoint import graphql_app

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    log.info("localAIStack API starting",
             llm_model=s.llm_model,
             embed_model=s.embed_model,
             ollama_url=s.ollama_base_url)
    # Warm up vector store connection on startup
    try:
        from core.vector_store import get_vector_store
        get_vector_store()
        log.info("Qdrant vector store ready", collection=s.collection_name)
    except Exception as e:
        log.warning("Vector store not ready at startup", error=str(e))
    # Register Reality Engine sensor sources (graceful — PE may not be running)
    try:
        from core.reality_bridge import (
            bind_graph_topology,
            import_carekit_machine,
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
    yield
    log.info("localAIStack API shutting down")


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

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(rag.router)
app.include_router(graph.router)
app.include_router(patient_wellness.router)
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
