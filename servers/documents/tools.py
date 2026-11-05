"""Three read-only tools that turn inbox documents into Markdown.

Only `doc_id`s from the inbox are accepted, never file paths or URLs. The Markdown is untrusted
data: the host wraps it in <document> delimiters before a model reads it.
"""

from servers.common.limits import clamp_offset
from servers.documents import convert as convert_mod
from servers.documents import manifest

MAX_CHARS = 7000  # most characters returned per call; use offset to read on


def list_documents() -> dict:
    """Documents waiting in the inbox: doc_id, filename, type, size and page count."""
    loaded = manifest.load()
    docs = [{"doc_id": d["doc_id"], "filename": d["filename"], "type": d["mime_type"],
             "size_bytes": d["size_bytes"], "pages": d["pages"]} for d in loaded["documents"].values()]
    return {"documents": docs, "returned": len(docs), "rejected": loaded["rejected"]}


def get_document_metadata(doc_id: str) -> dict:
    """SHA-256 hash, MIME type, size, page count and whether the PDF has a text layer."""
    entry, _path = manifest.resolve(doc_id)
    return {"doc_id": entry.doc_id, "filename": entry.filename, "sha256": entry.sha256,
            "mime_type": entry.mime_type, "size_bytes": entry.size_bytes, "pages": entry.pages,
            "has_text_layer": entry.has_text_layer}


def convert_document(doc_id: str, pages: str | int | None = None, offset: int = 0) -> dict:
    """Convert a document to Markdown.

    Returns markdown (at most about 7,000 characters), the method used (markitdown, pdf_text or
    ocr), the detected language, and truncated/next_offset when there is more text: call again
    with offset=next_offset. `pages` is optional, for example '1' or '1-2'.
    """
    entry, path = manifest.resolve(doc_id)
    converted = convert_mod.convert(entry, path, pages)
    offset = clamp_offset(offset)
    full = converted.markdown
    chunk = full[offset:offset + MAX_CHARS]
    end = offset + len(chunk)
    truncated = end < len(full)
    return {
        "doc_id": entry.doc_id,
        "markdown": chunk,
        "method": converted.method,
        "language": converted.language,
        "language_confidence": converted.language_confidence,
        "char_count": len(full),
        "offset": offset,
        "truncated": truncated,
        "next_offset": end if truncated else None,
        "pages_processed": converted.pages_processed,
        "warnings": converted.warnings,
    }
