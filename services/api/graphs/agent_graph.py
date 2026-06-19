"""
Agent Graph — ReAct-style agent with tool use, backed by local Ollama.

Tools available:
  rag_search    → query the local Qdrant RAG index
  web_search    → placeholder (swap in searxng or serpapi if needed)

Designed to be the integration surface for langgraph.x.reality — expose
the compiled graph and its input/output schema so the reality engine can
bind to it directly.
"""

from __future__ import annotations

from typing import Annotated, TypedDict, List, Sequence
import operator
import json

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from config import get_settings
from core.vector_store import get_vector_store, get_health_vector_store


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def rag_search(query: str) -> str:
    """Search the local knowledge base for information relevant to the query."""
    s = get_settings()
    store = get_vector_store()
    docs = store.similarity_search(query, k=4)
    if not docs:
        return "No relevant documents found in the knowledge base."
    return "\n\n---\n\n".join(
        f"[Source: {d.metadata.get('source', 'unknown')}]\n{d.page_content}"
        for d in docs
    )


@tool
def health_search(query: str) -> str:
    """Search the personal health knowledge base for information about health metrics,
    wellness indicators, sleep quality, HRV interpretation, heart rate, and recovery.
    Use this tool when the user asks health-related questions such as what their HRV
    or sleep metrics mean, how to improve recovery, or what health state changes signify."""
    store = get_health_vector_store()
    docs = store.similarity_search(query, k=4)
    if not docs:
        return "No relevant health information found in the knowledge base."
    return "\n\n---\n\n".join(
        f"[{d.metadata.get('category', 'health')}]\n{d.page_content}"
        for d in docs
    )


@tool
def list_collections(_: str = "") -> str:
    """List all document collections available in the vector store."""
    from core.vector_store import get_qdrant_client
    client = get_qdrant_client()
    collections = [c.name for c in client.get_collections().collections]
    return json.dumps(collections)


TOOLS = [rag_search, health_search, list_collections]


# ── State ─────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    system_prompt: str


# ── Nodes ─────────────────────────────────────────────────────────────────────

def agent_node(state: AgentState) -> dict:
    from core.reality_bridge import push_node_signal
    push_node_signal("agent", "agent", 1.0, trigger_push=True)

    s = get_settings()
    llm = ChatOllama(
        base_url=s.ollama_base_url,
        model=s.llm_model,
        temperature=0.1,
    ).bind_tools(TOOLS)

    system = state.get("system_prompt") or (
        "You are a helpful assistant with access to a local knowledge base. "
        "Use the rag_search tool to find relevant information before answering. "
        "Always ground your answers in retrieved context when available."
    )

    messages = [SystemMessage(content=system)] + state["messages"]
    response = llm.invoke(messages)

    push_node_signal("agent", "agent", 0.0, trigger_push=False)
    return {"messages": [response]}


_tool_node_instance = ToolNode(TOOLS)


def _count_activity_metrics(
    prior_messages: Sequence[BaseMessage],
    new_messages:   Sequence[BaseMessage],
) -> tuple[int, int, int]:
    """
    Return (tool_calls, tool_errors, reasoning_steps) describing the turn so far.

    tool_calls:      total tool invocations requested by the agent across the turn.
    tool_errors:     tool responses whose content looks like an exception/error string.
                     This is a keyword heuristic — exact failure semantics depend on
                     each tool's own error contract — but it's good enough to flag
                     "something keeps blowing up" so the classifier can see it.
    reasoning_steps: AIMessage count (one per agent LLM call).
    """
    all_messages = list(prior_messages) + list(new_messages)

    tool_calls = sum(
        len(getattr(m, "tool_calls", []) or [])
        for m in all_messages
    )
    tool_errors = sum(
        1 for m in new_messages
        if isinstance(m, ToolMessage)
        and any(
            marker in (m.content or "").lower()
            for marker in ("error", "exception", "traceback")
        )
    )
    reasoning_steps = sum(1 for m in all_messages if isinstance(m, AIMessage))
    return tool_calls, tool_errors, reasoning_steps


def _tools_node(state: AgentState) -> dict:
    from core.reality_bridge import push_node_signal, push_agent_activity_signal
    push_node_signal("agent", "tools", 1.0, trigger_push=True)
    result = _tool_node_instance(state)
    push_node_signal("agent", "tools", 0.0, trigger_push=False)

    # Emit the agent-activity signal so agent_activity_classifier can fire on
    # this push cycle. Metrics reflect the turn's shape up through the tools we
    # just executed. Safe to call even when the bridge is unreachable — it
    # swallows all network failures internally.
    calls, errors, depth = _count_activity_metrics(
        state["messages"], result.get("messages", []),
    )
    push_agent_activity_signal(
        tool_calls=calls,
        tool_errors=errors,
        reasoning_steps=depth,
    )

    return result


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# ── Graph ─────────────────────────────────────────────────────────────────────

def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", _tools_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


_agent_graph = None


def get_agent_graph():
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_agent_graph()
    return _agent_graph
