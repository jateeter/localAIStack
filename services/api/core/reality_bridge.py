"""
Perception Engine + Reality Engine bridge for localAIStack.

Startup responsibilities (called once from main.py lifespan):
  verify_machine_offsets()   — assert machine JSON perceptualMapping offsets
                               match the Python constants in this module; logs
                               a loud error on drift (does not block startup)
  register_sensors()         — create the two RAG signal sensors in the PE
  import_machine_if_missing()— import rag_corrective_cycle into the RE
  import_session_machines()  — import session_rag_context,
                               session_agent_context, and ai_load_bridge
                               bistable/projection machines
  bind_graph_topology()      — read /graph/schema, auto-assign perceptual space
                               regions, register node-activity sensors, and
                               import topology-tracking machines for each graph

Per-request responsibilities (called from graph node functions):
  push_retrieval_signal()      — write retrieve() output to PE sensor [52:56]
  push_grading_signal()        — write grade_documents() output, trigger RE push,
                                 return "generate"|"rewrite"|"abort" decision
  push_agent_activity_signal() — write tool-call / error / reasoning metrics to
                                 PE sensor [64:68], trigger RE push, return the
                                 enriched session context dict
  push_node_signal()           — write a node-activity signal to its sensor region
  get_session_context()        — read current carry state from perceptual space
                                 after the most recent push (includes agent
                                 activity classification and AI load tier)

All network calls use short timeouts and suppress exceptions; the bridge is
always optional — a missing or slow PE/RE never blocks graph execution.

Perceptual space layout (256-element vector):
  [52:56]   localai_rag_retrieval       — doc_count_norm, avg_score
  [56:60]   localai_rag_grading         — kept_ratio, rewrite_count_norm
  [60:64]   rag_corrective_cycle        — [generate, rewrite, abort, _] output
  [64:68]   localai_agent_activity      — [tool_calls_norm, tool_errors_norm,
                                           reasoning_depth_norm, _] sensor
  [68:72]   agent_activity_classifier   — [productive, normal, struggling, _] output
  [76:84]   rag topology nodes          — 4 nodes × 2 bytes (see topology_builder)
  [84:88]   rag topology output         — [retrieve, grade_documents, generate, rewrite_query]
  [104:108] agent topology nodes        — 2 nodes × 2 bytes
  [108:112] agent topology output       — [agent, tools, 0, 0]
  [112:116] session_rag_context         — bistable carry: [last_generate, last_rewrite, last_abort, _]
  [116:120] session_agent_context       — bistable carry: [agent_ever_engaged, tools_ever_used, _, _]
  [120:144] ai_load_bridge              — 6 × 4-byte AI machine input patterns projected
                                          from session carries (nominal/elevated/critical)
  (topology offsets computed dynamically by topology_builder.compute_bindings())

  Chunks A [52:64] and B [104:120] are carved from space freed by relocating
  four terminal DC flip-flop outputs to [144:150]; see topology_builder
  docstring and the DC machine JSONs for details.
"""

import json
import os
import pathlib
import httpx
import structlog

from config import get_settings

log = structlog.get_logger()

