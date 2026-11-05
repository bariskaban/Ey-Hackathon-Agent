"""Find where a discrepancy sits on the original document, so the verdict page can box it.

For each discrepancy, the invoice field it names is turned into the text that field prints (an amount as
`4,304.47`, an IBAN as written, a line item by its description). That text is then searched for on the page:

* PDFs with a text layer: an exact search, which also gives each character's position.
* Scans and photos (no text layer): Tesseract reports the position of every word it reads, and the words
  are matched to the text with some tolerance for misreads.

The result is a list of boxes in fractions of the page (0 to 1), so the page can draw them at any size.
Nothing here changes a verdict; a discrepancy that cannot be found on the page is simply not boxed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytesseract
from PIL import Image
from rapidfuzz import fuzz

from servers.documents.convert import OCR_CONFIG, OCR_DPI, find_tesseract

MIN_TEXT_CHARS = 40  # fewer characters than this on a page means it has no usable text layer
MAX_PAGES = 3
PAD = 0.004  # grow each box a little so it does not touch the text


@dataclass(frozen=True)
class Box:
    page: int  # 0-based
    x: float
    y: float
    w: float
    h: float
    index: int  # which discrepancy (position in the verdict's list)


@dataclass(frozen=True)
class Target:
    texts: tuple[str, ...]  # what the field prints; any of them counts
    band: bool = False  # box the whole row instead of only the text


# --------------------------------------------------------------------------- field -> printed text


def _money(value: Any) -> list[str]:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return []
    return list(dict.fromkeys([f"{amount:,.2f}", f"{amount:.2f}"]))


def _text(value: Any) -> list[str]:
    return [str(value).strip()] if value not in (None, "") else []


def targets_for(field: str | None, inv: Any) -> Target | None:
    """The text a discrepancy's `field` prints on the invoice, or None if nothing can be pointed at."""
    if not field or inv is None:
        return None
    get = lambda name: getattr(inv, name, None)
    if match := re.match(r"lines\[(\d+)\]", field):
        lines = get("lines") or []
        index = int(match[1])
        text = _text(getattr(lines[index], "description", None)) if index < len(lines) else []
        return Target(tuple(text), band=True) if text else None
    if field.startswith("vat_breakdown"):
        texts = [t for line in get("vat_lines") or [] for t in _money(getattr(line, "vat", None))]
        return Target(tuple(dict.fromkeys(texts + _money(get("total_vat"))))) or None
    if field == "notes":
        words = str(get("notes") or "").split()
        return Target((" ".join(words[:5]),)) if words else None
    plain = {"total_excl_vat": lambda: _money(get("total_excl_vat")), "total_vat": lambda: _money(get("total_vat")),
             "total_incl_vat": lambda: _money(get("total_incl_vat")),
             "supplier.iban": lambda: _text(get("iban")), "supplier.vat_number": lambda: _text(get("supplier_vat_number")),
             "buyer.vat_number": lambda: _text(get("buyer_vat_number")),
             "supplier.name": lambda: _text(get("supplier_name")), "supplier": lambda: _text(get("supplier_name")),
             "invoice_number": lambda: _text(get("invoice_number")), "invoice_date": lambda: _text(get("invoice_date")),
             "supply_date": lambda: _text(get("supply_date")), "po_number": lambda: _text(get("po_number"))}
    texts = plain.get(field, list)()
    return Target(tuple(texts)) if texts else None


# --------------------------------------------------------------------------- finding text on a page


def _pdf_hits(page: pdfium.PdfPage, text: str) -> list[tuple[float, float, float, float]]:
    """Exact matches in the text layer, as (x, y, w, h) fractions of the page."""
    width, height = page.get_size()
    textpage = page.get_textpage()
    searcher = textpage.search(text, match_case=True)
    found = []
    while hit := searcher.get_next():
        start, count = hit
        boxes = [textpage.get_charbox(i) for i in range(start, start + count)]
        left, right = min(b[0] for b in boxes), max(b[2] for b in boxes)
        bottom, top = min(b[1] for b in boxes), max(b[3] for b in boxes)
        found.append((left / width, 1 - top / height, (right - left) / width, (top - bottom) / height))
    return found


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _ocr_words(image: Image.Image) -> list[dict]:
    pytesseract.pytesseract.tesseract_cmd = find_tesseract()
    data = pytesseract.image_to_data(image.convert("L"), lang="eng", config=OCR_CONFIG,
                                     output_type=pytesseract.Output.DICT)
    words = []
    for i, text in enumerate(data["text"]):
        if text.strip() and _norm(text):
            words.append({"text": text, "left": data["left"][i], "top": data["top"][i], "w": data["width"][i],
                          "h": data["height"][i], "line": (data["block_num"][i], data["par_num"][i], data["line_num"][i])})
    return words


