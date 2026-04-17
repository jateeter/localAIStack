"""
Perception Engine + Reality Engine bridge for localAIStack.

Startup responsibilities (called once from main.py lifespan):
  register_sensors()         — create the two RAG signal sensors in the PE
  import_machine_if_missing()— import rag_corrective_cycle into the RE
  import_session_machines()  — import session_rag_context and
                               session_agent_context bistable machines
  bind_graph_topology()      — read /graph/schema, auto-assign perceptual space
                               regions, register node-activity sensors, and
                               import topology-tracking machines for each graph

Per-request responsibilities (called from graph node functions):
  push_retrieval_signal()    — write retrieve() output to PE sensor [64:68]
  push_grading_signal()      — write grade_documents() output, trigger RE push,
                               return "generate"|"rewrite"|"abort" decision
  push_node_signal()         — write a node-activity signal to its sensor region
  get_session_context()      — read current carry state from perceptual space
                               after the most recent push

All network calls use short timeouts and suppress exceptions; the bridge is
always optional — a missing or slow PE/RE never blocks graph execution.

Perceptual space layout (256-element vector):
  [64:68]  localai_rag_retrieval   — doc_count_norm, avg_score
  [68:72]  localai_rag_grading     — kept_ratio, rewrite_count_norm
  [72:76]  rag_corrective_cycle    — [generate, rewrite, abort, _] output
  [76:88]  rag topology nodes      — 4 nodes × 2 bytes (see topology_builder)
  [84:88]  rag topology output     — [retrieve, grade_documents, generate, rewrite_query]
  [88:92]  agent topology nodes    — 2 nodes × 2 bytes
  [92:96]  agent topology output   — [agent, tools, 0, 0]
  [96:100] session_rag_context     — bistable carry: [last_generate, last_rewrite, last_abort, _]
  [100:104] session_agent_context  — bistable carry: [agent_ever_engaged, tools_ever_used, _, _]
  (topology offsets computed dynamically by topology_builder.compute_bindings())
"""

import json
import pathlib
import httpx
import structlog

from config import get_settings

log = structlog.get_logger()

# ── RAG sensor definitions ────────────────────────────────────────────────────

_RAG_SENSORS = [
    {
        "sensorId": "localai_rag_retrieval",
        "name": "localai/rag_retrieval",
        "region": {"offset": 64, "length": 4},
        "ttlMs": 30_000,
    },
    {
        "sensorId": "localai_rag_grading",
        "name": "localai/rag_grading",
        "region": {"offset": 68, "length": 4},
        "ttlMs": 30_000,
    },
]

_MACHINE_JSON_PATH = (
    pathlib.Path(__file__).parent.parent.parent.parent
    / "data" / "machines" / "rag_corrective_cycle.json"
)
_MACHINE_NAME = "localai/rag_corrective_cycle"

_OUTPUT_GENERATE = 72
_OUTPUT_REWRITE  = 73
_OUTPUT_ABORT    = 74

# ── Session context carry machine definitions ─────────────────────────────────

_MACHINES_DIR = (
    pathlib.Path(__file__).parent.parent.parent.parent / "data" / "machines"
)

_SESSION_MACHINE_DEFS = [
    {
        "path": _MACHINES_DIR / "session_rag_context.json",
        "name": "localai/session_rag_context",
    },
    {
        "path": _MACHINES_DIR / "session_agent_context.json",
        "name": "localai/session_agent_context",
    },
]

# Perceptual space indices for session context carry read-back
_SESSION_RAG_OFFSET   = 96   # [last_generate, last_rewrite, last_abort, _]
_SESSION_AGENT_OFFSET = 100  # [agent_ever_engaged, tools_ever_used, _, _]

_SENSOR_TIMEOUT = httpx.Timeout(1.0)
_PUSH_TIMEOUT   = httpx.Timeout(2.0)

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


# ── Startup: RAG sensors + corrective-cycle machine ──────────────────────────

def register_sensors() -> None:
    """Create the two RAG sensor sources in the PE; skips existing sensorIds."""
    try:
        with httpx.Client(timeout=_SENSOR_TIMEOUT) as client:
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
        with httpx.Client(timeout=_PUSH_TIMEOUT) as client:
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
    Import session_rag_context and session_agent_context bistable machines
    into the RE if they are not already loaded.
    """
    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT) as client:
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
      rag   — "generate" | "rewrite" | "abort" | None
      agent — {"ever_engaged": bool, "tools_ever_used": bool}
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
    }


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
        with httpx.Client(timeout=_SENSOR_TIMEOUT) as pe_client:
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
        with httpx.Client(timeout=_PUSH_TIMEOUT) as re_client:
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
    Write retrieval outcome to PE sensor region [64:68] after retrieve() runs.
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
    Write grading outcome to PE sensor region [68:72], trigger a PE push so
    the rag_corrective_cycle machine processes the assembled vector, then read
    the machine's routing decision from perceptualSpace[72:76].

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
        with httpx.Client(timeout=_SENSOR_TIMEOUT) as client:
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
    Reads perceptualSpace[72:74] to decode generate/rewrite/abort.
    """
    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT) as client:
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


def _trigger_push_fire_and_forget() -> None:
    """Trigger a PE push without reading the result — used for node signals."""
    try:
        with httpx.Client(timeout=_PUSH_TIMEOUT) as client:
            r = client.post(f"{_pe_url()}/api/push")
            r.raise_for_status()
            log.debug("reality_bridge.node_push_ok",
                      global_step=r.json().get("globalStep"))
    except Exception as exc:
        log.debug("reality_bridge.node_push_skipped", error=str(exc))
