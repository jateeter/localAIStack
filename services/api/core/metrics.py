"""Prometheus metrics for the API: what a regression's localAI traffic did.

Without these, Grafana's localAI panels showed only up/down state and
inventory (localAIStack#180). This module keeps thread-safe counters and
histograms and renders them in the Prometheus text exposition format (0.0.4),
with no client dependency. ``GET /metrics`` serves them on the API's own
listener, so on the lane they travel the same TLS 1.3 mTLS as every route.

Series:
  localai_requests_total{route,method,status}      every HTTP request, by route template
  localai_request_duration_seconds{route}           histogram of the same
  localai_rag_queries_total{route,outcome}          /rag/query, /graph/rag
  localai_agent_turns_total{outcome}                /graph/agent
  localai_agent_turn_duration_seconds               histogram of /graph/agent
  localai_engine_calls_total{instance,outcome}      the per-engine calls of a fan-out (core/engine_fanout.py)
  localai_engine_call_duration_seconds{instance}    histogram of the same
  localai_engine_interactions_total{result}         agree(): agreed, diverged, single
  localai_llm_drift_records_total{event}            core/llm_drift.py: compared, diverged
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
_lock = threading.Lock()
_counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
_hists: dict[str, dict[tuple, list]] = defaultdict(dict)

_HELP = {
    "localai_requests_total": ("counter", "HTTP requests by route template, method and status."),
    "localai_request_duration_seconds": ("histogram", "HTTP request duration by route template."),
    "localai_rag_queries_total": ("counter", "RAG queries (/rag/query, /graph/rag) by outcome."),
    "localai_agent_turns_total": ("counter", "Agent turns (/graph/agent) by outcome."),
    "localai_agent_turn_duration_seconds": ("histogram", "Agent turn (/graph/agent) duration."),
    "localai_engine_calls_total": ("counter", "Per-engine calls of an engine fan-out by instance and outcome."),
    "localai_engine_call_duration_seconds": ("histogram", "Per-engine fan-out call duration by instance."),
    "localai_engine_interactions_total": ("counter", "Multi-engine interactions by result: agreed, diverged, single."),
    "localai_llm_drift_records_total": ("counter", "LLM drift records written, by event."),
}

_RAG_ROUTES = {"/rag/query", "/graph/rag"}
_AGENT_ROUTE = "/graph/agent"


def _key(labels: dict[str, str]) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def inc(name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
    with _lock:
        _counters[name][_key(labels or {})] += value


def observe(name: str, seconds: float, labels: dict[str, str] | None = None) -> None:
    key = _key(labels or {})
    with _lock:
        h = _hists[name].get(key)
        if h is None:
            h = _hists[name][key] = [[0] * len(_BUCKETS), 0.0, 0]
        for i, b in enumerate(_BUCKETS):
            if seconds <= b:
                h[0][i] += 1
        h[1] += seconds
        h[2] += 1


def outcome_of(status: int) -> str:
    return "ok" if status < 400 else "client_error" if status < 500 else "error"


def record_request(route: str, method: str, status: int, seconds: float) -> None:
    """One HTTP request, and the RAG / agent series derived from its route."""
    inc("localai_requests_total", {"route": route, "method": method, "status": str(status)})
    observe("localai_request_duration_seconds", seconds, {"route": route})
    if method == "POST" and route in _RAG_ROUTES:
        inc("localai_rag_queries_total", {"route": route, "outcome": outcome_of(status)})
    if method == "POST" and route == _AGENT_ROUTE:
        inc("localai_agent_turns_total", {"outcome": outcome_of(status)})
        observe("localai_agent_turn_duration_seconds", seconds)


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt(labels: tuple, extra: tuple = ()) -> str:
    items = list(labels) + list(extra)
    return "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in items) + "}" if items else ""


def render() -> str:
    lines: list[str] = []
    with _lock:
        names = sorted(set(_counters) | set(_hists) | set(_HELP))
        for name in names:
            kind, help_text = _HELP.get(name, ("untyped", name))
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            if kind == "histogram":
                for labels, (buckets, total, count) in sorted(_hists.get(name, {}).items()):
                    for b, n in zip(_BUCKETS, buckets):
                        lines.append(f"{name}_bucket{_fmt(labels, (('le', repr(b)),))} {n}")
                    lines.append(f"{name}_bucket{_fmt(labels, (('le', '+Inf'),))} {count}")
                    lines.append(f"{name}_sum{_fmt(labels)} {total}")
                    lines.append(f"{name}_count{_fmt(labels)} {count}")
            else:
                for labels, value in sorted(_counters.get(name, {}).items()):
                    lines.append(f"{name}{_fmt(labels)} {value:g}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    with _lock:
        _counters.clear()
        _hists.clear()


class MetricsMiddleware:
    """Pure-ASGI: times each HTTP request and records it by route template.

    The template (``/rag/query``, not a concrete path) comes from the matched
    route in the scope after the app has handled the request, so label
    cardinality stays bounded; an unmatched path is recorded as ``unmatched``.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.perf_counter()
        status = {"code": 500}

        async def _send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            if template != "/metrics":
                record_request(template, scope.get("method", "GET"), status["code"], time.perf_counter() - start)
