from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ollama_base_url: str = "http://localhost:11434"
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    redis_url: str = "redis://localhost:4379"

    llm_model: str = "llama3.1:8b-q4_K_M"
    embed_model: str = "ternary-bonsai:4"
    # Output dimension of embed_model. Must match the existing Qdrant collection;
    # recreate the collection if you swap to a model with a different dim.
    embed_dim: int = 768
    collection_name: str = "localai_docs"

    # RAG retrieval
    retrieval_top_k: int = 5
    retrieval_score_threshold: float = 0.4

    # LangGraph
    graph_recursion_limit: int = 25

    # Reality Engine stack URLs (PE = Perception Engine, RE = Reality Engine)
    # Docker: set to http://host.docker.internal:<port>
    # Local dev: http://localhost:<port>
    pe_url: str = "http://localhost:3004"
    re_url: str = "http://localhost:3000"

    # Personal health domain
    # Separate Qdrant collection for health knowledge documents.
    health_collection_name: str = "health_docs"
    # Set HEALTH_CONTEXT_ENABLED=true to automatically inject the current health
    # state into every chat system prompt. Can also be enabled per-request via
    # ChatRequest.health_context=true or the X-Health-Context: enabled header.
    health_context_enabled: bool = False

    log_level: str = "info"

    class Config:
        env_file = ".env"
        extra = "ignore"  # tolerate env vars from other services (WEBUI_SECRET_KEY, etc.)


@lru_cache
def get_settings() -> Settings:
    return Settings()
