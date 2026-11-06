"""The inbox manifest: doc_id -> file in data/inbox/.

The documents server only ever accepts a `doc_id`. It never takes a path or a URL, so a model
(or an injected instruction) cannot make it read arbitrary files. Files are checked once when
they enter the manifest: allowed type, real content type (magic bytes), size and page count.
"""

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pypdfium2 as pdfium

from servers.common.config import INBOX_DIR

ALLOWED_SUFFIXES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg", ".tif": "image/tiff", ".tiff": "image/tiff"}
MAGIC = {"application/pdf": (b"%PDF",), "image/png": (b"\x89PNG",), "image/jpeg": (b"\xff\xd8\xff",),
         "image/tiff": (b"II*\x00", b"MM\x00*")}
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MIN_CHARS_PER_PAGE = 40  # fewer extractable characters than this per page means "no text layer"
DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


@dataclass
class Entry:
    doc_id: str
    filename: str  # relative to the inbox, never an absolute path
    sha256: str
    size_bytes: int
    mime_type: str
    pages: int
    has_text_layer: bool


def inbox_dir() -> Path:
    """Read at call time so tests can point the inbox somewhere else."""
    return Path(os.environ.get("INVOICE_INBOX_DIR", INBOX_DIR))


def manifest_path(folder: Path | None = None) -> Path:
    return (folder or inbox_dir()) / "manifest.json"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_pages_and_text(path: Path, mime: str) -> tuple[int, bool]:
    if mime != "application/pdf":
        return 1, False
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = len(pdf)
        chars = sum(len(pdf[i].get_textpage().get_text_range().strip()) for i in range(min(pages, MAX_PAGES)))
    finally:
        pdf.close()
    return pages, chars >= MIN_CHARS_PER_PAGE * max(1, min(pages, MAX_PAGES))


def check_file(path: Path) -> tuple[str, int, bool] | str:
    """Return (mime, pages, has_text_layer) or a reason the file is not accepted."""
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return f"file type {suffix or '(none)'} is not allowed"
    size = path.stat().st_size
    if size == 0 or size > MAX_BYTES:
        return f"size {size} bytes is outside the allowed range (1 to {MAX_BYTES})"
    mime = ALLOWED_SUFFIXES[suffix]
    with path.open("rb") as f:
        head = f.read(8)
    if not any(head.startswith(magic) for magic in MAGIC[mime]):
        return f"content does not look like {mime}"
    try:
        pages, has_text = count_pages_and_text(path, mime)
    except Exception as exc:  # noqa: BLE001 - a corrupt file is rejected, not fatal
        return f"cannot be read: {type(exc).__name__}"
    if pages > MAX_PAGES:
        return f"{pages} pages exceeds the limit of {MAX_PAGES}"
    return mime, pages, has_text


def make_doc_id(path: Path, sha: str) -> str:
    return path.stem if re.fullmatch(r"INV-\d{1,6}", path.stem) else f"DOC-{sha[:8]}"


def load() -> dict:
    path = manifest_path()
    if not path.exists():
        return {"documents": {}, "rejected": []}
    return json.loads(path.read_text(encoding="utf-8"))


def sync_inbox(folder: Path | None = None) -> dict:
    """Scan the inbox (or another folder, such as the private one an evaluation uses), (re)build its
    manifest, and return it. Run when files are added."""
    folder = folder or inbox_dir()
    folder.mkdir(parents=True, exist_ok=True)
    documents, rejected = {}, []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        checked = check_file(path)
        if isinstance(checked, str):
            rejected.append({"filename": path.name, "reason": checked})
            continue
        mime, pages, has_text = checked
        sha = sha256_of(path)
        doc_id = make_doc_id(path, sha)
        if doc_id in documents:  # same name or same content twice: keep the first
            rejected.append({"filename": path.name, "reason": f"duplicate doc_id {doc_id}"})
            continue
        documents[doc_id] = asdict(Entry(doc_id, path.name, sha, path.stat().st_size, mime, pages, has_text))
    manifest = {"documents": documents, "rejected": rejected}
    manifest_path(folder).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def resolve(doc_id: str) -> tuple[Entry, Path]:
    """Look up a doc_id. Anything that is not a known id is rejected, whatever it looks like."""
    if not isinstance(doc_id, str) or not DOC_ID_RE.fullmatch(doc_id):
        raise ValueError("doc_id must be an id from list_documents (letters, digits, '-' and '_' only)")
    raw = load()["documents"].get(doc_id)
    if raw is None:
        raise ValueError(f"unknown doc_id {doc_id!r}")
    entry = Entry(**raw)
    path = (inbox_dir() / entry.filename).resolve()
    if not path.is_relative_to(inbox_dir().resolve()) or not path.is_file():
        raise ValueError(f"document {doc_id!r} is no longer in the inbox")
    return entry, path
