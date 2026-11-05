"""Turn a document into Markdown: text-layer PDFs are read directly, scans and images use OCR.

Methods:
  markitdown  Microsoft MarkItDown on a text PDF (used when its output keeps labels and values together)
  pdf_text    the PDF's own text layer in reading order (used when MarkItDown scrambles the layout)
  ocr         Tesseract (English) on scans and images
"""

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException
from markitdown import MarkItDown
from PIL import Image

from servers.common.config import DATA_DIR
from servers.documents.manifest import Entry

DetectorFactory.seed = 0  # langdetect is random by default; make it repeatable

OCR_DPI = 300
OCR_CONFIG = "--psm 4"
TESSERACT_FALLBACKS = (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                       r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")
MIN_LETTERS_FOR_LANGUAGE = 40


@dataclass
class Converted:
    markdown: str
    method: str
    language: str
    language_confidence: float
    warnings: list[str] = field(default_factory=list)
    pages_processed: list[int] = field(default_factory=list)


def cache_dir() -> Path:
    return Path(os.environ.get("INVOICE_CACHE_DIR", DATA_DIR / "cache"))


def find_tesseract() -> str:
    """PATH first, then the environment, then the default Windows install folders."""
    found = shutil.which("tesseract") or os.environ.get("INVOICE_TESSERACT_CMD")
    if found:
        return found
    for candidate in TESSERACT_FALLBACKS:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("Tesseract OCR is not installed or not on PATH (set INVOICE_TESSERACT_CMD)")


# --------------------------------------------------------------------------- language


def detect_language(text: str) -> tuple[str, float]:
    """Return (code, probability) of the most likely language, or ('unknown', 0.0) for short text."""
    letters = re.sub(r"[^A-Za-z]", "", text)
    if len(letters) < MIN_LETTERS_FOR_LANGUAGE:
        return "unknown", 0.0
    try:
        best = detect_langs(text)[0]
    except LangDetectException:
        return "unknown", 0.0
    return best.lang, round(best.prob, 3)


# --------------------------------------------------------------------------- readers


def parse_pages(spec: str | int | None, total: int) -> list[int]:
    """'1', '1-3' or '1,3-4' (1-based) -> sorted page numbers. None means every page."""
    if spec is None or str(spec).strip() == "":
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if m := re.fullmatch(r"(\d+)(?:-(\d+))?", part):
            start, end = int(m[1]), int(m[2] or m[1])
            if start < 1 or end < start or end > total:
                raise ValueError(f"pages must be within 1-{total}, got {part!r}")
            pages.update(range(start, end + 1))
        else:
            raise ValueError("pages must look like '1', '1-3' or '1,3-4'")
    return sorted(pages)


def _page_text(pdf, number: int) -> str:
    return pdf[number - 1].get_textpage().get_text_range().strip()


def _orphan_labels(text: str) -> int:
    """Lines that end in ':' hold a label whose value ended up somewhere else."""
    return sum(1 for line in text.splitlines() if line.strip().endswith(":"))


def _as_markdown(page_texts: dict[int, str]) -> str:
    if len(page_texts) == 1:
        return next(iter(page_texts.values()))
    return "\n\n".join(f"## Page {n}\n\n{text}" for n, text in page_texts.items())


def _ocr_image(image: Image.Image) -> str:
    pytesseract.pytesseract.tesseract_cmd = find_tesseract()
    return pytesseract.image_to_string(image.convert("L"), lang="eng", config=OCR_CONFIG).strip()


def _convert_pdf(path: Path, entry: Entry, pages: list[int], whole_document: bool) -> Converted:
    warnings: list[str] = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        if entry.has_text_layer:
            texts = {n: _page_text(pdf, n) for n in pages}
            reference = _as_markdown(texts)
            if whole_document:
                markitdown_text = MarkItDown().convert(str(path)).text_content.strip()
                if _orphan_labels(markitdown_text) <= max(1, _orphan_labels(reference)):
                    return _finish(markitdown_text, "markitdown", warnings, pages)
                warnings.append("MarkItDown separated labels from their values; used the PDF text layer instead")
            return _finish(reference, "pdf_text", warnings, pages)

        texts = {}
        for n in pages:
            image = pdf[n - 1].render(scale=OCR_DPI / 72).to_pil()
            texts[n] = _ocr_image(image)
        warnings.append("scanned document: text was read with OCR and may contain recognition errors")
        return _finish(_as_markdown(texts), "ocr", warnings, pages)
    finally:
        pdf.close()


def _convert_image(path: Path) -> Converted:
    with Image.open(path) as image:
        image.load()
        if image.width < 1800:  # small images OCR badly; scale up to roughly 300 dpi on A4
            factor = 1800 / image.width
            image = image.resize((round(image.width * factor), round(image.height * factor)), Image.LANCZOS)
        text = _ocr_image(image)
    return _finish(text, "ocr", ["image document: text was read with OCR and may contain recognition errors"], [1])


def _finish(markdown: str, method: str, warnings: list[str], pages: list[int]) -> Converted:
    language, confidence = detect_language(markdown)
    if not markdown.strip():
        warnings.append("no text could be read from this document")
    return Converted(markdown, method, language, confidence, warnings, pages)


# --------------------------------------------------------------------------- entry point


def convert(entry: Entry, path: Path, pages_spec: str | int | None = None) -> Converted:
    """Convert a document, using a cache keyed by the file hash and the page selection."""
    pages = parse_pages(pages_spec, entry.pages)
    whole = pages == list(range(1, entry.pages + 1))
    cache_file = cache_dir() / f"{entry.sha256}-{'all' if whole else '_'.join(map(str, pages))}.json"
    if cache_file.exists():
        return Converted(**json.loads(cache_file.read_text(encoding="utf-8")))

    if entry.mime_type == "application/pdf":
        result = _convert_pdf(path, entry, pages, whole)
    else:
        result = _convert_image(path)

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=cache_file.parent, delete=False, suffix=".tmp") as f:
        json.dump(result.__dict__, f)
    os.replace(f.name, cache_file)
    return result
