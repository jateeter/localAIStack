# localAIStack — PE/RE Health Integration Audit & Roadmap

Last reviewed: 2026-09-16

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
| Perceptual space | localAI band `[7440:7952]`; allocated through `[7594]`; 358 bytes free | ✅ |
| **Health machine** | `data/machines/personal_health_baseline.json` — `[7574:7578]`→`[7578:7582]` | ✅ Phase 1 |
| **Health bridge** | `push_health_signal()`, `get_health_state()`, `get_current_health_state()` | ✅ Phase 1+2 |
| **Health sim** | `scripts/simulate_health_push.py` — Yuma/MQTT analog, 4 scenarios + cycle | ✅ Phase 1 |
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
| Three competing PE integration registries in `config/` | T2 |
| Chat still makes a synchronous RE round-trip per request | T4 |
| `push_carekit_signal()` has no non-test caller | T5 |
| `/health` does not report CareKit state | T6 |
| No Swift ↔ Python band-threshold parity check | T7 |
| iOS bridge and localAI health machine read **different regions** — no end-to-end path | T8 |
| CareKit sync absent from the Swift bridge (upstream, deferred to its v0.2) | T9 |
| Personalisation feedback loop — health state → RAG re-rank | T10 |
| All three health machine JSONs carry stale offsets in their prose metadata | T11 |

---

## Perceptual space layout (verified 2026-09-16)

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
[7574:7577]  personal health sensors      hr.ok, hrv.ok, sleep.ok          Phase 1
[7574:7578]  personal_health_baseline input window
[7578:7582]  personal_health_baseline output  [thriving, balanced, watch, attention]
[7582:7585]  CareKit sensors              med_adherence, task_completion, symptom_ok
[7582:7586]  medication_adherence input window                             Phase 4a
[7586:7590]  medication_adherence output  [adherent, partial, lapsed, concern]
[7590:7594]  session_health_context carry [thriving, balanced, watch, attention]
[7594:7952]  free — 358 bytes                                              Phase 4b
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
  This is **not** localAI's `[7574:7578]`. See T8.
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
scripts/simulate_health_push.py   (see T8 for the iOS path, which is not yet wired)
  → band normalization (HR [60,100], HRV ≥30 ms, Sleep ≥6.5 h → 0.0/1.0)
  → PE sensor sources: localai_health_{hr,hrv,sleep}_ok  [7574:7577]
  → PE /api/push → RE /api/perceive
  → personal_health_baseline fires (thriving/balanced/watch/attention)
  → perceptualSpace[7578:7582] decoded by get_health_state()
  → session_health_context latches the state into [7590:7594]
  → POST /graphql  updateProcessState  (GREEN/AMBER/RED)
  → localAI ring buffer, Grafana logs
