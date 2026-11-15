"""core/metrics.py and GET /metrics (localAIStack#180)."""

import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core import metrics
from core.metrics import MetricsMiddleware
from routers import metrics as metrics_router


@pytest.fixture(autouse=True)
def _fresh():
    metrics.reset()
    yield
    metrics.reset()


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)
    app.include_router(metrics_router.router)

    @app.post("/rag/query")
    def rag_query():
        return {"answer": "a"}

    @app.post("/graph/agent")
    def agent():
        raise HTTPException(status_code=502, detail="model failed")

    @app.get("/items/{item_id}")
    def item(item_id: str):
        return {"id": item_id}

    return app


def _value(text: str, series: str) -> float:
    m = re.search(r"^" + re.escape(series) + r" (\S+)$", text, re.M)
    assert m, f"{series} not in:\n{text}"
    return float(m.group(1))


def test_requests_are_counted_by_route_template_not_path():
    c = TestClient(_app())
    c.get("/items/1")
    c.get("/items/2")
    text = c.get("/metrics").text
    assert (
        _value(text, 'localai_requests_total{method="GET",route="/items/{item_id}",status="200"}')
        == 2
    )
    assert "/items/1" not in text and "/items/2" not in text


def test_rag_and_agent_series_follow_their_routes_and_outcomes():
    c = TestClient(_app())
    c.post("/rag/query")
    c.post("/graph/agent")
    text = c.get("/metrics").text
    assert _value(text, 'localai_rag_queries_total{outcome="ok",route="/rag/query"}') == 1
    assert _value(text, 'localai_agent_turns_total{outcome="error"}') == 1
    assert _value(text, "localai_agent_turn_duration_seconds_count") == 1


def test_metrics_endpoint_is_prometheus_text_and_not_self_counted():
    c = TestClient(_app())
    r = c.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# TYPE localai_requests_total counter" in r.text
    assert 'route="/metrics"' not in c.get("/metrics").text


def test_histogram_buckets_are_cumulative():
    metrics.observe("localai_request_duration_seconds", 0.03, {"route": "/x"})
    metrics.observe("localai_request_duration_seconds", 3.0, {"route": "/x"})
    text = metrics.render()
    assert _value(text, 'localai_request_duration_seconds_bucket{route="/x",le="0.025"}') == 0
    assert _value(text, 'localai_request_duration_seconds_bucket{route="/x",le="0.05"}') == 1
    assert _value(text, 'localai_request_duration_seconds_bucket{route="/x",le="5.0"}') == 2
    assert _value(text, 'localai_request_duration_seconds_bucket{route="/x",le="+Inf"}') == 2
    assert _value(text, 'localai_request_duration_seconds_count{route="/x"}') == 2


def test_fan_out_calls_and_interactions_are_counted(monkeypatch):
    from core import engine_fanout

    monkeypatch.setattr(
        engine_fanout, "interaction_targets", lambda: [{"instance": "cpp-1"}, {"instance": "lsp-1"}]
    )
    results = engine_fanout.fan_out(lambda t: 1)
    engine_fanout.agree("t", results, 0)
    text = metrics.render()
    assert _value(text, 'localai_engine_calls_total{instance="cpp-1",outcome="ok"}') == 1
    assert _value(text, 'localai_engine_calls_total{instance="lsp-1",outcome="ok"}') == 1
    assert _value(text, 'localai_engine_interactions_total{result="agreed"}') == 1

    def boom(t):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        engine_fanout.fan_out(boom)
    assert (
        _value(metrics.render(), 'localai_engine_calls_total{instance="cpp-1",outcome="error"}')
        == 1
    )
