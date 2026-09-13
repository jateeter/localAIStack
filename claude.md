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

# Machine contract gates (need a sibling RealityEngine_Machines checkout)
./scripts/validate-machines.sh       # data/machines/*.json vs the canonical schema
./scripts/check_machine_regions.py   # regions vs domains/region-allocation.json
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

## MUST: every use of the word "registry" carries a qualifier

**The word "registry" MUST NEVER appear unqualified. Every single use of the
word takes a qualifier naming which registry is meant.**

This is a hard requirement, not a style preference. It applies to every
occurrence in every context, with no exceptions: prose, end-of-task summaries,
commit messages, PR bodies, issue titles and bodies, code comments, docstrings,
variable and function names, log lines, and documentation.

Wrong, in every case — these are all violations:

- "the registry"
- "a versioned registry"
- "the registry file" / "update the registry" / "registry-backed"
- "check the registry first"
- "registry drift"

Right — a qualifier every time:

- "the **instance** registry"
- "a versioned **cesgen** registry"
- "the **arbitration** registry"
- "**machine** registry drift"

If you type the word "registry" and the word immediately before it is not a
qualifier, stop and add one. Re-read every summary and every message for the
bare word before sending it — that is where this rule is actually broken, because
the surrounding context makes the referent feel obvious in the moment. That
feeling is exactly the assumption the rule exists to block.

Qualifiers currently in use. **This list is open, not exhaustive** — a registry
added later gets a qualifier too; nothing is ever promoted to being "the
registry" by virtue of being the one under discussion:

- **instance** registry — `/tmp/re-registry/re-registry.json`, served at
  `:5999/re-registry.json`. Running RE/PE instances with `re_url`/`pe_url`/ports,
  plus `services` and `allocation`. What `RE_REGISTRY_URL` points at.
- **machine** registry — the machines a runtime holds in memory, reported by
  `GET /api/machines`. Distinct from `GET /api/machines/json/list`, the on-disk
  corpus catalog.
- **cesgen** registry — `RealityEngine_Machines/domains/ces-contract-registry.json`.
  Which CES output-stream contract shards exist, what corpus each was recorded
  against, whether each is current.
- **arbitration** registry — `machines/domains/arbitration-registry.json`.
- **domain** registry — `machines/domains/domain-registry.json`.
- **semantic-bus** registry — `machines/domains/semantic-bus-registry.json`.
- **tag** registry — `RealityEngine_CI/docs/TAG_REGISTRY.md`.

## MUST: verify a merge beyond the hosted checks

**A green PR is not a verified PR. Never merge on the hosted checks alone.**

The hosted path does not exercise this system's integration points. A PR can show
every check green and still be unverified, because the checks that ran were a
security scan and — at most — a corpus gate. `localAIStack`, `localOpenClawStack`,
Ollama, Qdrant, MQTT, the OpenClaw ACP gateway and the multi-engine universe are
**not** reachable from the hosted runners, so nothing on that path can tell you
whether the change works where it has to work.

Observed repeatedly: RealityEngine_Machines PRs report exactly one check
(GitGuardian). That is not evidence about the corpus, the registries, the
engines, or any bridge.

Before merging, verify **locally**, and say in the PR which of these you ran and
what they returned:

- The repo's own gates — `validate-corpus.sh`, the contract suite,
  `npm test`, `make test`, `sbt test` — whichever the change touches.
- The integration points the change can reach: a live 3-of-3 universe, the
  local AI stack, the OpenClaw gateway, MQTT — whichever the change can affect.
- The specific behaviour the change claims, with the numbers it produced.

If an integration point cannot be exercised, **say so in the PR** and name it.
An unverified area that is named is a known gap; an unverified area that is
silent reads as tested.

A hosted green tells you the change did not break the hosted path. That is worth
having and is not the question being asked at merge time.
