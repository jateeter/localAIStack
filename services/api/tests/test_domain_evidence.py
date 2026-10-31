import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from core import collections
from core.domain_evidence import EDITION, EvidenceIndex, Unresolved
from core.entity_evidence import EntityProjection, ProjectionLag
from routers import domain_evidence as routes


class Embedder:
    calls = 0

    def embed_documents(self, texts):
        self.calls += len(texts)
        return [[1.0, 0.0, 0.0] for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0]


def bundle(
    path, text="Synthetic scoped source fixture. Conditions and exceptions belong together."
):
    path.mkdir(exist_ok=True)
    (path / "documents").mkdir(exist_ok=True)
    (path / "documents/a.md").write_text(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = dict(
        documentId="synthetic-standard",
        path="documents/a.md",
        proposedLogicalCollection="ifrs_smes_2025_standard",
        documentRole="standard_source",
        edition=EDITION,
        sectionId="01",
        authority="synthetic fixture, not accounting authority",
        status="SYNTHETIC",
        sourceSHA256=digest,
        contentSHA256=digest,
        sourcePath="fixture",
    )
    (path / "ingestion-manifest.json").write_text(
        json.dumps(dict(totalDocuments=1, documents=[item]))
    )
    return path


@pytest.fixture
def index(tmp_path):
    return EvidenceIndex(
        tmp_path / "state",
        QdrantClient(":memory:"),
        Embedder(),
        "fixture-embedder",
        3,
        lambda client, name: collections.ensure(client, name, "fixture-embedder", 3),
    )


def request(**extra):
    return dict(
        domain="ifrs-accounting",
        documentRole="standard_source",
        edition=EDITION,
        question="conditions",
        **extra,
    )


def test_reconciliation_is_idempotent_replaces_removes_and_survives_reopen(index, tmp_path):
    root = bundle(tmp_path / "bundle")
    first = index.reconcile(root)
    count = index.embedder.calls
    assert index.reconcile(root)["added"] == 0
    assert index.embedder.calls == count
    receipt = index.retrieve(request())
    assert (
        receipt["passages"][0]["textSHA256"]
        == hashlib.sha256(receipt["passages"][0]["text"].encode()).hexdigest()
    )
    assert index.receipt(receipt["interactionId"]) == receipt
    bundle(root, "Changed synthetic source fixture.")
    changed = index.reconcile(root)
    assert changed["removed"] == 1 and changed["added"] == 1
    with pytest.raises(Unresolved, match="Stale"):
        index.retrieve(request(indexSnapshot=first["indexSnapshot"]))
    (root / "ingestion-manifest.json").write_text(json.dumps(dict(totalDocuments=0, documents=[])))
    assert index.reconcile(root)["removed"] == 1
    assert index.retrieve(request())["status"] == "UNRESOLVED_NO_PASSAGE"


def test_scope_filters_are_enforced_in_qdrant_not_prompts(index, tmp_path):
    index.reconcile(bundle(tmp_path / "bundle"))
    assert not index.retrieve(request(documentId="wrong-document"))["passages"]
    assert not index.retrieve(request(sourceSHA256="wrong-source"))["passages"]
    assert not index.retrieve({**request(), "documentRole": "derived_review"})["passages"]
    with pytest.raises(Unresolved):
        index.retrieve({**request(), "edition": "old-edition"})
    with pytest.raises(Unresolved):
        index.retrieve({**request(), "domain": "health"})


def test_tamper_and_routing_and_incomplete_index_fail_closed(index, tmp_path):
    root = bundle(tmp_path / "bundle")
    manifest = root / "ingestion-manifest.json"
    data = json.loads(manifest.read_text())
    data["documents"][0]["proposedLogicalCollection"] = "health_docs"
    manifest.write_text(json.dumps(data))
    with pytest.raises(Unresolved):
        index.reconcile(root)
    bundle(root)
    index.reconcile(root)
    with index.db() as db:
        db.execute("UPDATE snapshot SET ready=0")
    with pytest.raises(Unresolved, match="incomplete"):
        index.retrieve(request())


def test_foreign_documents_and_health_collections_are_not_reconciled(index, tmp_path):
    import uuid

    from qdrant_client.models import PointStruct

    index.ensure(index.client, "health_docs")
    point = PointStruct(
        id=str(uuid.uuid4()),
        vector=[1.0, 0.0, 0.0],
        payload={"page_content": "foreign health document", "metadata": {"domain": "health"}},
    )
    index.client.upsert("health_docs", [point])
    root = bundle(tmp_path / "bundle")
    index.reconcile(root)
    foreign = PointStruct(
        id=str(uuid.uuid4()),
        vector=[1.0, 0.0, 0.0],
        payload={
            "page_content": "foreign hand-ingested IFRS note",
            "metadata": {"domain": "ifrs-accounting"},
        },
    )
    index.client.upsert("ifrs_smes_2025_standard", [foreign])
    (root / "ingestion-manifest.json").write_text(json.dumps(dict(totalDocuments=0, documents=[])))
    index.reconcile(root)
    assert index.client.count("health_docs").count == 1
    assert index.client.count("ifrs_smes_2025_standard").count == 1


def test_generation_is_request_local_and_unknown_citations_are_unresolved(index, tmp_path):
    index.reconcile(bundle(tmp_path / "bundle"))
    seen = []

    def generator(messages):
        seen.append(messages)
        return json.dumps(
            {"answer": "unsupported synthetic answer", "citations": ["invented-chunk"]}
        )

    result = index.generate(
        request(), generator, {"provider": "synthetic-fixture", "model": "fixture"}
    )
    assert result["generationStatus"] == "UNRESOLVED_UNSUPPORTED_GENERATION"
    assert result["answer"] is None and result["engineWrites"] == 0
    assert len(seen[0]) == 2 and seen[0][1]["role"] == "user"


def test_observational_api_auth_and_receipt(index, tmp_path, monkeypatch):
    index.reconcile(bundle(tmp_path / "bundle"))
    monkeypatch.setenv("IFRS_EVIDENCE_TOKEN", "fixture-service-token")
    monkeypatch.setattr(routes, "index", lambda: index)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    assert client.post("/rag/evidence/retrieve", json=request()).status_code == 401
    response = client.post(
        "/rag/evidence/retrieve",
        json=request(),
        headers={"Authorization": "Bearer fixture-service-token"},
    )
    assert response.status_code == 200 and response.json()["engineWrites"] == 0
    assert response.json()["generationStatus"] == "NOT_REQUESTED"


def test_projection_scope_out_of_order_and_stale_canonical(tmp_path):
    projection = EntityProjection(tmp_path, evidence_class="synthetic")
    event = dict(
        entityId="fixture-A",
        period="2026",
        assetTypeId="SOURCE-REGISTER",
        revision=1,
        sequence=1,
        status="APPROVED",
        contentHash="content",
        review={"reviewerName": "independent fixture reviewer"},
        evidenceRefs=["fixture-record"],
        sourceRefs=["fixture-passage"],
        synthetic=True,
        evidenceClass="synthetic",
        canonicalStoreId="fixture-store",
    )
    projection.apply(event)
    projection.apply(event)
    canonical = {
        "head": {k: event[k] for k in ("revision", "contentHash", "status")},
        "revision": {"contentHash": "content"},
        "canonicalStoreId": "fixture-store",
    }
    assert (
        projection.current("fixture-A", "2026", "SOURCE-REGISTER", lambda *a: canonical)[
            "evidenceClass"
        ]
        == "synthetic"
    )
    with pytest.raises(ProjectionLag):
        projection.current("fixture-B", "2026", "SOURCE-REGISTER", lambda *a: canonical)
    stale = {**event, "sequence": 2, "status": "STALE"}
    projection.apply(stale)
    projection.apply(event)
    with pytest.raises(ProjectionLag):
        projection.current("fixture-A", "2026", "SOURCE-REGISTER", lambda *a: canonical)
    actual = EntityProjection(tmp_path / "actual")
    with pytest.raises(ValueError, match="Evidence class"):
        actual.apply(event)
    with pytest.raises(ProjectionLag, match="store changed"):
        projection.apply({**event, "canonicalStoreId": "different-store", "sequence": 3})
