# localAIStack — PE/RE Health Integration Audit & Roadmap

## Audit: current integration state

### What is complete

| Layer | Component | Status |
|---|---|---|
| PE/RE bridge | `core/reality_bridge.py` — sensors, drift guard, push paths | ✅ |
| RAG pipeline | `rag_graph.py` — retrieve → grade → generate → rewrite | ✅ |
| Agent pipeline | `agent_graph.py` — agent/tools loop + activity metrics | ✅ |
| Session carries | 5 machines: rag, agent, ai_load_bridge, classifiers | ✅ |
| Graph topology | `topology_builder.py` — binds LangGraph nodes to perceptual space | ✅ |
| GraphQL receiver | `routers/graphql_endpoint.py` — machine → localAI upstream trigger | ✅ |
| Bridge unit tests | `tests/test_reality_bridge.py` — 20 tests, all passing | ✅ |
| Perceptual space | [0:186] fully allocated, [186:256] free (health + future) | ✅ |
| **Health machine** | `data/machines/personal_health_baseline.json` — [186:190]→[190:194] | ✅ Phase 1 |
| **Health bridge** | `push_health_signal()`, `get_health_state()`, `get_current_health_state()`, startup | ✅ Phase 1+2 |
| **Health sim** | `scripts/simulate_health_push.py` — Yuma/MQTT analog | ✅ Phase 1 |
| **Health tests** | 58 tests across `test_health_integration.py` + `test_phase2.py` | ✅ Phase 1+2 |
| **Health-aware chat** | `routers/chat.py` — health context injection, 3-level opt-in | ✅ Phase 2 |
| **HealthKit config** | `config/integrations.healthkit-localai.json` — HK type → PE sensor mapping | ✅ Phase 2 |
| **Health RAG** | `health_docs` collection, 9 knowledge docs, `health_search` agent tool | ✅ Phase 2+3 |
| **Health doc ingest** | `scripts/ingest_health_docs.py` — loads health docs into Qdrant | ✅ Phase 2 |
| **`/health` bridge status** | `pe` + `re` fields; `bridge` rollup; async parallel checks | ✅ Phase 3 |
| **Compose integration tests** | `tests/e2e/test_api_integration.py` — 15 tests, `--integration` flag | ✅ Phase 3 |
| **Live stack tests** | `tests/e2e/test_health_pipeline.py` — 13 tests, `--live` flag | ✅ Phase 3 |
| **CI e2e workflow** | `.github/workflows/e2e.yml` + `docker-compose.ci.yml` override | ✅ Phase 3 |

### What is not yet done

| Gap | Scope | Phase |
|---|---|---|
| CareKit bridge | `localHealthkitBridge` README describes CareKit alongside HealthKit; no machine yet | 4 |
| Personalisation feedback loop | Health state → RAG re-rank → session carry (health context persists across sessions) | 4 |

---

## Perceptual space layout (post-Phase 1)

```
[0:12]    Legacy machines (MultiStep, RSFlipFlop, KleeneStar …)
[12:60]   DC sensor inputs
[60:80]   DC control signals / rag topology
[76:84]   RAG topology nodes  (4 nodes × 2 bytes)
[84:88]   RAG topology output
[88:104]  DC machines
[104:108] Agent topology nodes (2 × 2 bytes)
[108:112] Agent topology output
[112:116] session_rag_context output  [last_generate, last_rewrite, last_abort, _]
[116:120] session_agent_context output [agent_ever_engaged, tools_ever_used, _, _]
[120:144] ai_load_bridge output (6 × 4D nominal/elevated/critical patterns)
[144:150] DC terminal FF outputs (relocated)
[150:186] AI DC machine outputs (6 machines × 6D)
[186:190] Personal health sensors (hr.ok, hrv.ok, sleep.ok, reserved)   Phase 1
[190:194] personal_health_baseline output (thriving, balanced, watch, attention)   Phase 1
[194:198] CareKit sensors (med_adherence, task_completion, symptom_ok, reserved)   Phase 4a
[198:202] medication_adherence output (adherent, partial, lapsed, concern)   Phase 4a
[202:206] session_health_context carry (thriving, balanced, watch, attention)   Phase 4b
[206:256] Free (50 bytes) — stress index, activity level, medication side-effects …
```

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
Apple Watch / iPhone (HealthKit)  OR  scripts/simulate_health_push.py
  → band normalization (HR [60,100], HRV ≥30ms, Sleep ≥6.5h → 0.0/1.0)
  → PE sensor sources: localai_health_{hr,hrv,sleep}_ok  [186:189]
  → PE /api/push → RE /api/perceive
  → personal_health_baseline machine fires (thriving/balanced/watch/attention)
  → perceptualSpace[190:194] decoded by get_health_state()
  → POST /graphql  updateProcessState  (GREEN/AMBER/RED)
  → localAI ring buffer, Grafana logs
