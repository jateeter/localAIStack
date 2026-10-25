"""The agent graph's tools node runs its ToolNode and emits agent activity.

Under langgraph-prebuilt 1.x a ToolNode is a Runnable, not a callable, and
`_tools_node` called it directly: every /graph/agent turn that reached a tool
raised `TypeError: 'ToolNode' object is not callable`, answered 500, and never
wrote the `localai_agent_activity` sensor. These tests run the real
`_tools_node` inside a compiled graph, which is where langgraph supplies its
config, with a stub tool in place of the Qdrant-backed ones and the bridge
calls recorded instead of sent.

They need the API's own dependencies. The minimal unit job skips them; the
machine-contract job installs services/api/requirements.txt and runs them, so
the skip is never the only place they run.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_ollama")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode  # noqa: E402

from core import reality_bridge  # noqa: E402
from graphs import agent_graph  # noqa: E402


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo:{text}"


class _State(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    system_prompt: str


def _requesting_tool(_state: _State) -> dict:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "call-1"}],
            )
        ]
    }


@pytest.fixture
def bridge_calls(monkeypatch):
    calls: dict[str, list] = {"node": [], "activity": []}
    monkeypatch.setattr(
        reality_bridge,
        "push_node_signal",
        lambda *a, **k: calls["node"].append((a, k)),
    )
    monkeypatch.setattr(
        reality_bridge,
        "push_agent_activity_signal",
        lambda **k: calls["activity"].append(k),
    )
    monkeypatch.setattr(agent_graph, "_tool_node_instance", ToolNode([echo]))
    return calls


def _run_tools_node():
    g = StateGraph(_State)
    g.add_node("start", _requesting_tool)
    g.add_node("tools", agent_graph._tools_node)
    g.set_entry_point("start")
    g.add_edge("start", "tools")
    g.add_edge("tools", END)
    return g.compile().invoke({"messages": [HumanMessage(content="x")], "system_prompt": ""})


def test_tool_node_is_invoked_not_called(bridge_calls):
    out = _run_tools_node()
    last = out["messages"][-1]
    assert isinstance(last, ToolMessage)
    assert last.content == "echo:hi"


def test_agent_activity_is_emitted_after_the_tools_run(bridge_calls):
    _run_tools_node()
    assert bridge_calls["activity"] == [{"tool_calls": 1, "tool_errors": 0, "reasoning_steps": 1}]
    # The tools node lights its own topology sensor and clears it afterwards.
    assert [a[:3] for a, _ in bridge_calls["node"]] == [
        ("agent", "tools", 1.0),
        ("agent", "tools", 0.0),
    ]
