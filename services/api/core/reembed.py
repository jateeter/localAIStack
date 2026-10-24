"""Re-embed a collection with a different model, without losing data.

Changing the embedding model invalidates every stored vector. On an installed
system that is a data migration, so it is done the way a migration should be:

  1. build ``<logical>__<new-model>`` **beside** the live collection, from the
     source documents (git-tracked under ``data/<domain>/documents/``);
  2. verify retrieval against the old collection on a fixed query set;
  3. switch the logical alias to the new collection in one atomic call;
  4. keep the old collection until ``drop_old`` is asked for;
  5. ``rollback`` is a second alias switch.

A collection with no source documents cannot be rebuilt, so it is refused unless
the caller says data loss is acceptable. A *legacy* collection (named like the
logical, model never recorded) cannot hold an alias of the same name: it is
snapshotted, deleted, and replaced by the alias. That one step is not an instant
rollback; the snapshot is, and its name is recorded.

This module takes the Qdrant client and the embedding objects as arguments so it
is testable without Ollama or a server; ``scripts/reembed.py`` wires the real ones.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from qdrant_client import QdrantClient

from core import collections as qc

MIN_RECALL = 0.8  # absolute floor on the new collection's recall@k
TOLERANCE = 0.1  # how far below the old collection's recall the new one may fall
TOP_K = 5


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


def state_path() -> pathlib.Path:
    base = os.getenv("LOCALAI_STATE_DIR") or os.path.expanduser("~/.localai")
    return pathlib.Path(base) / "collections.json"


def _history() -> list[dict]:
    try:
        return json.loads(state_path().read_text())["history"]
    except (OSError, ValueError, KeyError):
        return []


def _record(entry: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"history": [*_history(), entry]}, indent=2) + "\n")


@dataclass
class Result:
    logical: str
    action: str  # up-to-date | migrated | refused | verify-failed | rolled-back | dropped
    from_physical: str | None = None
    to_physical: str | None = None
    points: int | None = None
    recall_new: float | None = None
    recall_old: float | None = None
    snapshot: str | None = None
    k: int = TOP_K
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.action in ("up-to-date", "migrated", "rolled-back", "dropped")


def derive_queries(chunks: list[Any]) -> list[tuple[str, str]]:
    """One (query, expected source) per source document: its own title.

    A document's title should retrieve that document. This is a deliberately
    modest check, a fixed query set the old and the new collection are both held
    to; it compares retrieval, not LLM output (llm-drift rule).
    """
    seen: dict[str, str] = {}
    for c in chunks:
        src = (c.metadata or {}).get("source")
        if not src or src in seen:
            continue
        text = c.page_content.strip()
        heading = re.search(r"^\s*#+\s*(.+)$", text, re.M)
        seen[src] = (heading.group(1) if heading else text.splitlines()[0])[:120].strip()
    return [(q, src) for src, q in seen.items() if q]


def recall_at_k(
    client: QdrantClient,
    collection: str,
    embedder: Embedder,
    queries: list[tuple[str, str]],
    k: int = TOP_K,
) -> float:
    """Fraction of queries whose expected source appears in the top k."""
    if not queries:
        return 1.0
    hits = 0
    for query, expected in queries:
        vec = embedder.embed_query(query)
        points = client.query_points(collection, query=vec, limit=k, with_payload=True).points
        sources = {
            (p.payload or {}).get("metadata", {}).get("source") or (p.payload or {}).get("source")
            for p in points
        }
        hits += expected in sources
    return hits / len(queries)


def _upsert(client: QdrantClient, collection: str, embedder: Embedder, chunks: list[Any]) -> int:
    from qdrant_client.models import PointStruct

    texts = [c.page_content for c in chunks]
    vectors: list[list[float]] = []
    for i in range(0, len(texts), 32):
        vectors.extend(embedder.embed_documents(texts[i : i + 32]))
    points = [
        PointStruct(
            id=i,
            vector=vec,
            # langchain-qdrant's payload layout, so the live store reads what we wrote
            payload={"page_content": c.page_content, "metadata": dict(c.metadata or {})},
        )
        for i, (c, vec) in enumerate(zip(chunks, vectors, strict=True), start=1)
    ]
    for i in range(0, len(points), 128):
        client.upsert(collection, points[i : i + 128])
    return len(points)


def run(
    client: QdrantClient,
    logical: str,
    model_tag: str,
    embedder: Embedder,
    *,
    chunks: list[Any] | None,
    queries: list[tuple[str, str]] | None = None,
    old_embedder: Embedder | None = None,
    allow_data_loss: bool = False,
) -> Result:
    dim = len(embedder.embed_query("dimension probe"))
    cur = qc.resolve(client, logical)
    target = qc.physical_name(logical, model_tag)
    res = Result(logical, "refused", from_physical=cur.physical, to_physical=target)

    if cur.kind == "alias" and cur.physical == target:
        if cur.dim != dim:
            res.detail = f"{target} has vector size {cur.dim} but the embedder produces {dim}; delete it and re-run"
            return res
        res.action, res.points = "up-to-date", cur.points
        return res

    if chunks is None:
        if cur.points and not allow_data_loss:
            res.detail = (
                f"'{logical}' holds {cur.points} points and has no source documents to rebuild from. "
                "Re-run with --allow-data-loss to replace it with an empty collection."
            )
            return res
        chunks = []

    if client.collection_exists(target):  # an earlier build that never switched
        client.delete_collection(target)
    qc.create_physical(client, logical, model_tag, dim)
    res.points = _upsert(client, target, embedder, chunks) if chunks else 0

    queries = queries if queries is not None else derive_queries(chunks)
    if chunks and queries:
        # recall@k is vacuous once k reaches the number of sources (every embedder,
        # even a random one, then "finds" the right document), so k is at most half
        # of them. The real corpus has many chunks per source and stays meaningful.
        res.k = max(1, min(TOP_K, len({src for _, src in queries}) // 2))
        res.recall_new = recall_at_k(client, target, embedder, queries, res.k)
        if cur.points and old_embedder is not None and cur.physical:
            res.recall_old = recall_at_k(client, cur.physical, old_embedder, queries, res.k)
        # Against an old collection: not worse than it by more than the tolerance.
        # With nothing to compare to: clear an absolute floor.
        floor = MIN_RECALL if res.recall_old is None else max(0.0, res.recall_old - TOLERANCE)
        if res.recall_new < floor:
            res.action = "verify-failed"
            res.detail = (
                f"recall@{res.k} {res.recall_new:.2f} is below the floor {floor:.2f}"
                f"{'' if res.recall_old is None else f' (old collection: {res.recall_old:.2f})'}. "
                f"{target} is left in place, inactive, for inspection; the alias was not switched."
            )
            return res
    else:
        res.notes.append("no source documents or queries: nothing to verify retrieval against")

    if cur.kind == "legacy":
        # The only copy of a legacy collection is itself: never delete it unless a
        # snapshot exists. The new collection stays built and inactive.
        try:
            snap = client.create_snapshot(collection_name=logical)
        except Exception as exc:
            res.action = "refused"
            res.detail = (
                f"could not snapshot legacy '{logical}' ({str(exc)[:120]}); it was not replaced. "
                f"{target} is built and inactive."
            )
            return res
        res.snapshot = getattr(snap, "name", None)
        client.delete_collection(logical)
    qc.switch_alias(client, logical, target)
    _record(
        {
            "logical": logical,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "from": cur.physical,
            "from_kind": cur.kind,
            "to": target,
            "snapshot": res.snapshot,
        }
    )
    res.action = "migrated"
    return res


SNAPSHOT_ROOT = "/qdrant/snapshots"  # where the Qdrant server keeps collection snapshots by default
LEGACY_SLUG = "legacy"


def rollback(client: QdrantClient, logical: str, snapshot_root: str = SNAPSHOT_ROOT) -> Result:
    """Point ``logical`` back at the previous collection.

    Alias to alias is one switch. A legacy original was snapshotted and deleted
    when it was replaced, so it is restored from that snapshot into
    ``<logical>__legacy`` (the model was never recorded, so the slug says so) and
    the alias is pointed there; the server reads the snapshot from its own disk
    (``snapshot_root`` is the server's path, not this machine's).
    """
    res = Result(logical, "refused")
    entries = [e for e in _history() if e["logical"] == logical]
    if not entries:
        res.detail = f"no recorded migration for '{logical}'"
        return res
    last = entries[-1]
    res.from_physical, res.to_physical = last["to"], last["from"]
    if last["from_kind"] == "legacy":
        snap = last.get("snapshot")
        if not snap:
            res.detail = (
                f"the previous '{logical}' was a legacy collection and no snapshot was recorded"
            )
            return res
        restored = f"{logical}{qc.SEP}{LEGACY_SLUG}"
        res.to_physical = restored
        try:
            client.recover_snapshot(restored, location=f"file://{snapshot_root}/{logical}/{snap}")
        except Exception as exc:
            res.detail = (
                f"could not restore snapshot {snap} ({str(exc)[:120]}). It is on the Qdrant server at "
                f"{snapshot_root}/{logical}/{snap}; PUT /collections/<name>/snapshots/recover restores it."
            )
            return res
        qc.switch_alias(client, logical, restored)
        _record(
            {
                "logical": logical,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "from": last["to"],
                "from_kind": "alias",
                "to": restored,
                "snapshot": None,
                "rollback": True,
            }
        )
        res.action = "rolled-back"
        res.notes.append(
            f"restored the legacy collection as {restored}. Its embedding model was never recorded, so "
            "it will report a mismatch with any embedder until it is migrated again."
        )
        return res
    if not last["from"] or not client.collection_exists(last["from"]):
        res.detail = f"{last['from']} no longer exists (dropped?)"
        return res
    qc.switch_alias(client, logical, last["from"])
    _record(
        {
            "logical": logical,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "from": last["to"],
            "from_kind": "alias",
            "to": last["from"],
            "snapshot": None,
            "rollback": True,
        }
    )
    res.action = "rolled-back"
    return res


def drop_old(client: QdrantClient, logical: str) -> Result:
    """Delete every physical collection of ``logical`` the alias does not point at."""
    res = Result(logical, "refused")
    cur = qc.resolve(client, logical)
    if cur.kind != "alias":
        res.detail = f"'{logical}' is not an alias; nothing to compare the others against"
        return res
    dropped = []
    for c in client.get_collections().collections:
        parsed = qc.parse_physical(c.name)
        if parsed and parsed[0] == logical and c.name != cur.physical:
            client.delete_collection(c.name)
            dropped.append(c.name)
    res.action, res.detail = "dropped", f"dropped: {', '.join(dropped) or 'nothing'}"
    return res