```

Key structural parallels:

| Yuma/MQTT | Health/HealthKit |
|---|---|
| MQTT broker | iOS HealthKit / simulate_health_push.py |
| Band normalization rules (JSON) | Band thresholds in reality_bridge.py |
| 16 sensor regions | 3 sensor regions [186:189] |
| AGX001 … AGX032 machines | personal_health_baseline machine |
| GREEN/AMBER/RED governance | thriving→GREEN, watch→AMBER, attention→RED |
| Prometheus paging decisions | GraphQL events ring buffer → Loki/Grafana |

---

## Roadmap

### Phase 1 — Health machine + simulation (DONE, 2026-06-18)

- [x] `data/machines/personal_health_baseline.json` — 4-state CES classifier
- [x] `_HEALTH_SENSORS` + `push_health_signal()` + `get_health_state()` in `reality_bridge.py`
- [x] `import_health_machines()` wired into `main.py` startup
- [x] Health sensors added to `register_sensors()` and `_SENSOR_TO_MACHINE`
- [x] Health machine added to `_EXPECTED_MACHINE_OFFSETS` drift guard
- [x] `scripts/simulate_health_push.py` — cycles all four health scenarios
- [x] `tests/test_health_integration.py` — 20 unit + e2e tests (network-free)

**To verify Phase 1 (PE+RE running):**
```bash
python scripts/simulate_health_push.py --scenario cycle
curl http://localhost:4000/graphql/events
```

**To run new tests:**
```bash
cd services/api && python -m pytest tests/test_health_integration.py -v
```

---

### Phase 2 — Health-aware chat context (DONE, 2026-06-18)

**Goal:** the localAI chat responses are informed by the current health state without the user having to explicitly state their health status.

**Tasks:**

1. **`routers/chat.py` health context injection** ✅
   - `get_current_health_state()` added to `reality_bridge.py` — reads
     `GET /api/perceptual-simulation/state` on the RE (no PE push, no side effects).
   - `_inject_health_context()` + `_HEALTH_HINTS` dict added to `chat.py`.
   - Three-level opt-in: `ChatRequest.health_context` body field (highest priority) →
     `X-Health-Context: enabled` header → `Settings.health_context_enabled` global flag.
   - `config.py` extended with `health_collection_name: str = "health_docs"` and
     `health_context_enabled: bool = False` (opt-in, override via env var).

2. **HealthKit ingest registration** ✅
   - `config/integrations.healthkit-localai.json` created — maps 3 HK type identifiers
     (HeartRate, HRV SDNN, SleepAnalysis) to PE sensors at [186:188].
   - Primary sourceMappings use `normalize.mode = "passthrough"` for TS PE compatibility.
   - `cppLspRuntimeConfig` block documents native `band` mode for CPP/LSP runtimes.
   - All `bandThresholds` are locked to the Python constants in `reality_bridge.py`
     and verified by `test_healthkit_config_band_thresholds_match_python_constants`.

3. **Health docs RAG collection** ✅
   - `core/vector_store.py` extended with `get_health_vector_store()` —
     separate `_health_store` global pointing to `health_docs` collection.
   - `graphs/agent_graph.py` gains `health_search` tool added to `TOOLS`.
   - Four health knowledge documents created in `data/documents/health/`:
     - `hrv_interpretation.md` — SDNN ranges, recovery factors, cognitive impact
     - `heart_rate_guide.md` — RHR bands, nominal range, causes of HR anomalies
     - `sleep_quality.md` — duration thresholds, sleep stages, hygiene practices
     - `wellness_baselines.md` — the four health states and localAI behavior per state
   - `scripts/ingest_health_docs.py` — loads health docs into `health_docs` collection
     (run once after standing up Qdrant; `--clear` flag for clean rebuild).

**Tests added:** `tests/test_phase2.py` — 36 tests, all passing.
**Total Phase 1+2 health tests:** 94 passed (36 Phase 2 + 22 Phase 1 health + 36 Phase 1 bridge).

**To verify Phase 2 (services running):**
```bash
# Run tests (network-free)
cd services/api && python -m pytest tests/test_phase2.py tests/test_health_integration.py -v

