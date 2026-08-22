# localAI machine contract

Settles the ownership question in [#38](https://github.com/jateeter/localAIStack/issues/38): are these
machine definitions corpus machines that belong in `RealityEngine_Machines`, or a separate class that
needs its own contract here?

**Decision: separately contracted here.** They stay in this repo, and this document plus two CI gates
are the contract that replaces the corpus gates they will never be under.

## What they are

Ten `localai/*` machines load into the Reality Engine alongside the corpus:

| | count | where they live |
|---|---|---|
| static definitions | 8 | `data/machines/*.json` |
| topology machines | 2 | synthesized at runtime by `core.topology_builder.build_machine_json` |

```
agent_activity_classifier   ai_load_bridge            medication_adherence
personal_health_baseline    rag_corrective_cycle      session_agent_context
session_health_context      session_rag_context
```

They are real participants in the reality vector with real perceptual mappings — not fixtures. A
14-machine boot corpus was once observed loading as 24 machines, the extra ten being these.

## Why not move them into the corpus

The corpus is a *static, engine-agnostic* artifact: the same files mean the same thing to the C++,
Lisp, Scala and TypeScript runtimes, loaded from disk at startup, versioned independently of any
service. These machines are not that, on three counts:

1. **They are registered at runtime**, by the reality bridge, not loaded from the corpus directory.
   Their lifecycle is the stack's lifecycle.
2. **Two of them do not exist as files at all.** The topology machines are built from the live
   LangGraph graphs, so their shape follows this repo's graph definitions. A corpus file could not
   represent them without freezing a copy that drifts the moment a graph changes.
3. **They encode this stack's semantics** — RAG cycle state, agent session carry, localAI load tiers.
   Moving them to the corpus would make every runtime's boot depend on a service most deployments do
   not run.

The gap #38 identified was never that they were in the wrong place. It was that being outside the
corpus meant being outside *any* gate. That is what changes here.

## The contract

Two gates run in CI on every change, as the `Machine contract (schema + regions)` job.

### 1. Canonical schema

`scripts/validate-machines.sh` validates all eight against `machine.schema.json` **from
RealityEngine_Machines**, using that repo's own Ajv setup. The schema and the validator are both
borrowed on purpose: "validate against the canonical schema" must not become a second implementation
of validation that drifts from the first.

`services/api/tests/test_machine_schema.py` additionally covers the two synthesized topology
machines, which no file-based gate can reach.

Conformance was not free — `scripts/conform_machines.py` is the one-shot transform that brought all
eight under the schema, kept so the edit stays reproducible.

### 2. Region reconciliation

`scripts/check_machine_regions.py` answers the question schema validation cannot: *do these machines
write any cell the corpus already claims?*

| | cells |
|---|---|
| localAI occupies | `[52:280]` |
| localAI claims (`LOCALAI_BAND`) | `[0:512]` |
| corpus footprint begins at | `1731` |

The two are not merely non-colliding, they are in different parts of the vector. The gate fails on:

- a localAI writer landing on any cell the allocation registry declares — the undeclared contention
  the issue is about;
- two localAI writers overlapping each other;
- any region escaping the claimed band.

Only **outputs** are checked for contention. Reading a cell another machine owns is ordinary
composition — `ai_load_bridge` reads `[112:120]` deliberately. Writing one is contention.

## Known gap: the band is claimed, not reserved

`LOCALAI_BAND` is this repo's declared intent. It is **not** a reservation the corpus registry grants:
`domains/region-allocation.json` has an empty `reservedBands` list, and nothing there records that
`[0:512]` is spoken for.

Today that is harmless, because the corpus allocator has never assigned below 1731. It stops being
harmless the moment it does. Closing it properly means a `reservedBands` entry emitted by
`RealityEngine_Machines/scripts/build-region-allocation.py` — that file is generated and must not be
hand-edited, so it is a change in that repo, tracked separately.

Until then `check_machine_regions.py` is the detector rather than the preventer: it will fail the
build the first time the corpus allocates into this band, which is the outcome that matters.

## Changing a machine

1. Edit `data/machines/<name>.json`.
2. Run both gates locally against a sibling corpus checkout:
   ```bash
   ./scripts/validate-machines.sh
   ./scripts/check_machine_regions.py
   # or, from elsewhere:
   MACHINES_DIR=/path/to/RealityEngine_Machines ./scripts/check_machine_regions.py
   ```
3. Moving a machine's region is a contract change: update the table above, and widen `LOCALAI_BAND`
   deliberately rather than to make a failure go away.
