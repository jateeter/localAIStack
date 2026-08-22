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
| localAI occupies | `[52:280]` |
| localAI claims (`LOCALAI_BAND`) | `[0:512]` |
| corpus machine windows span | `[0:16944]`, first merged block `[0:2047]` contiguous |

The gate fails on:

- a localAI writer landing on a cell a **corpus machine writes** — genuine contention;
- a localAI writer landing on a registry-declared lane;
- two localAI writers overlapping each other;
- any region escaping the claimed band;
- a **stale baseline entry** — a frozen collision that no longer exists must be removed, so the
  baseline can only shrink.

Only **outputs** are checked for contention. Write→read overlaps are ordinary composition and some
are deliberate: `ai_load_bridge` writes `[272:280]` precisely so `AIModelWellness` and
`AIHardwareResilience` read it. Those are reported, never failed.

> **Correction.** The first version of this gate reconciled against `region-allocation.json` alone
> and reported no collisions. That was wrong. The registry records aggregate domain windows, service
> lanes, buses and shared output lanes — it does **not** enumerate machine `perceptualMapping`
> windows. Its lowest declared cell is 1731, which reads like "the corpus lives above 1731" and is
> not what it means. Corpus machines start at cell **0**, right where these eight sit.

## Known gap 1: five live collisions, held at baseline

Five localAI writers contend with corpus writers **today**:

| localAI writer | corpus writer | cells |
|---|---|---|
| `agent_activity_classifier` | `AGX005_aquaculture-dissolved-oxygen-control` | `[68:72]` exact |
| `session_rag_context` | `AGX013_aquaculture-algae-culture-balance` | `[112:116]` exact |
| `medication_adherence` | `AGX027_indoor-grow-house-lighting-schedule-integrity` | `[198:200]` |
| `personal_health_baseline` | `AGX026_indoor-grow-house-vpd-climate-management` | `[190:192]` |
| `session_health_context` | `AGX028_indoor-grow-house-nutrient-reservoir-balance` | `[204:206]` |

Two machines writing the same cells, with the cell arbiter resolving contention no registry
declares — the condition #38 set out to prevent.

They are frozen in `KNOWN_CORPUS_COLLISIONS` rather than fixed here, because remapping a machine's
output changes what it writes at runtime and is reviewable work in its own right (#57). The set is
frozen in both directions: a collision that gets fixed must be removed from the list, or the gate
fails on the stale entry. **Do not add to that list to make a build pass.**

## Known gap 2: the band is claimed, not reserved

`LOCALAI_BAND` is this repo's declared intent, not a reservation. `domains/region-allocation.json`
has an empty `reservedBands` list.

It cannot simply be granted, either: `[0:512]` holds 215 corpus machine regions, so reserving it
would fail the corpus's own `test_no_machine_lane_inside_reserved_band`. A real reservation means
picking a genuinely free band — `[7440:13000]` is the only one with room — and migrating these
machines into it. Tracked as jateeter/RealityEngine_Machines#96.

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