# SSL verification for RE/PE calls.  Set RE_SSL_VERIFY=false in docker-compose
# when the stack uses a self-signed certificate (the default dev setup).
_SSL_VERIFY: bool | str = os.getenv("RE_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

# ── localAI sensor definitions ────────────────────────────────────────────────
# Three PE sensors, one per observable stream the bridge drives:
#   - rag retrieval  (doc_count_norm, avg_score)        → [52:56]
#   - rag grading    (kept_ratio,    rewrite_count_norm) → [56:60]
#   - agent activity (tool_calls,    tool_errors,   reasoning_depth) → [64:68]
# All three are "sensor" sources, so writes bypass auto-assembly ordering and
# land directly in the named region.

_RAG_SENSORS = [
    {
        "sensorId": "localai_rag_retrieval",
        "name": "localai/rag_retrieval",
        "region": {"offset": 52, "length": 4},
        "ttlMs": 30_000,
    },
    {
        "sensorId": "localai_rag_grading",
        "name": "localai/rag_grading",
        "region": {"offset": 56, "length": 4},
        "ttlMs": 30_000,
    },
    {
        "sensorId": "localai_agent_activity",
        "name": "localai/agent_activity",
        "region": {"offset": 64, "length": 4},
        "ttlMs": 30_000,
    },
]

# MACHINES_DIR is set explicitly in docker-compose.yml to avoid relying on
# __file__ path arithmetic that breaks when the service/ subtree is mounted
# at /app (4 parents from /app/core/ overshoots to filesystem root).
_MACHINES_DIR = pathlib.Path(
    os.getenv(
        "MACHINES_DIR",
        str(pathlib.Path(__file__).parent.parent.parent.parent / "data" / "machines"),
    )
)
_MACHINE_JSON_PATH = _MACHINES_DIR / "rag_corrective_cycle.json"
_MACHINE_NAME = "localai/rag_corrective_cycle"

_OUTPUT_GENERATE = 60
_OUTPUT_REWRITE  = 61
_OUTPUT_ABORT    = 62

# agent_activity_classifier output, one-hot at [68:72]
_AGENT_ACT_PRODUCTIVE = 68
_AGENT_ACT_NORMAL     = 69
_AGENT_ACT_STRUGGLING = 70

# ai_load_bridge writes the same 4-byte tier vector six times starting at 120.
# A single 4-byte probe at the first window is enough to decode the tier.
_AI_LOAD_TIER_OFFSET = 120

# ── Session context carry machine definitions ─────────────────────────────────

_SESSION_MACHINE_DEFS = [
    {"path": _MACHINES_DIR / "session_rag_context.json",        "name": "localai/session_rag_context"},
    {"path": _MACHINES_DIR / "session_agent_context.json",      "name": "localai/session_agent_context"},
    {"path": _MACHINES_DIR / "ai_load_bridge.json",             "name": "localai/ai_load_bridge"},
    {"path": _MACHINES_DIR / "agent_activity_classifier.json",  "name": "localai/agent_activity_classifier"},
]

# Perceptual space indices for session context carry read-back
_SESSION_RAG_OFFSET   = 112  # [last_generate, last_rewrite, last_abort, _]
_SESSION_AGENT_OFFSET = 116  # [agent_ever_engaged, tools_ever_used, _, _]

_SENSOR_TIMEOUT = httpx.Timeout(1.0)
_PUSH_TIMEOUT   = httpx.Timeout(2.0)

# ── Offset-drift guard ────────────────────────────────────────────────────────
# Expected perceptualMapping offsets for every machine JSON this bridge owns.
# Keeping a single authoritative table here means offset changes must be mirrored
# in exactly one place; verify_machine_offsets() checks the JSON files agree.
_EXPECTED_MACHINE_OFFSETS = [
    {
        "path":   _MACHINE_JSON_PATH,  # rag_corrective_cycle.json
        "input":  {"offset": 52,  "length": 8},
        "output": {"offset": 60,  "length": 4},
    },
    {
        "path":   _MACHINES_DIR / "session_rag_context.json",
        "input":  {"offset": 60,  "length": 4},
        "output": {"offset": 112, "length": 4},
    },
    {
        "path":   _MACHINES_DIR / "session_agent_context.json",
        "input":  {"offset": 104, "length": 16},
        "output": {"offset": 116, "length": 4},
    },
    {
        "path":   _MACHINES_DIR / "ai_load_bridge.json",
        "input":  {"offset": 112, "length": 8},
        "output": {"offset": 120, "length": 24},
    },
    {
        "path":   _MACHINES_DIR / "agent_activity_classifier.json",
        "input":  {"offset": 64,  "length": 4},
        "output": {"offset": 68,  "length": 4},
    },
]

# Maps sensorId → the machine whose input window that sensor must live inside.
# The drift guard uses this to confirm sensors and their consumer machines stay
# wired together after any offset move.
_SENSOR_TO_MACHINE = {
    "localai_rag_retrieval":  "rag_corrective_cycle.json",
    "localai_rag_grading":    "rag_corrective_cycle.json",
    "localai_agent_activity": "agent_activity_classifier.json",
}

# ── Topology bindings (populated by bind_graph_topology at startup) ───────────

# {graph_name: {nodes: {node_name: {sensor_id, offset, length}}, ...}}
_TOPOLOGY_BINDINGS: dict = {}


def get_topology_bindings() -> dict:
    """Return the computed topology bindings (for schema introspection)."""
    return _TOPOLOGY_BINDINGS


# ── URL helpers ───────────────────────────────────────────────────────────────

def _pe_url() -> str:
    return get_settings().pe_url


def _re_url() -> str:
    return get_settings().re_url


# ── Startup: offset-drift guard ──────────────────────────────────────────────

def verify_machine_offsets() -> list[str]:
    """
    Assert that every machine JSON's perceptualMapping matches the offsets this
    module expects, and that the Python offset constants align with the JSON
    output regions they read back. Returns the list of mismatch strings (empty
    when everything agrees). On any mismatch emits a single structured error
    log — startup is NOT blocked so the bridge remains optional, but the error
    is loud enough that CI and operators will notice immediately.

    Rationale: all of the silent-bad-output bugs we've hit in this module were
    caused by a machine JSON's offset drifting away from a Python constant
    (or vice versa). One structural check catches that entire class.
    """
    mismatches: list[str] = []

    for spec in _EXPECTED_MACHINE_OFFSETS:
        path = spec["path"]
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            mismatches.append(f"{path.name}: read failed: {exc}")
            continue
        pm = data.get("machine", {}).get("perceptualMapping") or {}
        for side in ("input", "output"):
            actual   = pm.get(side) or {}
            expected = spec[side]
            if (actual.get("offset") != expected["offset"]
                    or actual.get("length") != expected["length"]):
                mismatches.append(
                    f"{path.name}.{side}: expected "
                    f"offset={expected['offset']} length={expected['length']}; "
                    f"got offset={actual.get('offset')} length={actual.get('length')}"
                )

    # Each sensor must land inside the input window of the machine that consumes it.
    machines_by_filename = {spec["path"].name: spec for spec in _EXPECTED_MACHINE_OFFSETS}
    for sensor in _RAG_SENSORS:
        sid   = sensor["sensorId"]
        fname = _SENSOR_TO_MACHINE.get(sid)
        if fname is None:
            mismatches.append(f"sensor {sid}: no consumer machine mapped in _SENSOR_TO_MACHINE")
            continue
        spec = machines_by_filename.get(fname)
        if spec is None:
            mismatches.append(
                f"sensor {sid}: _SENSOR_TO_MACHINE points at {fname} "
                f"but no such machine in _EXPECTED_MACHINE_OFFSETS"
            )
            continue
        sr      = sensor["region"]
        m_start = spec["input"]["offset"]
        m_end   = m_start + spec["input"]["length"]
        if sr["offset"] < m_start or sr["offset"] + sr["length"] > m_end:
            mismatches.append(
                f"sensor {sid}: region {sr} outside {fname} input "
                f"[{m_start}:{m_end}]"
            )

    # Python readback constants must match the rag_corrective_cycle output region
    if (_OUTPUT_GENERATE, _OUTPUT_REWRITE, _OUTPUT_ABORT) != (60, 61, 62):
        mismatches.append(
            f"_OUTPUT_(GENERATE,REWRITE,ABORT)="
            f"({_OUTPUT_GENERATE},{_OUTPUT_REWRITE},{_OUTPUT_ABORT}) "
            f"does not match rag_corrective_cycle output [60:64]"
        )
    if _SESSION_RAG_OFFSET != 112:
        mismatches.append(
            f"_SESSION_RAG_OFFSET={_SESSION_RAG_OFFSET} "
            f"!= 112 (session_rag_context output offset)"
        )
    if _SESSION_AGENT_OFFSET != 116:
        mismatches.append(
            f"_SESSION_AGENT_OFFSET={_SESSION_AGENT_OFFSET} "
            f"!= 116 (session_agent_context output offset)"
        )

    if mismatches:
        log.error("reality_bridge.offset_drift_detected",
                  count=len(mismatches),
                  mismatches=mismatches,
                  note="machine JSONs are out of sync with Python constants — "
                       "RE routing will be silently incorrect")
    else:
        log.info("reality_bridge.offset_verification_ok",
                 machines=[spec["path"].name for spec in _EXPECTED_MACHINE_OFFSETS])

    return mismatches


# ── Startup: RAG sensors + corrective-cycle machine ──────────────────────────

def register_sensors() -> None:
    """Create the two RAG sensor sources in the PE; skips existing sensorIds."""
    try:
        with httpx.Client(timeout=_SENSOR_TIMEOUT, verify=_SSL_VERIFY) as client:
            existing_ids = _get_existing_sensor_ids(client)
            for sensor in _RAG_SENSORS:
                sid = sensor["sensorId"]
                if sid in existing_ids:
                    log.info("reality_bridge.sensor_exists", sensor_id=sid)
                    continue
                payload = {
                    "type": "sensor",
                    "name": sensor["name"],
                    "region": sensor["region"],
                    "active": True,
                    "sensorId": sid,
                    "lastValue": [],
                    "lastUpdated": None,
                    "ttlMs": sensor["ttlMs"],
                }
                r = client.post(f"{_pe_url()}/api/sources", json=payload)
                r.raise_for_status()
                log.info("reality_bridge.sensor_registered",
                         sensor_id=sid, region=sensor["region"])
    except Exception as exc:
        log.warning("reality_bridge.register_failed",
                    error=str(exc), pe_url=_pe_url(),
                    note="RAG pipeline runs normally without RE telemetry")


def import_machine_if_missing() -> None:
    """Import the rag_corrective_cycle machine into the RE if not already loaded."""
    try:
        machine_json = json.loads(_MACHINE_JSON_PATH.read_text())
    except Exception as exc:
        log.warning("reality_bridge.machine_json_not_found",
                    path=str(_MACHINE_JSON_PATH), error=str(exc))
        return

    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT, verify=_SSL_VERIFY) as client:
            existing = _get_existing_machine_names(client)
            if _MACHINE_NAME in existing:
                log.info("reality_bridge.machine_exists", name=_MACHINE_NAME)
                return
            r = client.post(f"{_re_url()}/api/machines", json=machine_json)
            r.raise_for_status()
            machine_id = r.json().get("machine", {}).get("id", "unknown")
            log.info("reality_bridge.machine_imported",
                     name=_MACHINE_NAME, machine_id=machine_id)
    except Exception as exc:
        log.warning("reality_bridge.machine_import_failed",
                    error=str(exc), re_url=_re_url())


