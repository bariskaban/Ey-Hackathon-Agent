"""Review cases (append-only), evidence packs, the reporter stage, and OCR-misread handling."""

import asyncio
import copy
import sqlite3
from pathlib import Path

import pytest
from openpyxl import load_workbook
from stubs import TOOLS, DirectGateway, StubLLM, trusting_extraction

from agent import pipeline, service
from agent.gateway import Gateway
from agent.schemas import CaseStatus, Invoice, Severity, VerdictStatus
from servers.common.db import read_only, writable
from servers.documents import tools as document_tools
from servers.review_cases import store, tools

pytestmark = pytest.mark.usefixtures("temp_db")

EVIDENCE = {
    "verdict": {"invoice_id": "INV-1", "verdict": "MISMATCH", "confidence": 0.9, "reasoning": "R & D <b>bold</b>",
                "discrepancies": [{"type": "WRONG_AMOUNT", "severity": "medium", "field": "total_excl_vat",
                                   "expected": "1", "found": "2", "detail": "d"}],
                "checks": [{"name": "po_amount_match", "server": "transactions", "status": "fail", "detail": "x"}]},
    "invoice": {"invoice_number": "F-1", "lines": [{"sku": "A", "description": "x", "quantity": "1"}]},
}


def evidence_with(severity: str) -> dict:
    ev = copy.deepcopy(EVIDENCE)
    ev["verdict"]["discrepancies"][0]["severity"] = severity
    return ev


# --------------------------------------------------------------------------- the server tools


def test_create_note_and_idempotent_reopen():
    first = tools.create_review_case("INV-1", "MISMATCH", EVIDENCE)
    assert first == {"case_id": "RC-0001", "created": True, "status": "open"}
    again = tools.create_review_case("INV-1", "MISMATCH", EVIDENCE)
    assert again["case_id"] == "RC-0001" and again["created"] is False  # one open case per invoice
    updated = tools.add_case_note("RC-0001", "Checked the purchase order.")
    assert [n["author"] for n in updated["notes"]] == ["agent", "agent"] and "evidence" not in updated
    assert tools.create_review_case("INV-2", "NOT_FOUND", EVIDENCE)["case_id"] == "RC-0002"


@pytest.mark.parametrize("call", [
    lambda: tools.create_review_case("INV-1", "APPROVED", EVIDENCE),
    lambda: tools.create_review_case("", "MISMATCH", EVIDENCE),
    lambda: tools.create_review_case("INV-1", "MISMATCH", "not a dict"),
    lambda: tools.create_review_case("INV-1", "MISMATCH", {"blob": "x" * 500_000}),
    lambda: tools.add_case_note("RC-9999", "hello"),
    lambda: tools.add_case_note("RC-0001", "  "),
    lambda: tools.export_evidence_pack("RC-9999"),
    lambda: tools.export_evidence_pack("RC-0001", "docx"),
])
def test_bad_requests_are_readable_errors(call):
    tools.create_review_case("INV-X", "MISMATCH", EVIDENCE)
    with pytest.raises(ValueError):
        call()


def test_note_length_limit():
    tools.create_review_case("INV-1", "MISMATCH", EVIDENCE)
    with pytest.raises(ValueError, match="too long"):
        tools.add_case_note("RC-0001", "x" * (tools.MAX_NOTE_CHARS + 1))


def test_cases_and_notes_are_append_only_even_with_a_writable_connection():
    tools.create_review_case("INV-1", "MISMATCH", EVIDENCE)
    tools.add_case_note("RC-0001", "a note")
    statements = ["DELETE FROM review_cases", "UPDATE review_cases SET verdict = 'MATCHED'",
                  "UPDATE review_cases SET evidence_json = '{}'", "UPDATE review_cases SET invoice_id = 'other'",
                  "DELETE FROM case_notes", "UPDATE case_notes SET text = 'changed'"]
    for statement in statements:
        with pytest.raises(sqlite3.IntegrityError, match="append-only|cannot be changed"), writable() as con:
            con.execute(statement)


