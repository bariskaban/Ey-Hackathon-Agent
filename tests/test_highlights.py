"""Boxing a discrepancy on the original document, and the annotated copy that can be downloaded."""

import hashlib
from types import SimpleNamespace

import pypdfium2 as pdfium
from PIL import Image

from agent.schemas import Invoice
from servers.common.config import INVOICES_DIR
from ui import highlights
from ui.real_service import _ns, flat_invoice


def flat(entry):
    return _ns(flat_invoice(Invoice.model_validate(entry["expected_invoice"])))


def disc(field):
    return SimpleNamespace(field=field)


def test_a_field_becomes_the_text_it_prints(case):
    inv = flat(case("clean_services"))
    amount = f"{float(inv.total_excl_vat):,.2f}"
    assert amount in highlights.targets_for("total_excl_vat", inv).texts
    assert highlights.targets_for("invoice_number", inv).texts == (inv.invoice_number,)
    assert highlights.targets_for("supplier.iban", inv).texts == (inv.iban,)
    row = highlights.targets_for("lines[0].quantity", inv)
    assert row.band and row.texts == (inv.lines[0].description,)
    assert highlights.targets_for("lines[99].vat_rate", inv) is None
    assert highlights.targets_for("vat_breakdown", inv).texts
    assert highlights.targets_for("missing invoice_date", inv) is None  # nothing printed to point at
    assert highlights.targets_for(None, inv) is None


def test_a_text_pdf_gets_a_box_on_the_value_and_the_original_is_not_changed(inbox, case, tmp_path):
    entry = case("wrong_amount")
    path = INVOICES_DIR / entry["file"]
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    inv = flat(entry)

    boxes = highlights.locate(path, [disc("total_excl_vat")], inv)
    assert boxes and all(b.page == 0 and b.index == 0 for b in boxes)
    page = pdfium.PdfDocument(str(path))[0]
    width, height = page.get_size()
    textpage = page.get_textpage()
    for b in boxes:  # each box really contains the amount
        text = textpage.get_text_bounded(b.x * width, (1 - b.y - b.h) * height, (b.x + b.w) * width, (1 - b.y) * height)
        assert f"{float(inv.total_excl_vat):,.2f}" in text

    out = highlights.annotate(path, boxes, [("#DC2626", "Wrong amount")], tmp_path / "annotated.pdf")
    assert len(pdfium.PdfDocument(str(out))) == 2  # the page, plus the page that lists the findings
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_a_scanned_page_is_read_by_ocr_to_find_the_value(inbox, case):
    entry = case("duplicate")  # a scan: no text layer
    assert entry["scanned"]
    boxes = highlights.locate(INVOICES_DIR / entry["file"], [disc("invoice_number")], flat(entry))
    assert boxes and boxes[0].page == 0 and 0 < boxes[0].w < 0.5


def test_an_image_file_works_and_an_unfindable_value_gives_no_boxes(inbox, case, tmp_path):
    entry = case("clean_books")
    page = pdfium.PdfDocument(str(INVOICES_DIR / entry["file"]))[0].render(scale=150 / 72).to_pil()
    photo = tmp_path / "photo.png"
    page.save(photo)
    inv = flat(entry)
    assert highlights.locate(photo, [disc("invoice_number")], inv)
    inv.invoice_number = "ZZZ-NOT-ON-THE-PAGE-9"
    assert highlights.locate(photo, [disc("invoice_number")], inv) == []
    out = highlights.annotate(photo, [highlights.Box(0, 0.1, 0.1, 0.2, 0.05, 0)], [("#DC2626", "x")], tmp_path / "a.pdf")
    assert out.exists() and Image.open(photo).size == page.size
