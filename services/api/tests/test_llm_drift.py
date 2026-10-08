"""LLM drift between engines is recorded, never cached away (RealityEngine_CI#518)."""

from __future__ import annotations

import json

import pytest

from core import engine_fanout, llm_drift


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAI_STATE_DIR", str(tmp_path))
    llm_drift.reset()
    engine_fanout.reset_parity_report()
    yield tmp_path
    llm_drift.reset()


def _rows(state_dir):
    path = state_dir / "llm-drift.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_two_engines_asking_the_same_question_are_compared_and_persisted(state_dir):
    q = {"question": "how did I sleep?"}
    assert llm_drift.observe("rag.query", q, {"answer": "Well."}, instance="cpp-1") is None
    rec = llm_drift.observe("rag.query", q, {"answer": "Restlessly."}, instance="lsp-1")
    assert rec is not None
    assert rec["engines"] == ["cpp-1", "lsp-1"]
    assert rec["readings"] == {"cpp-1": {"answer": "Well."}, "lsp-1": {"answer": "Restlessly."}}
    assert rec["difference"] == 1.0  # differing text is a split answer
    assert [r["event"] for r in _rows(state_dir)] == ["compared"]
    assert _rows(state_dir)[0]["readings"]["lsp-1"]["answer"] == "Restlessly."


def test_a_third_engine_extends_the_comparison(state_dir):
    q = {"question": "q"}
    llm_drift.observe("rag.query", q, {"answer": "a"}, instance="cpp-1")
    llm_drift.observe("rag.query", q, {"answer": "a"}, instance="lsp-1")
    rec = llm_drift.observe("rag.query", q, {"answer": "b"}, instance="scala-1")
    assert rec is not None
    assert rec["engines"] == ["cpp-1", "lsp-1", "scala-1"]
    assert rec["difference"] == 1.0
    summary = llm_drift.report()["rag.query"]
    assert summary == {"comparisons": 2, "drifted": 1, "mean": 0.5, "max": 1.0}


def test_identical_answers_score_zero(state_dir):
    q = {"question": "q"}
    llm_drift.observe("chat", q, {"content": "same"}, instance="cpp-1")
    rec = llm_drift.observe("chat", q, {"content": "same"}, instance="scala-1")
    assert rec is not None
    assert rec["difference"] == 0.0
    assert llm_drift.report()["chat"]["drifted"] == 0


def test_different_questions_do_not_pair(state_dir):
    llm_drift.observe("rag.query", {"question": "a"}, {"answer": "x"}, instance="cpp-1")
    assert (
        llm_drift.observe("rag.query", {"question": "b"}, {"answer": "y"}, instance="lsp-1") is None
    )
    assert _rows(state_dir) == []


def test_the_same_engine_asking_again_starts_a_new_round(state_dir):
    q = {"question": "q"}
    llm_drift.observe("rag.query", q, {"answer": "x"}, instance="cpp-1")
    assert llm_drift.observe("rag.query", q, {"answer": "y"}, instance="cpp-1") is None
    rec = llm_drift.observe("rag.query", q, {"answer": "y"}, instance="lsp-1")
    assert rec is not None
    assert rec["readings"] == {"cpp-1": {"answer": "y"}, "lsp-1": {"answer": "y"}}


def test_answers_outside_the_window_do_not_pair(state_dir, monkeypatch):
    monkeypatch.setenv("LOCALAI_DRIFT_WINDOW_S", "10")
    clock = iter([1000.0, 1011.0])
    monkeypatch.setattr(llm_drift.time, "time", lambda: next(clock))
    llm_drift.observe("rag.query", {"question": "q"}, {"answer": "x"}, instance="cpp-1")
    assert (
        llm_drift.observe("rag.query", {"question": "q"}, {"answer": "y"}, instance="lsp-1") is None
    )


