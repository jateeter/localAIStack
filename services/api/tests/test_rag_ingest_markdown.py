"""POST /rag/ingest/file with Markdown: read as text, like the default corpus.

``UnstructuredMarkdownLoader`` pip-installed a spaCy model into the running
container on the first upload, or hung where it could not (core/health_docs.py).
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_text_splitters")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import rag  # noqa: E402

DOC = """---
title: Sleep and recovery
category: recovery
---
# Sleep and recovery

Deep sleep is when most physical recovery happens.

---

## Section after a rule

A horizontal rule in the body is content, not front matter.
"""


class _Store:
    def __init__(self):
        self.docs = []

    def add_documents(self, docs):
        self.docs.extend(docs)
        return [str(i) for i, _ in enumerate(docs)]


def _client(monkeypatch):
    store = _Store()
    monkeypatch.setattr(rag, "get_vector_store", lambda: store)
    app = FastAPI()
    app.include_router(rag.router)
    return TestClient(app), store


def test_markdown_upload_is_read_as_text(monkeypatch):
    client, store = _client(monkeypatch)
    r = client.post(
        "/rag/ingest/file",
        files={"file": ("sleep_and_recovery.md", DOC.encode(), "text/markdown")},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"filename": "sleep_and_recovery.md", "ingested_chunks": len(store.docs)}
    text = "\n".join(d.page_content for d in store.docs)
    assert text.startswith("# Sleep and recovery")
    assert "title: Sleep and recovery" not in text
    assert "A horizontal rule in the body is content" in text
    assert {d.metadata["source"] for d in store.docs} == {"sleep_and_recovery.md"}


def test_markdown_upload_runs_no_nlp_pipeline(monkeypatch):
    client, _ = _client(monkeypatch)
    before = set(sys.modules)
    r = client.post("/rag/ingest/file", files={"file": ("a.md", b"# A\n\nbody\n", "text/markdown")})
    assert r.status_code == 200, r.text
    loaded = {m.split(".")[0] for m in set(sys.modules) - before}
    assert not loaded & {"unstructured", "spacy", "nltk"}, loaded
    assert rag.LOADERS[".md"] is rag.MarkdownLoader
