"""The UI's view of the real backend (ui/real_service.py): same answers, in the shape the pages read."""

import asyncio

import pytest
from stubs import StubLLM, trusting_extraction

from agent import service
from agent.schemas import EvalInvoiceResult, EvalReport, Invoice, VerdictStatus
from ui import real_service as ui_service

pytestmark = pytest.mark.usefixtures("temp_db")


def run_scenario(entry, action):
    """Verify one invoice through the UI service (no language model), then run `action` on the results."""
    stub = StubLLM(Invoice.model_validate(entry["expected_invoice"]).model_dump_json())
    stub.model = "stub-model"

    async def scenario():
        await service._runtime.get()  # starts the real MCP servers
        service._runtime.llm = stub
        try:
            with trusting_extraction():
                events = [e async for e in _verify_without_agent(entry["invoice_id"])]
            return events, await action(events)
        finally:
            await service.close()

    return asyncio.run(scenario())


async def _verify_without_agent(doc_id):
    """ui_service.verify() with the model's assessment turned off, so the test needs no Ollama."""
    original = service.verify

    def no_agent(doc, **kwargs):
        return original(doc, use_agent=False)

    service.verify = no_agent
    try:
        async for event in ui_service.verify(doc_id):
            yield event
    finally:
        service.verify = original


def test_the_stream_uses_the_ui_names_and_shapes(inbox, case):
    entry = case("quantity_not_received")
    events, _ = run_scenario(entry, lambda events: asyncio.sleep(0))

    verdict_event = events[-1]
    assert verdict_event.type == "verdict" and verdict_event.verdict.verdict == "MISMATCH"
    checks = [e for e in events if e.type == "check_result"]
    assert {c.name for c in checks} == set(ui_service.EXPECTED_CHECKS)  # every check the checklist waits for
    assert all(ui_service.EXPECTED_CHECKS[c.name] == c.server for c in checks)
    assert all(hasattr(e, "result_preview") and hasattr(e, "refusal_reason") for e in events if e.type == "tool_call")
    extracted = next(e for e in events if e.type == "invoice_extracted").invoice
    assert extracted.supplier_name and isinstance(extracted.vat_lines, list) and extracted.iban_last4
    assert verdict_event.verdict.invoice.supplier_name == extracted.supplier_name
    assert verdict_event.verdict.discrepancies[0].check == "goods_received"


def test_documents_verdict_cases_and_audit_log_after_a_run(inbox, case):
    entry = case("quantity_not_received")
    doc_id = entry["invoice_id"]

    async def after(events):
        documents = {d.doc_id: d for d in await ui_service.list_documents()}
        verdict = await ui_service.get_verdict(doc_id)
        cases = await ui_service.list_cases()
        case_id = cases[0].case_id
        noted = await ui_service.add_case_note(case_id, "Called the supplier.", "Reviewer")
        with pytest.raises(ValueError, match="required"):
            await ui_service.set_case_status(case_id, "APPROVED", "Reviewer", "  ")
        decided = await ui_service.set_case_status(case_id, "REJECTED", "Reviewer", "Goods never arrived.")
        audit = await ui_service.get_audit_log(doc_id=doc_id)
        status = await ui_service.system_status()
        return documents, verdict, cases, noted, decided, audit, status

    _, (documents, verdict, cases, noted, decided, audit, status) = run_scenario(entry, after)

    doc = documents[doc_id]
    assert doc.status == "VERIFIED" and doc.verdict == "MISMATCH" and doc.vendor_name and doc.amount_total
    assert doc.main_finding == "QUANTITY_NOT_RECEIVED"
    assert any(d.status == "NOT_VERIFIED" and d.verdict is None for d in documents.values())  # the rest wait

    assert verdict.verdict == "MISMATCH" and verdict.model and verdict.tool_calls > 0
    assert verdict.vat.reverse_charge_applies is False and verdict.vat.shown_correctly is True
    assert verdict.vat.lines and verdict.bank.iban_matches is True and verdict.bank.vendor_iban_last4
    assert {c.name for c in verdict.checks} == set(ui_service.EXPECTED_CHECKS)

    assert len(cases) == 1 and cases[0].doc_id == doc_id and cases[0].status == "OPEN"
    assert cases[0].severity == "medium" and cases[0].error_type == "QUANTITY_NOT_RECEIVED"
    assert cases[0].vendor_name == doc.vendor_name and cases[0].checks_total == 16 and cases[0].decision is None
    assert noted.notes[-1].author == "Reviewer" and noted.notes[-1].text == "Called the supplier."
    assert decided.status == "REJECTED" and decided.decision.reviewer == "Reviewer"
    assert decided.decision.reason == "Goods never arrived."

    assert audit and all(e.doc_id == doc_id for e in audit) and all(e.seq for e in audit)
    assert status.service_mode == "real" and {r.role for r in status.roles} == {"extractor", "verifier", "reporter"}
    assert status.servers_total == 6


def test_an_evaluation_report_becomes_the_rows_the_eval_page_draws():
    row = EvalInvoiceResult(
        invoice_id="INV-001", expected_verdict=VerdictStatus.MISMATCH, expected_error="WRONG_AMOUNT",
        verdicts=[VerdictStatus.MISMATCH] * 3, error_caught=[True] * 3, caught_by=["po_amount_match"],
        consistent=True, outcome="pass", passed=True, seconds=30.0, tool_calls=14)
    report = EvalReport(run_id="r1", model="qwen3:8b", created_at="2026-11-05T10:00:00+00:00", runs_per_invoice=3,
                        invoices=[row], passed=1, total=1, consistent=1, mean_seconds=30.0)
    shown = ui_service._ns(ui_service._report(report))
    assert shown.report_id == "r1" and shown.sample is False and shown.duration_s == 90
    assert shown.rows[0].doc_id == "INV-001" and shown.rows[0].planted_error == "WRONG_AMOUNT"
    assert shown.rows[0].runs == ["MISMATCH"] * 3 and shown.rows[0].caught_by == "po_match"


def test_a_replay_plays_the_recorded_run_without_running_anything(inbox, case):
    entry = case("quantity_not_received")
    doc_id = entry["invoice_id"]

    async def after(events):
        before = len(await ui_service.get_audit_log(doc_id=doc_id))
        replayed = [e async for e in ui_service.replay(doc_id, speed=1000)]
        with pytest.raises(ValueError, match="no recording"):
            [e async for e in ui_service.replay("INV-999")]
        return replayed, before, len(await ui_service.get_audit_log(doc_id=doc_id))

    events, (replayed, before, after_count) = run_scenario(entry, after)
    assert [e.type for e in replayed] == [e.type for e in events]
    assert replayed[-1].verdict.verdict == "MISMATCH" and replayed[-1].verdict.discrepancies
    assert after_count == before  # nothing was run, so nothing new was logged