# Ingest health docs into Qdrant
python scripts/ingest_health_docs.py

# Chat with health context via header
curl -s http://localhost:4000/chat \
  -H "Content-Type: application/json" \
  -H "X-Health-Context: enabled" \
  -d '{"messages": [{"role": "user", "content": "How should I plan my day?"}]}'

# Or per-request body field
curl -s http://localhost:4000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "I feel off today"}], "health_context": true}'

# Agent health_search tool
curl -s http://localhost:4000/graph/agent \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What does low HRV mean for my recovery?"}]}'
```

---

### Phase 3 — Full stack e2e test (DONE, 2026-06-18)

**Goal:** a single `pytest` run against a live Docker compose stack that validates the entire pipeline end-to-end.

**Deliverables:**

1. **`/health` endpoint — PE/RE bridge status** ✅
   - New `pe` and `re` sub-objects in `services` response
   - `pe`: `status`, `sensor_count`, `health_sensors` count
   - `re`: `status`, `health_state` (live decode from perceptualSpace[190:194]), `machine_count`, `ps_length`
   - New top-level `bridge` field: `"ok"` | `"degraded"` (PE/RE optional — their status doesn't affect `status`)
   - PE and RE checks run in parallel via `asyncio.gather`

2. **E2E test structure** ✅
   - `tests/e2e/conftest.py` — `--integration` and `--live` CLI flags; `live_api`, `live_pe`, `live_re` session fixtures; `poll_until()` helper
   - `tests/e2e/test_api_integration.py` — 15 `@integration` tests, CI-friendly (no PE/RE): `/health` structure, bridge-degraded behaviour, Qdrant/Redis ok, root, docs, graphql/events
   - `tests/e2e/test_health_pipeline.py` — 13 `@live` tests: sensor registration, machine import, PE→RE push→state for all 4 scenarios, `/health` reports correct state, chat injection, agent health_search, GraphQL trigger, full cycle
   - All 28 e2e tests collect cleanly; all skip without `--integration`/`--live` flags
   - E2E excluded from default `pytest` run via `pyproject.toml addopts`

3. **CI workflow** ✅
   - `.github/workflows/e2e.yml` — two jobs: `api-integration` (compose stack) + `unit` (sanity check)
   - `docker-compose.ci.yml` — override strips loki logging driver, removes loki from `depends_on`, sets PE/RE to unreachable addresses
   - Compose stack start: `docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d qdrant redis api`
   - 90-second readiness poll on the API Docker healthcheck
   - Runs `pytest tests/e2e/test_api_integration.py --integration -v`

4. **Health docs compendium** ✅ — 7 documents in `data/documents/health/`:
   - `hrv_interpretation.md` — SDNN ranges, recovery factors, cognitive impact
   - `heart_rate_guide.md` — RHR bands, nominal range, medical thresholds
   - `sleep_quality.md` — duration, stages, hygiene practices
   - `wellness_baselines.md` — the four states and localAI behaviour
   - `recovery_protocols.md` — training load, periodisation, evidence-based recovery interventions
   - `stress_and_hrv.md` — ANS anatomy, breathing protocols, HRV biofeedback
   - `wearable_metrics.md` — Apple Watch measurement methods, accuracy, HK delivery cadence
   - `nutrition_and_recovery.md` — protein timing, hydration, caffeine, alcohol effects on HRV
   - `health_state_interventions.md` — specific actionable steps per state (thriving/balanced/watch/attention)

**To run locally:**
```bash
# Unit tests (default, no services needed)
pytest                # → 85 tests

# Compose integration tests (starts qdrant+redis+api)
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d qdrant redis api
pytest services/api/tests/e2e/test_api_integration.py --integration -v

# Live stack tests (PE + RE + localAI all running)
pytest services/api/tests/e2e/test_health_pipeline.py --live -v

