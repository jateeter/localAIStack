"""
RAG Graph — Corrective RAG pattern using LangGraph StateGraph.

Nodes:
  retrieve          → fetch top-k docs from Qdrant
  grade_documents   → score relevance, filter noise
  generate          → produce answer with grounded context
  rewrite_query     → rephrase if graded docs are insufficient

Edges:
  retrieve → grade_documents
  grade_documents → generate (if relevant docs found)
  grade_documents → rewrite_query (if all docs graded irrelevant)
  rewrite_query → retrieve (retry with new query)
  generate → END
"""

from __future__ import annotations

from typing import Annotated, TypedDict, List, Literal
import operator

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END

from config import get_settings
from core.vector_store import get_vector_store


# ── State ─────────────────────────────────────────────────────────────────────

class RAGState(TypedDict):
    question: str
    documents: Annotated[List[Document], operator.add]
    generation: str
    rewrite_count: int


# ── Node implementations ──────────────────────────────────────────────────────

def retrieve(state: RAGState) -> dict:
    s = get_settings()
    store = get_vector_store()
    docs = store.similarity_search(
        state["question"],
        k=s.retrieval_top_k,
        score_threshold=s.retrieval_score_threshold,
    )
    return {"documents": docs}


def grade_documents(state: RAGState) -> dict:
    """Keep only documents relevant to the question (simple keyword overlap gate)."""
    question_tokens = set(state["question"].lower().split())
    relevant = []
    for doc in state["documents"]:
        doc_tokens = set(doc.page_content.lower().split())
        overlap = len(question_tokens & doc_tokens) / max(len(question_tokens), 1)
        if overlap > 0.1 or len(doc.page_content) > 50:
            relevant.append(doc)
    return {"documents": relevant}


def generate(state: RAGState) -> dict:
    s = get_settings()
    llm = ChatOllama(base_url=s.ollama_base_url, model=s.llm_model, temperature=0.1)

    context = "\n\n---\n\n".join(d.page_content for d in state["documents"])
    messages = [
        SystemMessage(content=(
            "You are a helpful assistant. Answer the question using ONLY the provided context. "
            "If the context doesn't contain enough information, say so clearly. "
            "Be concise and factual.\n\n"
            f"Context:\n{context}"
        )),
        HumanMessage(content=state["question"]),
    ]
    response = llm.invoke(messages)
    return {"generation": response.content}


def rewrite_query(state: RAGState) -> dict:
    s = get_settings()
    llm = ChatOllama(base_url=s.ollama_base_url, model=s.llm_model, temperature=0.3)

    messages = [
        SystemMessage(content=(
            "Rephrase the following question to improve document retrieval. "
            "Make it more specific and use different terminology. "
            "Return ONLY the rephrased question, nothing else."
        )),
        HumanMessage(content=state["question"]),
    ]
    response = llm.invoke(messages)
    return {
        "question": response.content.strip(),
        "documents": [],  # clear stale docs
        "rewrite_count": state.get("rewrite_count", 0) + 1,
    }


# ── Conditional routing ───────────────────────────────────────────────────────

def route_after_grading(state: RAGState) -> Literal["generate", "rewrite_query"]:
    if state["documents"]:
        return "generate"
    if state.get("rewrite_count", 0) >= 2:
        # Give up rewriting — generate with empty context (LLM says "I don't know")
        return "generate"
    return "rewrite_query"


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_rag_graph() -> StateGraph:
    graph = StateGraph(RAGState)

    graph.add_node("retrieve", retrieve)
    graph.add_node("grade_documents", grade_documents)
    graph.add_node("generate", generate)
    graph.add_node("rewrite_query", rewrite_query)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        route_after_grading,
        {"generate": "generate", "rewrite_query": "rewrite_query"},
    )
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("generate", END)

    return graph.compile()


# Singleton — compiled once, reused across requests
_rag_graph = None


def get_rag_graph():
    global _rag_graph
    if _rag_graph is None:
        _rag_graph = build_rag_graph()
    return _rag_graph
