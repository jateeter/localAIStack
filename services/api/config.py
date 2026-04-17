from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    ollama_base_url: str = "http://localhost:11434"
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    redis_url: str = "redis://localhost:4379"

    llm_model: str = "llama3.1:8b-q4_K_M"
    embed_model: str = "nomic-embed-text"
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

    log_level: str = "info"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
