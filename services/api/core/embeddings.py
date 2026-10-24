from langchain_ollama import OllamaEmbeddings

from config import get_settings

_embeddings: OllamaEmbeddings | None = None


def get_embeddings() -> OllamaEmbeddings:
    global _embeddings
    if _embeddings is None:
        s = get_settings()
        _embeddings = OllamaEmbeddings(
            base_url=s.ollama_base_url,
            model=s.embed_model,
        )
    return _embeddings


def probe_dimension() -> int:
    """The vector length the configured model actually returns.

    Not read from model metadata: ``ollama show embeddinggemma-2:270m`` reports an
    embedding length of 512 while ``/api/embed`` returns 768-dim vectors. The
    output is the only thing that can be wrong in a way that matters, so measure it.
    """
    return len(get_embeddings().embed_query("dimension probe"))
