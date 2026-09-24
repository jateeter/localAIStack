# localAIStack — PE/RE Health Integration Audit & Roadmap

Last reviewed: 2026-09-23

## Status of this document

**This document was substantially wrong until 2026-09-16 and is corrected here
rather than quietly rewritten.** It declared Phase 4 unstarted while 4a and 4b
were fully implemented and carried 70 passing tests, and every perceptual-space
offset it printed was from a layout the code had already migrated off.

A reader who trusted it would have rebuilt work that exists, at offsets that do
not. The specific corrections are recorded below because a stale roadmap is how
a reader concludes the opposite of the truth — the same reason
`localHealthkitBridge/ROADMAP.md` corrects its own status line in place.

| What the document claimed | What the code does |
|---|---|
| Phase 4a CareKit "not yet done" | Shipped: machine, sensors, `push_carekit_signal()`, startup wiring, drift guard, tests |
| Phase 4b session carry "not yet done" | Shipped: `session_health_context.json`, `_HEALTH_CARRY_OFFSET`, carry decode, `get_session_context()` key |
| Phase 4c PE ingest endpoint is work to do | Already shipped in **all four** PE runtimes, under a **different, canonical** contract |
| CareKit sensors at 194/195/196, output 198, carry 202 | **7582 / 7583 / 7584**, output **7586**, carry **7590** |
| Health sensors at `[186:190]` | `[7574:7577]` |
| `[7594:256] free (50 bytes)` | Arithmetic is impossible; the free tail is `[7594:7952]` = 358 bytes |
| "85 tests, no services needed" | 211 unit tests collected — 210 pass, 1 skips on a missing optional import |
| "7 documents in `data/documents/health/`" then lists 9 | 9 files present |
| `config/integrations.healthkit-localai.json` is the HealthKit config | No runtime loads it, and its schema **cannot be resolved** by any PE ingest handler |

The offsets in the third and fourth rows are not a typo. localAIStack's machines
moved into the reserved `localaistack-integration` band at `[7440:7952]`
(declared in `RealityEngine_Machines/domains/domain-registry.json`,
`rangePolicy.reservedRanges`, 512 bytes, exclusive). This document was never
updated to follow them.

---

## Audit: current integration state

### What is complete

| Layer | Component | Status |
|---|---|---|
| PE/RE bridge | `core/reality_bridge.py` — sensors, drift guard, push paths | ✅ |
| RAG pipeline | `graphs/rag_graph.py` — retrieve → grade → generate → rewrite | ✅ |
| Agent pipeline | `graphs/agent_graph.py` — agent/tools loop + activity metrics | ✅ |
| Session carries | 5 machines: rag, agent, ai_load_bridge, activity classifier, health carry | ✅ |
| Graph topology | `core/topology_builder.py` — binds LangGraph nodes to perceptual space | ✅ |
| GraphQL receiver | `routers/graphql_endpoint.py` — machine → localAI upstream trigger | ✅ |
| Perceptual space | localAI band `[7440:7952]`; allocated through `[7594]` plus the slot table `[7600:7632]`; 326 bytes free | ✅ |
| **Health machine** | `data/machines/personal_health_baseline.json` — `[7574:7578]`→`[7578:7582]`, reads the worst-wins roll-up | ✅ Phase 1, re-based T8 |
| **Health bands** | `data/health/health_bands.json` + `core/health_bands.py` — grade the bridge's families, roll up worst-wins | ✅ **T8** |
| **Scope follower** | `core/health_scope.py` — follows HealthKit scope on every PE: dynamic slots, lock holds, remove frees, resync requests | ✅ **T8** |
| **Health bridge** | `push_health_signal()`, `get_health_state()`, `get_current_health_state()` | ✅ Phase 1+2 |
| **Health sim** | `scripts/simulate_health_push.py` — Yuma/MQTT analog, 4 scenarios + cycle, graded by the band table | ✅ Phase 1 |
| **Health-aware chat** | `routers/chat.py` — health context injection, 3-level opt-in | ✅ Phase 2 |
| **Health RAG** | `health_docs` collection, 9 knowledge docs, `health_search` agent tool | ✅ Phase 2+3 |
| **Health doc ingest** | `scripts/ingest_health_docs.py` — loads health docs into Qdrant | ✅ Phase 2 |
| **`/health` bridge status** | `pe` + `re` fields; `bridge` rollup; async parallel checks | ✅ Phase 3 |
| **Compose integration tests** | `tests/e2e/test_api_integration.py` — 15 tests, `--integration` | ✅ Phase 3 |
| **Live stack tests** | `tests/e2e/test_health_pipeline.py` — 21 tests, `--live` | ✅ Phase 3+4 |
| **CI e2e workflow** | `.github/workflows/e2e.yml` + `docker-compose.ci.yml` override | ✅ Phase 3 |
| **CareKit machine** | `data/machines/medication_adherence.json` — `[7582:7586]`→`[7586:7590]` | ✅ **Phase 4a** |
| **CareKit bridge** | `_CAREKIT_SENSORS`, `push_carekit_signal()`, `get_carekit_state()`, `import_carekit_machine()` | ✅ **Phase 4a** |
| **Health session carry** | `data/machines/session_health_context.json` — `[7578:7582]`→`[7590:7594]` | ✅ **Phase 4b** |
| **Carry decode** | `get_health_state_from_carry()`, `get_session_context()["health_state"]` | ✅ **Phase 4b** |
| **Phase 4 tests** | `tests/test_phase4.py` — 70 tests | ✅ **Phase 4a+4b** |
| **PE ingest endpoints** | HealthKit **and** CareKit ingest + status, shipped in TS / C++ / LSP / Scala PE | ✅ **Phase 4c** (upstream) |
| **iOS bridge** | `localHealthkitBridge` — 7 modules, 5 test suites, host app, device e2e green | ✅ **Phase 4c** (upstream, M0–M5) |