def import_session_machines() -> None:
    """
    Import the localAI session-side machines into the RE if not already loaded:
      - session_rag_context   (bistable carry: last RAG routing decision)
      - session_agent_context (bistable carry: agent engagement flags)
      - ai_load_bridge        (projects session carries to AI machine inputs
                               at [120:144], fanning one of three PUE-tier
                               patterns across all six AI machine input windows)
    """
    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT, verify=_SSL_VERIFY) as client:
            existing = _get_existing_machine_names(client)
            for defn in _SESSION_MACHINE_DEFS:
                name = defn["name"]
                if name in existing:
                    log.info("reality_bridge.session_machine_exists", name=name)
                    continue
                try:
                    machine_json = json.loads(defn["path"].read_text())
                except Exception as exc:
                    log.warning("reality_bridge.session_machine_json_not_found",
                                path=str(defn["path"]), error=str(exc))
                    continue
                r = client.post(f"{_re_url()}/api/machines", json=machine_json)
                r.raise_for_status()
                machine_id = r.json().get("machine", {}).get("id", "unknown")
                log.info("reality_bridge.session_machine_imported",
                         name=name, machine_id=machine_id)
    except Exception as exc:
        log.warning("reality_bridge.session_machine_import_failed",
                    error=str(exc), re_url=_re_url())


