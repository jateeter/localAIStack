"""
LangGraph → Reality Engine topology builder.

Reads the compiled node list from each LangGraph graph and produces:
  1. A binding dict  — perceptual space region assignments per node
  2. A CES machine JSON — one topology-tracking machine per graph

Region layout (per-graph base offsets in GRAPH_BASE_OFFSETS):
  Each node gets BYTES_PER_NODE bytes (default 2):
    [offset + 0]  node_active signal  (1.0 while node is executing)
    [offset + 1]  reserved

  Each graph's input region is immediately followed by its OUTPUT_LENGTH-byte
  output region where the topology machine writes which node is currently active.

Layout for default graphs (rag=4 nodes, agent=2 nodes):
  [76:84]   rag node signals     (4 nodes × 2 bytes)
  [84:88]   rag topology output  (4 bytes: [retrieve, grade_documents, generate, rewrite_query])
  [104:108] agent node signals   (2 nodes × 2 bytes)
  [108:112] agent topology output (4 bytes: [agent, tools, 0, 0])

The gap between the rag region (ends at 88) and agent region (starts at 104) is
required because DC machines occupy several bytes in [88:104]; the agent region
sits immediately before the session_rag/session_agent outputs at [112:120] so
session_agent_context can read its 16-byte input contiguously from [104:120].
"""

from __future__ import annotations

# Per-graph base offsets.  rag stays at 76 (no conflicts), agent starts at 104
# where DC alert FF outputs used to live (now relocated to [144:150]).
GRAPH_BASE_OFFSETS: dict[str, int] = {
    "rag": 76,
    "agent": 104,
}
# Legacy constant kept for any external consumers that still reference it;
# equivalent to GRAPH_BASE_OFFSETS["rag"].
TOPOLOGY_BASE_OFFSET = GRAPH_BASE_OFFSETS["rag"]
BYTES_PER_NODE = 2
OUTPUT_LENGTH = 4

# Node names excluded when introspecting compiled LangGraph graphs
_LANGGRAPH_INTERNALS = frozenset({"__start__", "__end__", ""})


def compute_bindings() -> dict:
    """
    Introspect the compiled LangGraph graphs and compute perceptual space
    region assignments for each node.

    Bindings are packed contiguously starting at TOPOLOGY_BASE_OFFSET:
      for each graph (in definition order):
        for each node (in compiled graph order):
          assign BYTES_PER_NODE bytes
        assign OUTPUT_LENGTH bytes for the topology machine output

    Returns a dict keyed by graph name:
      {
        "nodes": {node_name: {"sensor_id", "pe_name", "offset", "length"}},
        "node_order": [node_name, ...],
        "input_region": {"offset": int, "length": int},
        "output_region": {"offset": int, "length": int},
      }
    """
    # Lazy imports avoid circular dependencies at module load time
    from graphs.agent_graph import get_agent_graph
    from graphs.rag_graph import get_rag_graph

    raw_nodes = {
        "rag": [n for n in get_rag_graph().nodes if n not in _LANGGRAPH_INTERNALS],
        "agent": [n for n in get_agent_graph().nodes if n not in _LANGGRAPH_INTERNALS],
    }

    bindings: dict = {}

    for graph_name, nodes in raw_nodes.items():
        try:
            graph_base = GRAPH_BASE_OFFSETS[graph_name]
        except KeyError as exc:
            raise RuntimeError(
                f"No GRAPH_BASE_OFFSETS entry for graph '{graph_name}'. "
                f"Add it to topology_builder.py before registering new graphs."
            ) from exc

        current_offset = graph_base
        node_map: dict = {}

        for node in nodes:
            node_map[node] = {
                "sensor_id": f"localai_{graph_name}_{node}",
                "pe_name": f"localai/{graph_name}/{node}",
                "offset": current_offset,
                "length": BYTES_PER_NODE,
            }
            current_offset += BYTES_PER_NODE

        input_region = {"offset": graph_base, "length": len(nodes) * BYTES_PER_NODE}
        output_region = {"offset": current_offset, "length": OUTPUT_LENGTH}

        bindings[graph_name] = {
            "nodes": node_map,
            "node_order": nodes,
            "input_region": input_region,
            "output_region": output_region,
        }

    return bindings


def _bits_per_element(sequences: list) -> int:
    """Element width from the values the sequences carry.

    SEMANTIC_GUARDRAIL_CONTRACT.md derives this from evidence rather than a
    label, because the label is contradicted by the data often enough not to be
    trusted on its own:

        machine-native-binary   1   {0,1}
        machine-native-ordinal  4   {0..3}
        machine-native-scalar   8   0..1 continuous
    """
    values: set[float] = set()
    for sequence in sequences:
        for vector in sequence.get("vectors") or []:
            for element in vector.get("elements") or []:
                if isinstance(element.get("value"), (int, float)):
                    values.add(float(element["value"]))
            for ov in vector.get("outputVectors") or []:
                for value in ov.get("vector") or []:
                    if isinstance(value, (int, float)):
                        values.add(float(value))
    if not values:
        return 8
    if values <= {0.0, 1.0}:
        return 1
    if values <= {0.0, 1.0, 2.0, 3.0}:
        return 4
    return 8