### What is not yet done

| Gap | Task |
|---|---|
| ~~Three competing PE integration registries in `config/`~~ | T2 ✅ |
| ~~Chat still makes a synchronous RE round-trip per request~~ | T4 ✅ |
| ~~No operator-facing CareKit driver~~ | T5 ✅ |
| ~~`/health` does not report CareKit state~~ | T6 ✅ |
| ~~No Swift ↔ Python band-threshold parity check~~ | T7 ✅ |
| ~~iOS bridge and localAI health machine read different regions~~ | T8 ✅ |
| CareKit sync absent from the Swift bridge (upstream, deferred to its v0.2) | T9 |
| ~~Personalisation feedback loop — health state → RAG re-rank~~ | T10 ✅ |
| ~~All localAI machine JSONs carry stale offsets in their prose metadata~~ | T11 ✅ |

---

## Perceptual space layout (verified 2026-09-23)

Authority for every offset below is `services/api/core/reality_bridge.py`
(`_EXPECTED_MACHINE_OFFSETS`, `_RAG_SENSORS`, `_HEALTH_SENSORS`,
`_CAREKIT_SENSORS`). The drift guard `verify_machine_offsets()` asserts the
machine JSON files agree with that table, so the table is the single place an
offset change must be made.

localAIStack owns the reserved band `[7440:7952]` — `localaistack-integration`,
exclusive, 512 bytes, declared in
`RealityEngine_Machines/domains/domain-registry.json`. Machines in this band are
registered into the RE at runtime by the reality bridge, not loaded from the
corpus.

```
── localAI reserved band [7440:7952] ────────────────────────────────────────
[7440:7448]  rag_corrective_cycle input   (rag_retrieval [7440:7444],
                                           rag_grading   [7444:7448])
[7448:7452]  rag_corrective_cycle output  → session_rag_context input
[7452:7456]  agent_activity sensor        → agent_activity_classifier input
[7456:7460]  agent_activity_classifier output
[7492:7508]  session_agent_context input
[7500:7504]  session_rag_context output   [last_generate, last_rewrite, last_abort, _]
[7504:7508]  session_agent_context output [agent_ever_engaged, tools_ever_used, _, _]
[7500:7508]  ai_load_bridge input
[7574:7578]  health roll-up sensor         one-hot, worst band wins          T8
             = personal_health_baseline input window
[7578:7582]  personal_health_baseline output  [thriving, balanced, watch, attention]
[7582:7585]  CareKit sensors              med_adherence, task_completion, symptom_ok
[7582:7586]  medication_adherence input window                             Phase 4a
[7586:7590]  medication_adherence output  [adherent, partial, lapsed, concern]
[7590:7594]  session_health_context carry [thriving, balanced, watch, attention]
[7594:7600]  free — 6 bytes
[7600:7632]  health band slots            one per band in HealthKit scope    T8
             (ok 1.0 / watch 0.5 / concern 0.0), allocated dynamically;
             a band out of scope has no slot and no source
[7632:7952]  free — 320 bytes
─────────────────────────────────────────────────────────────────────────────

Outside the band (written by localAI, read elsewhere):
[272:280]    ai_load_bridge output — narrowed to the two inputs no corpus
             machine feeds (see jateeter/localAIStack#48)
[4210:4214]  ACP/OpenClaw completion sensor region
```