# ── Per-request: session context read-back ───────────────────────────────────

def get_session_context(ps: list) -> dict:
    """
    Extract session carry state from a perceptual space vector returned by
    a /api/push response.  Safe to call with a short or empty ps list.

    Returns a dict with:
      rag            — "generate" | "rewrite" | "abort" | None
      agent          — {"ever_engaged": bool, "tools_ever_used": bool}
      agent_activity — "productive" | "normal" | "struggling" | None
                       (output of agent_activity_classifier at [68:72])
      ai_load_tier   — "nominal" | "elevated" | "critical" | None
                       (decoded from the ai_load_bridge projection at [120:124])
    """
    def _safe(idx: int) -> float:
        return ps[idx] if len(ps) > idx else 0.0

    gen   = _safe(_SESSION_RAG_OFFSET)
    rew   = _safe(_SESSION_RAG_OFFSET + 1)
    abt   = _safe(_SESSION_RAG_OFFSET + 2)

    if gen >= 0.5:
        last_rag = "generate"
    elif rew >= 0.5:
        last_rag = "rewrite"
    elif abt >= 0.5:
        last_rag = "abort"
    else:
        last_rag = None

    return {
        "rag": last_rag,
        "agent": {
            "ever_engaged":   _safe(_SESSION_AGENT_OFFSET)     >= 0.5,
            "tools_ever_used": _safe(_SESSION_AGENT_OFFSET + 1) >= 0.5,
        },
        "agent_activity": _decode_agent_activity(ps),
        "ai_load_tier":   get_ai_load_tier(ps),
    }


