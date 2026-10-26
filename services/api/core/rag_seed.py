"""The default RAG corpus: data/health/documents, reconciled into Qdrant at startup.

Both collections the graphs read start empty on a fresh install: ``localai_docs``
(``/rag/query``, ``/graph/rag`` and the agent's ``rag_search``) and
``health_docs`` (the agent's ``health_search``). Until something was ingested
by hand, every retrieval found nothing, grading always took the rewrite branch,
and ``localai_rag_retrieval`` reported zero documents.

So the git-tracked health documents are the default corpus, and startup puts
them in both collections. It reconciles rather than appends:

- a chunk is identified by its source and a hash of its text, never by a point
  id (``reembed`` rebuilds ``health_docs`` with integer ids);
- a chunk already present is left alone, so a restart adds nothing;
- a seeded chunk no longer produced by the files (a document edited or removed)
  is deleted;
- nothing else is touched: documents ingested through ``/rag/ingest/*`` carry no
  seed tag and a source outside the seed set.

The chunking is ``core.health_docs.load_health_documents``, the one
``scripts/ingest_health_docs.py`` and ``reembed_cli`` use, so the three cannot
cut the same files differently. ``RAG_SEED_ON_STARTUP=false`` turns it off;
``LOCALAI_HEALTH_DOCS_DIR`` points it at another directory.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

SEED_TAG = "data/health/documents"
_NAMESPACE = uuid.UUID("3f0f6b8e-4f0b-4a51-9a39-6d2b5c7b1e21")


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


def chunk_key(source: str, text: str) -> str:
    """What makes a seeded chunk the same chunk across restarts and re-embeds."""
    return f"{source}#{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def point_id(key: str) -> str:
    """A stable point id for a chunk key, so a re-add is an overwrite, not a copy."""
    return str(uuid.uuid5(_NAMESPACE, key))


@dataclass
class SeedResult:
    collection: str
    expected: int = 0
    added: int = 0
    removed: int = 0
    kept: int = 0
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "expected": self.expected,
            "added": self.added,
            "removed": self.removed,
            "kept": self.kept,
            "sources": self.sources,
        }


def _seed_filter(sources: Iterable[str]):
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    # Tagged by this module, or (a re-embed's copy, or one written before the
    # tag existed) carrying a source the seed produces.
    return Filter(
        should=[
            FieldCondition(key="metadata.seed", match=MatchValue(value=SEED_TAG)),
            FieldCondition(key="metadata.source", match=MatchAny(any=sorted(set(sources)))),
        ]
    )


def _existing(client, collection: str, sources: Iterable[str]) -> dict[str, list]:
    """chunk key -> the ids of every point holding that chunk."""
    found: dict[str, list] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection,
            scroll_filter=_seed_filter(sources),
            with_payload=True,
            with_vectors=False,
            limit=256,
            offset=offset,
        )
        for p in points:
            payload = p.payload or {}
            meta = payload.get("metadata") or {}
            key = chunk_key(str(meta.get("source", "")), str(payload.get("page_content", "")))
            found.setdefault(key, []).append(p.id)
        if offset is None:
            return found


def reconcile(client, collection: str, chunks: list, embedder: Embedder) -> SeedResult:
    """Make ``collection`` hold exactly one copy of each seed chunk."""
    from qdrant_client.models import PointIdsList, PointStruct

    wanted: dict[str, Any] = {}
    for c in chunks:
        meta = dict(c.metadata or {})
        meta["seed"] = SEED_TAG
        key = chunk_key(str(meta.get("source", "")), c.page_content)
        wanted.setdefault(key, (c.page_content, meta))

    sources = sorted({m["source"] for _, m in wanted.values() if m.get("source")})
    res = SeedResult(collection, expected=len(wanted), sources=sources)
    have = _existing(client, collection, sources)

    # Stale chunks, and every copy beyond the first of a wanted one.
    doomed: list = []
    for key, ids in have.items():
        doomed.extend(ids if key not in wanted else ids[1:])
    if doomed:
        client.delete(collection, points_selector=PointIdsList(points=doomed))
    res.removed = len(doomed)
    res.kept = sum(1 for key in wanted if key in have)

    missing = [key for key in wanted if key not in have]
    texts = [wanted[k][0] for k in missing]
    vectors: list[list[float]] = []
    for i in range(0, len(texts), 32):
        vectors.extend(embedder.embed_documents(texts[i : i + 32]))
    points = [
        PointStruct(
            id=point_id(key),
            vector=vec,
            # langchain-qdrant's payload layout, so QdrantVectorStore reads it back.
            payload={"page_content": wanted[key][0], "metadata": wanted[key][1]},
        )
        for key, vec in zip(missing, vectors, strict=True)
    ]
    for i in range(0, len(points), 128):
        client.upsert(collection, points[i : i + 128])
    res.added = len(points)
    return res


def seed_default_corpus(
    load_chunks: Callable[[], list] | None = None,
) -> list[SeedResult]:
    """Reconcile the default corpus into every collection the graphs read."""
    from config import get_settings
    from core.embeddings import get_embeddings
    from core.health_docs import default_docs_dir, load_health_documents
    from core.vector_store import ensure_collection, get_qdrant_client

    s = get_settings()
    chunks = (
        load_chunks() if load_chunks else load_health_documents(default_docs_dir(), verbose=False)
    )
    if not chunks:
        return []
    client = get_qdrant_client()
    embedder = get_embeddings()
    results = []
    for logical in (s.collection_name, s.health_collection_name):
        ensure_collection(client, logical)
        results.append(reconcile(client, logical, chunks, embedder))
    return results
