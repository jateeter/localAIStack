# localAIStack Guidance

Last reviewed: 2026-06-22

See `/Users/johnt/workspace/GitHub/claude.md` for the integrated application map. Update both this file and the root map when local AI provider responsibilities, bridge endpoints, or runtime composition changes.

## Role

This repo provides local AI/RAG/vector services and a RealityEngine bridge. It should use the active RE/PE endpoints selected by the integrated universe rather than stale hard-coded endpoints.

## Codebase Map

- `services/api/main.py`: FastAPI entrypoint.
- `services/api/config.py`: runtime configuration.
- `services/api/core/reality_bridge.py`: RE/PE bridge.
- `services/api/core/embeddings.py`: embedding support.
- `services/api/core/vector_store.py`: vector store behavior.
- `services/api/core/topology_builder.py`: topology/graph construction.
- `services/api/core/model_registry.py`: model registry loader; joins registry
  metadata with the `.env` selection and live Ollama tags.
- `services/api/graphs/`: agent and RAG graph flows.
- `services/api/routers/`: chat, graph, GraphQL, health, models, and RAG routes.
- `services/api/tests/`: API and e2e tests.
- `config/`: dashboards and runtime config.
- `config/models.registry.json`: model registry — the available-model source of
  truth, also read by `scripts/lib/models_registry.sh`.
- `data/`: local documents and machine data.
- `models/`: local model assets.
- `scripts/`: operational helpers and examples.

## Key Commands

```bash
make setup
make start
make stop
make health
make query
make agent
make models                    # registry + installed state
make model-pull ID=<model-id>  # pull a registered model
```

## Runtime Contract

- Use the registry-selected RE/PE endpoints when launched by `RealityEngine_CI/startUniverse.sh`.
- Verify environment values against the live registry, not just static `.env` defaults.
- Models are declared in `config/models.registry.json`, selected in `.env`, and
  installed in Ollama. Check `GET /models` before debugging a model problem —
  it separates "not pulled" from "not registered" from "too big for this host".
  Note this is the *model* registry, distinct from the RE/PE instance registry
  resolved by `core/registry_resolver.py`.
- Keep local AI bridge behavior separate from OpenClaw ACP integration evidence.
- **PE sources are declared inactive and activated by their first value.** An
  active source contributes its region to every vector the PE assembles, so
  registering active changes what an engine perceives before any localAI data
  exists. Declaration fans out to every engine; activation follows that
  engine's own data flow. See `core/pe_sources.py`.
- **One interaction, one engine.** `X-RE-Instance: <registry instance id>`
  names the initiating engine; `core/bridge_binding.py` pins it for the whole
  request, so the sensor write, the push, and the perceptual space the response
  is normalized from all belong to that engine. A named engine that is not
  running resolves to nothing — the call degrades to its safe default rather
  than writing to a substitute. Without the header the bridge uses the
  registry-selected target, as before.

## LSP Support

Use Pyright and Ruff for Python/FastAPI, Docker/YAML support for stack files, JSON support for config, and markdown LSP for docs.

## Editing Rules

- Do not commit local model downloads, runtime caches, local data volumes, or secrets.
- Keep bridge endpoint changes tested against live RE/PE health where possible.
