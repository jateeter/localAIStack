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