def test_a_broadcast_interaction_is_not_observed(state_dir):
    # No initiating engine: localAI computed the values once and agree() compares them.
    assert llm_drift.observe("rag.query", {"question": "q"}, {"answer": "x"}) is None
    assert _rows(state_dir) == []


def test_a_broadcast_divergence_lands_in_the_same_corpus(state_dir):
    engine_fanout.agree("rag.routing", [("cpp-1", "generate"), ("lsp-1", "rewrite")], "generate")
    rows = _rows(state_dir)
    assert len(rows) == 1
    assert rows[0]["event"] == "diverged" and rows[0]["origin"] == "broadcast"
    assert rows[0]["readings"] == {"cpp-1": "generate", "lsp-1": "rewrite"}


def test_agreement_is_not_written(state_dir):
    engine_fanout.agree("rag.routing", [("cpp-1", "generate"), ("lsp-1", "generate")], "generate")
    assert _rows(state_dir) == []


def test_parity_report_carries_the_drift_summary(state_dir):
    llm_drift.observe("chat", {"m": 1}, {"content": "a"}, instance="cpp-1")
    llm_drift.observe("chat", {"m": 1}, {"content": "b"}, instance="lsp-1")
    assert engine_fanout.parity_report()["llmDrift"]["chat"]["drifted"] == 1


def test_records_read_back_from_disk_survive_a_restart(state_dir):
    llm_drift.observe("chat", {"m": 1}, {"content": "a"}, instance="cpp-1")
    llm_drift.observe("chat", {"m": 1}, {"content": "b"}, instance="lsp-1")
    llm_drift.reset()  # in-memory state gone, as after a restart
    assert [r["label"] for r in llm_drift.records()] == ["chat"]
    assert llm_drift.records(label="rag.query") == []


def test_an_unwritable_state_dir_never_fails_the_request(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setenv("LOCALAI_STATE_DIR", str(blocker))
    llm_drift.observe("chat", {"m": 1}, {"content": "a"}, instance="cpp-1")
    rec = llm_drift.observe("chat", {"m": 1}, {"content": "b"}, instance="lsp-1")
    assert rec is not None
    assert [r["label"] for r in llm_drift.records()] == ["chat"]  # kept in memory


def test_rag_query_route_records_drift_without_changing_the_answer(monkeypatch):
    pytest.importorskip("langchain_core")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import rag

    answers = iter(["Well.", "Restlessly."])

    class _Graph:
        def invoke(self, state):
            return {"generation": next(answers), "documents": [], "rewrite_count": 0}

    monkeypatch.setattr(rag, "get_rag_graph", lambda: _Graph())
    app = FastAPI()
    app.include_router(rag.router)
    client = TestClient(app)
    seen = []
    for instance in ("cpp-1", "lsp-1"):
        monkeypatch.setattr(llm_drift, "initiating_instance", lambda i=instance: i)
        seen.append(client.post("/rag/query", json={"question": "sleep?"}).json()["answer"])
    assert seen == ["Well.", "Restlessly."]  # each engine got its own generation
    rec = llm_drift.records()[-1]
    assert rec["engines"] == ["cpp-1", "lsp-1"]
    assert rec["readings"]["lsp-1"]["answer"] == "Restlessly."
    # A structure scores the mean over its fields: the answer split (1), the
    # sources and rewrite count equal (0, 0).
    assert rec["difference"] == pytest.approx(1 / 3)


def test_drift_route_returns_records_and_summary():
    pytest.importorskip("fastapi")  # the hosted unit job's minimal environment has none
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import observations

    llm_drift.observe("chat", {"m": 1}, {"content": "a"}, instance="cpp-1")
    llm_drift.observe("chat", {"m": 1}, {"content": "b"}, instance="lsp-1")
    app = FastAPI()
    app.include_router(observations.router)
    body = TestClient(app).get("/observations/drift").json()
    assert body["count"] == 1
    assert body["summary"]["chat"]["comparisons"] == 1
    assert body["records"][0]["readings"]["lsp-1"]["content"] == "b"
