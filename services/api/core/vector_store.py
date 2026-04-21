from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from config import get_settings
from core.embeddings import get_embeddings

_client: QdrantClient | None = None
_store: QdrantVectorStore | None = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = QdrantClient(host=s.qdrant_host, port=s.qdrant_port)
    return _client


def ensure_collection(client: QdrantClient, name: str) -> None:
    collections = [c.name for c in client.get_collections().collections]
    if name not in collections:
        s = get_settings()
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=s.embed_dim, distance=Distance.COSINE),
        )


def get_vector_store() -> QdrantVectorStore:
    global _store
    if _store is None:
        s = get_settings()
        client = get_qdrant_client()
        ensure_collection(client, s.collection_name)
        _store = QdrantVectorStore(
            client=client,
            collection_name=s.collection_name,
            embedding=get_embeddings(),
        )
    return _store
