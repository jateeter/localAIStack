# Codex Guidance: localAIStack

Read `claude.md` for the current codebase map and bridge context.

## Role

This repo provides local AI/RAG/vector services and a RealityEngine bridge. It must use the active RE/PE endpoints from the composed universe when launched by CI.

## Development Rules

- Treat `services/api/core/reality_bridge.py` and `services/api/config.py` as the primary integration surfaces.
- Verify live environment values against the registry-selected RE/PE pair before debugging RAG behavior.
- Keep local AI provider evidence separate from OpenClaw ACP evidence.
- Avoid committing local model data, vector stores, or secrets.

## Bug Triage

- For bridge failures, check service health, configured RE/PE URLs, registry values, and direct endpoint responses.
- For RAG/vector failures, verify embeddings, vector store state, and topology construction separately.
- For container failures, inspect Docker Compose config and live container environment.

## Verification

Common commands:

```bash
make health
make query
make agent
pytest services/api/tests --ignore=services/api/tests/e2e
```

## Artifact Hygiene

Do not commit `.env`, local volumes, model downloads, runtime caches, `.pytest_cache`, or generated vector data unless explicitly requested.