# Ingest health docs into running Qdrant
python scripts/ingest_health_docs.py
```

---

### Phase 4 — CareKit machine · health session carry · iOS bridge

**Goal:** extend the health domain to CareKit medication adherence, latch the health state as a durable session carry so downstream machines can consume it without waiting for a fresh push, and close the physical data loop with a real iOS HealthKit + CareKit bridge module.

**Perceptual space allocation (post-Phase 4):**

```
[186:190]  personal health sensors — hr.ok, hrv.ok, sleep.ok, reserved   (Phase 1)
[190:194]  personal_health_baseline output — thriving, balanced, watch, attention   (Phase 1)
[194:198]  CareKit sensors — med_adherence, task_completion, symptom_ok, reserved  (4a NEW)
[198:202]  medication_adherence output — adherent, partial, lapsed, concern        (4a NEW)
[202:206]  session_health_context carry — thriving, balanced, watch, attention      (4b NEW)
[206:256]  free — 50 bytes for stress index, activity classification, future        
```

---

#### 4a — CareKit machine at [194:202]

**What CareKit tracks:** Apple CareKit manages structured care plans — scheduled medication doses, daily activity tasks, and symptom check-ins. Each scheduled event produces an `OCKOutcome` when completed. The bridge aggregates these into three normalised scalars before sending to the PE.

**Sensor layout [194:198]:**

| Offset | Sensor | Range | Source |
|---|---|---|---|
| 194 | `localai_carekit_med_adherence` | 0.0–1.0 | doses taken / doses scheduled (rolling 24 h) |
| 195 | `localai_carekit_task_completion` | 0.0–1.0 | CareKit activity tasks completed / scheduled today |
| 196 | `localai_carekit_symptom_ok` | 0.0–1.0 | 1.0 = no symptoms reported or severity low; 0.0 = moderate+ |
| 197 | reserved | — | future (side-effect flag, pain scale) |

Sensor TTLs: med_adherence 3 600 000 ms (1 h, dose window), task_completion 86 400 000 ms (24 h), symptom_ok 86 400 000 ms.

**Machine: `data/machines/medication_adherence.json`**

```
perceptualMapping.input:  { offset: 194, length: 4 }
perceptualMapping.output: { offset: 198, length: 4 }
arbiterRule: OR   matchAlgorithm: gte   (same as personal_health_baseline)
```

Four mutually exclusive sequences (OR-arbiter, all isInitial), partition the `[med × task × symptom]` space:

| State | Output | Guard logic |
|---|---|---|
| `adherent` | `[1,0,0,0]` | med HIGH AND task HIGH AND symptom HIGH — full compliance, no symptoms |
| `partial` | `[0,1,0,0]` | med HIGH AND (task LOW OR symptom LOW) — medication taken but incomplete follow-through |
| `lapsed` | `[0,0,1,0]` | med LOW AND symptom HIGH — missed doses but no symptom escalation |
| `concern` | `[0,0,0,1]` | med LOW AND symptom LOW — missed doses with symptom flag (wildcard on task) |

`adherent` requires all three HIGH; `partial` requires med HIGH but at least one of task/symptom LOW; `lapsed` requires med LOW and symptom HIGH; `concern` requires med LOW and symptom LOW — the same disjoint partitioning used by `personal_health_baseline`.

**Python additions (`core/reality_bridge.py`):**

```python
# Sensors — add to register_sensors() via _CAREKIT_SENSORS list
_CAREKIT_SENSORS = [
    {"sensorId": "localai_carekit_med_adherence",   "region": {"offset": 194, "length": 1}, "ttlMs": 3_600_000},
    {"sensorId": "localai_carekit_task_completion", "region": {"offset": 195, "length": 1}, "ttlMs": 86_400_000},
    {"sensorId": "localai_carekit_symptom_ok",      "region": {"offset": 196, "length": 1}, "ttlMs": 86_400_000},
]

# Offset constants
_CAREKIT_OUTPUT_OFFSET = 198   # one-hot: [adherent, partial, lapsed, concern]
_CAREKIT_MACHINE_PATH  = _MACHINES_DIR / "medication_adherence.json"
_CAREKIT_MACHINE_NAME  = "localai/medication_adherence"

def push_carekit_signal(
    med_adherence_ratio:    float,   # 0.0–1.0
    task_completion_ratio:  float,   # 0.0–1.0
    symptom_ok:             float,   # 1.0 = no significant symptoms
) -> str:
    """
    Write CareKit compliance scalars to PE, trigger push, return decoded
    adherence state: "adherent" | "partial" | "lapsed" | "concern".
    Falls back to "partial" when PE/RE is unreachable.
    """

