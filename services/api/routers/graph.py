"""
/graph endpoints — expose LangGraph compiled graphs as REST API.
This is the primary integration surface for langgraph.x.reality.

POST /graph/rag      → run the corrective RAG graph
POST /graph/agent    → run the ReAct agent graph
GET  /graph/schema   → return input/output schemas for both graphs
"""

from typing import Optional, List
from fastapi import APIRouter
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

from graphs.rag_graph import get_rag_graph
from graphs.agent_graph import get_agent_graph

router = APIRouter(prefix="/graph", tags=["graph"])


# ── RAG graph ─────────────────────────────────────────────────────────────────

class RAGRequest(BaseModel):
    question: str


class RAGResponse(BaseModel):
    question: str
    answer: str
    sources: List[str]
    rewrite_count: int


@router.post("/rag", response_model=RAGResponse)
async def run_rag_graph(req: RAGRequest):
    graph = get_rag_graph()
    result = graph.invoke(
        {"question": req.question, "documents": [], "rewrite_count": 0, "re_routing": "rewrite"}
    )
    sources = list({
        d.metadata.get("source", "unknown")
        for d in result.get("documents", [])
    })
    return RAGResponse(
        question=result["question"],
        answer=result.get("generation", ""),
        sources=sources,
        rewrite_count=result.get("rewrite_count", 0),
    )


# ── Agent graph ───────────────────────────────────────────────────────────────

class AgentMessage(BaseModel):
    role: str
    content: str


class AgentRequest(BaseModel):
    messages: List[AgentMessage]
    system_prompt: Optional[str] = None


class AgentResponse(BaseModel):
    answer: str
    tool_calls_made: int


@router.post("/agent", response_model=AgentResponse)
async def run_agent_graph(req: AgentRequest):
    graph = get_agent_graph()

    lc_messages = [HumanMessage(content=m.content) for m in req.messages if m.role == "user"]
    state = {
        "messages": lc_messages,
        "system_prompt": req.system_prompt or "",
    }
    result = graph.invoke(state)

    last_ai = next(
        (m for m in reversed(result["messages"]) if hasattr(m, "content") and not hasattr(m, "tool_call_id")),
        None,
    )
    tool_calls = sum(
        1 for m in result["messages"]
        if hasattr(m, "tool_call_id")
    )

    return AgentResponse(
        answer=last_ai.content if last_ai else "",
        tool_calls_made=tool_calls,
    )


# ── Schema introspection ──────────────────────────────────────────────────────

@router.get("/schema")
async def graph_schema():
    """Return graph topology for langgraph.x.reality binding."""
    from core.reality_bridge import get_topology_bindings
    topology = get_topology_bindings()

    def _topo(graph_name: str) -> dict:
        b = topology.get(graph_name)
        if not b:
            return {}
        return {
            "input_region":  b["input_region"],
            "output_region": b["output_region"],
            "node_order":    b["node_order"],
            "nodes": {
                node: {
                    "sensor_id": info["sensor_id"],
                    "offset":    info["offset"],
                    "length":    info["length"],
                }
                for node, info in b["nodes"].items()
            },
        }

    return {
        "session_context": {
            "rag": {
                "machine":  "localai/session_rag_context",
                "pattern":  "bistable-flip-flop",
                "input_region":  [72, 76],
                "output_region": [96, 100],
                "carry_signals": {
                    "last_generate": 96,
                    "last_rewrite":  97,
                    "last_abort":    98,
                },
                "hold_mechanism": "PE carry-forward — no sequence fires between RAG calls",
            },
            "agent": {
                "machine":  "localai/session_agent_context",
                "pattern":  "bistable-flip-flop",
                "input_region":  [88, 104],
                "output_region": [100, 104],
                "carry_signals": {
                    "agent_ever_engaged": 100,
                    "tools_ever_used":    101,
                },
                "hold_mechanism": "PE carry-forward + carry-feedback elements [12:14] prevent regression",
            },
        },
        "graphs": {
            "rag": {
                "entry": "retrieve",
                "nodes": ["retrieve", "grade_documents", "generate", "rewrite_query"],
                "state_schema": {
                    "question": "str",
                    "documents": "List[Document]",
                    "generation": "str",
                    "rewrite_count": "int",
                    "re_routing": "str",
                },
                "reality_engine": {
                    "machine": "localai/rag_corrective_cycle",
                    "input_region": [64, 72],
                    "output_region": [72, 76],
                    "routing_signals": {"generate": 72, "rewrite": 73, "abort": 74},
                    "topology": _topo("rag"),
                },
                "endpoint": "/graph/rag",
            },
            "agent": {
                "entry": "agent",
                "nodes": ["agent", "tools"],
                "tools": ["rag_search", "list_collections"],
                "state_schema": {
                    "messages": "List[BaseMessage]",
                    "system_prompt": "str",
                },
                "reality_engine": {
                    "topology": _topo("agent"),
                },
                "endpoint": "/graph/agent",
            },
        }
    }
