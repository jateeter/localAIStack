# localAIStack Guidance

Last reviewed: 2026-09-25

See `/Users/johnt/workspace/GitHub/CLAUDE.md` for the integrated application map. Update both this file and the root map when local AI provider responsibilities, bridge endpoints, or runtime composition changes.

## Role

This repo provides local AI/RAG/vector services and a RealityEngine bridge. It should use the active RE/PE endpoints selected by the integrated universe rather than stale hard-coded endpoints.

## Codebase Map

- `services/api/main.py`: FastAPI entrypoint.
- `services/api/config.py`: runtime configuration.
- `services/api/core/reality_bridge.py`: RE/PE bridge.
- `services/api/core/health_bands.py`, `data/health/health_bands.json`: grade
  the HealthKit bridge's families ok / watch / concern and roll them up, worst
  band wins (pure; no I/O).
- `services/api/core/health_scope.py`: HealthKit scope follower. Run from the
  lifespan every `HEALTH_SCOPE_INTERVAL_S` against each PE, it gives in-scope
  bands slots in `[7600:7632]` and writes the roll-up to `[7574:7578]`.
- `services/api/core/embeddings.py`: embedding support.
- `services/api/core/vector_store.py`: vector store behavior.
- `services/api/core/topology_builder.py`: topology/graph construction.
- `services/api/core/model_registry.py`: model registry loader; joins model registry
  metadata with the `.env` selection and live Ollama tags.
- `services/api/graphs/`: agent and RAG graph flows.
- `services/api/routers/`: chat, graph, GraphQL, health, models, and RAG routes.
- `services/api/tests/`: API and e2e tests.
- `config/`: dashboards and runtime config.
- `config/models.registry.json`: model registry — the available-model source of
  truth, also read by `scripts/lib/models_registry.sh`.
- `data/<domain>/`: everything domain-specific, one directory per domain.
  JSON (tables, config) at its top level and RAG knowledge documents in
  `data/<domain>/documents/`, all git tracked. Today: `data/health/`
  (`health_bands.json`; `documents/` is the curated health corpus loaded by
  `scripts/ingest_health_docs.py`). New domains follow the same shape, e.g.
  `data/communityServices/*.json` and `data/communityServices/documents/*`.
- `data/machines/`: localAI's machine definitions (contracted here, not in the corpus).
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
make models                    # model registry + installed state
make model-pull ID=<model-id>  # pull a registered model

# Machine contract gates (need a sibling RealityEngine_Machines checkout)
./scripts/validate-machines.sh       # data/machines/*.json vs the canonical schema
./scripts/check_machine_regions.py   # regions vs domains/region-allocation.json
```

## Runtime Contract

- Use the RE/PE endpoints selected from the instance registry when launched by `RealityEngine_CI/startUniverse.sh`.
- Verify environment values against the live instance registry, not just static `.env` defaults.
- Models are declared in `config/models.registry.json`, selected in `.env`, and
  installed in Ollama. Check `GET /models` before debugging a model problem —
  it separates "not pulled" from "not registered" from "too big for this host".
  Note this is the *model* registry, distinct from the RE/PE instance registry
  resolved by `core/registry_resolver.py`.
- Keep local AI bridge behavior separate from OpenClaw ACP integration evidence.
- **`data/machines/*.json` are contracted here, not in the corpus.** They are
  registered into the RE at runtime rather than loaded from the corpus, so the
  corpus gates never see them. `docs/MACHINE_CONTRACT.md` records that decision
  and the two CI gates that stand in for those gates: canonical-schema
  validation, and region reconciliation against `region-allocation.json`. They
  write `[7440:7594]`, inside the `localaistack-integration` band reserved for
  them in `RealityEngine_Machines domains/domain-registry.json`. The one
  exception is `ai_load_bridge`'s output at `[272:280]`, a deliberate bridge into
  the corpus AI machine input window. Moving a region is a contract change, not
  an edit.
- **PE sources are declared inactive and activated by their first value.** An
  active source contributes its region to every vector the PE assembles, so
  registering active changes what an engine perceives before any localAI data
  exists. Declaration fans out to every engine; activation follows that
  engine's own data flow. See `core/pe_sources.py`.
- **HealthKit scope is dynamic and the PE is its authority.** Which types flow
  changes through an authorization workflow (Solid pod, Swift bridge,
  OpenCommons PIM). localAI follows add / lock / remove from each PE's
  `/api/integrations/healthkit/status` and never assumes a fixed set of
  measures. A removed measure is absent, not zero. localAI may request a
  resync through the PE; the pod workflow does not. Contract:
  `localHealthkitBridge/docs/INGEST_CONTRACT.md` "Scope and resync".
- **One interaction, one engine.** `X-RE-Instance: <instance registry id>`
  names the initiating engine; `core/bridge_binding.py` pins it for the whole
  request, so the sensor write, the push, and the perceptual space the response
  is normalized from all belong to that engine. A named engine that is not
  running resolves to nothing — the call degrades to its safe default rather
  than writing to a substitute. Without the header the bridge uses the
  target selected from the instance registry, as before.

## LSP Support

Use Pyright and Ruff for Python/FastAPI, Docker/YAML support for stack files, JSON support for config, and markdown LSP for docs.

## Editing Rules

- Do not commit local model downloads, runtime caches, local data volumes, or secrets.
- Keep bridge endpoint changes tested against live RE/PE health where possible.

## Standing rules — authoritative in `../RealityEngine_CI/docs/ENGINEERING_CONTRACT.md`

These apply here and are **not** restated in this file. The table is an index
to the contract, not a copy of it: it names every rule so you know what to look
up, and the contract's wording governs wherever the two differ.

| Rule | In short |
| --- | --- |
| Qualify every "registry" | Never the bare word — instance / machine / cesgen / arbitration / domain / semantic-bus / tag. |
| Regenerate a stale `<name>` registry, don't fail it | Each `<name>` registry is a view of the running system. A gate regenerates it and fails only on a disagreement that survives regeneration. |
| Verify a merge beyond the hosted checks | A green PR is not a verified PR; the hosted path cannot reach the integration points. Name what you could not exercise, and record what you noticed but did not chase. |
| _CI is the authority | Peripheral repos keep minimal CI that forces local validation; RealityEngine_CI verifies fixes against a live universe. Check its `docs/` before adding CI anywhere else. |
| Name it `CLAUDE.md` | Uppercase, always. On a case-insensitive filesystem `claude.md` is the same inode; dedupe on `st_ino`, never on a resolved path. |
| Never commit to main | Branch from `origin/main`, PR, verify, squash-merge, clean up. |
| Use bash, not zsh | Shell work runs in `/opt/homebrew/bin/bash` (5.x), not zsh or macOS `/bin/bash` 3.2: any loop, unquoted variable, glob or `set --` goes through it with `set -euo pipefail`, and you check the command's exit status, not the pipeline tail. |

Read the contract for the full text, the qualifier table, and the cleanup steps.
