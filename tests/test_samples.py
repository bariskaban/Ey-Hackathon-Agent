"""The 30 sample invoices (scripts/make_samples.py): consistent data, backed by records, and different to look at."""
import hashlib
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "data")]

import make_samples

from servers.common.config import DB_PATH, INVOICES_DIR


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    """Make the samples once, into a temporary folder."""
    target = tmp_path_factory.mktemp("samples")
    original = make_samples.SAMPLES_DIR
    make_samples.SAMPLES_DIR = target
    try:
        make_samples.main()
    finally:
        make_samples.SAMPLES_DIR = original
    return target


@pytest.fixture(scope="module")
def sample_truth():
    return json.loads((INVOICES_DIR / "sample_ground_truth.json").read_text(encoding="utf-8"))


def test_thirty_files_of_types_the_inbox_accepts(folder):
    files = sorted(folder.glob("sample-*"))
    assert len(files) == 30 and (folder / "answer-key.md").exists()
    for path in files:
        assert path.suffix in {".pdf", ".png", ".jpg"}
        assert 0 < path.stat().st_size <= 10 * 1024 * 1024
        if path.suffix == ".pdf":
            pdf = pdfium.PdfDocument(str(path))
            try:
                assert 1 <= len(pdf) <= 10
            finally:
                pdf.close()


def test_no_two_samples_look_alike(folder):
    hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("sample-*")}
    assert len(hashes) == 30
    looks = {(d["layout"], d["palette"]) for d in make_samples.DESIGNS}
    assert len(looks) == 30  # no layout and colour pair is used twice
    assert len({d["layout"] for d in make_samples.DESIGNS}) >= 10  # many document structures
    assert len({d["logo"] for d in make_samples.DESIGNS}) >= 10  # many logos
    assert len({d["out"] for d in make_samples.DESIGNS}) >= 6  # PDFs, scans, a fax, photos, screenshots
    assert len({(d["money"], d["date"]) for d in make_samples.DESIGNS}) >= 15  # number and date formats differ


def test_every_new_sample_is_backed_by_the_records(sample_truth):
    assert len(sample_truth) == 23
    con = sqlite3.connect(DB_PATH)
    try:
        for entry in sample_truth:
            spec = entry["expected_invoice"]
            po = con.execute("SELECT vendor_id FROM purchase_orders WHERE po_number = ?", (spec["po_number"],)).fetchone()
            vendor = con.execute("SELECT 1 FROM vendors WHERE vat_number = ?", (spec["supplier"]["vat_number"],)).fetchone()
            if entry["slug"] == "unknown-vendor":
                assert po is None and vendor is None  # nothing in the records, on purpose
            elif entry["slug"] != "invalid-vat-number":
                assert po is not None and vendor is not None, entry["slug"]
    finally:
        con.close()


def test_clean_samples_add_up(sample_truth):
    clean = [e for e in sample_truth if e["error_type"] == "CLEAN"]
    assert len(clean) == 8
    for entry in clean:
        spec = entry["expected_invoice"]
        net = sum(Decimal(line["line_total"]) for line in spec["lines"])
        assert net == Decimal(spec["total_excl_vat"]), entry["slug"]
        assert net + Decimal(spec["total_vat"]) == Decimal(spec["total_incl_vat"]), entry["slug"]
        assert entry["expected_verdict"] == "MATCHED"


def test_the_samples_cover_every_verdict_and_every_error(sample_truth):
    cases = make_samples.load_cases()
    assert {c["verdict"] for c in cases} == {"MATCHED", "MISMATCH", "PARTIAL_MATCH", "NOT_FOUND", "NEEDS_REVIEW"}
    errors = {c["error"] for c in cases}
    assert {"WRONG_AMOUNT", "QUANTITY_NOT_RECEIVED", "DUPLICATE_INVOICE", "ALREADY_PAID", "UNKNOWN_VENDOR",
            "VAT_ARITHMETIC_ERROR", "WRONG_VAT_RATE", "REVERSE_CHARGE_ERROR", "INVALID_VAT_NUMBER",
            "MISSING_MANDATORY_FIELDS", "CHANGED_BANK_DETAILS", "SANCTIONED_VENDOR", "UNUSUAL_AMOUNT",
            "PROMPT_INJECTION", "CLEAN", "MULTIPLE"} <= errors


def test_text_samples_can_be_read_and_the_hidden_instruction_is_in_the_text(folder):
    def text_of(path: Path) -> str:
        pdf = pdfium.PdfDocument(str(path))
        try:
            return "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
        finally:
            pdf.close()

    cases = make_samples.load_cases()
    plain = folder / "sample-08-clean-it-consulting.pdf"
    assert cases[7]["spec"]["invoice_number"] in text_of(plain)
    hidden = text_of(folder / "sample-28-hidden-prompt-injection.pdf")
    assert "SYSTEM INSTRUCTION" in hidden  # present for a text extractor, invisible on the page
    scan = pdfium.PdfDocument(str(folder / "sample-02-clean-domestic-scan.pdf"))
    try:
        assert scan[0].get_textpage().count_chars() == 0  # image only: needs OCR
    finally:
        scan.close()