```

| Yuma/MQTT | Health |
|---|---|
| MQTT broker | `simulate_health_push.py` (iOS HealthKit path open — T8) |
| Band normalization rules (JSON) | Band thresholds in `reality_bridge.py` |
| 16 sensor regions | 3 sensor regions `[7574:7577]` |
| AGX001 … AGX032 machines | `personal_health_baseline` + `medication_adherence` |
| GREEN/AMBER/RED governance | thriving→GREEN, watch→AMBER, attention→RED |
| Prometheus paging decisions | GraphQL events ring buffer → Loki/Grafana |

---

## Completed phases

### Phase 1 — Health machine + simulation (DONE, 2026-06-18)

- `data/machines/personal_health_baseline.json` — 4-state classifier,
  `PASSTHROUGH` arbiter, `gte` match, input `[7574:7578]`, output `[7578:7582]`
- `_HEALTH_SENSORS` + `push_health_signal()` + `get_health_state()`
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

What remains for localAIStack is wiring, not building: T7 and T8.

---

## Remaining work

### T1 — Roadmap truth pass ✅ 2026-09-16

This document. Status tables, offsets, test counts and the Phase 4c contract
now match the code, and the corrections are recorded rather than silently applied.

### T2 — Collapse three PE integration registries into one

`config/` holds three PE integration registries with no declared authority:

| File | Shape | Loaded by | Tested by |
|---|---|---|---|
| `integrations.json` | canonical `healthkit:<type>` ids; health + CareKit + `openclaw-xacp` | the `INTEGRATIONS_CONFIG` target | — |
| `pe-integrations.json` | canonical, but a strict **subset** — health only, no ACP | nothing | `test_phase4.py:822-917`, 7 tests |
| `integrations.healthkit-localai.json` | `sourceMappings[].hkTypeIdentifier` — **unresolvable** by any PE | nothing | `test_phase2.py:66` |

Three failures compound here. The stale file's keys cannot be matched by the
canonical lookup, so it would silently resolve nothing if it were ever loaded.
`pe-integrations.json` duplicates the six health mappings verbatim and drops
ACP — and it is the file the CareKit region-parity tests assert against, so the
offsets guarding those sensors are checked against a PE integration registry no
runtime reads. `pe-integrations.json:99` then cites the stale file as the
authority for band thresholds, a third copy pointing at the one that cannot be
loaded.

This is the duplication failure the engineering contract's qualifier rule warns
about, in `config/`: copies drift, and with no authority a reader cannot tell
which is current.

- Keep `config/integrations.json` as the sole PE integration registry.
- Delete `config/pe-integrations.json` and `config/integrations.healthkit-localai.json`.
- Repoint `test_phase2.py:66` and the seven `test_phase4.py` PE-integration-registry tests at the survivor.
- Preserve the band-threshold assertions — they are the only check locking the
  JSON to the Python constants — against `integrations.json`.

Phase 4a's instruction to add a `carekitSourceMappings` key to the stale file is
withdrawn: the CareKit mappings already exist correctly in `integrations.json`.

### T3 — (withdrawn, folded into T2)

### T4 — Chat warm path: read the carry, not the network

`routers/chat.py:86-90` calls `get_current_health_state()` on every request with
health context enabled — an HTTP round-trip to the RE per chat turn. Phase 4b
built the carry precisely to remove it.

Prefer the `health_state` already present in the most recent push response via
`get_session_context()`, and fall back to the poll only when the carry is cold.

### T5 — Operator-facing CareKit driver

`push_carekit_signal()` has no non-test caller. `scripts/simulate_health_push.py`
exposes only the health scenarios. Add `--carekit adherent|partial|lapsed|concern`
so the CareKit leg is exercisable the way the health leg is.

### T6 — `/health` reports CareKit

`routers/health.py:48-55` decodes `health_state` only. Add `carekit_state` and
the CareKit sensor count to the `re` / `pe` sub-objects, mirroring Phase 3.

### T7 — Swift ↔ Python band-threshold parity check

`_HR_LOW_BPM` / `_HR_HIGH_BPM` / `_HRV_OK_MS` / `_SLEEP_OK_HOURS` in
`reality_bridge.py` have no counterpart assertion in `localHealthkitBridge` —
no `bandThresholds` reference exists anywhere in that repo. Note this is a
cross-repo gap and not merely a missing assert: the Swift normalizer targets the
corpus families at `[4320:4344]`, so a parity check must first decide *which*
thresholds it is locking together. Sequence it after T8.

### T8 — End-to-end iOS → localAI health machine

**No path connects the shipped iOS bridge to `personal_health_baseline` today.**
The bridge posts family vectors into the corpus regions `[4320:4344]`;
localAI's machine reads `[7574:7578]`. Both halves work; they are not joined.

Two routes, and the choice is the real decision in this task:

1. Run a PE with `INTEGRATIONS_CONFIG=localAIStack/config/integrations.json` and
   have the bridge post `bridgeId: healthkit-localai`. The mappings already
   resolve to the localAI sensors. Costs a dedicated PE or a merged PE integration registry.
2. Add localAI band-mode mappings to the shared PE integration registry so one PE feeds both
   region sets. Tracked upstream as `localHealthkitBridge` post-MVP
   ("localAI band-mode target").

This is the roadmap's one genuine remaining integration item. Everything else
under Phase 4c is upstream and green.

### T9 — CareKit sync in the Swift bridge (blocked upstream)

`CareKitSync.swift` does not exist. `localHealthkitBridge/ROADMAP.md` puts
CareKit sync explicitly out of scope for v0.1.0 and into v0.2+, naming this
stack's Phase 4a machine as its prerequisite — which is now done. Recorded here
as a dependency, not as ready work. The PE-side CareKit ingest it will target is
already shipped in all four runtimes.

### T10 — Personalisation feedback loop

The one unbuilt feature. No `rerank` / `re_rank` code exists anywhere in
`services/api/`. Health state should re-rank RAG retrieval — a user in
`attention` or `watch` should surface recovery and intervention documents ahead
of general ones.

- Consume `get_session_context()["health_state"]` in `graphs/rag_graph.py`
  (free to read once T4 lands).
- Re-rank retrieved documents by health relevance before the grade step.
- Tests: per-state ordering, and no-op when the carry is cold.

### T11 — Stale offsets inside the machine JSON metadata

All three health machine JSONs carry the pre-migration layout in their prose —
`metadata.eventSpace`, `metadata.outputSpace`, and every
`metadata.sensorSources[].region` — five stale strings per file, fifteen total:

| File | Says | Actual `perceptualMapping` |
|---|---|---|
| `medication_adherence.json` | `[194:198]` → `[198:202]` | `[7582:7586]` → `[7586:7590]` |
| `session_health_context.json` | pre-migration | `[7578:7582]` → `[7590:7594]` |
| `personal_health_baseline.json` | pre-migration | `[7574:7578]` → `[7578:7582]` |

The drift guard checks `perceptualMapping`, which is correct in all three — it
does not read prose, so nothing catches this. The risk is a reader trusting the
description over the mapping. Consider extending `verify_machine_offsets()` to
assert the `sensorSources` region strings parse to the mapped window, which
would make this class of drift impossible to reintroduce.

### Sequencing

T2 and T11 are config and data hygiene — one sitting, no dependencies.
T4, T5, T6 are small, self-contained code changes. T8 is the decision that
unblocks T7, and both need a live universe to verify. T10 is the only
multi-day feature and reads best after T4. T9 is a note, not work.

---

## Test coverage matrix (verified 2026-09-16)

| Test file | Type | Count | Flag | Network |
|---|---|---|---|---|
| `tests/test_reality_bridge.py` | unit (fake client) | 28 | (default) | no |
| `tests/test_health_integration.py` | health unit | 22 | (default) | no |
| `tests/test_phase2.py` | Phase 2 unit | 36 | (default) | no |
| `tests/test_phase4.py` | Phase 4a+4b unit | 70 | (default) | no |
| `tests/test_bridge_binding.py` | binding unit | 15 | (default) | no |
| `tests/test_model_registry.py` | model registry unit | 18 | (default) | no |
| `tests/test_machine_schema.py` | schema unit | 9 | (default) | no |
| `tests/test_import_guard.py` | import guard unit | 7 | (default) | no |
| `tests/test_registry_resolver.py` | resolver unit | 6 | (default) | no |
| `tests/e2e/test_api_integration.py` | compose integration | 15 | `--integration` | API only |
| `tests/e2e/test_health_pipeline.py` | full live stack | 21 | `--live` | PE+RE+API |
| `tests/e2e/test_patient_wellness_workflow.py` | live workflow | 1 | `--live` | PE+RE+API |

**Default run:** 211 collected, 210 pass, 1 skip
(`test_machine_schema.py:102`, topology builder needs `langchain_core`).
**Full collection:** 248 — the 37 e2e tests are collected and skip without their flag.

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
