"""The default RAG corpus is reconciled, not appended (core/rag_seed.py).

data/health/documents is seeded into localai_docs and health_docs at startup.
These pin that a restart adds nothing, an edited document replaces its old
chunks, a re-embed's copy is not duplicated, and documents ingested by hand
are never touched. Qdrant's in-memory client and a deterministic fake embedder
stand in for the server and Ollama.
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass, field

import pytest

pytest.importorskip("qdrant_client")

from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.models import Distance, PointStruct, VectorParams  # noqa: E402

from core import rag_seed  # noqa: E402

DIM = 16
COLL = "localai_docs"
REPO = pathlib.Path(__file__).resolve().parents[3]


@dataclass
class Doc:
    page_content: str
    metadata: dict = field(default_factory=dict)


class FakeEmbedder:
    def __init__(self):
        self.embedded = 0

    def embed_documents(self, texts):
        self.embedded += len(texts)
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text):
        h = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in h[:DIM]]


def _client():
    c = QdrantClient(":memory:")
    c.create_collection(COLL, vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))
    return c


def _chunks(*pairs):
    # The loader's fingerprint: source health/<file>.md and category <file stem, spaces>.
    return [
        Doc(
            text,
            {"source": f"health/{name}", "category": name.removesuffix(".md").replace("_", " ")},
        )
        for name, text in pairs
    ]


def _all(client):
    points, _ = client.scroll(COLL, limit=1000, with_payload=True)
    return [(p.payload["metadata"].get("source"), p.payload["page_content"]) for p in points]


SEED = (("sleep_quality.md", "Sleep is restorative."), ("hrv.md", "HRV reflects recovery."))


def test_first_seed_adds_every_chunk_tagged():
    client, emb = _client(), FakeEmbedder()
    res = rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    assert (res.expected, res.added, res.kept, res.removed) == (2, 2, 0, 0)
    points, _ = client.scroll(COLL, limit=10, with_payload=True)
    assert all(p.payload["metadata"]["seed"] == rag_seed.SEED_TAG for p in points)
    assert res.sources == ["health/hrv.md", "health/sleep_quality.md"]


def test_a_restart_adds_nothing():
    client, emb = _client(), FakeEmbedder()
    rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    before = emb.embedded
    res = rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    assert (res.added, res.kept, res.removed) == (0, 2, 0)
    assert emb.embedded == before, "nothing is re-embedded when the corpus is unchanged"
    assert len(_all(client)) == 2


def test_an_edited_document_replaces_its_old_chunk():
    client, emb = _client(), FakeEmbedder()
    rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    edited = (("sleep_quality.md", "Sleep is restorative; aim for seven hours."), SEED[1])
    res = rag_seed.reconcile(client, COLL, _chunks(*edited), emb)
    assert (res.added, res.kept, res.removed) == (1, 1, 1)
    assert sorted(_all(client)) == sorted(
        [("health/sleep_quality.md", edited[0][1]), ("health/hrv.md", SEED[1][1])]
    )


def test_a_removed_document_leaves_the_collection():
    client, emb = _client(), FakeEmbedder()
    rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    res = rag_seed.reconcile(client, COLL, _chunks(SEED[1]), emb)
    assert res.removed == 1
    assert _all(client) == [("health/hrv.md", SEED[1][1])]


def test_documents_ingested_by_hand_are_never_touched():
    client, emb = _client(), FakeEmbedder()
    client.upsert(
        COLL,
        [
            PointStruct(
                id=900,
                vector=FakeEmbedder._vec("mine"),
                payload={"page_content": "my own notes", "metadata": {"source": "notes.md"}},
            )
        ],
    )
    rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    rag_seed.reconcile(client, COLL, _chunks(SEED[1]), emb)
    assert ("notes.md", "my own notes") in _all(client)


def _point(i, source, text, **meta):
    return PointStruct(
        id=i,
        vector=FakeEmbedder._vec(text),
        payload={"page_content": text, "metadata": {"source": source, **meta}},
    )


def test_a_hand_ingested_document_sharing_a_seed_file_name_is_never_deleted():
    # Same source name as a seeded file, different text, no seed tag and not the
    # loader's category: someone ingested their own "health/hrv.md".
    client, emb = _client(), FakeEmbedder()
    client.upsert(COLL, [_point(900, "health/hrv.md", "My own HRV notes.")])
    res = rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    assert ("health/hrv.md", "My own HRV notes.") in _all(client)
    assert res.foreign == 1 and res.removed == 0
    # and a later change to the seed still leaves it alone
    res = rag_seed.reconcile(client, COLL, _chunks(SEED[0]), emb)
    assert ("health/hrv.md", "My own HRV notes.") in _all(client)


def test_an_untagged_copy_with_the_loaders_fingerprint_is_the_seeds_to_replace():
    # What scripts/ingest_health_docs.py wrote before the seed tag existed.
    client, emb = _client(), FakeEmbedder()
    client.upsert(COLL, [_point(1, "health/hrv.md", "Old loader text for HRV.", category="hrv")])
    res = rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    assert ("health/hrv.md", "Old loader text for HRV.") not in _all(client)
    assert res.removed == 1 and res.foreign == 0


def test_seed_ownership_is_the_tag_or_the_exact_fingerprint():
    src = ["health/hrv_interpretation.md"]
    assert rag_seed.seed_owned({"seed": rag_seed.SEED_TAG}, src)
    assert rag_seed.seed_owned(
        {"source": "health/hrv_interpretation.md", "category": "hrv interpretation"}, src
    )
    assert not rag_seed.seed_owned({"source": "health/hrv_interpretation.md"}, src)
    assert not rag_seed.seed_owned(
        {"source": "health/hrv_interpretation.md", "category": "mine"}, src
    )
    assert not rag_seed.seed_owned({"source": "notes.md", "category": "notes"}, src)


def test_a_reembed_copy_counts_and_duplicates_collapse():
    # reembed._upsert rebuilds health_docs with integer ids and no seed tag.
    client, emb = _client(), FakeEmbedder()
    copy = [
        PointStruct(
            id=i,
            vector=FakeEmbedder._vec(text),
            payload={
                "page_content": text,
                "metadata": {
                    "source": f"health/{name}",
                    "category": name.removesuffix(".md").replace("_", " "),
                },
            },
        )
        for i, (name, text) in enumerate([SEED[0], SEED[0], SEED[1]], start=1)
    ]
    client.upsert(COLL, copy)
    res = rag_seed.reconcile(client, COLL, _chunks(*SEED), emb)
    assert (res.added, res.kept, res.removed) == (0, 2, 1), (
        "the duplicate goes, nothing is re-added"
    )
    assert sorted(_all(client)) == sorted(
        [("health/sleep_quality.md", SEED[0][1]), ("health/hrv.md", SEED[1][1])]
    )


def test_the_real_health_documents_load_as_the_seed():
    pytest.importorskip("langchain_text_splitters")
    from core.health_docs import load_health_documents

    docs_dir = REPO / "data" / "health" / "documents"
    chunks = load_health_documents(docs_dir, verbose=False)
    sources = {c.metadata["source"] for c in chunks}
    assert sources == {f"health/{p.name}" for p in docs_dir.glob("*.md")}
    client, emb = _client(), FakeEmbedder()
    res = rag_seed.reconcile(client, COLL, chunks, emb)
    assert (
        res.added
        == res.expected
        == len({rag_seed.chunk_key(c.metadata["source"], c.page_content) for c in chunks})
    )
    assert rag_seed.reconcile(client, COLL, chunks, emb).added == 0
