import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from routers import health, chat, rag, graph

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