def test_evidence_packs_are_written(temp_db):
    tools.create_review_case("INV-1", "MISMATCH", EVIDENCE)
    tools.add_case_note("RC-0001", "a note")
    pdf = tools.export_evidence_pack("RC-0001", "pdf")
    xlsx = tools.export_evidence_pack("RC-0001", "xlsx")
    from servers.common.config import DATA_DIR

    assert pdf["path"] == "outbox/RC-0001.pdf" and (DATA_DIR / pdf["path"]).read_bytes()[:4] == b"%PDF"
    sheets = load_workbook(DATA_DIR / xlsx["path"]).sheetnames
    assert sheets == ["Summary", "Discrepancies", "Checks", "Invoice", "Lines", "Tool calls", "Notes"]
    rows = list(load_workbook(DATA_DIR / xlsx["path"])["Discrepancies"].iter_rows(values_only=True))
    assert rows[1][0] == "WRONG_AMOUNT" and rows[1][1] == "medium"


# --------------------------------------------------------------------------- the human side (service)


def test_service_lists_cases_most_severe_first_and_filters_by_status():
    tools.create_review_case("INV-1", "PARTIAL_MATCH", evidence_with("low"))
    tools.create_review_case("INV-2", "MISMATCH", evidence_with("high"))
    tools.create_review_case("INV-3", "MISMATCH", evidence_with("medium"))

    async def scenario():
        everything = await service.list_cases()
        closed = await service.set_case_status("RC-0003", "approved", "Pat", "The price was renegotiated")
        return everything, closed, await service.list_cases("open"), await service.list_cases("approved")

    everything, closed, open_cases, approved = asyncio.run(scenario())
    assert [c.invoice_id for c in everything] == ["INV-2", "INV-3", "INV-1"]
    assert everything[0].top_severity == Severity.HIGH and everything[0].error_types[0].value == "WRONG_AMOUNT"
    assert closed.status == CaseStatus.APPROVED and closed.status_changed_by == "Pat"
    assert "approved: The price was renegotiated" in closed.notes[-1].text and closed.notes[-1].author == "Pat"
    assert [c.invoice_id for c in open_cases] == ["INV-2", "INV-1"] and [c.invoice_id for c in approved] == ["INV-3"]
    assert closed.evidence == everything[1].evidence  # the evidence itself is never touched (INV-3 was second)


def test_only_a_named_person_with_a_reason_can_set_a_status():
    tools.create_review_case("INV-1", "MISMATCH", EVIDENCE)

    async def scenario():
        for args in (("RC-0001", "approved", "", "reason"), ("RC-0001", "approved", "Pat", " "),
                     ("RC-0001", "paid", "Pat", "reason"), ("RC-9999", "approved", "Pat", "reason")):
            with pytest.raises(ValueError):
                await service.set_case_status(*args)

    asyncio.run(scenario())
    with read_only() as con:
        assert store.get_case(con, "RC-0001")["status"] == "open"


def test_service_exports_an_evidence_pack():
    tools.create_review_case("INV-1", "MISMATCH", EVIDENCE)
    path = asyncio.run(service.export_evidence_pack("RC-0001", "xlsx"))
    assert isinstance(path, Path) and path.exists() and path.suffix == ".xlsx"


def test_agents_cannot_reach_the_status_change(inbox, tmp_path):
    from agent.roles import ROLES
    from servers.common.audit import AuditLog

    async def scenario():
        async with Gateway(audit=AuditLog(tmp_path / "audit.jsonl")) as gateway:
            refused = await gateway.call("reporter", "get_vendor", {"name_or_vat_number": "x"})
            return gateway.tool_names(), refused, {r: gateway.llm_tools(r) for r in ROLES}

    names, refused, llm_tools = asyncio.run(scenario())
    assert "set_case_status" not in names and not any("status" in n for n in names if "case" in n)
    assert {"create_review_case", "add_case_note", "export_evidence_pack"} <= set(names)
    assert refused["refused"]  # the reporter may only use review_cases
    assert {t["function"]["name"] for t in llm_tools["verifier"]} == {
        "get_vendor", "lookup_invoice_number", "find_transactions", "get_purchase_order",
        "vendor_payment_summary", "convert_document"}
    assert {t["function"]["name"] for t in llm_tools["extractor"]} == {"convert_document", "get_document_metadata"}