Two regions belong to other owners and are named here only because they are
routinely confused with localAI's:

- `[4320:4344]` — the **corpus** HealthKit regions that `localHealthkitBridge`
  posts into, consumed by `RealityEngine_Machines/machines/domains/health-personal/`.
  localAI *reads* them (the scope follower grades each family) and writes
  only its own band. See T8.
- `[0:186]` — the pre-migration layout this document used to print. Historical.

---

## Simple example: personal health baseline (Yuma/MQTT analog)

### Yuma pipeline (agriculture domain)

```
yuma.lateraledge.cloud:1883 (MQTT broker)
  → config/mqtt-mappings.yuma-agriculture.json (16 band rules)
  → PE sensor sources (16 × 1-byte regions)
  → RE: AGX001/005/026/032 machines fire
  → CES governance trigger (GREEN/AMBER/RED)
  → Prometheus + Grafana
```

### Health pipeline (personal domain)

```
iOS bridge → PE ingest → corpus lanes [4320:4344]      (device path)
  → core/health_scope.py, every HEALTH_SCOPE_INTERVAL_S per PE:
      grade each in-scope family ok/watch/concern (data/health/health_bands.json)
      → slot sensors [7600:7632]; roll-up sensor localai_health_rollup [7574:7578]
scripts/simulate_health_push.py                        (simulated path)
  → grades made-up readings with the same table → roll-up sensor [7574:7578]
  → PE /api/push → RE /api/perceive
  → personal_health_baseline fires (thriving/balanced/watch/attention)
  → perceptualSpace[7578:7582] decoded by get_health_state()
  → session_health_context latches the state into [7590:7594]
  → POST /graphql  updateProcessState  (GREEN/AMBER/RED)
  → localAI ring buffer, Grafana logs
```

| Yuma/MQTT | Health |
|---|---|
| MQTT broker | iOS HealthKit bridge, or `simulate_health_push.py` |
| Band normalization rules (JSON) | `data/health/health_bands.json` |
| 16 sensor regions | roll-up `[7574:7578]` + up to 32 band slots `[7600:7632]` |
| AGX001 … AGX032 machines | `personal_health_baseline` + `medication_adherence` |
| GREEN/AMBER/RED governance | thriving→GREEN, watch→AMBER, attention→RED |
| Prometheus paging decisions | GraphQL events ring buffer → Loki/Grafana |

---

## Completed phases

### Phase 1 — Health machine + simulation (DONE, 2026-06-18)

- `data/machines/personal_health_baseline.json` — 4-state classifier,
  `PASSTHROUGH` arbiter, `gte` match, input `[7574:7578]`, output `[7578:7582]`
- `_HEALTH_SENSORS` + `push_health_signal()` + `get_health_state()` (the three
  per-measure sensors were replaced by the roll-up in T8)
- `import_health_machines()` wired into `main.py` startup
- Health machine in the `_EXPECTED_MACHINE_OFFSETS` drift guard
- `scripts/simulate_health_push.py` — `--scenario thriving|balanced|watch|attention|cycle`
- `tests/test_health_integration.py` — 22 tests, network-free

### Phase 2 — Health-aware chat context (DONE, 2026-06-18)

- `_inject_health_context()` + `_HEALTH_HINTS` in `routers/chat.py`; three-level
  opt-in: `ChatRequest.health_context` → `X-Health-Context: enabled` header →
  `Settings.health_context_enabled`
- `get_current_health_state()` — reads the RE state; prefers the live classifier
  output and falls back to the carry
- `core/vector_store.py` → `get_health_vector_store()` (`health_docs` collection);
  `health_search` tool in `graphs/agent_graph.py`