def _decode_agent_activity(ps: list) -> str | None:
    """One-hot decode of agent_activity_classifier output at [68:72]."""
    def _safe(idx: int) -> float:
        return ps[idx] if len(ps) > idx else 0.0
    prod   = _safe(_AGENT_ACT_PRODUCTIVE)
    normal = _safe(_AGENT_ACT_NORMAL)
    strug  = _safe(_AGENT_ACT_STRUGGLING)
    if prod >= 0.5:
        return "productive"
    if strug >= 0.5:
        return "struggling"
    if normal >= 0.5:
        return "normal"
    return None


def get_ai_load_tier(ps: list) -> str | None:
    """
    Decode which tier ai_load_bridge projected this push by inspecting the
    first 4-byte window it writes (all six windows are identical). Patterns:
      nominal  → [0.15, 0.30, 0.20, 0.10]
      elevated → [0.62, 0.65, 0.58, 0.60]
      critical → [0.92, 0.95, 0.88, 0.91]
    Uses the first element as a cheap discriminator with tolerant thresholds,
    since PE quantization and arbiter composition can shift the exact value
    slightly on replay. Returns None when ai_load_bridge has not yet written
    (e.g. before the first /api/push or when all session carries are cold).
    """
    if len(ps) <= _AI_LOAD_TIER_OFFSET:
        return None
    v0 = ps[_AI_LOAD_TIER_OFFSET]
    if v0 >= 0.80:
        return "critical"
    if v0 >= 0.45:
        return "elevated"
    if v0 >= 0.10:
        return "nominal"
    return None


# ── Startup: graph topology binding ──────────────────────────────────────────

def bind_graph_topology() -> None:
    """
    Read the node lists from the compiled LangGraph graphs, compute
    perceptual space region assignments, register PE sensors for each node,
    and import topology-tracking CES machines into the RE.

    Populates _TOPOLOGY_BINDINGS so push_node_signal() works at request time.
    """
    global _TOPOLOGY_BINDINGS

    try:
        from core.topology_builder import compute_bindings, build_machine_json
        bindings = compute_bindings()
    except Exception as exc:
        log.warning("reality_bridge.topology_bindings_failed", error=str(exc))
        return

    _TOPOLOGY_BINDINGS = bindings

    # Register PE sensors for every node in every graph
    try:
        with httpx.Client(timeout=_SENSOR_TIMEOUT, verify=_SSL_VERIFY) as pe_client:
            existing_ids = _get_existing_sensor_ids(pe_client)
            for graph_name, graph_binding in bindings.items():
                for node, node_info in graph_binding["nodes"].items():
                    sid = node_info["sensor_id"]
                    if sid in existing_ids:
                        log.info("reality_bridge.topo_sensor_exists",
                                 sensor_id=sid)
                        continue
                    payload = {
                        "type": "sensor",
                        "name": node_info["pe_name"],
                        "region": {
                            "offset": node_info["offset"],
                            "length": node_info["length"],
                        },
                        "active": True,
                        "sensorId": sid,
                        "lastValue": [],
                        "lastUpdated": None,
                        "ttlMs": 10_000,   # short TTL — nodes complete in seconds
                    }
                    r = pe_client.post(f"{_pe_url()}/api/sources", json=payload)
                    r.raise_for_status()
                    log.info("reality_bridge.topo_sensor_registered",
                             sensor_id=sid,
                             offset=node_info["offset"])
    except Exception as exc:
        log.warning("reality_bridge.topo_sensor_registration_failed",
                    error=str(exc))

    # Import topology machines into the RE
    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT, verify=_SSL_VERIFY) as re_client:
            existing_machine_names = _get_existing_machine_names(re_client)
            for graph_name, graph_binding in bindings.items():
                machine_name = f"localai/{graph_name}_topology"
                if machine_name in existing_machine_names:
                    log.info("reality_bridge.topo_machine_exists",
                             name=machine_name)
                    continue
                from core.topology_builder import build_machine_json
                machine_json = build_machine_json(graph_name, graph_binding)
                r = re_client.post(f"{_re_url()}/api/machines", json=machine_json)
                r.raise_for_status()
                machine_id = r.json().get("machine", {}).get("id", "unknown")
                log.info("reality_bridge.topo_machine_imported",
                         name=machine_name, machine_id=machine_id,
                         nodes=graph_binding["node_order"],
                         input_region=graph_binding["input_region"],
                         output_region=graph_binding["output_region"])
    except Exception as exc:
        log.warning("reality_bridge.topo_machine_import_failed", error=str(exc))

    log.info("reality_bridge.topology_bound",
             graphs=list(bindings.keys()),
             total_sensors=sum(len(b["nodes"]) for b in bindings.values()))


