"""Documents server: inbox manifest, conversion, OCR fallback, language check, safety rules."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from agent.gateway import Gateway
from servers.common.audit import AuditLog
from servers.common.config import INVOICES_DIR
from servers.documents import convert, manifest, tools

# Where the test data lives (conftest generates it) and which PDFs were rendered
# Rendering needs a separate step, so render into the test data folder here.


@pytest.fixture(scope="module")
def inbox(tmp_path_factory, ground_truth):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "data" / "render_pdfs.py")], check=True, cwd=root)
    folder = tmp_path_factory.mktemp("inbox")
    for entry in ground_truth:
        shutil.copy2(INVOICES_DIR / entry["file"], folder / entry["file"])
    os.environ["INVOICE_INBOX_DIR"] = str(folder)
    os.environ["INVOICE_CACHE_DIR"] = str(tmp_path_factory.mktemp("cache"))
    result = manifest.sync_inbox()
    yield folder, result
    os.environ.pop("INVOICE_INBOX_DIR")
    os.environ.pop("INVOICE_CACHE_DIR")


def test_manifest_has_all_documents_and_flags_scans(inbox, ground_truth):
    _folder, result = inbox
    assert len(result["documents"]) == 20 and result["rejected"] == []
    for entry in ground_truth:
        doc = result["documents"][entry["invoice_id"]]
        assert doc["has_text_layer"] is (not entry["scanned"])
        assert len(doc["sha256"]) == 64 and doc["pages"] == 1 and doc["mime_type"] == "application/pdf"


def test_list_documents_never_shows_paths(inbox):
    listing = tools.list_documents()
    assert listing["returned"] == 20
    assert all(set(d) == {"doc_id", "filename", "type", "size_bytes", "pages"} for d in listing["documents"])
    assert "\\" not in str(listing) and "/" not in str(listing["documents"][0]["filename"])


def test_text_pdf_is_converted_with_labels_next_to_values(inbox, ground_truth):
    text_doc = next(g for g in ground_truth if not g["scanned"] and g["case"] == "clean_books")
    result = tools.convert_document(text_doc["invoice_id"])
    assert result["method"] in ("markitdown", "pdf_text") and result["language"] == "en"
    invoice = text_doc["expected_invoice"]
    for needle in (invoice["invoice_number"], invoice["supplier"]["vat_number"], invoice["total_incl_vat"]):
        flat = result["markdown"].replace(",", "")
        assert needle.replace(",", "") in flat


def test_scanned_pdf_uses_ocr_and_stays_readable(inbox, ground_truth):
    scan = next(g for g in ground_truth if g["scanned"])
    result = tools.convert_document(scan["invoice_id"])
    assert result["method"] == "ocr" and any("OCR" in w for w in result["warnings"])
    invoice = scan["expected_invoice"]
    flat = result["markdown"].replace(",", "").replace(" ", "")
    found = [n for n in (invoice["invoice_number"], invoice["invoice_date"], invoice["supplier"]["vat_number"],
                         invoice["total_incl_vat"]) if n.replace(",", "") in flat]
    assert len(found) >= 3, "OCR lost too many key values"


def test_output_is_capped_and_pageable(inbox, ground_truth, monkeypatch):
    monkeypatch.setattr(tools, "MAX_CHARS", 300)
    doc_id = ground_truth[0]["invoice_id"]
    first = tools.convert_document(doc_id)
    assert first["truncated"] and len(first["markdown"]) == 300 and first["next_offset"] == 300
    second = tools.convert_document(doc_id, offset=first["next_offset"])
    assert second["markdown"] != first["markdown"] and second["offset"] == 300
    pieces, offset = [], 0
    while True:
        page = tools.convert_document(doc_id, offset=offset)
        pieces.append(page["markdown"])
        if not page["truncated"]:
            break
        offset = page["next_offset"]
    assert len("".join(pieces)) == first["char_count"]


def test_results_are_cached_by_file_hash(inbox, ground_truth, monkeypatch):
    doc_id = ground_truth[1]["invoice_id"]
    tools.convert_document(doc_id)
    monkeypatch.setattr(convert, "_convert_pdf", lambda *a, **k: pytest.fail("conversion should be cached"))
    assert tools.convert_document(doc_id)["char_count"] > 0


def test_metadata(inbox, ground_truth):
    meta = tools.get_document_metadata(ground_truth[0]["invoice_id"])
    assert meta["mime_type"] == "application/pdf" and len(meta["sha256"]) == 64 and "path" not in meta


@pytest.mark.parametrize("bad", ["../../etc/passwd", "C:\\Windows\\win.ini", "file:///etc/passwd",
                                 "http://example.com/x.pdf", "INV-001.pdf", "INV-999", "", "a" * 80, None])
def test_only_known_doc_ids_are_accepted(inbox, bad):
    with pytest.raises(ValueError):
        tools.convert_document(bad)
    with pytest.raises(ValueError):
        tools.get_document_metadata(bad)


def test_bad_page_selection_rejected(inbox, ground_truth):
    for spec in ("0", "2", "1-5", "abc", "-1"):
        with pytest.raises(ValueError):
            tools.convert_document(ground_truth[0]["invoice_id"], pages=spec)
    assert tools.convert_document(ground_truth[0]["invoice_id"], pages=1)["pages_processed"] == [1]


def test_files_are_checked_before_entering_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOICE_INBOX_DIR", str(tmp_path))
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "fake.pdf").write_bytes(b"this is not a pdf at all")
    (tmp_path / "empty.pdf").write_bytes(b"")
    (tmp_path / "big.pdf").write_bytes(b"%PDF-1.4" + b"0" * (manifest.MAX_BYTES + 1))
    result = manifest.sync_inbox()
    assert result["documents"] == {}
    reasons = {r["filename"]: r["reason"] for r in result["rejected"]}
    assert set(reasons) == {"notes.txt", "fake.pdf", "empty.pdf", "big.pdf"}
    assert "not allowed" in reasons["notes.txt"] and "does not look like" in reasons["fake.pdf"]


def test_language_detection():
    english = "Invoice number 2026-001. Please pay the total amount of the invoice within thirty days."
    dutch = "Factuur nummer 2026-001. Gelieve het totale bedrag van de factuur binnen dertig dagen te betalen."
    assert convert.detect_language(english)[0] == "en"
    assert convert.detect_language(dutch)[0] == "nl"
    assert convert.detect_language("EUR 12")[0] == "unknown"


def test_extractor_role_can_only_reach_the_documents_server(inbox, ground_truth, tmp_path):
    async def scenario():
        async with Gateway(audit=AuditLog(tmp_path / "audit.jsonl"), server_names=["documents", "transactions"]) as gw:
            doc = await gw.call("extractor", "convert_document", {"doc_id": ground_truth[0]["invoice_id"]},
                                invoice_id="INV-X")
            other = await gw.call("extractor", "get_vendor", {"name_or_vat_number": "x"}, invoice_id="INV-X")
            return doc, other, gw.llm_tools("extractor")

    doc, other, llm_tools = asyncio.run(scenario())
    assert doc["ok"] and doc["data"]["markdown"]
    assert other["refused"] and "may not use server" in other["error"]
    assert {t["function"]["name"] for t in llm_tools} == {"convert_document", "get_document_metadata"}