- `scripts/ingest_health_docs.py`
- `tests/test_phase2.py` — 36 tests

**Correction:** this phase recorded `config/integrations.healthkit-localai.json`
as its HealthKit config. That file is not loadable by any PE — see T2.

### Phase 3 — Full stack e2e (DONE, 2026-06-18)

- `/health` gained `pe` and `re` sub-objects and a top-level `bridge` rollup;
  PE and RE checks run in parallel via `asyncio.gather`
- `tests/e2e/conftest.py` — `--integration` / `--live` flags, `live_api`,
  `live_pe`, `live_re` fixtures, `poll_until()`
- `tests/e2e/test_api_integration.py` (15) and `tests/e2e/test_health_pipeline.py` (21)
- `.github/workflows/e2e.yml` + `docker-compose.ci.yml`
- 9 health knowledge documents in `data/documents/health/`

**Correction:** e2e tests are *collected* in a default run and skip on the
missing flag; they are not excluded from collection as this document claimed.

### Phase 4a — CareKit machine (DONE)

`data/machines/medication_adherence.json` — input `[7582:7586]`, output
`[7586:7590]`, `PASSTHROUGH` arbiter, `gte` match.

**Five sequences, four states.** `partial` is reached by two disjoint guards, so
the sequence count is 5 while the output alphabet is 4:

| Sequence | Output | Guard |
|---|---|---|
| `carekit-adherent` | `[1,0,0,0]` | med HIGH ∧ task HIGH ∧ symptom HIGH |
| `carekit-partial-task` | `[0,1,0,0]` | med HIGH ∧ task LOW |
| `carekit-partial-symptom` | `[0,1,0,0]` | med HIGH ∧ task HIGH ∧ symptom LOW |
| `carekit-lapsed` | `[0,0,1,0]` | med LOW ∧ symptom HIGH |
| `carekit-concern` | `[0,0,0,1]` | med LOW ∧ symptom LOW |

Sensors (`_CAREKIT_SENSORS`), pre-normalised ratios, no band step:

| Offset | Sensor | TTL |
|---|---|---|
| 7582 | `localai_carekit_med_adherence` | 3 600 000 ms (1 h dose window) |
| 7583 | `localai_carekit_task_completion` | 86 400 000 ms (24 h) |
| 7584 | `localai_carekit_symptom_ok` | 86 400 000 ms (24 h) |

Python: `push_carekit_signal()` (clamps to `[0,1]`, falls back to `"partial"`
when the PE is unreachable), `get_carekit_state()`, `import_carekit_machine()`
wired at `main.py:54`, drift-guard entry, `_SENSOR_TO_MACHINE` entries.

### Phase 4b — Health session carry (DONE, except the chat warm path)

`data/machines/session_health_context.json` — input `[7578:7582]` (reads the
classifier output directly), output `[7590:7594]`, four isInitial sequences, one
per state. When no health push occurs in a cycle none fire and PE carry-forward
holds the region, so the state persists across a conversation.

Python: `_HEALTH_CARRY_OFFSET = 7590`, `get_health_state_from_carry()`,
`session_health_context` in `_SESSION_MACHINE_DEFS`, drift-guard entry, and
`get_session_context()` now returns:

```python
{
    "rag":            "generate" | "rewrite" | "abort" | None,
    "agent":          {"ever_engaged": bool, "tools_ever_used": bool},
    "agent_activity": "productive" | "normal" | "struggling" | None,
    "ai_load_tier":   "nominal" | "elevated" | "critical" | None,
    "health_state":   "thriving" | "balanced" | "watch" | "attention" | None,
}
```

**Open:** the chat integration this phase specified — prefer the carry, drop the
synchronous poll — was never done. See T4.

### Phase 4c — PE ingest and the iOS bridge (DONE upstream, under a different contract)

The schema this document specified for Phase 4c — `hkTypeIdentifier`, a raw
`value`, server-side band normalization, no auth, port 3004 — **was never
built, and should not be.** It is one of three mutually incompatible
descriptions of the ingest contract that existed in the workspace; the conflict
was resolved against it.

**`localHealthkitBridge/docs/INGEST_CONTRACT.md` is canonical.** Batch body is
`bridgeId`, `bridgeToken`, `samples[{type, sourceName?, unit, values[4], metadata}]`,
optional `anchorToken`; mapping resolution is `healthkit:<type>:<sourceName>`
then `healthkit:<type>`; values are pre-normalized by the device.

