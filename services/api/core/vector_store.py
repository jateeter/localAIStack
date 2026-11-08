import importlib.metadata

import structlog
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.common.version_check import is_compatible

from config import get_settings
from core import collections, lane_tls
from core.embeddings import get_embeddings

log = structlog.get_logger()

_client: QdrantClient | None = None
_store: QdrantVectorStore | None = None
_health_store: QdrantVectorStore | None = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = QdrantClient(host=s.qdrant_host, port=s.qdrant_port, **lane_tls.qdrant_kwargs())
        if lane_tls.enabled():
            _check_server_version(_client)
    return _client


def _check_server_version(client: QdrantClient) -> None:
    """qdrant-client's compatibility check, over the lane's TLS (core/lane_tls.qdrant_kwargs)."""
    try:
        server = client.info().version
        mine = importlib.metadata.version("qdrant-client")
        if not is_compatible(mine, server):
            log.warning("qdrant.version_incompatible", client=mine, server=server)
    except Exception as exc:  # noqa: BLE001 — a warning, as qdrant-client's own check is
        log.warning("qdrant.version_unknown", error=str(exc)[:200])


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
