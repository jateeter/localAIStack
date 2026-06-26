
from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel

from config import get_settings

router = APIRouter(prefix="/chat", tags=["chat"])

_HEALTH_HINTS: dict[str, str] = {
    "thriving":  "The user's health metrics are all in nominal range. They are rested, recovered, and at full capacity.",
    "balanced":  "The user's cardiovascular metrics are good but sleep is below target. They may benefit from an early wind-down today.",
    "watch":     "The user's HRV indicates low recovery today. Consider recommending lighter activities and extra rest.",
    "attention": "The user's heart rate is outside the nominal range. Gently recommend a check-in with a healthcare provider if this persists.",
}


class Message(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    model: str | None = None
    temperature: float | None = 0.7
    stream: bool | None = False
    # Set to True to inject the current RE health state into the system prompt.
    # Overrides the global health_context_enabled setting for this request.
    # Set to False to explicitly disable even when health_context_enabled=True.
    health_context: bool | None = None


def _to_lc_messages(messages: list[Message]):
    mapping = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
    return [mapping.get(m.role, HumanMessage)(content=m.content) for m in messages]


def _inject_health_context(
    lc_messages: list,
    health_state: str,
) -> list:
    """Prepend or append the health hint to the existing system message."""
    hint = _HEALTH_HINTS.get(health_state)
    if not hint:
        return lc_messages

    health_snippet = f"[Health context: {hint}]"
    sys_idx = next(
        (i for i, m in enumerate(lc_messages) if isinstance(m, SystemMessage)),
        None,
    )
    if sys_idx is not None:
        existing = lc_messages[sys_idx].content
        lc_messages[sys_idx] = SystemMessage(content=f"{existing}\n\n{health_snippet}")
    else:
        lc_messages = [SystemMessage(content=health_snippet)] + lc_messages
    return lc_messages


@router.post("")
async def chat(
    req: ChatRequest,
    x_health_context: str | None = Header(None),
):
    s = get_settings()
    model = req.model or s.llm_model
    llm = ChatOllama(
        base_url=s.ollama_base_url,
        model=model,
        temperature=req.temperature,
    )
    lc_messages = _to_lc_messages(req.messages)

    # Resolve whether health context injection is active for this request.
    # Priority: per-request body field > X-Health-Context header > global setting.
    if req.health_context is not None:
        inject_health = req.health_context
    elif x_health_context is not None:
        inject_health = x_health_context.lower() == "enabled"
    else:
        inject_health = s.health_context_enabled

    if inject_health:
        from core.reality_bridge import get_current_health_state
        health_state = get_current_health_state()
        if health_state:
            lc_messages = _inject_health_context(lc_messages, health_state)

    if req.stream:
        async def _stream():
            async for chunk in llm.astream(lc_messages):
                yield chunk.content

        return StreamingResponse(_stream(), media_type="text/plain")

    response = llm.invoke(lc_messages)
    return {"role": "assistant", "content": response.content, "model": model}
