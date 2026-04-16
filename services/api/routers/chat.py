from typing import Optional, List
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from config import get_settings

router = APIRouter(prefix="/chat", tags=["chat"])


class Message(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str


class ChatRequest(BaseModel):
    messages: List[Message]
    model: Optional[str] = None
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False


def _to_lc_messages(messages: List[Message]):
    mapping = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
    return [mapping.get(m.role, HumanMessage)(content=m.content) for m in messages]


@router.post("")
async def chat(req: ChatRequest):
    s = get_settings()
    model = req.model or s.llm_model
    llm = ChatOllama(
        base_url=s.ollama_base_url,
        model=model,
        temperature=req.temperature,
    )
    lc_messages = _to_lc_messages(req.messages)

    if req.stream:
        async def _stream():
            async for chunk in llm.astream(lc_messages):
                yield chunk.content

        return StreamingResponse(_stream(), media_type="text/plain")

    response = llm.invoke(lc_messages)
    return {"role": "assistant", "content": response.content, "model": model}
