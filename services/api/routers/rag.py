import os
import tempfile

from fastapi import APIRouter, File, HTTPException, UploadFile
from langchain_community.document_loaders import (
    Docx2txtLoader,
    PyPDFLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
)
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel

from core.vector_store import get_vector_store
from graphs.rag_graph import get_rag_graph

router = APIRouter(prefix="/rag", tags=["rag"])

SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)

LOADERS = {
    ".pdf": PyPDFLoader,
    ".txt": TextLoader,
    ".md": UnstructuredMarkdownLoader,
    ".docx": Docx2txtLoader,
}


class IngestTextRequest(BaseModel):
    text: str
    source: str | None = "manual"
    metadata: dict | None = {}


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    rewrite_count: int


@router.post("/ingest/text")
async def ingest_text(req: IngestTextRequest):
    doc = Document(
        page_content=req.text,
        metadata={"source": req.source, **req.metadata},
    )
    chunks = SPLITTER.split_documents([doc])
    store = get_vector_store()
    ids = store.add_documents(chunks)
    return {"ingested_chunks": len(ids), "source": req.source}


@router.post("/ingest/file")
async def ingest_file(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in LOADERS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Supported: {list(LOADERS)}")

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        loader = LOADERS[ext](tmp_path)
        docs = loader.load()
        for d in docs:
            d.metadata["source"] = file.filename
        chunks = SPLITTER.split_documents(docs)
        store = get_vector_store()
        ids = store.add_documents(chunks)
        return {"filename": file.filename, "ingested_chunks": len(ids)}
    finally:
        os.unlink(tmp_path)


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    graph = get_rag_graph()
    result = graph.invoke({"question": req.question, "documents": [], "rewrite_count": 0})

    sources = list({
        d.metadata.get("source", "unknown")
        for d in result.get("documents", [])
    })

    return QueryResponse(
        answer=result.get("generation", "No answer generated."),
        sources=sources,
        rewrite_count=result.get("rewrite_count", 0),
    )