# --------------------------------------------------------------------------- the reporter stage


def run_pipeline(entry, invoice=None, **kwargs):
    invoice_json = (invoice or Invoice.model_validate(entry["expected_invoice"])).model_dump_json()
    events, gateway = [], DirectGateway()
    with trusting_extraction():
        verdict = asyncio.run(pipeline.verify_document(entry["invoice_id"], gateway, StubLLM(invoice_json),
                                                       events.append, use_agent=False, **kwargs))
    return verdict, events


def test_a_mismatch_opens_a_case_with_evidence_and_a_note(inbox, case):
    verdict, events = run_pipeline(case("wrong_amount"))
    assert verdict.verdict == VerdictStatus.MISMATCH and verdict.review_case_id == "RC-0001"
    stages = [(e.stage.value, e.status.value) for e in events if e.type == "stage"]
    assert ("report", "started") in stages and ("report", "done") in stages
    assert events[-1].type == "verdict" and events[-1].verdict.review_case_id == "RC-0001"
    assert [e.tool for e in events if e.type == "tool_call" and e.role == "reporter"] == [
        "create_review_case", "add_case_note"]
    with read_only() as con:
        stored = store.get_case(con, "RC-0001")
    assert stored["status"] == "open" and stored["evidence"]["verdict"]["verdict"] == "MISMATCH"
    assert stored["evidence"]["invoice"]["invoice_number"] and stored["error_types"] == ["WRONG_AMOUNT"]
    assert stored["notes"][0]["text"].startswith("Assessment:")


def test_a_clean_match_opens_no_case(inbox, case):
    verdict, events = run_pipeline(case("clean_books"))
    assert verdict.verdict == VerdictStatus.MATCHED and verdict.review_case_id is None
    assert not any(e.type == "stage" and e.stage.value == "report" for e in events)
    with read_only() as con:
        assert con.execute("SELECT COUNT(*) FROM review_cases").fetchone()[0] == 0


def test_verifying_again_reuses_the_open_case(inbox, case):
    first, _ = run_pipeline(case("duplicate"))
    second, events = run_pipeline(case("duplicate"))
    assert first.review_case_id == second.review_case_id == "RC-0001"
    with read_only() as con:
        notes = store.get_case(con, "RC-0001")["notes"]
        assert con.execute("SELECT COUNT(*) FROM review_cases").fetchone()[0] == 1
    assert any("verified again" in n["text"] for n in notes)
    assert [e.tool for e in events if e.type == "tool_call" and e.role == "reporter"] == ["create_review_case"]


def test_documents_that_cannot_be_read_still_open_a_case(inbox, case, monkeypatch):
    original = document_tools.convert_document
    monkeypatch.setitem(TOOLS, "convert_document", (
        "documents", lambda **kw: {**original(**kw), "language": "nl", "language_confidence": 0.99}))
    verdict, _ = run_pipeline(case("clean_standard"))
    assert verdict.verdict == VerdictStatus.NEEDS_REVIEW and verdict.review_case_id
    with read_only() as con:
        assert store.get_case(con, verdict.review_case_id)["evidence"]["invoice"] is None


# --------------------------------------------------------------------------- scans: misreads vs. real errors


def as_scan(monkeypatch):
    original = document_tools.convert_document
    monkeypatch.setitem(TOOLS, "convert_document", ("documents", lambda **kw: {**original(**kw), "method": "ocr"}))


def misread_invoice(entry, **changes):
    invoice = Invoice.model_validate(entry["expected_invoice"])
    for path, value in changes.items():
        target = invoice.supplier if path.startswith("supplier.") else invoice
        setattr(target, path.split(".")[-1], value)
    return invoice


def real_check_run(entry, invoice):
    """Like run_pipeline but with the real source-text confidence check switched on."""
    events = []
    verdict = asyncio.run(pipeline.verify_document(entry["invoice_id"], DirectGateway(),
                                                   StubLLM(invoice.model_dump_json()), events.append, use_agent=False))
    return verdict