# ── Per-request: RAG signal writes ───────────────────────────────────────────

def push_retrieval_signal(doc_count: int, avg_score: float) -> None:
    """
    Write retrieval outcome to PE sensor region [52:56] after retrieve() runs.
      [0] doc_count normalized   min(count / 10, 1.0)
      [1] avg_relevance_score    clamped to [0, 1]
      [2–3] reserved 0.0
    """
    values = [
        min(doc_count / 10.0, 1.0),
        float(max(0.0, min(1.0, avg_score))),
        0.0,
        0.0,
    ]
    _write_sensor("localai_rag_retrieval", values)


def push_grading_signal(
    retrieved_count: int,
    kept_count: int,
    rewrite_count: int,
) -> str:
    """
    Write grading outcome to PE sensor region [56:60], trigger a PE push so
    the rag_corrective_cycle machine processes the assembled vector, then read
    the machine's routing decision from perceptualSpace[60:64].

      [0] kept / retrieved ratio   [0, 1]
      [1] rewrite_count / 2        [0, 1]  (2 rewrites = max)
      [2–3] reserved 0.0

    Returns: "generate" | "rewrite" | "abort"
    Falls back to "rewrite" when the PE/RE is unreachable.
    """
    values = [
        kept_count / max(retrieved_count, 1),
        min(rewrite_count / 2.0, 1.0),
        0.0,
        0.0,
    ]
    _write_sensor("localai_rag_grading", values)
    return _trigger_push_and_read_routing()


def push_agent_activity_signal(
    tool_calls:      int,
    tool_errors:     int,
    reasoning_steps: int,
) -> dict:
    """
    Agent-side analog of push_grading_signal. Writes an activity vector to the
    localai_agent_activity sensor at [64:68], triggers a PE push so the
    agent_activity_classifier can fire on the same cycle, then reads back the
    full session context from perceptualSpace.

      [0] tool_calls_norm       min(tool_calls / 5, 1.0)
      [1] tool_errors_norm      min(tool_errors / 3, 1.0)
      [2] reasoning_depth_norm  min(reasoning_steps / 10, 1.0)
      [3] reserved 0.0

    Returns the session-context dict as produced by get_session_context() —
    including the freshly-written agent_activity classification. Returns a
    dict of all-None values when the PE/RE is unreachable (graceful degrade).
    """
    values = [
        min(tool_calls      / 5.0,  1.0),
        min(tool_errors     / 3.0,  1.0),
        min(reasoning_steps / 10.0, 1.0),
        0.0,
    ]
    _write_sensor("localai_agent_activity", values)
    return _trigger_push_and_read_session()


# ── Per-request: node activity signals ───────────────────────────────────────

def push_node_signal(
    graph_name: str,
    node_name: str,
    value: float = 1.0,
    trigger_push: bool = True,
) -> None:
    """
    Write a node-activity signal to the PE sensor assigned to this node.

    Call with value=1.0 at the start of a node and value=0.0 at the end.
    When trigger_push=True (default for node-start), also triggers a PE push
    so the topology machine in the RE sees the update in near-real-time.

    Silently no-ops if _TOPOLOGY_BINDINGS is not yet populated (bridge not ready).
    """
    node_info = _TOPOLOGY_BINDINGS.get(graph_name, {}).get("nodes", {}).get(node_name)
    if not node_info:
        return
    _write_sensor(node_info["sensor_id"], [value, 0.0])
    if trigger_push:
        _trigger_push_fire_and_forget()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_existing_sensor_ids(client: httpx.Client) -> set:
    try:
        resp = client.get(f"{_pe_url()}/api/sources")
        resp.raise_for_status()
        return {
            s.get("sensorId")
            for s in resp.json().get("sources", [])
            if s.get("type") == "sensor"
        }
    except Exception:
        return set()