def get_carekit_state(ps: list) -> str | None:
    """One-hot decode of medication_adherence output at [198:202]."""
```

**Drift guard additions** (`_EXPECTED_MACHINE_OFFSETS`):

```python
{"path": _CAREKIT_MACHINE_PATH, "input": {"offset": 194, "length": 4}, "output": {"offset": 198, "length": 4}},
```

**Startup wiring (`main.py`):** add `import_carekit_machine()` to the lifespan startup sequence (after `import_health_machines()`).

**HealthKit config extension (`config/integrations.healthkit-localai.json`):** add CareKit source mappings under a new `"carekitSourceMappings"` key mirroring the HealthKit section — HK types map to `localai_carekit_*` sensor IDs with `normalize.mode = "passthrough"`.

**Tests (`tests/test_phase4.py`):**
- `test_carekit_sensor_layout_matches_machine_input_window` — verifies [194:198] ⊂ machine input
- `test_carekit_machine_json_four_sequences_mutually_exclusive`
- `test_push_carekit_signal_returns_correct_state[adherent/partial/lapsed/concern]`
- `test_get_carekit_state_decodes_onehot_correctly`
- `test_carekit_drift_guard_in_expected_offsets`
- `test_carekit_config_extension_present_in_integrations_json`

---

#### 4b — Health session carry

**The problem:** `personal_health_baseline` writes [190:194] when a HealthKit push arrives. Between pushes — during a conversation that may span minutes — the RE perceptual space holds those values via PE carry-forward semantics. However, downstream machines (e.g., a future RAG re-rank machine) need to consume a stable health-state signal as part of their *input* window, not read it out-of-band via `get_current_health_state()`. A bistable carry machine provides that.

**Machine: `data/machines/session_health_context.json`**

```
perceptualMapping.input:  { offset: 190, length: 4 }   ← reads health classifier output directly
perceptualMapping.output: { offset: 202, length: 4 }   ← writes to carry region
arbiterRule: OR   matchAlgorithm: gte
```

Four sequences (one per health state), all isInitial, same OR-arbiter bistable pattern as `session_rag_context.json`:

| Sequence | Trigger | Carry output |
|---|---|---|
| `sess-health-thriving` | ps[190] ≥ 0.5 | `[1,0,0,0]` |
| `sess-health-balanced` | ps[191] ≥ 0.5 | `[0,1,0,0]` |
| `sess-health-watch`    | ps[192] ≥ 0.5 | `[0,0,1,0]` |
| `sess-health-attention`| ps[193] ≥ 0.5 | `[0,0,0,1]` |

When no health push occurs in a cycle (agent tool call, RAG step) none of the four sequences fire and PE carry-forward holds [202:206] unchanged — the health state persists across the entire conversation without re-querying the RE.

**Why this is necessary vs. just reading [190:194] directly:** `get_current_health_state()` makes an HTTP call to the RE on every chat request. The carry machine removes that synchronous call from the request path entirely — the health state is available as part of the assembled perceptual space on every push and can be read from the push response body.

**Python additions (`core/reality_bridge.py`):**

```python
_HEALTH_CARRY_OFFSET = 202   # [thriving, balanced, watch, attention] carry

# Add to _SESSION_MACHINE_DEFS
{"path": _MACHINES_DIR / "session_health_context.json", "name": "localai/session_health_context"},

# Add to _EXPECTED_MACHINE_OFFSETS
{"path": _MACHINES_DIR / "session_health_context.json",
 "input": {"offset": 190, "length": 4}, "output": {"offset": 202, "length": 4}},

# Extend get_session_context() return dict:
"health_state": get_health_state_from_carry(ps),   # reads [202:206]

def get_health_state_from_carry(ps: list) -> str | None:
    """Decode health state from the session carry at [202:206].
    Differs from get_health_state() which reads the live classifier output [190:194].
    Returns None until the first health push this RE session."""
