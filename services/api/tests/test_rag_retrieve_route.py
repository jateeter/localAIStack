"""POST /rag/retrieve: one retrieval, its signal written, no generation (RealityEngine_CI#518)."""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from graphs import rag_graph  # noqa: E402
from routers import rag  # noqa: E402


class _Doc:
    def __init__(self, source):
        self.metadata = {"source": source}


def test_retrieve_route_runs_retrieval_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rag_graph,
        "retrieve",
        lambda state: (
            calls.append(state["question"]) or {"documents": [_Doc("a.md"), _Doc("b.md")]}
        ),
    )
    app = FastAPI()
    app.include_router(rag.router)
    r = TestClient(app).post("/rag/retrieve", json={"question": "sleep"})
    assert r.status_code == 200
    assert r.json() == {"doc_count": 2, "sources": ["a.md", "b.md"]}
    assert calls == ["sleep"]
