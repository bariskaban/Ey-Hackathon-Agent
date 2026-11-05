"""Render the invoice specs to PDFs. About 15 stay normal, 5 become "scanned".

Run after generate.py, from the repository root:

    python data/render_pdfs.py

A scanned invoice is rasterised, slightly rotated, blurred and noisy, then saved as an
image-only PDF, so it has no text layer and needs OCR.
"""

import json
import random
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from servers.common.config import INVOICES_DIR

STYLES = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=STYLES["Normal"], fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10, textColor=colors.HexColor("#444444"))
TITLE = ParagraphStyle("title", parent=STYLES["Title"], fontSize=24, alignment=0, spaceAfter=6)
RIGHT = ParagraphStyle("right", parent=BODY, alignment=2)


def eur(value: str | Decimal) -> str:
    return f"EUR {Decimal(value):,.2f}"


def rate_label(value: str) -> str:
    return f"{Decimal(value).normalize():f}%"


def supplier_block(spec: dict) -> Paragraph:
    s = spec["supplier"]
    parts = [f"<b>{s['name']}</b>", s["address"], f"VAT number: {s['vat_number']}"]
    if s.get("kvk_number"):
        parts.append(f"KvK number: {s['kvk_number']}")
    return Paragraph("<br/>".join(parts), BODY)


def buyer_block(spec: dict) -> Paragraph:
    b = spec["buyer"]
    parts = ["<b>Bill to</b>", b["name"], b["address"], f"VAT number: {b['vat_number']}"]
    if b.get("kvk_number"):
        parts.append(f"KvK number: {b['kvk_number']}")
    return Paragraph("<br/>".join(parts), BODY)


def meta_block(spec: dict) -> Table:
    rows = [
        ["Invoice number:", spec["invoice_number"]],
        ["Invoice date:", spec["invoice_date"]],
    ]
    if spec.get("supply_date"):
        rows.append(["Supply date:", spec["supply_date"]])
    rows.append(["Due date:", spec["due_date"]])
    if spec.get("po_number"):
        rows.append(["PO number:", spec["po_number"]])
    table = Table(rows, colWidths=[32 * mm, 42 * mm])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def build_story(spec: dict) -> list:
    story = [Paragraph("Invoice", TITLE)]

    header = Table([[supplier_block(spec), meta_block(spec)]], colWidths=[95 * mm, 80 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 8 * mm), buyer_block(spec), Spacer(1, 8 * mm)]

    rows = [["Item", "Description", "Qty", "Unit price", "VAT", "Amount excl. VAT"]]
    for line in spec["lines"]:
        rows.append([
            line.get("sku") or "",
            Paragraph(line["description"], BODY),
            f"{Decimal(line['quantity']):f}",
            eur(line["unit_price"]),
            rate_label(line["vat_rate"]),
            eur(line["line_total"]),
        ])
    lines_table = Table(rows, colWidths=[20 * mm, 66 * mm, 14 * mm, 26 * mm, 14 * mm, 32 * mm], repeatRows=1)
    lines_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story += [lines_table, Spacer(1, 6 * mm)]

    vat_rows = [["VAT rate", "Amount excl. VAT", "VAT amount"]]
    for item in spec["vat_breakdown"]:
        vat_rows.append([rate_label(item["rate"]), eur(item["base"]), eur(item["vat"])])
    vat_table = Table(vat_rows, colWidths=[28 * mm, 40 * mm, 34 * mm])
    vat_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
    ]))
    totals = Table([
        ["Total excl. VAT", eur(spec["total_excl_vat"])],
        ["Total VAT", eur(spec["total_vat"])],
        ["Total incl. VAT", eur(spec["total_incl_vat"])],
    ], colWidths=[38 * mm, 36 * mm])
    totals.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, 2), (-1, 2), 0.75, colors.black),
    ]))
    summary = Table([[vat_table, totals]], colWidths=[105 * mm, 75 * mm])
    summary.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [summary, Spacer(1, 6 * mm)]

    if spec.get("reverse_charge"):
        story.append(Paragraph(
            "<b>Reverse charge</b>: VAT is reverse-charged to the recipient. "
            f"Supplier VAT number: {spec['supplier']['vat_number']}. "
            f"Customer VAT number: {spec['buyer']['vat_number']}.", BODY))
        story.append(Spacer(1, 4 * mm))

    story.append(Paragraph(
        f"Please pay {eur(spec['total_incl_vat'])} before {spec['due_date']} to IBAN "
        f"<b>{spec['supplier']['iban']}</b>, quoting the invoice number.", BODY))
    if spec.get("notes"):
        story += [Spacer(1, 4 * mm), Paragraph(f"Remarks: {spec['notes']}", SMALL)]
    return story


def render_pdf(spec: dict, path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=f"Invoice {spec['invoice_number']}")
    doc.build(build_story(spec))


def make_scan(source: Path, target: Path, rng: random.Random) -> None:
    """Rasterise a PDF and degrade it like a poor photocopy, then save an image-only PDF."""
    page = pdfium.PdfDocument(str(source))[0]
    image = page.render(scale=150 / 72).to_pil().convert("L")
    angle = rng.choice([-1, 1]) * rng.uniform(0.8, 2.2)
    image = image.rotate(angle, expand=True, fillcolor=238, resample=Image.BICUBIC)
    image = image.filter(ImageFilter.GaussianBlur(0.7))
    noise = Image.effect_noise(image.size, 30)
    image = Image.blend(image, noise, 0.10)
    image.save(target, "PDF", resolution=150)


def main() -> None:
    rng = random.Random(7)
    truth = json.loads((INVOICES_DIR / "ground_truth.json").read_text(encoding="utf-8"))
    for old in INVOICES_DIR.glob("INV-*.pdf"):
        old.unlink()
    scanned = 0
    with tempfile.TemporaryDirectory() as tmp:
        for entry in truth:
            spec = entry["expected_invoice"]
            target = INVOICES_DIR / entry["file"]
            if entry["scanned"]:
                clean = Path(tmp) / entry["file"]
                render_pdf(spec, clean)
                make_scan(clean, target, rng)
                scanned += 1
            else:
                render_pdf(spec, target)
    print(f"rendered {len(truth)} PDFs ({scanned} scanned) into {INVOICES_DIR}")


if __name__ == "__main__":
    main()