```

**`get_session_context()` return shape (after 4b):**

```python
{
    "rag":            "generate" | "rewrite" | "abort" | None,
    "agent":          {"ever_engaged": bool, "tools_ever_used": bool},
    "agent_activity": "productive" | "normal" | "struggling" | None,
    "ai_load_tier":   "nominal" | "elevated" | "critical" | None,
    "health_state":   "thriving" | "balanced" | "watch" | "attention" | None,  # NEW
}
```

**Chat integration impact:** `chat.py` currently calls `get_current_health_state()` synchronously on every request. After 4b, update `_inject_health_context()` to prefer the carry from `get_session_context()` (available from the most recent push response) and fall back to the HTTP poll only when the carry is None. This eliminates the extra HTTP round-trip on warm paths.

**Tests (extend `tests/test_phase4.py`):**
- `test_session_health_context_machine_reads_health_output_window`
- `test_session_health_context_carry_correct_per_state[thriving/balanced/watch/attention]`
- `test_get_session_context_includes_health_state_key`
- `test_health_state_from_carry_decodes_onehot`
- `test_bistable_hold_when_no_health_push` — assert None when carry region is all-zero

---

#### 4c — iOS localHealthkitBridge Swift module

**Architecture overview:**

```
Apple Watch / iPhone
  ↓  HK anchored observers (background delivery)
localHealthkitBridge (Swift Package)
  ↓  BandNormalizer — applies thresholds → 0.0/1.0
  ↓  LocalAIBridge — HTTP POST /api/integrations/healthkit/ingest
PE (perception-engine, port 3004)
  ↓  /api/integrations/healthkit/ingest handler
  ↓  loads config/integrations.healthkit-localai.json
  ↓  writes to sensor regions via existing _write_sensor() path
  ↓  calls /api/push → RE evaluates machines
RE (reality-engine, port 3000)
  ↓  personal_health_baseline + medication_adherence fire
  ↓  perceptualSpace[190:202] updated
localAI API (port 4000)
  ↓  chat.py reads health_state via get_current_health_state() or carry
```

**Step 1 — PE ingest endpoint (TypeScript, `perception-engine/backend/src/server.ts`)**

Add `POST /api/integrations/healthkit/ingest` before the existing `/api/push` route:

```typescript
// Body schema
interface HKIngestPayload {
  samples: Array<{
    hkTypeIdentifier: string;
    value: number;           // raw HK value (bpm, ms, hours — pre-aggregated)
    unit: string;            // "bpm" | "ms" | "h" (informational)
    startDate: string;       // ISO 8601
    endDate: string;
  }>;
}
```

Handler logic:
1. Load `integrations.healthkit-localai.json` (cached at startup, `INTEGRATIONS_CONFIG` env var)
2. For each sample, look up `hkTypeIdentifier` in `sourceMappings`
3. Apply normalization:
   - `passthrough` → write `sample.value` directly (iOS bridge pre-computes 0.0/1.0)
   - `minmax` → `(value - min) / (max - min)` clamped [0, 1]
4. Call existing `writeSensor(sensorId, [normalizedValue])` — same path as `/api/sensors/:id`
5. After all samples processed, call `/api/push` to trigger RE evaluation
6. Return `{ accepted: N, rejected: 0, state: <decoded health state from push response> }`

Also add `GET /api/integrations/healthkit/status` — returns sensor TTL status for the three health sensors (used by the iOS bridge to display last-sync time).

**Step 2 — Swift Package structure (`localHealthkitBridge/`)**

```
localHealthkitBridge/
  Package.swift
  Sources/HealthKitBridge/
    BridgeConfiguration.swift    — PE base URL, retry policy, auth token (if any)
    HealthKitManager.swift       — HK store access, permission request, sample queries
    BandNormalizer.swift         — threshold logic mirroring integrations JSON bandThresholds
    LocalAIBridge.swift          — HTTP client; POST /api/integrations/healthkit/ingest
    BackgroundDelivery.swift     — HK background delivery observer registration
    CareKitSync.swift            — aggregates OCKOutcome → adherence ratio, writes carekit sensors
  Tests/HealthKitBridgeTests/
    BandNormalizerTests.swift
    LocalAIBridgeTests.swift     — URLProtocol mock
```

**Package.swift dependencies:**

```swift
dependencies: [
    .package(url: "https://github.com/StanfordSpezi/SpeziHealthKit.git", from: "0.5.0"),
    .package(url: "https://github.com/StanfordSpezi/SpeziCareKit.git",   from: "0.5.0"),
]
```

**`BandNormalizer.swift` — mirrors `reality_bridge.py` constants:**

```swift
struct BandNormalizer {
    static let hrLow:   Double = 60.0
    static let hrHigh:  Double = 100.0
    static let hrvOk:   Double = 30.0   // SDNN ms
    static let sleepOk: Double = 6.5    // hours

