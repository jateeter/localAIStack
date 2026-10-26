"""
A collection knows which embedding model filled it, and changing the model is
loud and lossless (RealityEngine_CI#593).

Vectors from two models do not mix. These tests pin the rules on Qdrant's
in-memory client and deterministic fake embedders (no Ollama, no server). The
owner rule that each role's default runs on MLX is in test_embedding_defaults.py.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass, field

import pytest

# The lightweight E2E unit-test job installs no Qdrant client; skip there, run in CI.
pytest.importorskip("qdrant_client")

from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.models import Distance, PointStruct, VectorParams  # noqa: E402

from core import collections as qc  # noqa: E402
from core import reembed as rb  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
DIM = 24


@dataclass
class Document:
    """The two attributes re-embedding reads; avoids a langchain dependency in the test."""

    page_content: str
    metadata: dict = field(default_factory=dict)


# ── names ────────────────────────────────────────────────────────────────────


def test_physical_names_carry_the_model():
    assert qc.model_slug("embeddinggemma-2:270m") == "embeddinggemma-2-270m"
    assert (
        qc.physical_name("localai_docs", "embeddinggemma-2:270m")
        == "localai_docs__embeddinggemma-2-270m"
    )
    assert qc.parse_physical("localai_docs__embeddinggemma-2-270m") == (
        "localai_docs",
        "embeddinggemma-2-270m",
    )
    assert qc.parse_physical("localai_docs") is None


# ── collections: verify / ensure ─────────────────────────────────────────────


def _client() -> QdrantClient:
    return QdrantClient(":memory:")


def _legacy(client: QdrantClient, name: str, points: int, dim: int = DIM) -> None:
    client.create_collection(name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
    if points:
        client.upsert(
            name,
            [
                PointStruct(id=i, vector=[1.0] + [0.0] * (dim - 1), payload={})
                for i in range(1, points + 1)
            ],
        )


def test_ensure_creates_the_physical_collection_and_the_alias():
    c = _client()
    res = qc.ensure(c, "docs", "embeddinggemma-2:270m", DIM)
    assert (res.kind, res.physical, res.model_slug, res.dim) == (
        "alias",
        "docs__embeddinggemma-2-270m",
        "embeddinggemma-2-270m",
        DIM,
    )
    assert qc.ensure(c, "docs", "embeddinggemma-2:270m", DIM) == res  # idempotent


def test_a_collection_filled_by_another_model_is_refused_with_the_fix():
    c = _client()
    qc.ensure(c, "docs", "nomic-embed-text", DIM)
    with pytest.raises(qc.CollectionMismatch) as exc:
        qc.verify(c, "docs", "embeddinggemma-2:270m", DIM)
    msg = str(exc.value)
    assert "nomic-embed-text" in msg and "embeddinggemma-2:270m" in msg
    assert "scripts/reembed.py run docs" in msg


def test_a_dimension_mismatch_is_refused():
    c = _client()
    qc.ensure(c, "docs", "embeddinggemma-2:270m", DIM)
    with pytest.raises(qc.CollectionMismatch, match="vector size"):
        qc.verify(c, "docs", "embeddinggemma-2:270m", DIM + 1)


def test_a_populated_legacy_collection_is_refused_and_an_empty_one_adopted():
    c = _client()
    _legacy(c, "full", points=3)
    with pytest.raises(qc.CollectionMismatch, match="unrecorded model"):
        qc.ensure(c, "full", "embeddinggemma-2:270m", DIM)
    assert qc.resolve(c, "full").kind == "legacy"  # untouched

    _legacy(c, "empty", points=0)
    res = qc.ensure(c, "empty", "embeddinggemma-2:270m", DIM)
    assert res.kind == "alias" and res.physical == "empty__embeddinggemma-2-270m"


def test_inventory_reports_every_state():
    c = _client()
    qc.ensure(c, "ok_docs", "embeddinggemma-2:270m", DIM)
    qc.ensure(c, "old_docs", "nomic-embed-text", DIM)
    _legacy(c, "legacy_docs", points=2)
    rows = {
        r["logical"]: r
        for r in qc.inventory(
            c, "embeddinggemma-2:270m", DIM, ["ok_docs", "old_docs", "legacy_docs", "absent_docs"]
        )
    }
    assert rows["ok_docs"]["state"] == "ok"
    assert rows["old_docs"]["state"] == "mismatch" and rows["old_docs"]["why"]
    assert rows["legacy_docs"]["state"] == "mismatch"
    assert rows["absent_docs"]["state"] == "absent"


# ── re-embedding ─────────────────────────────────────────────────────────────


class FakeEmbedder:
    """Deterministic bag-of-words hashing; a different salt is a different vector space."""

    def __init__(self, salt: str, dim: int = DIM, scramble: bool = False):
        self.salt, self.dim, self.scramble = salt, dim, scramble

    def embed_query(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            key = f"{self.salt}:{word}"
            if self.scramble:  # a broken model: unrelated to the content
                key = f"{self.salt}:{hashlib.md5(text.encode()).hexdigest()}"
            vec[int(hashlib.md5(key.encode()).hexdigest(), 16) % self.dim] += 1.0
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


def _docs() -> list[Document]:
    topics = {
        "sleep": "Sleep quality and circadian rhythm. Deep sleep restores recovery; consistent bedtime stabilises circadian timing.",
        "hrv": "Heart rate variability interpretation. Low HRV signals stress; vagal tone and autonomic balance explain HRV trends.",
        "nutrition": "Nutrition and recovery. Protein timing, hydration and electrolytes support muscle repair after training.",
        "baseline": "Wellness baselines. Personal baselines are rolling medians that make a deviation meaningful.",
    }
    return [
        Document(
            page_content=f"# {body.split('.')[0]}\n\n{body}",
            metadata={"source": f"health/{name}.md"},
        )
        for name, body in topics.items()
    ]


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAI_STATE_DIR", str(tmp_path / "state"))


def test_first_build_then_migration_keeps_both_until_dropped():
    c = _client()
    a, b = FakeEmbedder("A"), FakeEmbedder("B")
    docs = _docs()

    first = rb.run(c, "docs", "model-a", a, chunks=docs)
    assert first.action == "migrated" and first.points == len(docs) and first.recall_new == 1.0
    assert qc.resolve(c, "docs").physical == "docs__model-a"

    again = rb.run(c, "docs", "model-a", a, chunks=docs)
    assert again.action == "up-to-date"

    moved = rb.run(c, "docs", "model-b", b, chunks=docs, old_embedder=a)
    assert moved.action == "migrated"
    assert moved.recall_new == 1.0 and moved.recall_old == 1.0
    assert qc.resolve(c, "docs").physical == "docs__model-b"
    assert c.collection_exists("docs__model-a"), "the old collection is kept until dropped"
    qc.verify(c, "docs", "model-b", DIM)  # now consistent with the new embedder

    # the live store reads what the migration wrote (langchain-qdrant's payload layout)
    hit = c.query_points(
        "docs",
        query=b.embed_query("Heart rate variability interpretation"),
        limit=1,
        with_payload=True,
    ).points[0]
    assert hit.payload["metadata"]["source"] == "health/hrv.md" and "page_content" in hit.payload


def test_rollback_is_one_alias_switch_and_drop_old_removes_the_rest():
    c = _client()
    a, b = FakeEmbedder("A"), FakeEmbedder("B")
    rb.run(c, "docs", "model-a", a, chunks=_docs())
    rb.run(c, "docs", "model-b", b, chunks=_docs(), old_embedder=a)

    back = rb.rollback(c, "docs")
    assert back.action == "rolled-back" and qc.resolve(c, "docs").physical == "docs__model-a"

    dropped = rb.drop_old(c, "docs")
    assert dropped.action == "dropped"
    assert not c.collection_exists("docs__model-b") and c.collection_exists("docs__model-a")


def test_a_migration_that_retrieves_worse_is_not_switched():
    c = _client()
    a = FakeEmbedder("A")
    rb.run(c, "docs", "model-a", a, chunks=_docs())
    broken = FakeEmbedder("B", scramble=True)
    res = rb.run(c, "docs", "model-b", broken, chunks=_docs(), old_embedder=a)
    assert res.action == "verify-failed" and res.recall_new < res.recall_old
    assert res.k == 2, "k is capped at half the sources; with k >= sources any embedder scores 1.0"
    assert qc.resolve(c, "docs").physical == "docs__model-a", "the alias must not move"
    assert c.collection_exists("docs__model-b"), "left in place, inactive, for inspection"
    assert not res.ok


def test_a_populated_collection_with_no_source_documents_is_refused():
    c = _client()
    _legacy(c, "docs", points=5)
    res = rb.run(c, "docs", "model-b", FakeEmbedder("B"), chunks=None)
    assert res.action == "refused" and "no source documents" in res.detail
    assert qc.resolve(c, "docs").kind == "legacy" and qc.resolve(c, "docs").points == 5

    class Snap:
        name = "docs.snapshot"

    c.create_snapshot = lambda collection_name: Snap()  # the in-memory client cannot snapshot
    res = rb.run(c, "docs", "model-b", FakeEmbedder("B"), chunks=None, allow_data_loss=True)
    assert res.action == "migrated"  # the caller said so


def test_a_legacy_collection_is_snapshotted_before_it_is_replaced(monkeypatch):
    c = _client()
    _legacy(c, "docs", points=2)
    taken = []

    class Snap:
        name = "docs-snap-1.snapshot"

    monkeypatch.setattr(
        c, "create_snapshot", lambda collection_name: taken.append(collection_name) or Snap()
    )
    res = rb.run(c, "docs", "model-b", FakeEmbedder("B"), chunks=_docs())
    assert res.action == "migrated" and res.snapshot == "docs-snap-1.snapshot" and taken == ["docs"]
    assert qc.resolve(c, "docs").kind == "alias"

    # A legacy original is restored from its snapshot (the server reads it from its own disk).
    recovered = []

    def fake_recover(name, location):
        recovered.append((name, location))
        _legacy(c, name, points=2)

    monkeypatch.setattr(c, "recover_snapshot", fake_recover)
    back = rb.rollback(c, "docs")
    assert back.action == "rolled-back"
    assert recovered == [("docs__legacy", "file:///qdrant/snapshots/docs/docs-snap-1.snapshot")]
    now = qc.resolve(c, "docs")
    assert (now.kind, now.physical, now.points) == ("alias", "docs__legacy", 2)
    with pytest.raises(qc.CollectionMismatch):  # the model is unrecorded: still loud
        qc.verify(c, "docs", "embeddinggemma-2:270m", DIM)


def test_a_failed_snapshot_restore_says_where_the_snapshot_is(monkeypatch):
    c = _client()
    _legacy(c, "docs", points=2)

    class Snap:
        name = "docs-snap-1.snapshot"

    monkeypatch.setattr(c, "create_snapshot", lambda collection_name: Snap())
    rb.run(c, "docs", "model-b", FakeEmbedder("B"), chunks=_docs())

    def boom(name, location):
        raise RuntimeError("no such file")

    monkeypatch.setattr(c, "recover_snapshot", boom)
    back = rb.rollback(c, "docs")
    assert back.action == "refused" and "/qdrant/snapshots/docs/docs-snap-1.snapshot" in back.detail
    assert qc.resolve(c, "docs").physical == "docs__model-b"  # unchanged


def test_a_legacy_collection_is_not_deleted_when_the_snapshot_fails(monkeypatch):
    c = _client()
    _legacy(c, "docs", points=2)

    def boom(collection_name):
        raise RuntimeError("snapshot storage unavailable")

    monkeypatch.setattr(c, "create_snapshot", boom)
    res = rb.run(c, "docs", "model-b", FakeEmbedder("B"), chunks=_docs())
    assert res.action == "refused" and "snapshot" in res.detail
    assert qc.resolve(c, "docs").kind == "legacy" and qc.resolve(c, "docs").points == 2


def test_the_history_is_recorded_for_rollback():
    c = _client()
    rb.run(c, "docs", "model-a", FakeEmbedder("A"), chunks=_docs())
    rb.run(c, "docs", "model-b", FakeEmbedder("B"), chunks=_docs())
    hist = json.loads(rb.state_path().read_text())["history"]
    assert [(h["from"], h["to"]) for h in hist] == [
        (None, "docs__model-a"),
        ("docs__model-a", "docs__model-b"),
    ]


def test_derived_queries_are_each_documents_title():
    q = dict((src, text) for text, src in rb.derive_queries(_docs()))
    assert q["health/hrv.md"].startswith("Heart rate variability")
    assert len(q) == 4


def test_the_shared_loader_chunks_the_tracked_health_corpus():
    """scripts/ingest_health_docs.py, reembed_cli and rag_seed chunk the same files the same way."""
    pytest.importorskip("langchain_text_splitters")
    from core import health_docs

    docs_dir = health_docs.default_docs_dir()
    assert docs_dir == REPO / "data" / "health" / "documents"
    chunks = health_docs.load_health_documents(docs_dir, verbose=False)
    assert len(chunks) > 20
    assert {c.metadata["source"] for c in chunks} == {
        f"health/{p.name}" for p in docs_dir.glob("*.md")
    }
    # every document yields a title the verification can query by: its own
    # heading, not a front-matter line
    queries = rb.derive_queries(chunks)
    assert len(queries) == len(list(docs_dir.glob("*.md")))
    assert not any(q.startswith(("category:", "source:", "---")) for q, _ in queries)
    # The front matter is metadata, not text. A `---` inside the body is a
    # section rule and stays.
    assert not any("source: health/" in c.page_content for c in chunks), "front matter is not indexed"
