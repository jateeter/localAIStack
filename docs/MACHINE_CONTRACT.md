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
write any cell a corpus machine also writes?*

| | cells |
|---|---|
| localAI occupies | `[7440:7594]` |
| reserved band (`LOCALAI_BAND`) | `[7440:7952]` |
| bridge lane, outside the band | `ai_load_bridge` output `[272:280]` |

The band is a real reservation now: `localaistack-integration` in
`RealityEngine_Machines domains/domain-registry.json` `rangePolicy.reservedRanges`, mirrored into
`region-allocation.json` `reservedBands`. Corpus machines are forbidden from mapping inside it by
that repo's `test_no_machine_lane_inside_reserved_band`.

The gate fails on:

- a localAI writer landing on a cell a **corpus machine writes** — genuine contention;
- a localAI writer landing on a registry-declared lane, **except** the band reserved for this repo,
  which these machines are meant to occupy;
- two localAI writers overlapping each other;
- any region escaping the band, except declared `BRIDGE_LANES`;
- a **stale baseline entry** — `KNOWN_CORPUS_COLLISIONS` is now empty and must stay that way.

Only **outputs** are checked for contention. Write→read overlaps are composition: exactly two
remain, both deliberate — `ai_load_bridge` writes `[272:280]` so `AIModelWellness` `[272:276]` and
`AIHardwareResilience` `[276:280]` read it.

> **Correction, kept deliberately.** An earlier version of this document claimed the corpus footprint
> began at 1731 and that these machines collided with nothing. Both were false. 1731 is the lowest
> *declared span* in `region-allocation.json`, which does not enumerate machine `perceptualMapping`
> windows at all. Corpus machines start at cell 0, and five localAI writers were contending with them
> until the migration below.

## The migration

All eight machines moved by a uniform **+7388**, which preserves every internal coupling by
construction:

| machine | input | output |
|---|---|---|
| `rag_corrective_cycle` | `[7440:7448]` | `[7448:7452]` |
| `session_rag_context` | `[7448:7452]` | `[7500:7504]` |
| `agent_activity_classifier` | `[7452:7456]` | `[7456:7460]` |
| `session_agent_context` | `[7492:7508]` | `[7504:7508]` |
| `ai_load_bridge` | `[7500:7508]` | `[272:280]` *(unmoved)* |
| `personal_health_baseline` | `[7574:7578]` | `[7578:7582]` |
| `session_health_context` | `[7578:7582]` | `[7590:7594]` |
| `medication_adherence` | `[7582:7586]` | `[7586:7590]` |

`ai_load_bridge`'s **output stays** at `[272:280]`. That write is the machine's whole purpose — it
feeds two corpus machines' input windows — so it belongs in the corpus AI input region, not in this
repo's band. No corpus machine *writes* those cells, so it is a bridge, not contention.

Topology graph bases moved with everything else: `rag` 76→7464, `agent` 104→7492.

Migrating also removed accidental entanglement: write→read overlaps with corpus machines dropped
from **11 to 2**, and the two that remain are the intended ones.

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