Shipped against it, none of it localAIStack work:

| Surface | Where |
|---|---|
| TS PE ingest + status | `RealityEngine_Manager/perception-engine/backend/src/server.ts` + `integrations/adapters/HealthKitBridge.ts` |
| TS PE CareKit ingest + status | same, + `integrations/adapters/CareKitBridge.ts` |
| C++ PE | `RealityEngine_CPP/src/perception_engine_server.cpp` |
| Lisp PE | `RealityEngine_LSP/src/perception-service.lisp` |
| Scala PE | `perception-engine/.../api/PerceptionRoutes.scala` |
| Per-engine parity | `RealityEngine_Machines/tests/integration/healthkit-ingest-contract.spec.ts` |
| iOS bridge | `localHealthkitBridge` — M0–M5, device e2e green 2026-07-24 |

What remained for localAIStack was wiring, not building: T7 and T8, both done 2026-09-23.

---

## Remaining work

### T1 — Roadmap truth pass ✅ 2026-09-16

This document. Status tables, offsets, test counts and the Phase 4c contract
now match the code, and the corrections are recorded rather than silently applied.

### T2 — Collapse three PE integration registries into one ✅ 2026-09-23

`config/integrations.json` is the only one left. `pe-integrations.json` and
`integrations.healthkit-localai.json` are deleted, and the `test_phase2` and
`test_phase4` PE-integration-registry tests point at the survivor.

T8 changed what the survivor holds: it no longer maps HealthKit sensors at all.
Its three HR/HRV/sleep mappings wrote `[7574:7577]`, inside the roll-up's
window, so leaving them would have put two writers on one window. HealthKit
families reach the PE through the lane mappings in RealityEngine_CI's
`config/integrations.json`. Tests now assert that localAIStack's config maps no
HealthKit sensor and writes nothing into `[7574:7578]` or `[7600:7632]`.

The band-threshold assertions this task said to preserve had nothing left to
lock: the Python constants they compared against are gone, replaced by
`data/health/health_bands.json`. The check that matters now is T7's.

### T3 — (withdrawn, folded into T2)

### T4 — Chat warm path: no network round trip per turn ✅ 2026-09-24

`routers/chat.py` now calls `current_health_state()`. That function takes the
scope follower's last state for the bound engine, which localAI computes itself
every `HEALTH_SCOPE_INTERVAL_S` and which costs nothing to read. It falls back
to reading the RE (live output, then carry) only when the follower has no state,
and caches that read per engine for 30 s.

This supersedes the plan above (read the carry from the last push response). The
follower's state is fresher than the carry and needs no push.

### T5 — Operator-facing CareKit driver ✅ 2026-09-24

`scripts/simulate_health_push.py --carekit adherent|partial|lapsed|concern|cycle`
registers the CareKit sensors (declared inactive, activated after their first
value), writes each scenario, pushes, and decodes `medication_adherence`'s
output. It uses the service's own sensor definitions and decoder. Verified live
on cpp-1, lsp-1 and scala-1: all four states correct on each.

### T6 — `/health` reports CareKit ✅ 2026-09-24

`re` gains `carekit_state`. `pe` gains `carekit_sensors` and `health_scope`,
the follower's last reconciliation of that PE (declared, generation, state,
slots). Verified live, except where an engine returns the state in a different
shape: LSP's `GET /api/perceptual-simulation/state` is flat where C++ and Scala
nest it under `state`, so every reader, `/health` included, sees no state on
lsp-1. Filed as RealityEngine_CI#453. Deliberately not masked here.

### T7 — Swift ↔ Python band-threshold parity check ✅ 2026-09-23

T8 settled the question this task was waiting on: which thresholds lock
together. The bridge normalises every lane value to `[0,1]` against a declared
`sourceRange` (`localHealthkitBridge/docs/lane-semantics.json`), and localAI
de-normalises with its own copy of that range before grading in raw units. If
the two ranges differ, localAI grades a different reading from the one taken.

`tests/test_health_bands.py::test_band_lanes_match_lane_semantics` asserts,
per band, that the lane region, axis index, axis name and `sourceRange` match
`lane-semantics.json`. It needs a sibling `localHealthkitBridge` checkout and
skips without one, the same posture as `scripts/validate-machines.sh`.

