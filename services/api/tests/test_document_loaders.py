"""core.document_loaders: the upload readers, without langchain-community.

Real files, built here: a one-page PDF with text, a .docx (a zip holding
word/document.xml), UTF-8 text and Markdown with front matter.
"""

from __future__ import annotations

import sys
import zipfile

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("pypdf")
pytest.importorskip("docx2txt")

from core import document_loaders as dl  # noqa: E402


def _pdf(path, text: str) -> None:
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(out)


def _docx(path, *paragraphs: str) -> None:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/'
            '2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
            "</Types>",
        )
        z.writestr(
            "word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
        )


def test_pdf_is_one_document_per_page_with_its_page(tmp_path):
    f = tmp_path / "sleep.pdf"
    _pdf(f, "Sleep debt lowers next-day HRV.")
    docs = dl.PdfLoader(str(f)).load()
    assert [d.metadata for d in docs] == [{"source": str(f), "page": 0}]
    assert "Sleep debt lowers next-day HRV." in docs[0].page_content


def test_docx_is_read(tmp_path):
    f = tmp_path / "note.docx"
    _docx(f, "Recovery note", "Resting heart rate above baseline suggests incomplete recovery.")
    (doc,) = dl.DocxLoader(str(f)).load()
    assert "Recovery note" in doc.page_content
    assert "incomplete recovery" in doc.page_content
    assert doc.metadata == {"source": str(f)}


def test_text_is_read_as_utf8(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("Hydration — and recovery.\n", encoding="utf-8")
    (doc,) = dl.TextFileLoader(str(f)).load()
    assert doc.page_content == "Hydration — and recovery.\n"


def test_markdown_drops_front_matter(tmp_path):
    f = tmp_path / "m.md"
    f.write_text("---\ntitle: x\n---\n# Heading\n\nbody\n", encoding="utf-8")
    (doc,) = dl.MarkdownLoader(str(f)).load()
    assert doc.page_content == "# Heading\n\nbody"


def test_every_extension_has_a_loader_and_none_is_langchain_community():
    assert set(dl.LOADERS) == {".pdf", ".txt", ".md", ".docx"}
    assert all(c.__module__ == "core.document_loaders" for c in dl.LOADERS.values())
    assert "langchain_community" not in sys.modules