def build_machine_json(graph_name: str, binding: dict) -> dict:
    """
    Build the CES machine JSON for a topology-tracking machine.

    The machine has one isInitial sequence per node.  Each sequence fires
    when its node's "active" signal element is in the HIGH zone (>= 0.5),
    and asserts a 1.0 at that node's output position.

    With OR arbiter and mutually-exclusive node signals (only one node
    executes at a time), exactly one sequence fires per RE step, giving
    a clean "which node is active" readout on the output region.
    """
    nodes = binding["node_order"]
    input_region = binding["input_region"]
    output_region = binding["output_region"]
    input_length = input_region["length"]

    sequences = []
    for i, node in enumerate(nodes):
        # Build the element array for this node's initial vector.
        # Position i * BYTES_PER_NODE carries the node's active signal;
        # all other positions are wildcards (Threshold ±0.5 spans [0,1]).
        elements = []
        for j in range(input_length):
            if j == i * BYTES_PER_NODE:
                # HIGH check: GTE split at 0.5 → matches when signal >= 0.5
                elements.append({"value": 1.0, "threshold": 0.5})
            else:
                # Wildcard: |input - 0.5| <= 0.5 is always true for [0,1] inputs
                elements.append({"value": 0.5, "threshold": 0.5, "comparatorType": "threshold"})

        # Output vector: 1.0 at this node's index, 0.0 elsewhere
        output_vector = [0.0] * output_region["length"]
        if i < output_region["length"]:
            output_vector[i] = 1.0

        sequences.append(
            {
                "id": f"topo-{graph_name}-{node}",
                "name": f"Node active: {node}",
                "metadata": {
                    "description": f"Fires when LangGraph node '{node}' is executing",
                    "node": node,
                    "graph": graph_name,
                    "signal_element": i * BYTES_PER_NODE,
                    "output_bit": i,
                },
                "vectors": [
                    {
                        "id": f"vec-topo-{graph_name}-{node}",
                        "isInitial": True,
                        "elements": elements,
                        "nextVectorIds": [],
                        "outputVectors": [
                            {
                                "id": f"out-topo-{graph_name}-{node}",
                                "vector": output_vector,
                                "metadata": {
                                    "description": f"Active node: {node}",
                                    "node": node,
                                },
                            }
                        ],
                    }
                ],
            }
        )

    input_label = f"[{input_region['offset']}:{input_region['offset'] + input_region['length']}]"
    output_label = (
        f"[{output_region['offset']}:{output_region['offset'] + output_region['length']}]"
    )
    node_signals = ", ".join(f"{n}_active" for n in nodes)
    output_bits = ", ".join(f"{n}={i}" for i, n in enumerate(nodes[:OUTPUT_LENGTH]))

    return {
        "version": "1.0.0",
        "machine": {
            "name": f"localai/{graph_name}_topology",
            "description": (
                f"Auto-generated topology machine for the '{graph_name}' LangGraph graph. "
                f"Each sequence fires when its LangGraph node begins execution, "
                f"giving real-time node visibility in the Tobias canvas."
            ),
            "metadata": {
                "category": "ai-pipeline",
                "author": "localAIStack topology builder",
                "created": "2026-04-16T00:00:00Z",
                "eventSpace": f"{input_region['length']}D node-signal vector at {input_label}: [{node_signals}]",
                "outputSpace": f"{output_region['length']}D binary at {output_label}: [{output_bits}]",
                "auto_generated": True,
                "graph_name": graph_name,
                "nodes": nodes,
                # The canonical machine schema requires these three. A topology
                # machine is registered into the RE alongside the corpus and
                # writes the universal vector, so it is held to the same
                # contract as a corpus machine — see jateeter/localAIStack#38
                # and tests/test_machine_schema.py, which validates this output.
                "machineClass": "signal-monitor",
                "governance": {
                    "schemaVersion": "1.0.0",
                    "ownerTeam": "localaistack",
                    "runbook": f"https://runbooks.example.org/localai/{graph_name}-topology",
                    "escalationPolicy": "slack:#localaistack",
                    "contact": {
                        "primary": "localaistack-primary@example.org",
                        "secondary": "localaistack-secondary@example.org",
                    },
                    "sla": {"ok": None, "info": None, "warning": None, "error": None},
                    "notes": (
                        f"Auto-generated topology machine for the '{graph_name}' graph; "
                        "built at runtime by the topology builder, not loaded from the corpus."
                    ),
                },
                # GREEN/info throughout: triggerConfig is not inert. The runtimes
                # join ragStatusCode onto each contribution and the SEVERITY rule
                # resolves contended cells by it, so asserting a severity here
                # would change arbitration outcomes for any cell this machine
                # contends. Node-visibility signals carry no severity of their own.
                "triggerConfig": {
                    "processId": f"{graph_name.upper()}TOPOLOGY",
                    "processName": f"LocalAI {graph_name} Topology",
                    "rules": [
                        {
                            "sequenceId": sequence["id"],
                            "outputMatches": (sequence["vectors"][0].get("outputVectors") or [{}])[
                                0
                            ].get("vector", []),
                            "ragStatusCode": "GREEN",
                            "processStatus": "info",
                            "description": sequence.get("name") or sequence["id"],
                        }
                        for sequence in sequences
                        if sequence.get("vectors")
                        and (sequence["vectors"][0].get("outputVectors") or [])
                    ],
                },
            },
            # PASSTHROUGH, not OR. The two are the same predicate in all three
            # runtimes — "some sequence produced output" and "the concatenation
            # is non-empty" agree on every input — and PASSTHROUGH is the only
            # value the canonical schema admits.
            "arbiterRule": "PASSTHROUGH",
            "matchAlgorithm": "gte",
            "perceptualMapping": {
                "input": {"offset": input_region["offset"], "length": input_region["length"]},
                "output": {"offset": output_region["offset"], "length": output_region["length"]},
                # Derived from the values these sequences actually carry, per the
                # derivation table in SEMANTIC_GUARDRAIL_CONTRACT.md — never from
                # a label or a guess. The comparator thresholds put 0.5 in the
                # value set, so these are machine-native-scalar (8), not the
                # binary (1) the {0,1} output bits alone would suggest.
                "bitsPerElement": _bits_per_element(sequences),
            },
            "sequences": sequences,
        },
    }