def _ocr_hits(words: list[dict], size: tuple[int, int], text: str) -> list[tuple[float, float, float, float]]:
    """Runs of consecutive words on one line that read like `text`, tolerating a few misread characters."""
    target = _norm(text)
    if not target:
        return []
    needed = 100 if len(target) < 6 else 88
    candidates = []
    for i, first in enumerate(words):
        joined = ""
        for j in range(i, min(i + len(text.split()) + 2, len(words))):
            if words[j]["line"] != first["line"]:
                break
            joined += _norm(words[j]["text"])
            if len(joined) > len(target) + 3:
                break
            score = fuzz.ratio(joined, target)
            if score >= needed:
                candidates.append((score, j - i, i, j))
    taken: set[int] = set()
    found = []
    for _score, _length, i, j in sorted(candidates, key=lambda c: (-c[0], c[1])):
        if taken & set(range(i, j + 1)):
            continue
        taken |= set(range(i, j + 1))
        run = words[i:j + 1]
        left, top = min(w["left"] for w in run), min(w["top"] for w in run)
        right, bottom = max(w["left"] + w["w"] for w in run), max(w["top"] + w["h"] for w in run)
        found.append((left / size[0], top / size[1], (right - left) / size[0], (bottom - top) / size[1]))
    return found


# --------------------------------------------------------------------------- the entry point

_cache: dict[tuple, list[Box]] = {}


def locate(path: Path, discrepancies: list, inv: Any) -> list[Box]:
    """Boxes for every discrepancy that can be found on the document at `path`. Never raises."""
    wanted = [(i, targets_for(d.field, inv)) for i, d in enumerate(discrepancies)]
    wanted = [(i, t) for i, t in wanted if t]
    if not wanted:
        return []
    key = (str(path), path.stat().st_mtime_ns, tuple((i, t) for i, t in wanted))
    if key not in _cache:
        try:
            _cache[key] = _locate(path, wanted)
        except Exception:  # noqa: BLE001 - a missing OCR engine or a damaged file only means no boxes
            _cache[key] = []
    return _cache[key]


def _locate(path: Path, wanted: list[tuple[int, Target]]) -> list[Box]:
    boxes: list[Box] = []

    def add(page: int, hits: list[tuple[float, float, float, float]], index: int, target: Target) -> None:
        for x, y, w, h in hits:
            if target.band:
                x, w = 0.04, 0.92
            boxes.append(Box(page, max(0.0, x - PAD), max(0.0, y - PAD), min(1.0, w + 2 * PAD), min(1.0, h + 2 * PAD),
                             index))

    def search_page(number: int, hits_for) -> None:
        for index, target in wanted:
            for text in target.texts:
                hits = hits_for(text)
                if hits:
                    add(number, hits, index, target)
                    break

    if path.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(str(path))
        try:
            for number in range(min(len(pdf), MAX_PAGES)):
                page = pdf[number]
                if page.get_textpage().count_chars() >= MIN_TEXT_CHARS:
                    search_page(number, lambda text, page=page: _pdf_hits(page, text))
                else:
                    image = page.render(scale=OCR_DPI / 72).to_pil()
                    words = _ocr_words(image)
                    search_page(number, lambda text, words=words, size=image.size: _ocr_hits(words, size, text))
        finally:
            pdf.close()
    else:
        image = Image.open(path)
        words = _ocr_words(image)
        search_page(0, lambda text: _ocr_hits(words, image.size, text))
    return boxes


# --------------------------------------------------------------------------- an annotated copy to download


def _hex(colour: str, alpha: int) -> tuple[int, int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16), alpha


def annotate(path: Path, boxes: list[Box], labels: list[tuple[str, str]], out: Path) -> Path:
    """Write a copy of the document with the boxes drawn on it, plus a last page that explains each number.

    `labels` has one (colour, text) per discrepancy, in the same order as the verdict's list. The original
    file is only read. The copy is a PDF made of page images, so its text cannot be selected.
    """
    from PIL import ImageDraw, ImageFont

    if path.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(str(path))
        try:
            pages = [pdf[i].render(scale=150 / 72).to_pil().convert("RGBA") for i in range(min(len(pdf), 20))]
        finally:
            pdf.close()
    else:
        pages = [Image.open(path).convert("RGBA")]
    for number, page in enumerate(pages):
        layer = Image.new("RGBA", page.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        font = ImageFont.load_default(size=max(14, page.width // 55))
        for box in (b for b in boxes if b.page == number):
            colour = labels[box.index][0]
            left, top = box.x * page.width, box.y * page.height
            right, bottom = left + box.w * page.width, top + box.h * page.height
            draw.rectangle((left, top, right, bottom), fill=_hex(colour, 60), outline=_hex(colour, 255),
                           width=max(2, page.width // 400))
            tag = str(box.index + 1)
            size = font.size + 8
            draw.rectangle((left, top - size, left + size, top), fill=_hex(colour, 255))
            draw.text((left + 4, top - size + 2), tag, fill=(255, 255, 255, 255), font=font)
        pages[number] = Image.alpha_composite(page, layer)

    legend = Image.new("RGBA", pages[0].size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(legend)
    font = ImageFont.load_default(size=max(16, legend.width // 45))
    draw.text((legend.width * 0.08, legend.height * 0.06), f"Findings for {path.name}", fill=(26, 36, 48, 255), font=font)
    y = legend.height * 0.06 + font.size * 2.2
    for number, (colour, text) in enumerate(labels, 1):
        draw.rectangle((legend.width * 0.08, y, legend.width * 0.08 + font.size, y + font.size), fill=_hex(colour, 255))
        draw.text((legend.width * 0.08 + font.size * 1.6, y), f"{number}. {text}", fill=(26, 36, 48, 255), font=font)
        y += font.size * 1.9
    pages.append(legend)

    out.parent.mkdir(parents=True, exist_ok=True)
    rgb = [p.convert("RGB") for p in pages]
    rgb[0].save(out, "PDF", resolution=150.0, save_all=True, append_images=rgb[1:])
    return out