### T8 — End-to-end iOS → localAI health machine ✅ 2026-09-23

**The premise was wrong.** This task assumed the bridge sends heart rate, HRV and
sleep, and that joining the two regions was only a routing question. The bridge
delivers **blood-pressure** (systolic, diastolic, pulse), **workout** (energy,
exercise minutes, steps) and **sleep** (total, REM, core) families, each with a
confidence axis, into `[4320:4344]`. It sends no HRV and no standalone heart
rate. Neither of the two routes listed here could have worked, because both
mapped HealthKit types the bridge does not send.

**The data scope is dynamic.** Which HealthKit types flow changes through an
authorization workflow tied to the owner's Solid pod, driven by the Swift bridge
and/or the OpenCommons PIM workflow. localAI and every engine must accept
add / lock / remove as they happen. The authorization workflow itself does not
exist yet. The scope semantics below are provisional, and pod semantics do not
include resync.

What was built:

1. **Scope and resync in the contract, 3-of-3 plus the TS PE.**
   `localHealthkitBridge/docs/INGEST_CONTRACT.md` "Scope and resync":
   `POST /api/integrations/healthkit/scope {bridgeId, action add|lock|remove, types[], source}`
   and `POST /api/integrations/healthkit/resync {bridgeId, types?, requestedBy}`,
   with a `scope` block on `/status`. A bridge is open until its first
   declaration; `lock` refuses new samples; `remove` refuses them and removes
   the type's sources from the PE (absent, not zero). Resync is a consumer
   request made through the PE and fulfilled by an ingest carrying `resyncId`.
   Implemented in C++, LSP, Scala and the TS PE. Agreement is enforced by
   `RealityEngine_Machines/tests/integration/healthkit-scope-quorum.spec.ts`,
   which fails unless all three native engines agree.
2. **localAI re-based onto the delivered families.**
   `data/health/health_bands.json` grades each family ok / watch / concern in raw
   units: pulse 60–100 bpm ok, 50–120 watch; blood pressure < 130/80 ok,
   < 140/90 watch; sleep ≥ 6.5 h ok, ≥ 5 h watch; exercise ≥ 30 min ok, ≥ 10 min
   watch. HRV (≥ 30 ms ok, ≥ 20 ms watch) is dormant until a lane exists. A
   family below the confidence floor, or with no current reading, is not graded
   at all rather than graded as a failure. The watch zones are provisional.
3. **Worst band wins.** Any concern → attention; otherwise one watch → balanced,
   two or more → watch; all ok → thriving; nothing graded → no state. An
   all-zero roll-up fires a fifth sequence, `health-none`, which writes
   `[0,0,0,0]`. Without it, the output region would keep showing the last state
   after every measure had left scope (seen live, then fixed). `personal_health_baseline` now reads
   that one-hot roll-up rather than one element per measure, so the machine's
   shape does not change as scope does.
4. **Dynamic slots.** `core/health_scope.py` runs from the API lifespan every
   `HEALTH_SCOPE_INTERVAL_S` (default 30; 0 disables it), once per PE. It
   gives each in-scope band a slot in `[7600:7632]`: stable, lowest free index,
   capacity 32. A locked type holds its last grade. A removed type's slot
   source is deleted. An active type with no current data gets one resync
   request per scope generation (`requestedBy: localAIStack`).
5. `push_health_signal(hr, hrv, sleep)` remains as the simulator and
   compatibility path. It grades raw readings against the same table and writes
   the roll-up. The three legacy sensors (`localai_health_{hr,hrv,sleep}_ok`)
   are removed from any PE at registration, because they sit inside the
   roll-up's window.

Not verified here: the device path end to end on hardware, and the scope
follower against a live universe beyond the probes recorded in the PR.

### T9 — CareKit sync in the Swift bridge (blocked upstream)

`CareKitSync.swift` does not exist. `localHealthkitBridge/ROADMAP.md` puts
CareKit sync explicitly out of scope for v0.1.0 and into v0.2+, naming this
stack's Phase 4a machine as its prerequisite — which is now done. Recorded here
as a dependency, not as ready work. The PE-side CareKit ingest it will target is
already shipped in all four runtimes.

### T10 — Personalisation feedback loop ✅ 2026-09-24

