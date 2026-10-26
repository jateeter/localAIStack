"""Readers for ``POST /rag/ingest/file``: one per accepted extension.

Each turns a file into ``langchain_core`` ``Document``s with ``source`` set, which
the router overwrites with the uploaded filename and then splits. They read the
file directly with the library the format needs, rather than through
``langchain_community.document_loaders``: that package is being sunset and is no
longer maintained (langchain-ai/langchain-community#674), and its loaders here
were thin wrappers over the same calls.

- ``.pdf``: ``pypdf``, one document per page with its 0-based ``page``, as
  ``PyPDFLoader`` produced. A page with no extractable text is dropped.
- ``.docx``: ``docx2txt``, the whole document as one text, as
  ``Docx2txtLoader`` produced.
- ``.txt``: the file as UTF-8.
- ``.md``: ``core.health_docs.read_markdown``, the reader the default corpus
  uses, so an upload and a seeded document are chunked alike.
  ``UnstructuredMarkdownLoader`` ran Markdown through an NLP pipeline whose spaCy
  model is not in the image, so the first upload pip-installed
  ``en_core_web_sm`` into the running container, or hung where it could not.
"""

from __future__ import annotations

import pathlib

from langchain_core.documents import Document

from core.health_docs import read_markdown


class _FileLoader:
    def __init__(self, path: str):
        self.path = pathlib.Path(path)

    def _doc(self, text: str, **metadata) -> Document:
        return Document(page_content=text, metadata={"source": str(self.path), **metadata})


class PdfLoader(_FileLoader):
    def load(self) -> list[Document]:
        from pypdf import PdfReader

        docs = []
        for page, p in enumerate(PdfReader(self.path).pages):
            text = (p.extract_text() or "").strip()
            if text:
                docs.append(self._doc(text, page=page))
        return docs


class DocxLoader(_FileLoader):
    def load(self) -> list[Document]:
        import docx2txt

        return [self._doc((docx2txt.process(str(self.path)) or "").strip())]


class TextFileLoader(_FileLoader):
    def load(self) -> list[Document]:
        return [self._doc(self.path.read_text(encoding="utf-8"))]


class MarkdownLoader(_FileLoader):
    def load(self) -> list[Document]:
        return [self._doc(read_markdown(self.path))]


LOADERS: dict[str, type[_FileLoader]] = {
    ".pdf": PdfLoader,
    ".txt": TextFileLoader,
    ".md": MarkdownLoader,
    ".docx": DocxLoader,
}