def test_a_scan_misread_of_the_vat_number_and_iban_is_repaired_not_rejected(inbox, case, monkeypatch):
    as_scan(monkeypatch)
    entry = case("clean_books")
    supplier = entry["expected_invoice"]["supplier"]
    garbled = misread_invoice(entry, **{"supplier.vat_number": supplier["vat_number"].replace("B01", "801"),
                                        "supplier.iban": supplier["iban"][:-1]})
    verdict = real_check_run(entry, garbled)
    assert verdict.verdict == VerdictStatus.MATCHED, (verdict.needs_review_reasons, verdict.discrepancies)
    assert any("VAT number misread by OCR" in f for f in verdict.risk_flags)
    assert any("IBAN misread by OCR" in f for f in verdict.risk_flags)
    assert verdict.review_case_id is None


def test_the_same_garbling_in_a_text_pdf_is_a_real_error(inbox, case):
    entry = case("clean_books")
    supplier = entry["expected_invoice"]["supplier"]
    garbled = misread_invoice(entry, **{"supplier.vat_number": supplier["vat_number"].replace("B01", "801")})
    verdict = run_pipeline(entry, garbled)[0]
    assert "INVALID_VAT_NUMBER" in {d.type.value for d in verdict.discrepancies}  # no OCR excuse in a text PDF


def test_a_scan_with_a_wrong_but_valid_looking_iban_is_still_flagged(inbox, case, monkeypatch):
    as_scan(monkeypatch)
    entry = case("changed_bank")  # a different, valid IBAN: not explainable as a misread
    verdict = run_pipeline(entry)[0]
    assert "CHANGED_BANK_DETAILS" in {d.type.value for d in verdict.discrepancies}


def test_unreadable_fields_on_a_scan_need_a_person_but_are_not_rejections(inbox, case, monkeypatch):
    as_scan(monkeypatch)
    entry = case("clean_standard")
    unreadable = misread_invoice(entry, invoice_date="AUAE-10-19")
    verdict = run_pipeline(entry, unreadable)[0]
    assert verdict.verdict == VerdictStatus.NEEDS_REVIEW
    assert "invoice_date" in verdict.needs_review_reasons[0]
    assert "MISSING_MANDATORY_FIELDS" not in {d.type.value for d in verdict.discrepancies}


def test_a_duplicate_on_a_scan_with_an_unreadable_date_is_still_a_mismatch(inbox, case, monkeypatch):
    as_scan(monkeypatch)
    entry = case("duplicate")
    verdict = run_pipeline(entry, misread_invoice(entry, invoice_date="seca di"))[0]
    assert "DUPLICATE_INVOICE" in {d.type.value for d in verdict.discrepancies}
    assert verdict.verdict == VerdictStatus.MISMATCH and verdict.review_case_id  # the finding stands
    assert any("invoice_date" in r for r in verdict.needs_review_reasons)  # and the gap is still on record


def test_an_unrepairable_vat_number_on_a_scan_is_flagged_with_a_note_to_check_the_original(inbox, case, monkeypatch):
    as_scan(monkeypatch)
    entry = case("invalid_vat")
    verdict = run_pipeline(entry)[0]
    assert "INVALID_VAT_NUMBER" in {d.type.value for d in verdict.discrepancies}
    assert any("scan" in flag for flag in verdict.risk_flags)


def test_every_invoice_stays_within_the_call_budget(inbox, ground_truth):
    from agent.gateway import DEFAULT_MAX_CALLS

    for entry in ground_truth:
        gateway = DirectGateway()
        events = []
        asyncio.run(pipeline.verify_document(
            entry["invoice_id"], gateway, StubLLM(Invoice.model_validate(entry["expected_invoice"]).model_dump_json()),
            events.append, use_agent=False))
        assert len(gateway.calls) <= DEFAULT_MAX_CALLS - 10, (entry["case"], len(gateway.calls))  # room for the agent