`core/health_rerank.py` re-orders retrieved documents by health state; it never
drops or adds one. Documents about bands the follower graded concern come
first, then watch (for example sleep → `sleep_quality.md`, pulse →
`heart_rate_guide.md`), then the state's guidance (attention → interventions
and recovery; watch → recovery and stress; balanced → sleep and recovery), then
everything else. Within each group the retriever's similarity order is kept, so
thriving or no state is a no-op.

It is applied where the health documents are actually read, in the agent's
`health_search` tool (fetch 8, re-rank, keep 4), and in `graphs/rag_graph.py`
`retrieve` before grading. It is keyed on `metadata.source`. Tests assert every
mapped document exists and every live band has guidance.

Not exercised live: the regression universe's Qdrant has no `health_docs`
collection, and the dockerised API cannot currently run (Docker store damage).

### T11 — Stale offsets inside the machine JSON metadata ✅ 2026-09-23

The problem was wider than the three health machines: all eight localAI
machines had pre-migration offsets in their prose. Every reference was rebased
by the migration's uniform +7388. The exceptions are the references that
correctly point outside the band: `ai_load_bridge`'s corpus outputs
`[272:280]`, the corpus AI window it narrowed from, the HealthKit lanes, and
`session_agent_context`'s element-relative slices.

`tests/test_machine_prose_offsets.py` fails on any region reference in machine
prose that falls outside the localAI band and is not one of those named
exceptions. It fails on the pre-T11 tree.

### Sequencing

T2, T7, T8 and T11 are done. T4, T5 and T6 are small, self-contained code
changes. T10 is the only multi-day feature and reads best after T4. T9 is a
note, not work.

---

## Test coverage matrix (verified 2026-09-23)

| Test file | Type | Count | Flag | Network |
|---|---|---|---|---|
| `tests/test_reality_bridge.py` | unit (fake client) | 28 | (default) | no |
| `tests/test_health_integration.py` | health unit | 29 | (default) | no |
| `tests/test_health_bands.py` | bands, roll-up, slots, T7 parity | 30 | (default) | no |
| `tests/test_health_scope.py` | scope follower (fake PE) | 13 | (default) | no |
| `tests/test_machine_prose_offsets.py` | T11 prose guard | 1 | (default) | no |
| `tests/test_phase2.py` | Phase 2 unit | 31 | (default) | no |
| `tests/test_phase4.py` | Phase 4a+4b unit | 70 | (default) | no |
| `tests/test_bridge_binding.py` | binding unit | 15 | (default) | no |
| `tests/test_model_registry.py` | model registry unit | 18 | (default) | no |
| `tests/test_machine_schema.py` | schema unit | 9 | (default) | no |
| `tests/test_import_guard.py` | import guard unit | 7 | (default) | no |
| `tests/test_registry_resolver.py` | resolver unit | 6 | (default) | no |
| `tests/e2e/test_api_integration.py` | compose integration | 15 | `--integration` | API only |
| `tests/e2e/test_health_pipeline.py` | full live stack | 21 | `--live` | PE+RE+API |
| `tests/e2e/test_patient_wellness_workflow.py` | live workflow | 1 | `--live` | PE+RE+API |

**Default run:** 257 collected, 256 pass, 1 skip
(`test_machine_schema.py:102`, topology builder needs `langchain_core`).
**Full collection:** 294 — the 37 e2e tests are collected and skip without their flag.

```bash
# Unit tests
cd services/api && python -m pytest -v

# Compose integration tests
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d qdrant redis api
python -m pytest services/api/tests/e2e/test_api_integration.py --integration -v

# Live stack tests — PE (3004), RE (3000), localAI (4000) running
python -m pytest services/api/tests/e2e/ --live -v

# Ingest health docs into a running Qdrant
python scripts/ingest_health_docs.py

# Drive the health pipeline
python scripts/simulate_health_push.py --scenario cycle
curl http://localhost:4000/graphql/events
```

---

## Grafana / Loki queries for the health domain

```logql
# All health bridge events
{app="localaistack", service="api"} |~ "health_state_read|health_machine|health_sensor"

# GraphQL triggers from the health machine
{app="localaistack", service="api"} |~ "personal_health_baseline"

# Health state distribution over the last hour
{app="localaistack", service="api"} | json | state =~ "thriving|balanced|watch|attention"
```