    static func normalizeHR(_ bpm: Double)       -> Double { (bpm >= hrLow && bpm <= hrHigh) ? 1.0 : 0.0 }
    static func normalizeHRV(_ sdnnMs: Double)   -> Double { sdnnMs >= hrvOk  ? 1.0 : 0.0 }
    static func normalizeSleep(_ hours: Double)  -> Double { hours  >= sleepOk ? 1.0 : 0.0 }
}
```

These thresholds must stay locked to `bandThresholds` in `config/integrations.healthkit-localai.json`. Add a CI step that parses the JSON and asserts matching constants (Swift test or bash comparison).

**`HealthKitManager.swift` — data types and query strategy:**

| HK type | Query method | Aggregation |
|---|---|---|
| `HKQuantityTypeIdentifierHeartRate` | `HKStatisticsQuery` (discreteAverage) | Average over last 10 min at rest |
| `HKQuantityTypeIdentifierHeartRateVariabilitySDNN` | `HKSampleQuery` | Most recent SDNN sample |
| `HKCategoryTypeIdentifierSleepAnalysis` | `HKSampleQuery` | Sum of `.asleepCore + .asleepDeep + .asleepREM` in last 24 h |

Background delivery: use `HKObserverQuery` + `enableBackgroundDelivery(for:frequency:)` with `.immediate` for HR and HRV, `.daily` for sleep. SpeziHealthKit wraps this in `HealthKit.requestAuthorization` + `@HealthKitQuery` property wrapper.

**`CareKitSync.swift` — CareKit → carekit sensor scalars:**

```swift
// Queries OCKStore for the rolling 24-h window
func computeAdherenceRatio() async -> Double   // doses taken / scheduled
func computeTaskCompletion() async -> Double   // tasks completed / scheduled
func computeSymptomOk() async -> Double        // 1.0 if no symptom entry or severity < moderate
```

Called by `BackgroundDelivery` on CareKit store change notifications.

**`LocalAIBridge.swift` — HTTP client:**

```swift
struct HKSample: Codable { let hkTypeIdentifier: String; let value: Double; let unit: String;
                            let startDate: String; let endDate: String }
struct IngestPayload: Codable { let samples: [HKSample] }

func ingest(samples: [HKSample]) async throws -> IngestResponse
```

Retry policy: 3 attempts with exponential backoff (2 s, 4 s, 8 s). On permanent failure, log locally; do not surface an alert to the user unless the bridge has been silent for > 30 min.

**`BridgeConfiguration.swift` — runtime configuration:**

```swift
struct BridgeConfiguration {
    var peBaseURL: URL       // default: http://localhost:3004 (dev) / http://host.docker.internal:3004 (device)
    var integrationId: String = "healthkit-localai-v1"
    var retryCount: Int = 3
    var pushAfterIngest: Bool = true   // trigger RE evaluation on every batch
}
```

Load from app `Info.plist` key `LocalAIPEBaseURL` or `LOCALAI_PE_URL` env (useful for Xcode scheme environment variables during development).

**Step 3 — Connecting localAI chat:**

The iOS app does not need to call the localAI chat API directly from the bridge — the bridge only pushes sensor data. The chat connection is a separate app-level concern (WebView, native URLSession, or open-webui). What the bridge enables:

- The PE/RE health state is updated in the background on every HK delivery
- When the user opens a chat in the iOS app and sends a message:
  - The app includes `X-Health-Context: enabled` header (or `"health_context": true` body field)
  - The localAI API reads the current health state from the RE carry and injects it into the system prompt
  - No additional round-trip is needed from the iOS side

For apps using open-webui, add `HEALTH_CONTEXT_ENABLED=true` to the API environment — this enables injection globally without requiring the iOS app to set the header per-request.

**Step 4 — Developer testing guide**

```bash
# 1. Unit tests — Swift Package
cd localHealthkitBridge
swift test

# 2. Simulator testing (no real Apple Watch needed)
#    Use the simulate script to push band values directly:
python scripts/simulate_health_push.py --scenario thriving
#    Then verify the PE received the push:
curl http://localhost:3004/api/integrations/healthkit/status

# 3. Device testing checklist
#    a. Build bridge in Xcode, add to target app
#    b. Grant HealthKit permissions (HR, HRV, Sleep)
#    c. Wear Apple Watch for 10 min (HK delivers HR in background)
#    d. Check PE sources: curl http://localhost:3004/api/sources
#    e. Check RE state: curl http://localhost:3000/api/perceptual-simulation/state
#    f. Verify chat injection: curl -H "X-Health-Context: enabled" http://localhost:4000/chat ...