def _get_existing_machine_names(client: httpx.Client) -> set:
    try:
        resp = client.get(f"{_re_url()}/api/machines")
        resp.raise_for_status()
        return {m.get("name") for m in resp.json().get("machines", [])}
    except Exception:
        return set()


def _write_sensor(sensor_id: str, values: list[float]) -> None:
    try:
        with httpx.Client(timeout=_SENSOR_TIMEOUT, verify=_SSL_VERIFY) as client:
            r = client.post(
                f"{_pe_url()}/api/sensors/{sensor_id}",
                json={"values": values},
            )
            if r.status_code == 404:
                log.warning("reality_bridge.sensor_not_found",
                            sensor_id=sensor_id)
                return
            r.raise_for_status()
            log.debug("reality_bridge.sensor_written",
                      sensor_id=sensor_id, values=values)
    except Exception as exc:
        log.debug("reality_bridge.write_skipped",
                  sensor_id=sensor_id, error=str(exc))


def _trigger_push_and_read_routing() -> str:
    """
    POST /api/push → PE assembles vector → RE runs machines → return routing.
    Reads perceptualSpace[60:63] to decode generate/rewrite/abort.
    """
    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT, verify=_SSL_VERIFY) as client:
            r = client.post(f"{_pe_url()}/api/push")
            r.raise_for_status()
            data = r.json()
            ps   = data.get("step", {}).get("perceptualSpace", [])

            if len(ps) > _OUTPUT_ABORT:
                generate = ps[_OUTPUT_GENERATE]
                rewrite  = ps[_OUTPUT_REWRITE]
                abort    = ps[_OUTPUT_ABORT]
                session  = get_session_context(ps)
                log.info("reality_bridge.routing_read",
                         generate=round(generate, 3),
                         rewrite=round(rewrite,  3),
                         abort=round(abort,    3),
                         session_rag=session["rag"],
                         session_agent=session["agent"],
                         agent_activity=session["agent_activity"],
                         ai_load_tier=session["ai_load_tier"],
                         global_step=data.get("globalStep"))
                if generate >= 0.5:
                    return "generate"
                if abort >= 0.5:
                    return "abort"
                return "rewrite"

            log.warning("reality_bridge.perceptual_space_short",
                        length=len(ps), needed=_OUTPUT_ABORT + 1)
    except Exception as exc:
        log.debug("reality_bridge.push_skipped", error=str(exc))

    return "rewrite"


def _trigger_push_and_read_session() -> dict:
    """
    POST /api/push then return the full session context dict. Used by
    push_agent_activity_signal — the caller wants classification + load tier,
    not a single routing decision. Degrades to all-None on bridge failure.
    """
    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT, verify=_SSL_VERIFY) as client:
            r = client.post(f"{_pe_url()}/api/push")
            r.raise_for_status()
            data = r.json()
            ps   = data.get("step", {}).get("perceptualSpace", [])
            session = get_session_context(ps)
            log.info("reality_bridge.agent_activity_read",
                     agent_activity=session["agent_activity"],
                     ai_load_tier=session["ai_load_tier"],
                     session_rag=session["rag"],
                     global_step=data.get("globalStep"))
            return session
    except Exception as exc:
        log.debug("reality_bridge.agent_push_skipped", error=str(exc))
    return {
        "rag": None,
        "agent": {"ever_engaged": False, "tools_ever_used": False},
        "agent_activity": None,
        "ai_load_tier":   None,
    }


def _trigger_push_fire_and_forget() -> None:
    """Trigger a PE push without reading the result — used for node signals."""
    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT, verify=_SSL_VERIFY) as client:
            r = client.post(f"{_pe_url()}/api/push")
            r.raise_for_status()
            log.debug("reality_bridge.node_push_ok",
                      global_step=r.json().get("globalStep"))
    except Exception as exc:
        log.debug("reality_bridge.node_push_skipped", error=str(exc))
