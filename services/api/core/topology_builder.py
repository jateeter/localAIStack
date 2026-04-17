"""
LangGraph → Reality Engine topology builder.

Reads the compiled node list from each LangGraph graph and produces:
  1. A binding dict  — perceptual space region assignments per node
  2. A CES machine JSON — one topology-tracking machine per graph

Region layout  (packed contiguously, base offset 76):
  Each node gets BYTES_PER_NODE bytes (default 2):
    [offset + 0]  node_active signal  (1.0 while node is executing)
    [offset + 1]  reserved

  Each graph's input region is immediately followed by its OUTPUT_LENGTH-byte
  output region where the topology machine writes which node is currently active.

Example layout for default graphs (rag=4 nodes, agent=2 nodes):
  [76:84]  rag node signals    (4 nodes × 2 bytes)
  [84:88]  rag topology output (4 bytes: [retrieve, grade_documents, generate, rewrite_query])
  [88:92]  agent node signals  (2 nodes × 2 bytes)
  [92:96]  agent topology output (4 bytes: [agent, tools, 0, 0])
"""

from __future__ import annotations

TOPOLOGY_BASE_OFFSET = 76  # first free byte after RAG routing signals [64:76]
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
    from graphs.rag_graph import get_rag_graph
    from graphs.agent_graph import get_agent_graph

    raw_nodes = {
        "rag":   [n for n in get_rag_graph().nodes   if n not in _LANGGRAPH_INTERNALS],
        "agent": [n for n in get_agent_graph().nodes if n not in _LANGGRAPH_INTERNALS],
    }

    current_offset = TOPOLOGY_BASE_OFFSET
    bindings: dict = {}

    for graph_name, nodes in raw_nodes.items():
        graph_base = current_offset
        node_map: dict = {}

        for node in nodes:
            node_map[node] = {
                "sensor_id": f"localai_{graph_name}_{node}",
                "pe_name":   f"localai/{graph_name}/{node}",
                "offset":    current_offset,
                "length":    BYTES_PER_NODE,
            }
            current_offset += BYTES_PER_NODE

        input_region  = {"offset": graph_base,       "length": len(nodes) * BYTES_PER_NODE}
        output_region = {"offset": current_offset,   "length": OUTPUT_LENGTH}
        current_offset += OUTPUT_LENGTH

        bindings[graph_name] = {
            "nodes":         node_map,
            "node_order":    nodes,
            "input_region":  input_region,
            "output_region": output_region,
        }

    return bindings


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
    nodes         = binding["node_order"]
    input_region  = binding["input_region"]
    output_region = binding["output_region"]
    input_length  = input_region["length"]

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

        sequences.append({
            "id":   f"topo-{graph_name}-{node}",
            "name": f"Node active: {node}",
            "metadata": {
                "description":    f"Fires when LangGraph node '{node}' is executing",
                "node":           node,
                "graph":          graph_name,
                "signal_element": i * BYTES_PER_NODE,
                "output_bit":     i,
            },
            "vectors": [{
                "id":          f"vec-topo-{graph_name}-{node}",
                "isInitial":   True,
                "elements":    elements,
                "nextVectorIds": [],
                "outputVectors": [{
                    "id":     f"out-topo-{graph_name}-{node}",
                    "vector": output_vector,
                    "metadata": {
                        "description": f"Active node: {node}",
                        "node":        node,
                    },
                }],
            }],
        })

    input_label  = f"[{input_region['offset']}:{input_region['offset']  + input_region['length']}]"
    output_label = f"[{output_region['offset']}:{output_region['offset'] + output_region['length']}]"
    node_signals = ", ".join(f"{n}_active" for n in nodes)
    output_bits  = ", ".join(f"{n}={i}" for i, n in enumerate(nodes[:OUTPUT_LENGTH]))

    return {
        "version": "1.0.0",
        "machine": {
            "name":        f"localai/{graph_name}_topology",
            "description": (
                f"Auto-generated topology machine for the '{graph_name}' LangGraph graph. "
                f"Each sequence fires when its LangGraph node begins execution, "
                f"giving real-time node visibility in the Tobias canvas."
            ),
            "metadata": {
                "category":       "ai-pipeline",
                "author":         "localAIStack topology builder",
                "created":        "2026-04-16T00:00:00Z",
                "eventSpace":     f"{input_region['length']}D node-signal vector at {input_label}: [{node_signals}]",
                "outputSpace":    f"{output_region['length']}D binary at {output_label}: [{output_bits}]",
                "auto_generated": True,
                "graph_name":     graph_name,
                "nodes":          nodes,
            },
            "arbiterRule":    "OR",
            "matchAlgorithm": "gte",
            "perceptualMapping": {
                "input":  {"offset": input_region["offset"],  "length": input_region["length"]},
                "output": {"offset": output_region["offset"], "length": output_region["length"]},
            },
            "sequences": sequences,
        },
    }
