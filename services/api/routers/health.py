import httpx
from fastapi import APIRouter
from qdrant_client import QdrantClient
import redis as redis_lib

from config import get_settings

router = APIRouter()


@router.get("/health")
async def health():
    s = get_settings()
    status = {"api": "ok", "ollama": "unknown", "qdrant": "unknown", "redis": "unknown"}

    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{s.ollama_base_url}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            status["ollama"] = "ok"
            status["ollama_models"] = models
    except Exception as e:
        status["ollama"] = f"error: {e}"

    try:
        qc = QdrantClient(host=s.qdrant_host, port=s.qdrant_port, timeout=3)
        qc.get_collections()
        status["qdrant"] = "ok"
    except Exception as e:
        status["qdrant"] = f"error: {e}"

    try:
        rc = redis_lib.from_url(s.redis_url, socket_timeout=3)
        rc.ping()
        status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"error: {e}"

    overall = "ok" if all(v == "ok" for v in [status["ollama"], status["qdrant"], status["redis"]]) else "degraded"
    return {"status": overall, "services": status}