# 4. CareKit testing
#    a. Create a test care plan in your app with OCKStore
#    b. Mark a task as completed → verify localai_carekit_task_completion sensor updates
#    c. Log a medication dose → verify localai_carekit_med_adherence sensor updates
```

**Tests (`tests/test_phase4.py`, extended):**
- `test_pe_healthkit_ingest_endpoint_registered` — verifies `/api/integrations/healthkit/ingest` is in the PE server routes
- `test_pe_ingest_maps_hk_types_to_sensors` — POST sample payload, assert sensors written
- `test_pe_ingest_triggers_push` — confirm `/api/push` called after sensor writes
- `test_pe_integration_config_loaded_from_env` — `INTEGRATIONS_CONFIG` env var path
- `test_carekit_adherence_ratio_boundary_values` — 0 doses taken, partial, full
- `test_band_normalizer_thresholds_match_python_constants` — parity check against `integrations.healthkit-localai.json`

Live stack (`tests/e2e/test_health_pipeline.py`, new `@live` tests):
- `test_pe_healthkit_ingest_endpoint_accepts_hr_sample`
- `test_pe_ingest_full_payload_fires_correct_re_state`
- `test_carekit_push_updates_carekit_sensor_region`

---

**Phase 4 effort breakdown:**

| Task | Subtask | Estimated days |
|---|---|---|
| 4a CareKit machine | JSON + Python sensors + drift guard + tests | 1.5 |
| 4b Session carry | Machine JSON + bridge extension + carry read + tests | 1.0 |
| 4c PE ingest endpoint | TypeScript server.ts addition + config loading + tests | 1.5 |
| 4c Swift module | Package scaffold + HealthKitManager + BandNormalizer + bridge | 3.0 |
| 4c CareKit sync | CareKitSync.swift + OCKStore queries + device testing | 2.5 |
| 4c CI parity check | JSON threshold vs Swift constant check | 0.5 |
| **Total** | | **~10 days** |

**Sequencing:** 4a and 4b can be done in parallel (no dependencies between them). The PE ingest endpoint (4c step 1) should follow 4a and 4b since it needs the full sensor registry. The Swift module (4c steps 2–4) can start in parallel with 4a/4b but depends on the PE endpoint being reachable for device testing.

---

## Test coverage matrix

| Test file | Type | Count | Flag | Network |
|---|---|---|---|---|
| `tests/test_reality_bridge.py` | unit (fake client) | 36 | (default) | no |
| `tests/test_health_integration.py` | health unit (fake client) | 22 | (default) | no |
| `tests/test_phase2.py` | Phase 2 unit | 36 | (default) | no |
| `tests/test_phase4.py` | Phase 4 unit — CareKit + carry | ~20 | (default) | no |
| `tests/e2e/test_api_integration.py` | compose integration | 15 | `--integration` | API only |
| `tests/e2e/test_health_pipeline.py` | full live stack | 13+3 | `--live` | PE+RE+API |
| `localHealthkitBridge/Tests/` | Swift unit — normalizer + HTTP mock | ~12 | `swift test` | no |

**Total tests (after Phase 4):** ~157 (114 Python unit + 18 Python e2e + 12 Swift unit + 13 Python live)

**Run unit tests (default):**
```bash
cd services/api
python -m pytest -v                    # 85 tests, no services needed
```

**Run compose integration tests:**
```bash
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d qdrant redis api
python -m pytest services/api/tests/e2e/test_api_integration.py --integration -v
```

**Run live stack tests:**
```bash
# Ensure PE (port 3004), RE (port 3000), localAI (port 4000) are running
python -m pytest services/api/tests/e2e/test_health_pipeline.py --live -v
```

**Run the simulate script (PE+RE required):**
```bash
python scripts/simulate_health_push.py --scenario cycle
```

---

## Grafana / Loki queries for health domain

```logql
# All health bridge events
{app="localaistack", service="api"} |~ "health_state_read|health_machine|health_sensor"

# GraphQL triggers from health machine
{app="localaistack", service="api"} |~ "personal_health_baseline"

# Health state distribution over last hour
{app="localaistack", service="api"} | json | state =~ "thriving|balanced|watch|attention"
```
