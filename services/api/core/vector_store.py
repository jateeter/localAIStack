from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from config import get_settings
from core import collections
from core.embeddings import get_embeddings

_client: QdrantClient | None = None
_store: QdrantVectorStore | None = None
_health_store: QdrantVectorStore | None = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = QdrantClient(host=s.qdrant_host, port=s.qdrant_port)
    return _client


def ensure_collection(client: QdrantClient, name: str) -> None:
    """Make the logical collection ``name`` usable with the configured embedder.

    ``name`` is an alias onto ``<name>__<model>`` (core.collections). A
    collection filled by a different model raises ``CollectionMismatch`` with the
    command that fixes it, rather than answering from vectors that do not mix.
    """
    s = get_settings()
    collections.ensure(client, name, s.embed_model, s.embed_dim)


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


def get_health_vector_store() -> QdrantVectorStore:
    """Return the QdrantVectorStore for the personal health knowledge collection."""
    global _health_store
    if _health_store is None:
        s = get_settings()
        client = get_qdrant_client()
        ensure_collection(client, s.health_collection_name)
        _health_store = QdrantVectorStore(
            client=client,
            collection_name=s.health_collection_name,
            embedding=get_embeddings(),
        )
    return _health_store
