# localAIStack API Guidance

This directory contains the FastAPI service for local AI/RAG/vector workflows.

- `main.py` is the service entrypoint.
- `config.py` owns runtime settings.
- `core/reality_bridge.py` is the key RE/PE integration surface.
- `routers/` exposes chat, graph, GraphQL, health, and RAG APIs.
- `graphs/` contains agent/RAG flows.
- Use Pyright/Ruff and run relevant API tests after behavior changes.

## Standing rules

The standing rules are in
`../../../RealityEngine_CI/docs/ENGINEERING_CONTRACT.md`, which is
authoritative. They are not listed or summarised here, so this file never
falls behind the contract. Read it before changing anything here.

## Metrics

`GET /metrics` serves Prometheus text (`core/metrics.py`, no client dependency): requests by route template, RAG queries, agent turns, per-engine fan-out calls, multi-engine interaction results and LLM drift records (localAIStack#180). It is served on the API listener, so on the lane it is TLS 1.3 with mTLS like every route. Any new interaction worth watching gets a series there.
