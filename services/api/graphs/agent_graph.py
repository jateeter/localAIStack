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
from core.vector_store import get_vector_store


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
def list_collections(_: str = "") -> str:
    """List all document collections available in the vector store."""
    from core.vector_store import get_qdrant_client
    client = get_qdrant_client()
    collections = [c.name for c in client.get_collections().collections]
    return json.dumps(collections)


TOOLS = [rag_search, list_collections]


# ── State ─────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    system_prompt: str


# ── Nodes ─────────────────────────────────────────────────────────────────────

def agent_node(state: AgentState) -> dict:
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
    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# ── Graph ─────────────────────────────────────────────────────────────────────

def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(TOOLS))

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
