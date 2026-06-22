# localAIStack API Guidance

This directory contains the FastAPI service for local AI/RAG/vector workflows.

- `main.py` is the service entrypoint.
- `config.py` owns runtime settings.
- `core/reality_bridge.py` is the key RE/PE integration surface.
- `routers/` exposes chat, graph, GraphQL, health, and RAG APIs.
- `graphs/` contains agent/RAG flows.
- Use Pyright/Ruff and run relevant API tests after behavior changes.

